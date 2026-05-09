package com.ecom.functions;

import com.ecom.events.ClickstreamEvent;
import org.apache.flink.api.common.state.MapState;
import org.apache.flink.api.common.state.MapStateDescriptor;
import org.apache.flink.api.common.state.StateTtlConfig;
import java.time.Duration;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * DedupFunction — stateful Flink {@link KeyedProcessFunction} that drops duplicate
 * {@link ClickstreamEvent}s within a configurable time window.
 *
 * <h2>Design</h2>
 * <p>The upstream stream is keyed by {@code event_id}. Each key partition owns an
 * independent {@code MapState<String, Long>} cell that stores the event_id → first-seen
 * epoch-millis mapping. Because the stream is already partitioned by event_id, the map
 * will contain at most <em>one</em> entry per partition; the map was chosen over
 * {@code ValueState<Long>} per the interface contract so that the key is explicit
 * in the stored state and inspectable via RocksDB / savepoint tools.
 *
 * <h2>TTL policy</h2>
 * <ul>
 *   <li>{@link StateTtlConfig.UpdateType#OnCreateAndWrite} — the TTL window is measured
 *       from when the event_id was <em>first stored</em>. Reads do not reset the
 *       clock, so a duplicated event that is read 1.9 hours after the original cannot
 *       extend the window beyond 2 hours from the initial write.</li>
 *   <li>{@link StateTtlConfig.StateVisibility#NeverReturnExpired} — once the TTL has
 *       elapsed, {@code contains()} returns {@code false} and {@code get()} returns
 *       {@code null}. The entry is logically absent from the state, so the next
 *       occurrence of that event_id is treated as new.</li>
 *   <li>RocksDB compaction filter — expired entries are physically removed during
 *       compaction so they do not accumulate unboundedly.</li>
 * </ul>
 *
 * <h2>Metrics</h2>
 * <ul>
 *   <li>{@code duplicates_dropped} (Counter) — incremented each time an event is
 *       silently discarded. Monitor this counter in Kibana to detect upstream
 *       producer retry storms or at-least-once redelivery spikes.</li>
 * </ul>
 *
 * <h2>Usage in job topology</h2>
 * <pre>
 *   SingleOutputStreamOperator&lt;ClickstreamEvent&gt; deduped = cleaned
 *       .keyBy(e -&gt; e.getEventId().toString())
 *       .process(new DedupFunction())
 *       .name("op:dedup");
 * </pre>
 */
public class DedupFunction extends KeyedProcessFunction<String, ClickstreamEvent, ClickstreamEvent> {

    private static final Logger LOG = LoggerFactory.getLogger(DedupFunction.class);

    // ── Configuration ─────────────────────────────────────────────────────────

    /** Dedup window in hours — events older than this are forgotten. */
    private final int windowHours;

    // ── State ─────────────────────────────────────────────────────────────────

    /**
     * Stores {@code event_id → first-seen-epoch-ms} for events seen within the TTL
     * window.  Declared {@code transient} because Flink manages serialization of
     * state handles through its own checkpointing mechanism, not Java serialization.
     */
    private transient MapState<String, Long> seenEvents;

    // ── Metrics ───────────────────────────────────────────────────────────────

    /** Counts events silently dropped because their event_id was already in state. */
    private transient Counter duplicatesDropped;

    // ── Constructors ──────────────────────────────────────────────────────────

    /** Default: 2-hour dedup window. */
    public DedupFunction() {
        this(2);
    }

    /**
     * @param windowHours size of the dedup window in hours.
     *                    Must be positive. Use 2 h for most production workloads
     *                    to cover the longest realistic producer retry budget.
     */
    public DedupFunction(int windowHours) {
        if (windowHours <= 0) throw new IllegalArgumentException("windowHours must be > 0");
        this.windowHours = windowHours;
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @Override
    public void open(Configuration parameters) throws Exception {
        // ── State TTL ────────────────────────────────────────────────────────
        // OnCreateAndWrite: the clock starts at first-write and resets on updates.
        // A read-only lookup (contains / get) does NOT extend the window.
        // This ensures the dedup window is always measured from the original ingestion,
        // preventing a slow consumer from indefinitely holding an event_id in state.
        StateTtlConfig ttlConfig = StateTtlConfig
                .newBuilder(Duration.ofHours(windowHours))
                .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
                .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                // Incrementally clean up expired entries during RocksDB compaction
                // (fires the expiry filter every 1 000 records scanned).
                .cleanupInRocksdbCompactFilter(1_000)
                .build();

        // ── MapState descriptor ──────────────────────────────────────────────
        // Key   = event_id  (String)
        // Value = first-seen epoch-millis (Long) — useful for auditing and replay
        MapStateDescriptor<String, Long> descriptor = new MapStateDescriptor<>(
                "seen-events",
                Types.STRING,
                Types.LONG);
        descriptor.enableTimeToLive(ttlConfig);

        seenEvents = getRuntimeContext().getMapState(descriptor);

        // ── Metrics ──────────────────────────────────────────────────────────
        // Registered under the operator's default metric group.
        // Visible in Flink Web UI → operator → Custom Metrics → duplicates_dropped.
        duplicatesDropped = getRuntimeContext()
                .getMetricGroup()
                .counter("duplicates_dropped");

        LOG.info("DedupFunction opened | windowHours={}", windowHours);
    }

    // ── Core logic ────────────────────────────────────────────────────────────

    @Override
    public void processElement(
            ClickstreamEvent event,
            Context ctx,
            Collector<ClickstreamEvent> out) throws Exception {

        // Guard: event_id must be non-null by the time we reach this stage
        // (ValidateAndClean enforces this), but be defensive.
        if (event.getEventId() == null) {
            LOG.warn("Received event with null event_id — forwarding without dedup check");
            out.collect(event);
            return;
        }

        String eventId = event.getEventId().toString();

        if (seenEvents.contains(eventId)) {
            // ── Duplicate path ────────────────────────────────────────────────
            // The event_id was stored within the TTL window.
            // NeverReturnExpired guarantees contains() is false for expired entries,
            // so we only reach this branch for genuinely recent duplicates.
            LOG.debug("Dedup drop | event_id={} session={} type={}",
                    eventId, event.getSessionId(), event.getEventType());
            duplicatesDropped.inc();
            return;
        }

        // ── First-seen path ───────────────────────────────────────────────────
        // Record the ingestion timestamp alongside the event_id.
        // OnCreateAndWrite TTL starts the clock here.
        seenEvents.put(eventId, ctx.timestamp() != null
                ? ctx.timestamp()
                : System.currentTimeMillis());

        out.collect(event);
    }
}

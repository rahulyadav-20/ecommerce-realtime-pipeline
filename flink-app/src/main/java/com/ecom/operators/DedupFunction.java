package com.ecom.operators;

import com.ecom.events.ClickstreamEvent;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.time.Time;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * DedupFunction — drops duplicate {@link ClickstreamEvent}s within a configurable
 * TTL window using RocksDB-backed {@link ValueState}.
 *
 * <p>The upstream stream <em>must</em> be keyed by {@code event_id} before this
 * operator. State TTL is reset on every read/write so the window covers the
 * full upstream producer retry budget (default: 2 hours).
 *
 * <p>State backend must be {@code EmbeddedRocksDBStateBackend(incremental=true)}
 * to keep checkpoint sizes manageable at 80 k+ events/sec.
 */
public class DedupFunction extends KeyedProcessFunction<String, ClickstreamEvent, ClickstreamEvent> {

    private static final Logger LOG = LoggerFactory.getLogger(DedupFunction.class);

    private final int windowHours;

    /** {@code true} once we have seen this event_id within the TTL window. */
    private transient ValueState<Boolean> seen;

    public DedupFunction() {
        this(2);
    }

    public DedupFunction(int windowHours) {
        this.windowHours = windowHours;
    }

    @Override
    public void open(Configuration parameters) {
        StateTtlConfig ttl = StateTtlConfig
                .newBuilder(Time.hours(windowHours))
                // Reset TTL on every access — covers the full producer retry window.
                .setUpdateType(StateTtlConfig.UpdateType.OnReadAndWrite)
                // Never surface expired state; treat as if never seen.
                .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                // Lazily clean up expired entries during RocksDB compaction.
                .cleanupInRocksdbCompactFilter(1_000)
                .build();

        ValueStateDescriptor<Boolean> descriptor =
                new ValueStateDescriptor<>("dedup-seen", Boolean.class);
        descriptor.enableTimeToLive(ttl);

        seen = getRuntimeContext().getState(descriptor);
    }

    @Override
    public void processElement(
            ClickstreamEvent event,
            Context ctx,
            Collector<ClickstreamEvent> out) throws Exception {

        if (seen.value() == null) {
            // First time we see this event_id in the window → forward and mark
            seen.update(Boolean.TRUE);
            out.collect(event);
        } else {
            // Duplicate within the TTL window → silently drop
            LOG.debug("Dedup drop | event_id={}", event.getEventId());
        }
    }
}

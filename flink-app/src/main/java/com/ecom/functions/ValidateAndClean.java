package com.ecom.functions;

import com.ecom.events.ClickstreamEvent;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;
import org.apache.flink.util.OutputTag;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;

/**
 * ValidateAndClean — Flink {@link ProcessFunction} that enforces data quality on every
 * inbound {@link ClickstreamEvent} before it enters the deduplication stage.
 *
 * <h2>Design rationale</h2>
 * <p>Keeping ETL concerns (validate + clean) in a single operator minimises state and
 * lets bad records be routed to the DLQ before they consume keyed-state slots in the
 * downstream {@code DedupFunction}. The operator is intentionally stateless so it can
 * be scaled independently and restarted instantly without a savepoint.
 *
 * <h2>Validation rules</h2>
 * <ol>
 *   <li><b>Required fields</b> — {@code event_id}, {@code session_id}, {@code event_type},
 *       {@code device}, {@code ip}, {@code event_time} must all be non-null (and non-blank
 *       for string fields).</li>
 *   <li><b>quantity ≥ 0</b> when present — negative quantities indicate a producer bug.</li>
 *   <li><b>price ≥ 0</b> when present — negative prices indicate a producer bug.</li>
 *   <li><b>event_time within ±48 h</b> of processing wall-clock time — events outside
 *       this window are either clock-skewed producers or delayed replays that would
 *       corrupt watermarks and time-windowed analytics downstream.</li>
 * </ol>
 *
 * <h2>Cleaning operations</h2>
 * <ol>
 *   <li><b>Trim whitespace</b> from all String fields so that {@code "  web  "} and
 *       {@code "web"} are treated identically by downstream engines.</li>
 *   <li><b>Lowercase category</b> so that {@code "Electronics"} and {@code "electronics"}
 *       merge into a single Druid rollup bucket.</li>
 *   <li><b>Normalize device</b> to the canonical set {@code web | android | ios | other}
 *       so that variant spellings (e.g. {@code "browser"}, {@code "iPhone"}) collapse.</li>
 * </ol>
 *
 * <h2>DLQ message format</h2>
 * <pre>
 * {
 *   "reason":      "missing required field: event_id",
 *   "raw_payload": { ...full Avro record as JSON... },
 *   "ingest_ts":   1715000000000
 * }
 * </pre>
 *
 * <h2>Usage in job topology</h2>
 * <pre>
 *   OutputTag&lt;String&gt; dlqTag = ValidateAndClean.DLQ_TAG;
 *
 *   SingleOutputStreamOperator&lt;ClickstreamEvent&gt; cleaned = rawStream
 *       .process(new ValidateAndClean())
 *       .name("op:validate-clean");
 *
 *   cleaned.getSideOutput(dlqTag).sinkTo(dlqSink);
 * </pre>
 */
public class ValidateAndClean extends ProcessFunction<ClickstreamEvent, ClickstreamEvent> {

    private static final Logger LOG = LoggerFactory.getLogger(ValidateAndClean.class);

    // ── Constants ────────────────────────────────────────────────────────────

    /** Maximum allowed difference between event_time and processing time. */
    private static final long MAX_SKEW_MS = 48L * 60 * 60 * 1_000;   // 48 hours in ms

    /**
     * Side-output tag for records that fail validation.
     * Consumers (DLQ sink, alerting, replay) subscribe to this tag.
     * Defined as a static constant so downstream operators can reference it
     * without needing an instance of this class.
     */
    public static final OutputTag<String> DLQ_TAG = new OutputTag<>("dlq") {};

    // ── ProcessFunction ───────────────────────────────────────────────────────

    @Override
    public void processElement(
            ClickstreamEvent event,
            Context ctx,
            Collector<ClickstreamEvent> out) {

        // Step 1 — Validate: if any rule fails, build a DLQ message and return.
        String violation = validate(event);
        if (violation != null) {
            emitToDlq(event, violation, ctx);
            return;
        }

        // Step 2 — Clean: mutate the record in-place (Avro SpecificRecord is mutable).
        // Cleaning only runs on records that have already passed validation, so we can
        // safely assume required fields are non-null here.
        clean(event);

        // Step 3 — Forward the cleaned record to the main output (dedup stage).
        out.collect(event);
    }

    // ── Validation ────────────────────────────────────────────────────────────

    /**
     * Checks every validation rule in priority order.
     *
     * @param e the inbound event (unmodified)
     * @return a human-readable violation description, or {@code null} if the event is valid
     */
    private static String validate(ClickstreamEvent e) {

        // ── 1. Required fields ────────────────────────────────────────────────
        // Each check returns immediately on the first violation so the DLQ message
        // describes exactly one root cause rather than a combined error.

        if (isBlank(e.getEventId()))
            return "missing required field: event_id";

        if (isBlank(e.getSessionId()))
            return "missing required field: session_id";

        if (e.getEventType() == null)
            return "missing required field: event_type";

        if (isBlank(e.getDevice()))
            return "missing required field: device";

        if (isBlank(e.getIp()))
            return "missing required field: ip";

        // event_time is a timestamp-millis logical type → Avro 1.11 generates Instant
        if (e.getEventTime() == null)
            return "missing required field: event_time";

        // ── 2. Value ranges ───────────────────────────────────────────────────

        if (e.getQuantity() != null && e.getQuantity() < 0)
            return "quantity must be >= 0, got: " + e.getQuantity();

        if (e.getPrice() != null && e.getPrice() < 0.0)
            return "price must be >= 0, got: " + e.getPrice();

        // ── 3. Timestamp freshness ────────────────────────────────────────────
        // Events far in the future (clock drift) or past (stale replay outside the
        // hub topic's retention window) cannot be processed correctly by downstream
        // time-windowed aggregations (Druid 1-min rollup, ClickHouse 5-min MV).

        long nowMs = System.currentTimeMillis();
        long evMs  = e.getEventTime().toEpochMilli();
        if (Math.abs(nowMs - evMs) > MAX_SKEW_MS)
            return "event_time out of ±48h window: " + e.getEventTime()
                    + " (now=" + Instant.ofEpochMilli(nowMs) + ")";

        return null;   // all rules passed
    }

    // ── Cleaning ──────────────────────────────────────────────────────────────

    /**
     * Applies in-place cleaning operations to a validated event.
     * Only called after {@link #validate(ClickstreamEvent)} returns {@code null}.
     */
    private static void clean(ClickstreamEvent e) {

        // 1. Trim whitespace from all String fields.
        //    Avro 1.11 with stringType=String generates java.lang.String fields,
        //    so a direct toString() + trim() is safe.
        if (e.getEventId()     != null) e.setEventId(    e.getEventId().toString().strip());
        if (e.getSessionId()   != null) e.setSessionId(  e.getSessionId().toString().strip());
        if (e.getUserId()      != null) e.setUserId(     e.getUserId().toString().strip());
        if (e.getProductId()   != null) e.setProductId(  e.getProductId().toString().strip());
        if (e.getIp()          != null) e.setIp(         e.getIp().toString().strip());
        if (e.getPaymentMode() != null) e.setPaymentMode(e.getPaymentMode().toString().strip());

        // 2. Lowercase category so downstream rollup dimensions are case-consistent.
        //    "Electronics" and "electronics" must hash to the same Druid bucket.
        if (e.getCategory() != null)
            e.setCategory(e.getCategory().toString().strip().toLowerCase());

        // 3. Normalize device to the canonical set: web | android | ios | other.
        //    Producers may send browser-agent strings, OS names, or mixed case.
        if (e.getDevice() != null) {
            String normalised = normaliseDevice(e.getDevice().toString());
            e.setDevice(normalised);
        }
    }

    /**
     * Maps a raw device string to the canonical four-value set.
     *
     * <p>The canonical set is intentionally small — ClickHouse and Druid use it as
     * a low-cardinality dimension, so adding values here requires a schema migration.
     *
     * @param raw the raw device string from the producer
     * @return one of {@code web}, {@code android}, {@code ios}, {@code other}
     */
    static String normaliseDevice(String raw) {
        if (raw == null) return "other";
        return switch (raw.strip().toLowerCase()) {
            case "web", "browser", "desktop", "pwa" -> "web";
            case "android", "android_app"            -> "android";
            case "ios", "iphone", "ipad", "ios_app" -> "ios";
            default                                  -> "other";
        };
    }

    // ── DLQ helpers ───────────────────────────────────────────────────────────

    /**
     * Builds a DLQ JSON message and emits it via the side output.
     *
     * <p>The JSON structure is:
     * <pre>
     * {
     *   "reason":      "&lt;validation failure description&gt;",
     *   "raw_payload": &lt;Avro JSON representation of the record&gt;,
     *   "ingest_ts":   &lt;epoch-millis at the time of DLQ emission&gt;
     * }
     * </pre>
     *
     * <p>Using Avro's built-in {@code toString()} for {@code raw_payload} avoids
     * an explicit Jackson dependency in the ETL job. The format is valid JSON and
     * contains all field values, including nulls for optional fields.
     *
     * @param event     the invalid event
     * @param reason    human-readable validation failure description
     * @param ctx       Flink process-function context for side-output access
     */
    private static void emitToDlq(ClickstreamEvent event, String reason, Context ctx) {
        String eventId = event.getEventId() != null ? event.getEventId().toString() : "null";
        LOG.debug("DLQ | reason={} event_id={}", reason, eventId);

        // Avro SpecificRecord.toString() returns a JSON representation.
        // Escape the reason string to keep the outer JSON well-formed.
        String dlq = String.format(
                "{\"reason\":\"%s\",\"raw_payload\":%s,\"ingest_ts\":%d}",
                escapeJson(reason),
                event,                              // Avro toString() → JSON
                System.currentTimeMillis());

        ctx.output(DLQ_TAG, dlq);
    }

    // ── Static utilities ──────────────────────────────────────────────────────

    /** Returns {@code true} if the value is null, or its String representation is blank. */
    static boolean isBlank(Object value) {
        return value == null || value.toString().isBlank();
    }

    /**
     * Escapes characters that would break the JSON string surrounding the reason text.
     * Only the reason field needs escaping; the raw_payload comes from Avro toString()
     * which already produces well-formed JSON.
     */
    static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}

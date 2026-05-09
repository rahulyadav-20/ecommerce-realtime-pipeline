package com.ecom.operators;

import com.ecom.events.ClickstreamEvent;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;
import org.apache.flink.util.OutputTag;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;


/**
 * ValidateAndClean — validates and normalises every inbound {@link ClickstreamEvent}.
 *
 * <p><b>Validation rules</b> (failure → DLQ with reason):
 * <ol>
 *   <li>Required fields non-null / non-blank: {@code event_id, session_id,
 *       event_type, device, ip, event_time}.</li>
 *   <li>{@code quantity >= 0} when present.</li>
 *   <li>{@code price >= 0.0} when present.</li>
 *   <li>{@code event_time} within ±48 h of processing wall-clock time.</li>
 * </ol>
 *
 * <p><b>Cleaning rules</b> (applied before emitting to main output):
 * <ol>
 *   <li>Trim leading/trailing whitespace on all String fields.</li>
 *   <li>Lowercase {@code category}.</li>
 *   <li>Normalise {@code device} → one of: {@code web | android | ios | other}.</li>
 * </ol>
 *
 * <p>Records that fail validation are emitted to the DLQ side output as a JSON
 * string with schema:
 * <pre>
 *   {
 *     "reason":      "missing required field: event_id",
 *     "event_id":    "...",
 *     "ingest_ts":   1715000000000
 *   }
 * </pre>
 */
public class ValidateAndClean extends ProcessFunction<ClickstreamEvent, ClickstreamEvent> {

    private static final Logger LOG = LoggerFactory.getLogger(ValidateAndClean.class);

    private static final long MAX_SKEW_MS = 48L * 60 * 60 * 1_000;   // 48 hours

    private final OutputTag<String> dlqTag;

    public ValidateAndClean(OutputTag<String> dlqTag) {
        this.dlqTag = dlqTag;
    }

    @Override
    public void processElement(
            ClickstreamEvent event,
            Context ctx,
            Collector<ClickstreamEvent> out) {

        // ── Validate ─────────────────────────────────────────────────────────
        String violation = validate(event);
        if (violation != null) {
            String eventId = event.getEventId() != null ? event.getEventId().toString() : "null";
            String dlqMsg  = String.format(
                    "{\"reason\":\"%s\",\"event_id\":\"%s\",\"ingest_ts\":%d}",
                    escapeJson(violation), escapeJson(eventId), System.currentTimeMillis());
            LOG.debug("DLQ | reason={} event_id={}", violation, eventId);
            ctx.output(dlqTag, dlqMsg);
            return;
        }

        // ── Clean ─────────────────────────────────────────────────────────────
        clean(event);
        out.collect(event);
    }

    // ── Validation ────────────────────────────────────────────────────────────

    private String validate(ClickstreamEvent e) {
        if (isBlank(e.getEventId()))    return "missing required field: event_id";
        if (isBlank(e.getSessionId()))  return "missing required field: session_id";
        if (e.getEventType() == null)   return "missing required field: event_type";
        if (isBlank(e.getDevice()))     return "missing required field: device";
        if (isBlank(e.getIp()))         return "missing required field: ip";
        if (e.getEventTime() == null || e.getEventTime().toEpochMilli() <= 0)
                                        return "missing required field: event_time";

        // Optional field range checks
        if (e.getQuantity() != null && e.getQuantity() < 0)
            return "quantity must be >= 0, got: " + e.getQuantity();
        if (e.getPrice() != null && e.getPrice() < 0.0)
            return "price must be >= 0, got: " + e.getPrice();

        // Event timestamp must be within ±48 h of now
        long nowMs  = System.currentTimeMillis();
        long evMs   = e.getEventTime().toEpochMilli();   // Avro timestamp-millis → Instant
        if (Math.abs(nowMs - evMs) > MAX_SKEW_MS)
            return "event_time out of ±48h window: " + e.getEventTime();

        return null;   // valid
    }

    // ── Cleaning ──────────────────────────────────────────────────────────────

    private void clean(ClickstreamEvent e) {
        // Trim all string fields
        if (e.getEventId()      != null) e.setEventId(e.getEventId().toString().trim());
        if (e.getSessionId()    != null) e.setSessionId(e.getSessionId().toString().trim());
        if (e.getUserId()       != null) e.setUserId(e.getUserId().toString().trim());
        if (e.getProductId()    != null) e.setProductId(e.getProductId().toString().trim());
        if (e.getIp()           != null) e.setIp(e.getIp().toString().trim());
        if (e.getPaymentMode()  != null) e.setPaymentMode(e.getPaymentMode().toString().trim());

        // Lowercase category
        if (e.getCategory() != null)
            e.setCategory(e.getCategory().toString().trim().toLowerCase());

        // Normalise device → canonical set
        if (e.getDevice() != null) {
            String d = e.getDevice().toString().trim().toLowerCase();
            e.setDevice(switch (d) {
                case "web", "browser", "desktop" -> "web";
                case "android"                   -> "android";
                case "ios", "iphone", "ipad"     -> "ios";
                default                          -> "other";
            });
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static boolean isBlank(Object s) {
        return s == null || s.toString().isBlank();
    }

    private static String escapeJson(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}

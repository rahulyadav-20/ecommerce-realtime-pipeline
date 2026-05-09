package com.ecom.functions;

import com.ecom.events.ClickstreamEvent;
import com.ecom.events.EventType;
import org.apache.flink.formats.avro.typeutils.AvroSerializer;
import org.apache.flink.streaming.api.operators.ProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.OneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for {@link ValidateAndClean} using Flink's
 * {@link ProcessFunctionTestHarnesses}.
 *
 * <h2>Harness API notes (Flink 1.19)</h2>
 * <ul>
 *   <li>{@code harness.getOutput()} returns a {@link java.util.concurrent.ConcurrentLinkedQueue}
 *       of {@link Object} — each element is either a {@link StreamRecord} (data) or
 *       a {@link org.apache.flink.streaming.api.watermark.Watermark}. We filter
 *       to {@link StreamRecord} to count / extract data records.</li>
 *   <li>{@code harness.getSideOutput(tag)} returns a
 *       {@link java.util.concurrent.ConcurrentLinkedQueue ConcurrentLinkedQueue}
 *       of {@link StreamRecord} — no index access; use {@code iterator().next()}
 *       or {@code size()} only.</li>
 *   <li>The {@code event_time} field in the Avro schema is a required {@code long
 *       (timestamp-millis)}, NOT a nullable union. {@code setEventTime(null)} throws
 *       an NPE inside the generated setter. Tests for that field use out-of-window
 *       timestamps instead.</li>
 * </ul>
 */
class ValidateAndCleanTest {

    private OneInputStreamOperatorTestHarness<ClickstreamEvent, ClickstreamEvent> harness;

    private static final long PROC_TS = System.currentTimeMillis();

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @BeforeEach
    void setUp() throws Exception {
        // Pass AvroSerializer explicitly so the harness handles java.time.Instant
        // (the timestamp-millis type) correctly. Without this, Flink's default Kryo
        // serializer loses Instant fields during the input → operator roundtrip,
        // causing event_time to appear null inside processElement().
        harness = new OneInputStreamOperatorTestHarness<>(
                new ProcessOperator<>(new ValidateAndClean()),
                new AvroSerializer<>(ClickstreamEvent.class));
        harness.open();
    }

    @AfterEach
    void tearDown() throws Exception {
        harness.close();
    }

    // ── Builders ──────────────────────────────────────────────────────────────

    /** Builds a fully valid event. Tests override individual fields to trigger rules. */
    private static ClickstreamEvent validEvent() {
        return ClickstreamEvent.newBuilder()
                .setEventId("evt-001")
                .setSessionId("sess-abc")
                .setEventTime(Instant.ofEpochMilli(System.currentTimeMillis()))
                .setEventType(EventType.VIEW)
                .setDevice("web")
                .setIp("203.0.113.42")
                .setUserId("USR-00001")
                .setProductId("ELEC-001")
                .setCategory("electronics")
                .setQuantity(1)
                .setPrice(29_999.0)
                .setPaymentMode("credit_card")
                .build();
    }

    // ── Harness output helpers ────────────────────────────────────────────────

    /**
     * Counts data records (non-watermarks) in the main output queue.
     * <p>In Flink 1.19 the harness wraps data in {@link StreamRecord};
     * watermarks are {@link org.apache.flink.streaming.api.watermark.Watermark}.
     */
    private long mainOutputCount() {
        return harness.getOutput().stream()
                .filter(o -> o instanceof StreamRecord)
                .count();
    }

    /**
     * Extracts the first cleaned {@link ClickstreamEvent} from the main output.
     * Throws if the main output is empty.
     */
    @SuppressWarnings("unchecked")
    private ClickstreamEvent firstMainOutput() {
        return harness.getOutput().stream()
                .filter(o -> o instanceof StreamRecord)
                .map(o -> (ClickstreamEvent) ((StreamRecord<?>) o).getValue())
                .findFirst()
                .orElseThrow(() -> new AssertionError("Main output is empty"));
    }

    /**
     * All DLQ records emitted via the side output.
     * Flink's harness returns {@code null} (not an empty Queue) when no records
     * have been emitted to a given tag — guard against that here.
     */
    @SuppressWarnings("unchecked")
    private Queue<StreamRecord<String>> dlqOutput() {
        // getSideOutput returns null (not an empty queue) when no records exist for the tag
        Queue<StreamRecord<String>> q = harness.getSideOutput(ValidateAndClean.DLQ_TAG);
        return q != null ? q : new ConcurrentLinkedQueue<>();
    }

    /** String payload of the first DLQ record. Throws if DLQ is empty. */
    private String firstDlqValue() {
        return dlqOutput().iterator().next().getValue();
    }

    /** Sends one event and asserts it reached the main output (not DLQ). */
    private void assertPasses(ClickstreamEvent event) throws Exception {
        harness.processElement(event, PROC_TS);
        // Include DLQ content in failure message so we can diagnose which rule fired
        String dlqReason = dlqOutput().isEmpty() ? "" : firstDlqValue();
        assertThat(dlqOutput())
                .as("dlq should be empty but got: [%s]", dlqReason)
                .isEmpty();
        assertThat(mainOutputCount()).as("main output").isEqualTo(1);
    }

    /** Sends one event and asserts it was routed to the DLQ. */
    private void assertRejected(ClickstreamEvent event) throws Exception {
        harness.processElement(event, PROC_TS);
        assertThat(mainOutputCount()).as("main output").isZero();
        assertThat(dlqOutput()).as("dlq").hasSize(1);
    }

    // =========================================================================
    // Happy-path
    // =========================================================================

    @Test
    @DisplayName("Valid event → main output, DLQ empty")
    void validEvent_forwardedToMainOutput() throws Exception {
        assertPasses(validEvent());
        assertThat(firstMainOutput().getEventId().toString()).isEqualTo("evt-001");
    }

    @Test
    @DisplayName("Event with all optional fields null → still valid")
    void validEvent_nullOptionalFields_passes() throws Exception {
        ClickstreamEvent event = ClickstreamEvent.newBuilder()
                .setEventId("evt-002")
                .setSessionId("sess-xyz")
                .setEventTime(Instant.ofEpochMilli(System.currentTimeMillis()))
                .setEventType(EventType.ORDER_CREATED)
                .setDevice("android")
                .setIp("10.0.0.1")
                .setUserId(null).setProductId(null).setCategory(null)
                .setQuantity(null).setPrice(null).setPaymentMode(null)
                .build();

        assertPasses(event);
    }

    // =========================================================================
    // Validation — required fields
    // =========================================================================

    @Nested
    @DisplayName("Required field validation")
    class RequiredFieldValidation {

        @Test @DisplayName("null event_id → DLQ with 'event_id' in reason")
        void nullEventId() throws Exception {
            ClickstreamEvent e = validEvent(); e.setEventId(null);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("event_id");
        }

        @Test @DisplayName("blank event_id → DLQ")
        void blankEventId() throws Exception {
            ClickstreamEvent e = validEvent(); e.setEventId("   ");
            assertRejected(e);
        }

        @Test @DisplayName("null session_id → DLQ with 'session_id' in reason")
        void nullSessionId() throws Exception {
            ClickstreamEvent e = validEvent(); e.setSessionId(null);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("session_id");
        }

        @Test @DisplayName("null event_type → DLQ with 'event_type' in reason")
        void nullEventType() throws Exception {
            ClickstreamEvent e = validEvent(); e.setEventType(null);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("event_type");
        }

        @Test @DisplayName("null device → DLQ with 'device' in reason")
        void nullDevice() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice(null);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("device");
        }

        @Test @DisplayName("blank ip → DLQ with 'ip' in reason")
        void blankIp() throws Exception {
            ClickstreamEvent e = validEvent(); e.setIp("");
            assertRejected(e);
            assertThat(firstDlqValue()).contains("ip");
        }

        /**
         * event_time is a required non-nullable long in the Avro schema, so it can
         * never be literally null in a real message. We test the implicit zero-epoch
         * value (which IS out of the ±48 h window) to verify the check is wired up.
         */
        @Test @DisplayName("event_time at epoch-0 → DLQ (treated as stale/invalid)")
        void epochZeroEventTime_sentToDlq() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventTime(Instant.ofEpochMilli(0L));   // 1970-01-01, definitely outside ±48 h
            assertRejected(e);
            assertThat(firstDlqValue()).contains("event_time");
        }
    }

    // =========================================================================
    // Validation — value ranges
    // =========================================================================

    @Nested
    @DisplayName("Value range validation")
    class ValueRangeValidation {

        @Test @DisplayName("quantity = -1 → DLQ")
        void negativeQuantity() throws Exception {
            ClickstreamEvent e = validEvent(); e.setQuantity(-1);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("quantity");
        }

        @Test @DisplayName("quantity = 0 → valid (boundary)")
        void zeroQuantity() throws Exception {
            ClickstreamEvent e = validEvent(); e.setQuantity(0);
            assertPasses(e);
        }

        @Test @DisplayName("price = -0.01 → DLQ")
        void negativePrice() throws Exception {
            ClickstreamEvent e = validEvent(); e.setPrice(-0.01);
            assertRejected(e);
            assertThat(firstDlqValue()).contains("price");
        }

        @Test @DisplayName("price = 0.0 → valid (boundary)")
        void zeroPrice() throws Exception {
            ClickstreamEvent e = validEvent(); e.setPrice(0.0);
            assertPasses(e);
        }
    }

    // =========================================================================
    // Validation — event_time freshness  (±48 h window)
    // =========================================================================

    @Nested
    @DisplayName("event_time freshness (±48 h window)")
    class EventTimeFreshnessValidation {

        @Test @DisplayName("event_time 47 h in the past → valid")
        void fortySevenHoursPast_valid() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventTime(Instant.ofEpochMilli(System.currentTimeMillis() - 47 * 3_600_000L));
            assertPasses(e);
        }

        @Test @DisplayName("event_time 49 h in the past → DLQ")
        void fortyNineHoursPast_rejected() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventTime(Instant.ofEpochMilli(System.currentTimeMillis() - 49 * 3_600_000L));
            assertRejected(e);
            assertThat(firstDlqValue()).contains("event_time");
        }

        @Test @DisplayName("event_time 49 h in the future → DLQ (clock skew)")
        void fortyNineHoursFuture_rejected() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventTime(Instant.ofEpochMilli(System.currentTimeMillis() + 49 * 3_600_000L));
            assertRejected(e);
        }
    }

    // =========================================================================
    // Cleaning operations
    // =========================================================================

    @Nested
    @DisplayName("Cleaning operations")
    class CleaningOperations {

        @Test @DisplayName("whitespace stripped from event_id, session_id, user_id")
        void whitespace_stripped() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventId("  evt-001  ");
            e.setSessionId("\tsess-abc\t");
            e.setUserId("  USR-001  ");

            harness.processElement(e, PROC_TS);
            ClickstreamEvent cleaned = firstMainOutput();

            assertThat(cleaned.getEventId().toString()).isEqualTo("evt-001");
            assertThat(cleaned.getSessionId().toString()).isEqualTo("sess-abc");
            assertThat(cleaned.getUserId().toString()).isEqualTo("USR-001");
        }

        @Test @DisplayName("category lowercased and stripped")
        void category_lowercasedAndStripped() throws Exception {
            ClickstreamEvent e = validEvent(); e.setCategory("  ELECTRONICS  ");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getCategory().toString()).isEqualTo("electronics");
        }

        @Test @DisplayName("null category remains null")
        void nullCategory_remainsNull() throws Exception {
            ClickstreamEvent e = validEvent(); e.setCategory(null);
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getCategory()).isNull();
        }

        @Test @DisplayName("'Browser' → 'web'")
        void browser_normalisedToWeb() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("Browser");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("web");
        }

        @Test @DisplayName("'Desktop' → 'web'")
        void desktop_normalisedToWeb() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("Desktop");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("web");
        }

        @Test @DisplayName("'iOS' → 'ios'")
        void iOS_normalisedToIos() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("iOS");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("ios");
        }

        @Test @DisplayName("'iPhone' → 'ios'")
        void iPhone_normalisedToIos() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("iPhone");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("ios");
        }

        @Test @DisplayName("'Android_App' → 'android'")
        void androidApp_normalisedToAndroid() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("Android_App");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("android");
        }

        @Test @DisplayName("unknown device → 'other'")
        void unknownDevice_normalisedToOther() throws Exception {
            ClickstreamEvent e = validEvent(); e.setDevice("SmartTV");
            harness.processElement(e, PROC_TS);
            assertThat(firstMainOutput().getDevice().toString()).isEqualTo("other");
        }

        @Test @DisplayName("Cleaning preserves event_type and event_id values")
        void preservesEventTypeAndId() throws Exception {
            ClickstreamEvent e = validEvent();
            e.setEventType(EventType.PAYMENT_SUCCESS);
            harness.processElement(e, PROC_TS);
            ClickstreamEvent cleaned = firstMainOutput();
            assertThat(cleaned.getEventType()).isEqualTo(EventType.PAYMENT_SUCCESS);
            assertThat(cleaned.getEventId().toString()).isEqualTo("evt-001");
        }
    }

    // =========================================================================
    // DLQ message format
    // =========================================================================

    @Nested
    @DisplayName("DLQ message format")
    class DlqMessageFormat {

        @Test @DisplayName("DLQ JSON contains reason, raw_payload, ingest_ts keys")
        void containsRequiredKeys() throws Exception {
            ClickstreamEvent e = validEvent(); e.setEventId(null);
            harness.processElement(e, PROC_TS);
            String dlq = firstDlqValue();
            assertThat(dlq).contains("\"reason\"", "\"raw_payload\"", "\"ingest_ts\"");
        }

        @Test @DisplayName("DLQ reason describes the violated rule")
        void reasonDescribesViolation() throws Exception {
            ClickstreamEvent e = validEvent(); e.setPrice(-5.0);
            harness.processElement(e, PROC_TS);
            assertThat(firstDlqValue()).contains("price");
        }

        @Test @DisplayName("DLQ ingest_ts is a recent epoch-millis value")
        void ingestTsIsRecentEpochMillis() throws Exception {
            long before = System.currentTimeMillis();
            ClickstreamEvent e = validEvent(); e.setQuantity(-99);
            harness.processElement(e, PROC_TS);
            long after = System.currentTimeMillis();

            String dlq = firstDlqValue();
            long ingestTs = Long.parseLong(
                    dlq.split("\"ingest_ts\":")[1].replace("}", "").strip());
            assertThat(ingestTs).isBetween(before, after);
        }

        @Test @DisplayName("Three invalid events → three DLQ records, zero main output")
        void multipleInvalidEventsEachProduceOneDlqRecord() throws Exception {
            ClickstreamEvent e1 = validEvent(); e1.setEventId(null);
            ClickstreamEvent e2 = validEvent(); e2.setPrice(-1.0);
            ClickstreamEvent e3 = validEvent(); e3.setDevice(null);

            harness.processElement(e1, PROC_TS);
            harness.processElement(e2, PROC_TS);
            harness.processElement(e3, PROC_TS);

            assertThat(mainOutputCount()).isZero();
            assertThat(dlqOutput()).hasSize(3);
        }

        @Test @DisplayName("2 valid + 1 invalid → 2 main, 1 DLQ")
        void mixedEvents_correctlySplit() throws Exception {
            ClickstreamEvent valid   = validEvent();
            ClickstreamEvent invalid = validEvent(); invalid.setEventId(null);

            harness.processElement(valid,   PROC_TS);
            harness.processElement(invalid, PROC_TS);
            harness.processElement(valid,   PROC_TS);

            assertThat(mainOutputCount()).isEqualTo(2);
            assertThat(dlqOutput()).hasSize(1);
        }
    }

    // =========================================================================
    // Static helpers  (pure logic — no harness needed)
    // =========================================================================

    @Nested
    @DisplayName("Static helper methods")
    class StaticHelpers {

        @Test void isBlank_null()     { assertThat(ValidateAndClean.isBlank(null)).isTrue(); }
        @Test void isBlank_blank()    { assertThat(ValidateAndClean.isBlank("   ")).isTrue(); }
        @Test void isBlank_nonBlank() { assertThat(ValidateAndClean.isBlank("a")).isFalse(); }

        @Test void normalise_webVariants() {
            assertThat(ValidateAndClean.normaliseDevice("web")).isEqualTo("web");
            assertThat(ValidateAndClean.normaliseDevice("Browser")).isEqualTo("web");
            assertThat(ValidateAndClean.normaliseDevice("DESKTOP")).isEqualTo("web");
            assertThat(ValidateAndClean.normaliseDevice("PWA")).isEqualTo("web");
        }

        @Test void normalise_androidVariants() {
            assertThat(ValidateAndClean.normaliseDevice("android")).isEqualTo("android");
            assertThat(ValidateAndClean.normaliseDevice("Android_App")).isEqualTo("android");
        }

        @Test void normalise_iosVariants() {
            assertThat(ValidateAndClean.normaliseDevice("iOS")).isEqualTo("ios");
            assertThat(ValidateAndClean.normaliseDevice("iPhone")).isEqualTo("ios");
            assertThat(ValidateAndClean.normaliseDevice("iPad")).isEqualTo("ios");
            assertThat(ValidateAndClean.normaliseDevice("ios_app")).isEqualTo("ios");
        }

        @Test void normalise_unknownAndNull() {
            assertThat(ValidateAndClean.normaliseDevice("SmartTV")).isEqualTo("other");
            assertThat(ValidateAndClean.normaliseDevice(null)).isEqualTo("other");
            assertThat(ValidateAndClean.normaliseDevice("")).isEqualTo("other");
        }

        @Test void escapeJson_specialChars() {
            assertThat(ValidateAndClean.escapeJson("field \"x\" with\nnewline"))
                    .isEqualTo("field \\\"x\\\" with\\nnewline");
        }

        @Test void escapeJson_null() {
            assertThat(ValidateAndClean.escapeJson(null)).isEmpty();
        }
    }
}

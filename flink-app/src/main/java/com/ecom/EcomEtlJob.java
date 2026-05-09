package com.ecom;

import com.ecom.events.ClickstreamEvent;
import com.ecom.functions.DedupFunction;
import com.ecom.functions.ValidateAndClean;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.contrib.streaming.state.EmbeddedRocksDBStateBackend;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.OutputTag;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Properties;

/**
 * EcomEtlJob — main entry point for the e-commerce clickstream ETL pipeline.
 *
 * <h2>Pipeline topology</h2>
 * <pre>
 *   KafkaSource (ecommerce.events.raw.v1)           ← Avro + Schema Registry
 *       │
 *       ▼
 *   ValidateAndClean (ProcessFunction)
 *       │  main output  → validated, cleaned events
 *       │  side output  → DLQ (schema-violating / out-of-range records)
 *       │
 *       ▼
 *   keyBy(event_id)
 *       │
 *       ▼
 *   DedupFunction (KeyedProcessFunction)             ← RocksDB MapState, 2 h TTL
 *       │
 *       ▼
 *   KafkaSink (ecommerce.events.clean.v1)            ← EXACTLY_ONCE 2PC
 *   KafkaSink (dlq.events)                           ← AT_LEAST_ONCE, String JSON
 * </pre>
 *
 * <h2>CLI arguments</h2>
 * <table>
 *   <tr><th>Flag</th><th>Default</th><th>Description</th></tr>
 *   <tr><td>--kafka</td><td>kafka-1:29092,kafka-2:29093</td><td>Broker list</td></tr>
 *   <tr><td>--schema-registry</td><td>http://schema-registry:8081</td><td>SR URL</td></tr>
 *   <tr><td>--source-topic</td><td>ecommerce.events.raw.v1</td><td>Raw input</td></tr>
 *   <tr><td>--sink-topic</td><td>ecommerce.events.clean.v1</td><td>Clean hub output</td></tr>
 *   <tr><td>--dlq-topic</td><td>dlq.events</td><td>Dead-letter queue</td></tr>
 *   <tr><td>--checkpoint-dir</td><td>file:///tmp/flink-checkpoints/ecom</td><td>Checkpoint storage</td></tr>
 *   <tr><td>--group-id</td><td>flink-ecom-etl</td><td>Kafka consumer group</td></tr>
 *   <tr><td>--parallelism</td><td>2</td><td>Default operator parallelism</td></tr>
 *   <tr><td>--dedup-window-hours</td><td>2</td><td>DedupFunction TTL window</td></tr>
 * </table>
 *
 * <h2>Build and run</h2>
 * <pre>
 *   cd flink-app
 *   mvn clean package -DskipTests
 *
 *   # Submit to running Flink cluster
 *   flink run -d -p 2 -c com.ecom.EcomEtlJob \
 *       target/ecom-streaming-etl-1.0-SNAPSHOT.jar \
 *       --kafka kafka-1:29092,kafka-2:29093 \
 *       --schema-registry http://schema-registry:8081
 *
 *   # Verify: Flink Web UI → Jobs → ecom-etl-v1 → RUNNING
 *   # Kafka UI → Topics → ecommerce.events.clean.v1 → messages appearing
 * </pre>
 */
public class EcomEtlJob {

    private static final Logger LOG = LoggerFactory.getLogger(EcomEtlJob.class);

    /** Side-output tag shared with {@link ValidateAndClean} for DLQ routing. */
    public static final OutputTag<String> DLQ_TAG = ValidateAndClean.DLQ_TAG;

    // ── Default values ────────────────────────────────────────────────────────
    private static final String DEFAULT_KAFKA         = "kafka-1:29092,kafka-2:29093";
    private static final String DEFAULT_SCHEMA_REG    = "http://schema-registry:8081";
    private static final String DEFAULT_SOURCE_TOPIC  = "ecommerce.events.raw.v1";
    private static final String DEFAULT_SINK_TOPIC    = "ecommerce.events.clean.v1";
    private static final String DEFAULT_DLQ_TOPIC     = "dlq.events";
    private static final String DEFAULT_CHECKPOINT    = "file:///tmp/flink-checkpoints/ecom";
    private static final String DEFAULT_GROUP_ID      = "flink-ecom-etl";
    private static final int    DEFAULT_PARALLELISM   = 2;
    private static final int    DEFAULT_DEDUP_HOURS   = 2;

    // ── Checkpointing constants ───────────────────────────────────────────────
    /** Checkpoint interval (ms). A 30-second interval gives good recovery vs. overhead balance. */
    private static final long CHECKPOINT_INTERVAL_MS  = 30_000L;

    /** Max checkpoint duration before it is aborted (ms). */
    private static final long CHECKPOINT_TIMEOUT_MS   = 120_000L;

    /**
     * Kafka producer transaction timeout (ms).
     * Must be ≤ broker's {@code transaction.max.timeout.ms} (default 900 000 ms / 15 min).
     * We use 60 s to avoid hitting the broker limit in development environments.
     */
    private static final String TX_TIMEOUT_MS = "60000";

    // =========================================================================
    // main()
    // =========================================================================

    public static void main(String[] args) throws Exception {
        // ── Parse CLI arguments ───────────────────────────────────────────────
        // ParameterTool reads --key value pairs from args[] and falls back to
        // system properties (-Dkey=value) when the flag is absent.
        ParameterTool params = ParameterTool.fromArgs(args);

        String kafka           = params.get("kafka",            DEFAULT_KAFKA);
        String schemaRegistry  = params.get("schema-registry",  DEFAULT_SCHEMA_REG);
        String sourceTopic     = params.get("source-topic",     DEFAULT_SOURCE_TOPIC);
        String sinkTopic       = params.get("sink-topic",       DEFAULT_SINK_TOPIC);
        String dlqTopic        = params.get("dlq-topic",        DEFAULT_DLQ_TOPIC);
        String checkpointDir   = params.get("checkpoint-dir",   DEFAULT_CHECKPOINT);
        String groupId         = params.get("group-id",         DEFAULT_GROUP_ID);
        int    parallelism     = params.getInt("parallelism",   DEFAULT_PARALLELISM);
        int    dedupHours      = params.getInt("dedup-window-hours", DEFAULT_DEDUP_HOURS);

        LOG.info("Starting EcomEtlJob | kafka={} sr={} source={} sink={} dlq={} p={}",
                kafka, schemaRegistry, sourceTopic, sinkTopic, dlqTopic, parallelism);

        // =========================================================================
        // 1. Execution environment
        // =========================================================================
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(parallelism);

        // Make CLI parameters available to all operators via getRuntimeContext().
        // This enables runtime access to dynamic config without re-deploying the JAR.
        env.getConfig().setGlobalJobParameters(params);

        // ── State backend: RocksDB (incremental checkpoints) ──────────────────
        // RocksDB is required for the DedupFunction's 2-hour MapState. With the
        // heap backend, large dedup state would cause long GC pauses and full
        // checkpoint snapshots that grow into GB territory at 80k events/sec.
        //
        // incremental=true: only changed RocksDB SST files are written per checkpoint.
        // This reduces checkpoint time from minutes to seconds for large state.
        env.setStateBackend(new EmbeddedRocksDBStateBackend(/* incremental= */ true));

        // ── Checkpoint storage ────────────────────────────────────────────────
        // Points to a local directory for development.
        // In production, replace with s3://flink-ckpt/ecom/ (MinIO or AWS S3).
        env.getCheckpointConfig().setCheckpointStorage(checkpointDir);

        // ── Checkpointing policy ──────────────────────────────────────────────
        // EXACTLY_ONCE: Flink aligns barriers across all source partitions before
        // taking the checkpoint. Combined with the transactional Kafka sink (2PC),
        // this provides end-to-end exactly-once delivery.
        env.enableCheckpointing(CHECKPOINT_INTERVAL_MS, CheckpointingMode.EXACTLY_ONCE);

        // Abort a checkpoint that takes longer than 2 minutes to avoid indefinite
        // blocking of the pipeline during slow network or storage conditions.
        env.getCheckpointConfig().setCheckpointTimeout(CHECKPOINT_TIMEOUT_MS);

        // Allow only one checkpoint to run concurrently. A second checkpoint
        // cannot start until the first has completed or been aborted.
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);

        // ── Unaligned checkpoints ─────────────────────────────────────────────
        // Unaligned checkpoints skip barrier alignment by persisting in-flight
        // records as part of the checkpoint state. This dramatically reduces
        // checkpoint duration under backpressure at the cost of larger checkpoint
        // sizes. Recommended when p99 checkpoint duration exceeds target SLA.
        //
        // Compatible with EXACTLY_ONCE mode in Flink 1.15+.
        env.getCheckpointConfig().enableUnalignedCheckpoints();

        // =========================================================================
        // 2. KafkaSource — read raw events (Avro + Schema Registry)
        // =========================================================================
        //
        // ConfluentRegistryAvroDeserializationSchema handles the Confluent wire format:
        //   byte 0   = magic byte (0x00)
        //   bytes 1–4 = schema ID (big-endian int)
        //   bytes 5+  = Avro binary payload
        //
        // The schema is fetched from Schema Registry by ID on first access and cached.
        KafkaSource<ClickstreamEvent> source = KafkaSource.<ClickstreamEvent>builder()
                .setBootstrapServers(kafka)
                .setTopics(sourceTopic)
                .setGroupId(groupId)
                // Resume from last committed offset; fall back to EARLIEST if no
                // committed offset exists (first run or after consumer group reset).
                .setStartingOffsets(
                        OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST))
                .setValueOnlyDeserializer(
                        ConfluentRegistryAvroDeserializationSchema.forSpecific(
                                ClickstreamEvent.class, schemaRegistry))
                .build();

        // =========================================================================
        // 3. Processing pipeline
        // =========================================================================

        // ── 3a. Read from Kafka ───────────────────────────────────────────────
        // WatermarkStrategy.noWatermarks(): this job is processing-time based —
        // it does not use event-time windowing. Adding watermarks is unnecessary
        // overhead and would introduce latency for the streaming ETL path.
        //
        // Downstream analytics engines (Druid 1-min rollup, ClickHouse 5-min MVs)
        // handle their own time-windowing independently.
        SingleOutputStreamOperator<ClickstreamEvent> cleaned = env
                .fromSource(source, WatermarkStrategy.noWatermarks(), "kafka-source-raw")
                .uid("source-kafka-raw")   // stable UID for savepoint compatibility
                .name("source:kafka-raw")

                // ── 3b. Validate and clean ────────────────────────────────────
                // ProcessFunction — stateless, scales independently.
                // Bad records are emitted to DLQ_TAG side output instead of the
                // main stream, so they cannot pollute the clean hub topic.
                .process(new ValidateAndClean())
                .uid("op-validate-clean")
                .name("op:validate-clean");

        // ── 3c. Deduplication ─────────────────────────────────────────────────
        // Key by event_id before the stateful dedup operator.
        // All events with the same event_id reach the same subtask, enabling the
        // MapState<String, Long> to detect duplicates within the TTL window.
        SingleOutputStreamOperator<ClickstreamEvent> deduped = cleaned
                .keyBy(event -> event.getEventId().toString())
                .process(new DedupFunction(dedupHours))
                .uid("op-dedup")
                .name("op:dedup-" + dedupHours + "h");

        // =========================================================================
        // 4. KafkaSink — clean hub topic (EXACTLY_ONCE)
        // =========================================================================
        //
        // EXACTLY_ONCE uses Kafka transactions (two-phase commit):
        //   1. Flink triggers the checkpoint barrier.
        //   2. On barrier receipt each task pre-commits its Kafka transactions.
        //   3. After all tasks pre-commit, the checkpoint succeeds.
        //   4. Flink commits the Kafka transactions (post-checkpoint callback).
        //
        // This guarantees that every record written to the clean topic appears
        // exactly once, even after a failure and recovery.
        //
        // Consumers of the clean topic MUST use isolation.level=read_committed
        // (Druid, ClickHouse, Kafka Connect all do this) to skip aborted records.
        Properties txProps = new Properties();
        // transaction.timeout.ms must be ≤ broker's transaction.max.timeout.ms.
        // Default broker max is 900 000 ms (15 min); we use 60 s for safety.
        txProps.setProperty("transaction.timeout.ms", TX_TIMEOUT_MS);

        deduped.sinkTo(
                KafkaSink.<ClickstreamEvent>builder()
                        .setBootstrapServers(kafka)
                        .setKafkaProducerConfig(txProps)
                        .setRecordSerializer(
                                KafkaRecordSerializationSchema.<ClickstreamEvent>builder()
                                        .setTopic(sinkTopic)
                                        .setValueSerializationSchema(
                                                // Subject name = topic + "-value" (Confluent convention)
                                                ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                        ClickstreamEvent.class,
                                                        sinkTopic + "-value",
                                                        schemaRegistry))
                                        .build())
                        .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                        // Transactional ID prefix must be unique per job to avoid
                        // producer ID conflicts when multiple jobs write to the same broker.
                        .setTransactionalIdPrefix("ecom-etl-clean-")
                        .build())
                .uid("sink-kafka-clean")
                .name("sink:kafka-clean");

        // =========================================================================
        // 5. KafkaSink — DLQ (AT_LEAST_ONCE, String JSON)
        // =========================================================================
        //
        // DLQ records are emitted by ValidateAndClean as JSON strings:
        //   { "reason": "...", "raw_payload": {...}, "ingest_ts": 1715... }
        //
        // AT_LEAST_ONCE is appropriate here because:
        //   a) DLQ records are for inspection / alerting, not query correctness.
        //   b) Transactional producers are unnecessary overhead for a low-volume
        //      error channel; AT_LEAST_ONCE avoids the 2PC round-trip cost.
        cleaned.getSideOutput(DLQ_TAG)
                .sinkTo(
                        KafkaSink.<String>builder()
                                .setBootstrapServers(kafka)
                                .setRecordSerializer(
                                        KafkaRecordSerializationSchema.<String>builder()
                                                .setTopic(dlqTopic)
                                                .setValueSerializationSchema(new SimpleStringSchema())
                                                .build())
                                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                                .build())
                .uid("sink-kafka-dlq")
                .name("sink:kafka-dlq");

        // =========================================================================
        // 6. Execute
        // =========================================================================
        //
        // In detached mode (-d flag), flink run returns immediately and the job
        // runs as a cluster-side daemon. The job name appears in the Flink Web UI
        // at http://localhost:8082 → Running Jobs.
        LOG.info("Submitting job: ecom-etl-v1");
        env.execute("ecom-etl-v1");
    }
}

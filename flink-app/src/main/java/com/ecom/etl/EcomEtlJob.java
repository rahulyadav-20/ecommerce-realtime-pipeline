package com.ecom.etl;

import com.ecom.operators.DedupFunction;
import com.ecom.operators.ValidateAndClean;
import com.ecom.events.ClickstreamEvent;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.util.OutputTag;
import java.util.Properties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * EcomEtlJob — Flink 1.19 streaming ETL for e-commerce clickstream events.
 *
 * <p>Topology:
 * <pre>
 *   KafkaSource(ecommerce.events.raw.v1)   [Avro + Schema Registry]
 *       → ValidateAndClean                 [required fields, ranges, cleaning]
 *           ↘ DLQ side output              → KafkaSink(dlq.events)
 *       → keyBy(event_id)
 *       → DedupFunction(2h TTL, RocksDB)
 *       → KafkaSink(ecommerce.events.clean.v1) [EXACTLY_ONCE 2PC]
 * </pre>
 *
 * <p>Submit:
 * <pre>
 *   flink run -d -p 4 -c com.ecom.etl.EcomEtlJob ecom-streaming-etl-1.0-SNAPSHOT.jar \
 *     --kafka kafka-1:29092,kafka-2:29093 \
 *     --schema-registry http://schema-registry:8081
 * </pre>
 */
public class EcomEtlJob {

    private static final Logger LOG = LoggerFactory.getLogger(EcomEtlJob.class);

    /** Side-output tag for records that fail validation. */
    public static final OutputTag<String> DLQ_TAG = new OutputTag<>("dlq") {};

    public static void main(String[] args) throws Exception {
        JobConfig cfg = JobConfig.fromArgs(args);
        LOG.info("Starting EcomEtlJob | config={}", cfg);

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(cfg.parallelism());

        // ── Checkpointing ────────────────────────────────────────────────────
        env.enableCheckpointing(30_000, CheckpointingMode.EXACTLY_ONCE);
        env.getCheckpointConfig().setCheckpointStorage(cfg.checkpointDir());
        // RocksDB incremental — only changed SST files per checkpoint cycle.
        // Critical for the 2-hour dedup window that can grow to several GBs.
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);
        env.getCheckpointConfig().setCheckpointTimeout(120_000);

        // ── Source ───────────────────────────────────────────────────────────
        KafkaSource<ClickstreamEvent> source = KafkaSource.<ClickstreamEvent>builder()
                .setBootstrapServers(cfg.kafka())
                .setTopics(cfg.sourceTopic())
                .setGroupId("flink-ecom-etl")
                .setStartingOffsets(
                        OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST))
                .setValueOnlyDeserializer(
                        ConfluentRegistryAvroDeserializationSchema.forSpecific(
                                ClickstreamEvent.class, cfg.schemaRegistry()))
                .build();

        // ── ValidateAndClean ─────────────────────────────────────────────────
        SingleOutputStreamOperator<ClickstreamEvent> cleaned = env
                .fromSource(source, WatermarkStrategy.noWatermarks(), "kafka-raw")
                .name("source:kafka-raw")
                .process(new ValidateAndClean(DLQ_TAG))
                .name("op:validate-clean");

        // ── Dedup (keyed by event_id, RocksDB state, TTL = 2 h) ─────────────
        SingleOutputStreamOperator<ClickstreamEvent> deduped = cleaned
                .keyBy(e -> e.getEventId().toString())
                .process(new DedupFunction(cfg.dedupWindowHours()))
                .name("op:dedup");

        // ── Sink: clean hub topic (EXACTLY_ONCE via 2PC transactions) ────────
        // transaction.timeout.ms must be ≤ broker's transaction.max.timeout.ms (default 900000).
        Properties txProps = new Properties();
        txProps.setProperty("transaction.timeout.ms", "60000");

        deduped.sinkTo(
                KafkaSink.<ClickstreamEvent>builder()
                        .setBootstrapServers(cfg.kafka())
                        .setKafkaProducerConfig(txProps)
                        .setRecordSerializer(
                                KafkaRecordSerializationSchema.<ClickstreamEvent>builder()
                                        .setTopic(cfg.sinkTopic())
                                        .setValueSerializationSchema(
                                                ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                        ClickstreamEvent.class,
                                                        cfg.sinkTopic() + "-value",
                                                        cfg.schemaRegistry()))
                                        .build())
                        .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                        .setTransactionalIdPrefix("ecom-etl-clean-")
                        .build())
                .name("sink:kafka-clean");

        // ── Sink: DLQ (AT_LEAST_ONCE, plain JSON strings) ───────────────────
        cleaned.getSideOutput(DLQ_TAG)
                .sinkTo(
                        KafkaSink.<String>builder()
                                .setBootstrapServers(cfg.kafka())
                                .setRecordSerializer(
                                        KafkaRecordSerializationSchema.<String>builder()
                                                .setTopic(cfg.dlqTopic())
                                                .setValueSerializationSchema(new SimpleStringSchema())
                                                .build())
                                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                                .build())
                .name("sink:kafka-dlq");

        env.execute("ecom-etl-v1");
    }
}

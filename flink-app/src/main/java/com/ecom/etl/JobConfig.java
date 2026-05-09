package com.ecom.etl;

import org.apache.flink.api.java.utils.ParameterTool;

/**
 * Parses and exposes all runtime configuration for the ETL job.
 *
 * <p>Values can be supplied as CLI flags (--key value) or as system properties
 * (-Dkey=value), both handled transparently by Flink's {@link ParameterTool}.
 *
 * <p>Example:
 * <pre>
 *   flink run ... EcomEtlJob.jar \
 *     --kafka kafka-1:29092,kafka-2:29093 \
 *     --schema-registry http://schema-registry:8081 \
 *     --source-topic ecommerce.events.raw.v1 \
 *     --sink-topic   ecommerce.events.clean.v1 \
 *     --dlq-topic    dlq.events \
 *     --checkpoint-dir s3://flink-ckpt/ecom/ \
 *     --parallelism  8
 * </pre>
 */
public final class JobConfig {

    private final ParameterTool params;

    private JobConfig(ParameterTool params) {
        this.params = params;
    }

    public static JobConfig fromArgs(String[] args) {
        return new JobConfig(ParameterTool.fromArgs(args));
    }

    /** Kafka bootstrap servers (comma-separated). */
    public String kafka() {
        return params.get("kafka", "localhost:9092");
    }

    /** Confluent Schema Registry base URL. */
    public String schemaRegistry() {
        return params.get("schema-registry", "http://localhost:8081");
    }

    /** Raw event topic — the job reads from here. */
    public String sourceTopic() {
        return params.get("source-topic", "ecommerce.events.raw.v1");
    }

    /** Clean hub topic — validated, deduped events are written here. */
    public String sinkTopic() {
        return params.get("sink-topic", "ecommerce.events.clean.v1");
    }

    /** Dead-letter queue topic for records that fail validation. */
    public String dlqTopic() {
        return params.get("dlq-topic", "dlq.events");
    }

    /**
     * Checkpoint storage URI.
     * Use an S3/MinIO path in cluster mode; local path for development.
     */
    public String checkpointDir() {
        return params.get("checkpoint-dir", "file:///tmp/flink-checkpoints/ecom");
    }

    /** Deduplication window in hours (events older than this are forgotten). */
    public int dedupWindowHours() {
        return params.getInt("dedup-window-hours", 2);
    }

    /** Flink operator parallelism. */
    public int parallelism() {
        return params.getInt("parallelism", 1);
    }

    @Override
    public String toString() {
        return "JobConfig{"
                + "kafka='" + kafka() + '\''
                + ", schemaRegistry='" + schemaRegistry() + '\''
                + ", sourceTopic='" + sourceTopic() + '\''
                + ", sinkTopic='" + sinkTopic() + '\''
                + ", dlqTopic='" + dlqTopic() + '\''
                + ", checkpointDir='" + checkpointDir() + '\''
                + ", dedupWindowHours=" + dedupWindowHours()
                + ", parallelism=" + parallelism()
                + '}';
    }
}

# Real-Time E-Commerce Analytics Pipeline

## Contents

1. [Executive Summary](#-1-executive-summary)
2. [Architecture Overview](#-2-architecture-overview)
3. [Design Principles](#-3-design-principles)
4. [Data Flow & Topology](#-4-data-flow--topology)
5. [Event Schema (Avro)](#-5-event-schema-avro)
6. [Flink ETL Job](#-6-flink-etl-job)
7. [Kafka Hub Topic](#-7-kafka-hub-topic)
8. [Druid — Real-time OLAP](#-8-druid--real-time-olap)
9. [ClickHouse — Historical OLAP](#-9-clickhouse--historical-olap)
10. [Iceberg via Kafka Connect](#-10-iceberg-via-kafka-connect)
11. [ELK Observability](#-11-elk-observability)
12. [Airflow Orchestration](#-12-airflow-orchestration)
13. [Operations & Replay](#-13-operations--replay)
14. [Performance Targets](#-14-performance-targets)
15. [Project Structure & Quick Start](#-15-project-structure--quick-start)

---

# Part I — Foundations

> What this pipeline does, why it's designed the way it is, and how data flows from the e-commerce edge to analytics.

## § 1 Executive Summary

This pipeline ingests live e-commerce events — product views, cart actions, orders, payments — and makes them queryable across three analytical surfaces within seconds:

- **Apache Druid** for sub-second dashboard queries on the most recent days of data
- **ClickHouse** for ad-hoc SQL across full history with rich materialized views
- **Apache Iceberg** as the lakehouse system of record, queryable via Trino or Spark for batch and ML workloads

Apache Flink sits between the raw event stream and these three systems, but its job is intentionally narrow: **validate, deduplicate, and clean**. All aggregation work is pushed down to Druid (rollup at ingestion) and ClickHouse (materialized views) — the engines that already optimize for it.

> **🔑 Key Takeaway**
>
> One Flink job. One Kafka hub topic. Three independent sinks. No redundant aggregation logic. Replay any sink by resetting its consumer group — no backfill DAG required.

---

## § 2 Architecture Overview

```
┌────────────────────────────────────┐
│   E-Commerce Microservices         │
│   (web, mobile, order, payment)    │
└─────────────────┬──────────────────┘
                  │ Avro events
                  ▼
        ┌──────────────────┐
        │      KAFKA       │
        │  ecommerce.      │
        │  events.raw.v1   │
        └────────┬─────────┘
                 │
                 ▼
   ┌──────────────────────────────┐
   │       APACHE FLINK 1.19      │
   │       (ETL only)             │
   │                              │
   │   1. Avro schema validation  │
   │   2. Deduplication (event_id)│
   │   3. Field-level cleaning    │
   │   4. Bad records → DLQ       │
   │                              │
   │   Scope: ETL only            │
   │   (no aggregation/joins)     │
   └──────────────┬───────────────┘
                  │
                  ▼
        ┌──────────────────┐
        │      KAFKA       │
        │  ecommerce.      │
        │  events.clean.v1 │ ◄── single hub topic
        └────┬────┬────┬───┘
             │    │    │
   ┌─────────┘    │    └──────────────┐
   │              │                   │
   ▼              ▼                   ▼
┌────────┐   ┌────────────┐   ┌────────────────┐
│ DRUID  │   │ CLICKHOUSE │   │ KAFKA CONNECT  │
│ Kafka  │   │ Kafka      │   │ Iceberg Sink   │
│ Index  │   │ Engine + MV│   │ → MinIO/S3     │
│ rollup │   │ MV-based   │   │                │
│ at     │   │ aggregation│   │                │
│ ingest │   │            │   │                │
└───┬────┘   └─────┬──────┘   └────────┬───────┘
    │              │                   │
    ▼              ▼                   ▼
 Superset     Metabase /          Trino / Spark
 (live <1s)   Grafana             (ad-hoc, batch)
              (full history)
                                       ▲
                                       │
                              ┌────────┴───────┐
                              │  AIRFLOW 2.9   │
                              │  - compaction  │
                              │  - DQ checks   │
                              │  - GDPR        │
                              │  - savepoints  │
                              └────────────────┘

══════════════ MONITORING (ELK) ══════════════
   Filebeat ─► Logstash ─► Elasticsearch ─► Kibana
   Metricbeat ──────────►       ▲
   Watcher / ElastAlert ──► Slack / PagerDuty
```

_Figure 2.1 — End-to-end architecture: one Flink ETL job, one Kafka hub topic, three independent consumers._

### Component Roles

| Component             | Role                          | What It Owns                                                 |
| --------------------- | ----------------------------- | ------------------------------------------------------------ |
| **Kafka (raw)**       | Source of truth for events    | Producer-side delivery guarantees, schema-versioned messages |
| **Flink**             | ETL — validate, dedupe, clean | Schema validation, dedup state (RocksDB), DLQ routing        |
| **Kafka (clean hub)** | Decoupling fan-out            | Single contract for all downstream consumers                 |
| **Druid**             | Real-time OLAP                | Rollup-time aggregations, sub-second queries on hot data     |
| **ClickHouse**        | Historical OLAP               | Materialized-view aggregations, full-history ad-hoc          |
| **Iceberg**           | Lakehouse / SOR               | Time travel, schema evolution, batch/ML access               |
| **Kafka Connect**     | Iceberg writer                | Centralized small-file commits, exactly-once                 |
| **Airflow**           | Orchestration                 | Compaction, DQ, GDPR, savepoint rotation                     |
| **ELK**               | Observability                 | Logs, metrics, alerts, dashboards                            |

---

## § 3 Design Principles

### 1. Flink does ETL — nothing else

Aggregation, joins, and enrichment are explicitly excluded from the streaming layer. The Flink job validates Avro payloads, deduplicates by `event_id`, normalizes string fields, and emits to a single sink. State is bounded to a 2-hour dedup window, ensuring fast recovery and a compact codebase that is straightforward to review and maintain.

> **💡 Engineering Insight**
>
> The most robust streaming jobs are the simplest ones. The more state and logic Flink owns, the longer savepoints take, the slower recovery becomes, and the harder it is to evolve schemas. Push complexity to the edges — into the source contract (Schema Registry) and into the query engines (Druid rollup, ClickHouse MVs).

### 2. Aggregations live where queries live

Druid handles 1-minute rollup natively at ingestion. ClickHouse uses `ReplicatedAggregatingMergeTree` materialized views to compute KPIs at write time. Adding a new metric becomes a SQL DDL statement — not a Flink redeploy and savepoint migration.

### 3. One hub topic, three independent consumers

Druid (Kafka Indexing Service), ClickHouse (Kafka Engine), and Kafka Connect (Iceberg sink) each maintain their own consumer group. A failure or restart in one does not affect the others. Adding a fourth consumer — Snowflake, Pinot, an alerting service — is a config change, not an architecture change.

### 4. Native integrations only

No custom sink code. Druid's Kafka Indexing Service, ClickHouse's Kafka Engine, and the Kafka Connect Iceberg Sink are all maintained by their respective communities and handle exactly-once semantics, schema evolution, and operational concerns out of the box.

### 5. Replay is a consumer-group reset, not a backfill DAG

For the 7-day retention window, replaying any sink is a single offset reset. For older data, the Iceberg SOR is the source for batch reprocessing through Trino or Spark. There is no separate Lambda-style batch path duplicating stream logic.

> **⚠️ Pitfall**
>
> Avoid placing aggregations in Flink simply because the streaming job is already running. Window aggregations expand Flink state, complicate recovery, and introduce duplicate logic that must remain consistent with the BI layer's own computations. Aggregation responsibility should remain in the OLAP engines.

---

## § 4 Data Flow & Topology

The lifecycle of a single event:

```
┌─────────────┐   ┌────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ 1. Producer │ → │ 2. Kafka raw   │ → │ 3. Flink        │ → │ 4. Flink dedup  │
│ microservice│   │ events.raw.v1  │   │ validate        │   │ RocksDB keyed   │
│ emits Avro  │   │ 12 partitions  │   │ required fields │   │ state, 2h TTL   │
└─────────────┘   └────────────────┘   └─────────────────┘   └────────┬────────┘
                                                                      │
       ┌───────────────────┐   ┌────────────────┐   ┌─────────────────┘
       │ 6. Kafka hub      │ ◄ │ 5. Flink clean │ ◄─┘
       │ events.clean.v1   │   │ trim/normalize │
       │ 2PC exactly-once  │   │                │
       └───┬──────┬──────┬─┘   └────────────────┘
           │      │      │
   ┌───────┘      │      └──────────┐
   ▼              ▼                 ▼
┌──────────┐  ┌──────────────┐  ┌──────────────────┐
│ 7a. Druid│  │7b. ClickHouse│  │7c. Iceberg via   │
│ Kafka    │  │ Kafka Engine │  │ Kafka Connect    │
│ Indexing │  │ → MV →       │  │ Iceberg Sink     │
│ rollup → │  │ Replicated   │  │ Connector        │
│ segments │  │ ReplacingMT  │  │ commit every 60s │
└──────────┘  └──────────────┘  └──────────────────┘
```

_Figure 4.1 — Event lifecycle: producer to three independent OLAP/lakehouse sinks._

### Topic Inventory

| Topic                       | Producer            | Consumers                    | Purpose                            |
| --------------------------- | ------------------- | ---------------------------- | ---------------------------------- |
| `ecommerce.events.raw.v1`   | Microservices       | Flink ETL job                | Source of truth (raw events)       |
| `ecommerce.events.clean.v1` | Flink               | Druid · ClickHouse · Connect | Hub topic (validated, deduped)     |
| `dlq.events`                | Flink (side output) | Filebeat → ELK               | Schema-violating / invalid records |

---

# Part II — Stream Layer

> The Avro contract, the Kafka hub topic, and the minimal Flink ETL job that validates and deduplicates events.

## § 5 Event Schema (Avro)

Both raw and clean topics use the same Avro schema. Schema Registry compatibility is set to `FULL_TRANSITIVE`, blocking any breaking change at the producer side.

```json
{
  "namespace": "com.ecom.events",
  "type": "record",
  "name": "ClickstreamEvent",
  "fields": [
    { "name": "event_id", "type": "string" },
    { "name": "user_id", "type": ["null", "string"], "default": null },
    { "name": "session_id", "type": "string" },
    { "name": "product_id", "type": ["null", "string"], "default": null },
    { "name": "category", "type": ["null", "string"], "default": null },
    {
      "name": "event_type",
      "type": {
        "type": "enum",
        "name": "EventType",
        "symbols": [
          "VIEW",
          "ADD_TO_CART",
          "REMOVE_CART",
          "WISHLIST",
          "ORDER_CREATED",
          "PAYMENT_SUCCESS",
          "PAYMENT_FAILED",
          "ORDER_CANCELLED",
          "REFUND"
        ]
      }
    },
    { "name": "quantity", "type": ["null", "int"], "default": null },
    { "name": "price", "type": ["null", "double"], "default": null },
    { "name": "payment_mode", "type": ["null", "string"], "default": null },
    { "name": "device", "type": "string" },
    { "name": "ip", "type": "string" },
    {
      "name": "event_time",
      "type": { "type": "long", "logicalType": "timestamp-millis" }
    }
  ]
}
```

> **📌 Convention**
>
> Every event must carry a producer-side `event_id` that is stable across retries. Idempotency at the producer + dedup at Flink + exactly-once at the sinks gives end-to-end exactly-once delivery without coordination.

---

## § 6 Flink ETL Job

The job consists of two operators and two sinks, with a clear, linear topology.

```
                                                ┌──────────────────────────┐
                                                │ KafkaSink (clean hub)    │
                                                │ events.clean.v1 · 2PC    │
                                            ┌─► └──────────────────────────┘
                                            │
┌────────────┐    ┌───────────────┐    ┌────┴──────────┐
│ KafkaSource│ →  │ ValidateAnd   │ →  │ DedupFunction │
│ raw.v1     │    │ Clean         │    │ keyed/RocksDB │
└────────────┘    │ ProcessFn     │    └───────────────┘
                  └───────┬───────┘            │
                          │ side output (bad)  │
                          ▼                    │
                  ┌──────────────────┐         │
                  │ KafkaSink (DLQ)  │ ◄───────┘
                  │ dlq.events       │  (data path also tagged
                  └──────────────────┘   when validation fails)
```

_Figure 6.1 — Flink job DAG: source → validate-clean → dedup → clean sink, with DLQ side output._

### Job Skeleton

```java
public class EcomEtlJob {
  public static void main(String[] args) throws Exception {
    StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
    env.enableCheckpointing(30_000, CheckpointingMode.EXACTLY_ONCE);
    env.getCheckpointConfig().setCheckpointStorage("s3://flink-ckpt/ecom/");
    env.setStateBackend(new EmbeddedRocksDBStateBackend(true));   // incremental

    KafkaSource<ClickstreamEvent> source = KafkaSource.<ClickstreamEvent>builder()
      .setBootstrapServers(KAFKA)
      .setTopics("ecommerce.events.raw.v1")
      .setGroupId("flink-ecom-etl")
      .setStartingOffsets(OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST))
      .setValueOnlyDeserializer(ConfluentRegistryAvroDeserializationSchema
          .forSpecific(ClickstreamEvent.class, SR_URL))
      .build();

    final OutputTag<String> dlqTag = new OutputTag<String>("dlq"){};

    SingleOutputStreamOperator<ClickstreamEvent> clean = env
      .fromSource(source, WatermarkStrategy.noWatermarks(), "kafka-source")
      .process(new ValidateAndClean(dlqTag))            // validation + cleaning
      .keyBy(ClickstreamEvent::getEventId)
      .process(new DedupFunction(Time.hours(2)));       // dedup

    // Main sink → clean topic (exactly-once 2PC)
    clean.sinkTo(KafkaSink.<ClickstreamEvent>builder()
      .setBootstrapServers(KAFKA)
      .setRecordSerializer(KafkaRecordSerializationSchema.builder()
        .setTopic("ecommerce.events.clean.v1")
        .setValueSerializationSchema(new ConfluentAvroSerializer(SR_URL))
        .build())
      .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
      .setTransactionalIdPrefix("ecom-etl-")
      .build());

    // DLQ side output
    clean.getSideOutput(dlqTag).sinkTo(kafkaSink("dlq.events"));

    env.execute("ecom-etl-v3");
  }
}
```

> **💡 Engineering Insight**
>
> Use `EmbeddedRocksDBStateBackend(true)` for _incremental_ checkpoints. With a 2-hour dedup window at 80k events/sec, the keyed state can reach a few GB; full-snapshot checkpoints would be slow and disk-heavy. Incremental checkpoints write only changed RocksDB SST files.

### Validation Rules (inside `ValidateAndClean`)

- **Required fields present:** `event_id`, `session_id`, `event_type`, `device`, `ip`, `event_time` all non-null and non-empty
- **Value ranges:** `quantity ≥ 0`; `price ≥ 0`; `event_time` within ±48h of processing time
- **Cleaning:** trim whitespace on string fields, lowercase `category`, normalize `device` to canonical set (`web`, `android`, `ios`, `other`)
- **Failures:** emit a JSON envelope with `{reason, raw_payload, ingest_ts}` to the DLQ side output

> **⚠️ Pitfall — DedupFunction TTL**
>
> If your TTL is shorter than the longest legitimate retry window of an upstream producer, you will let through duplicates after a producer rebalance. Two hours is a safe default for typical microservice retry budgets; verify against your producer SLA.

---

## § 7 Kafka Hub Topic

The clean topic is the contract every downstream system depends on. It is sized and configured for a 7-day replay window and three independent consumer groups.

| Property           | Value                                 | Why                                                                                      |
| ------------------ | ------------------------------------- | ---------------------------------------------------------------------------------------- |
| Partitions         | 12                                    | Caps parallelism for downstream consumers (Druid taskCount, CH consumers, Connect tasks) |
| Replication factor | 3 (min.isr=2)                         | Survives single-broker loss without producer block                                       |
| Retention          | 7 days                                | Long enough for OLAP replay; Iceberg holds longer history                                |
| Compression        | zstd                                  | ~3× over snappy on Avro-encoded payloads                                                 |
| Cleanup policy     | `delete`                              | Event log, not state — no compaction                                                     |
| Schema             | Avro via Schema Registry              | Strict contract; FULL_TRANSITIVE compat                                                  |
| Key                | `user_id` (or `session_id` if null)   | Co-locates user activity in one partition for stateful consumers                         |
| Producer           | `acks=all, idempotent, transactional` | Exactly-once from Flink                                                                  |

### Consumer Groups

| Group ID                 | Owner                           | Read mode        |
| ------------------------ | ------------------------------- | ---------------- |
| `druid-ecom-gold`        | Druid Kafka Indexing supervisor | `read_committed` |
| `clickhouse-ecom-events` | ClickHouse Kafka Engine table   | `read_committed` |
| `connect-iceberg-events` | Kafka Connect Iceberg sink      | `read_committed` |

> **💡 Engineering Insight — read_committed**
>
> All three consumers must use `isolation.level=read_committed`. Flink commits its sink writes transactionally; without read_committed, downstream consumers would see aborted-transaction records and end up with duplicates that bypass exactly-once entirely.

---

# Part III — Storage & Analytics

> How Druid, ClickHouse, and Iceberg each consume the hub topic — and why each gets the workload it gets.

## § 8 Druid — Real-time OLAP

Druid is the surface for live operational dashboards: revenue in the last 5 minutes, conversion rates by category, current cart-abandonment counts. Its Kafka Indexing Service consumes the hub topic and applies **rollup at ingestion** — pre-aggregating events into 1-minute buckets per dimension combination — which gives sub-second query latency on 7 days of hot data.

### Ingestion Spec

```json
{
  "type": "kafka",
  "spec": {
    "dataSchema": {
      "dataSource": "ecommerce_events",
      "timestampSpec": { "column": "event_time", "format": "iso" },
      "dimensionsSpec": {
        "dimensions": ["event_type", "category", "payment_mode", "device"]
      },
      "granularitySpec": {
        "type": "uniform",
        "segmentGranularity": "HOUR",
        "queryGranularity": "MINUTE",
        "rollup": true
      },
      "metricsSpec": [
        { "type": "count", "name": "events" },
        { "type": "longSum", "name": "qty", "fieldName": "quantity" },
        { "type": "doubleSum", "name": "revenue", "fieldName": "price" },
        {
          "type": "HLLSketchBuild",
          "name": "uniq_users",
          "fieldName": "user_id"
        }
      ]
    },
    "ioConfig": {
      "topic": "ecommerce.events.clean.v1",
      "consumerProperties": {
        "bootstrap.servers": "kafka:9092",
        "group.id": "druid-ecom-gold",
        "isolation.level": "read_committed"
      },
      "taskCount": 2,
      "replicas": 2
    }
  }
}
```

> **📌 Why rollup beats Flink windowing here**
>
> With `rollup=true` + `queryGranularity=MINUTE`, Druid stores one row per (event_type, category, payment_mode, device, minute) tuple instead of one row per event. For 80k events/sec across ~40 unique dimension tuples per minute, that's a ~120,000× compression — and queries like "revenue per category last 24h" become a small range scan on pre-aggregated data.

---

## § 9 ClickHouse — Historical OLAP

ClickHouse holds the full event history (2-year TTL) and is the surface for ad-hoc analyst queries, A/B test analysis, and complex SQL that doesn't fit Druid's pre-aggregated model. The pattern is canonical: a `Kafka Engine` table consumes the hub topic, a materialized view glues it to a `ReplicatedReplacingMergeTree` storage table, and additional MVs compute KPIs at write time.

```
┌──────────────────┐     ┌──────────────┐     ┌────────────┐    ┌─────────────────────┐
│ Kafka clean topic│ ──► │ events_kafka │ ──► │ events_mv  │ ─► │ events_local        │
│ events.clean.v1  │     │ Engine=Kafka │     │ MV (glue)  │    │ ReplicatedReplacing │
└──────────────────┘     └──────────────┘     └────────────┘    │ MergeTree           │
                                                                │                     │
                                                                ├─► kpi_1min_mv       │
                                                                │   AggregatingMT     │
                                                                │                     │
                                                                └─► funnel_5min_mv    │
                                                                    AggregatingMT     │
                                                                └─────────────────────┘
```

_Figure 9.1 — ClickHouse ingestion pattern: Kafka Engine → MV glue → MergeTree storage → cascading KPI MVs._

### Storage Table

```sql
CREATE TABLE ecom.events_local ON CLUSTER '{cluster}' (
  event_id      String,
  event_time    DateTime64(3),
  user_id       Nullable(String),
  session_id    String,
  product_id    Nullable(String),
  category      LowCardinality(Nullable(String)),
  event_type    LowCardinality(String),
  quantity      Nullable(UInt32),
  price         Nullable(Float64),
  payment_mode  LowCardinality(Nullable(String)),
  device        LowCardinality(String),
  ingest_ts     DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplicatedReplacingMergeTree(
  '/clickhouse/tables/{shard}/events', '{replica}')
PARTITION BY toYYYYMMDD(event_time)
ORDER BY (event_type, category, event_time, event_id)
TTL event_time + INTERVAL 2 YEAR;
```

### Kafka Engine + Glue MV

```sql
CREATE TABLE ecom.events_kafka ON CLUSTER '{cluster}' (
  event_id String, event_time DateTime64(3),
  user_id Nullable(String), session_id String,
  product_id Nullable(String), category Nullable(String),
  event_type String, quantity Nullable(UInt32),
  price Nullable(Float64), payment_mode Nullable(String),
  device String
)
ENGINE = Kafka
SETTINGS
  kafka_broker_list         = 'kafka:9092',
  kafka_topic_list          = 'ecommerce.events.clean.v1',
  kafka_group_name          = 'clickhouse-ecom-events',
  kafka_format              = 'AvroConfluent',
  kafka_schema_registry_url = 'http://schema-registry:8081',
  kafka_num_consumers       = 4,
  kafka_handle_error_mode   = 'stream';

CREATE MATERIALIZED VIEW ecom.events_mv ON CLUSTER '{cluster}'
TO ecom.events_local AS SELECT * FROM ecom.events_kafka;
```

### KPI Materialized Views

```sql
-- 1-minute KPI rollup
CREATE MATERIALIZED VIEW ecom.kpi_1min_mv ON CLUSTER '{cluster}'
ENGINE = ReplicatedAggregatingMergeTree(
  '/clickhouse/tables/{shard}/kpi_1min', '{replica}')
PARTITION BY toYYYYMMDD(window_start)
ORDER BY (category, event_type, window_start)
AS SELECT
  toStartOfMinute(event_time)        AS window_start,
  category,
  event_type,
  countState()                       AS event_count,
  sumState(quantity)                 AS qty,
  sumState(price * quantity)         AS revenue,
  uniqExactState(user_id)            AS unique_users
FROM ecom.events_local
GROUP BY window_start, category, event_type;

-- 5-minute funnel
CREATE MATERIALIZED VIEW ecom.funnel_5min_mv ON CLUSTER '{cluster}'
ENGINE = ReplicatedAggregatingMergeTree(
  '/clickhouse/tables/{shard}/funnel_5min', '{replica}')
PARTITION BY toYYYYMMDD(window_start)
ORDER BY (category, window_start)
AS SELECT
  toStartOfFiveMinute(event_time) AS window_start,
  category,
  countIfState(event_type = 'VIEW')                          AS views,
  countIfState(event_type = 'ADD_TO_CART')                   AS carts,
  countIfState(event_type = 'ORDER_CREATED')                 AS orders,
  countIfState(event_type = 'PAYMENT_SUCCESS')               AS payments,
  sumIfState(price * quantity, event_type='PAYMENT_SUCCESS') AS revenue
FROM ecom.events_local
GROUP BY window_start, category;
```

> **💡 Engineering Insight**
>
> Adding a new metric is now a `CREATE MATERIALIZED VIEW`, not a Flink savepoint-and-redeploy. Compare that with a Flink-side aggregation where adding a metric requires updating the windowed operator, restoring from savepoint, and reprocessing — all while keeping the previous version running.

---

## § 10 Iceberg via Kafka Connect

The Iceberg sink is operationally a _sidecar_ — a Kafka Connect cluster running the [Iceberg Sink Connector](https://github.com/databricks/iceberg-kafka-connect). Connect tasks read the same hub topic, batch records, and centrally commit small files into Iceberg tables on MinIO/S3.

```json
{
  "name": "iceberg-sink-events",
  "config": {
    "connector.class": "io.tabular.iceberg.connect.IcebergSinkConnector",
    "tasks.max": "4",
    "topics": "ecommerce.events.clean.v1",
    "iceberg.tables": "warehouse.silver.events_clean",
    "iceberg.tables.upsert-mode-enabled": "true",
    "iceberg.tables.id-columns": "event_id",
    "iceberg.catalog.type": "rest",
    "iceberg.catalog.uri": "http://iceberg-rest:8181",
    "iceberg.control.commit.interval-ms": "60000",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter.schema.registry.url": "http://schema-registry:8081"
  }
}
```

### Why Connect Instead of a Flink Iceberg Sink

- **Centralized commits.** The Iceberg Connect connector elects one task as commit coordinator — one commit per minute across all tasks → no metadata explosion.
- **Independent failure domain.** Iceberg writes can fail or restart without touching the Flink job's checkpointing.
- **Independent scaling.** Connect `tasks.max` scales independently from Flink parallelism.
- **Less code in Flink.** Keeps the streaming job small and focused.

> **📝 Note — File Compaction**
>
> Even with centralized commits, an actively written table accumulates many small Parquet files over weeks. The Airflow `iceberg_compaction` DAG (see § 12) runs `rewrite_data_files` nightly to keep query performance high.

---

# Part IV — Operations

> Observability, orchestration, replays, and the performance envelope this design is built for.

## § 11 ELK Observability

Logs, metrics, and synthetic checks all flow through the Elastic stack. There is no separate Prometheus or Grafana — Kibana is the single pane.

```
┌─────────────────────┐
│ Filebeat (logs)     │ ──┐
└─────────────────────┘   │
┌─────────────────────┐   │     ┌─────────────────┐     ┌─────────────────┐     ┌─────────┐
│ Metricbeat (metrics)│ ──┼───► │ Logstash (parse)│ ──► │ Elasticsearch   │ ──► │ Kibana  │
└─────────────────────┘   │     └─────────────────┘     └────────┬────────┘     └─────────┘
┌─────────────────────┐   │                                      │
│ Heartbeat (synthetic)│ ─┘                                      ▼
└─────────────────────┘                              ┌────────────────────┐    ┌─────────────┐
                                                    │ Watcher · ElastAlert│ ─► │ Slack · PD  │
                                                    └────────────────────┘    └─────────────┘
```

_Figure 11.1 — ELK pipeline: Beats agents → Logstash → Elasticsearch → Kibana / Alerting._

### Beats Layout

| Beat           | Where                | What it watches                                                             |
| -------------- | -------------------- | --------------------------------------------------------------------------- |
| **Filebeat**   | Every node/container | stdout/stderr from Flink, Kafka, Connect, CH, Druid, Airflow                |
| **Metricbeat** | Every node           | `system`, `docker`, `kafka` (JMX), `jvm`, `clickhouse`, `http` (Flink REST) |
| **Heartbeat**  | Dedicated            | Druid SQL, CH `SELECT 1`, Flink JM, consumer-group lag thresholds           |

### Index Lifecycle (ILM)

| Index pattern       | Hot       | Warm | Cold | Delete |
| ------------------- | --------- | ---- | ---- | ------ |
| `logs-flink-*`      | 1d / 50GB | 7d   | 30d  | 90d    |
| `logs-kafka-*`      | 1d / 50GB | 7d   | 30d  | 90d    |
| `logs-clickhouse-*` | 1d / 30GB | 7d   | 30d  | 90d    |
| `logs-druid-*`      | 1d / 30GB | 7d   | 30d  | 90d    |
| `metricbeat-*`      | 1d / 50GB | 7d   | 30d  | 180d   |
| `heartbeat-*`       | 1d        | 14d  | —    | 90d    |

Hot tier on NVMe, warm on SSD, cold on HDD. ILM automates rollover via the Elasticsearch policy engine.

### Standard Alert Set

| Alert                       | Trigger            | Severity |
| --------------------------- | ------------------ | -------- |
| Druid consumer lag          | > 50k for 2 min    | P1       |
| ClickHouse consumer lag     | > 100k for 5 min   | P2       |
| Flink checkpoint failures   | ≥ 3 consecutive    | P1       |
| Flink job restart           | > 0 in last 1 min  | P1       |
| Kafka URP                   | > 0 for 5 min      | P1       |
| CH parts per partition      | > 300              | P2       |
| Iceberg connect commit fail | any                | P2       |
| DLQ rate                    | > 1% of throughput | P2       |

### Sample Watcher Alert

```json
{
  "trigger": { "schedule": { "interval": "1m" } },
  "input": {
    "search": {
      "request": {
        "indices": ["metricbeat-kafka-*"],
        "body": {
          "query": {
            "bool": {
              "filter": [
                { "term": { "kafka.consumergroup.id": "druid-ecom-gold" } },
                { "range": { "@timestamp": { "gte": "now-2m" } } }
              ]
            }
          },
          "aggs": {
            "max_lag": { "max": { "field": "kafka.consumergroup.lag" } }
          }
        }
      }
    }
  },
  "condition": {
    "compare": { "ctx.payload.aggregations.max_lag.value": { "gt": 50000 } }
  },
  "actions": { "slack": { "...": "..." }, "pagerduty": { "...": "..." } }
}
```

### Kibana Dashboards

1. **Pipeline Health** — Flink checkpoint duration, restart count, throughput per operator
2. **Sink Lag Watchtower** — One panel per consumer group with color-coded thresholds
3. **DLQ Inspector** — `dlq.events` reasons, rates, top offenders
4. **Druid Ops** — segment counts, query latency, supervisor health
5. **ClickHouse Ops** — parts per partition, merge backlog, replication delay

---

## § 12 Airflow Orchestration

Airflow handles everything that is genuinely batch-shaped — periodic maintenance, compliance, and recovery jobs. The streaming layer never depends on Airflow at runtime.

| DAG                        | Schedule    | Purpose                                                                 |
| -------------------------- | ----------- | ----------------------------------------------------------------------- |
| `iceberg_compaction`       | daily 02:00 | `rewrite_data_files` + `expire_snapshots` on silver tables              |
| `dq_great_expectations`    | hourly      | Run GE suites against Iceberg silver layer; page on failure             |
| `gdpr_right_to_forget`     | triggered   | Cascade deletes across Iceberg / ClickHouse / Druid for a given user_id |
| `flink_savepoint_rotation` | daily 03:00 | Trigger Flink savepoint, retain last 7                                  |

> **💡 Engineering Insight**
>
> The streaming pipeline is fully self-sufficient at runtime — Airflow exists only for housekeeping. If Airflow is down, dashboards keep working, ingestion keeps working, replays work via consumer-group offsets. This is a strict separation of _data plane_ (always-on streaming) from _control plane_ (periodic batch).

---

## § 13 Operations & Replay

### Replay ClickHouse from Earliest

To replay all retained data into ClickHouse, recreate the Kafka Engine table with a new consumer group name:

```sql
DETACH TABLE ecom.events_kafka ON CLUSTER '{cluster}';

ALTER TABLE ecom.events_kafka ON CLUSTER '{cluster}'
  MODIFY SETTING kafka_group_name = 'ch-ecom-replay-2026-05-08';

ATTACH TABLE ecom.events_kafka ON CLUSTER '{cluster}';
```

### Replay Druid from a Specific Timestamp

```bash
# 1. Terminate the supervisor
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce_events/terminate

# 2. Reset the consumer group offsets to a timestamp
kafka-consumer-groups.sh --bootstrap-server kafka:9092 \
  --group druid-ecom-gold --topic ecommerce.events.clean.v1 \
  --reset-offsets --to-datetime 2026-05-08T00:00:00.000 --execute

# 3. Re-create the supervisor
curl -X POST -H 'Content-Type: application/json' \
  -d @druid/ingestion-spec.json \
  http://localhost:8888/druid/indexer/v1/supervisor
```

### Trigger a Flink Savepoint

```bash
flink savepoint <jobId> s3://flink-savepoints/ecom/
```

> **📌 Operational Tip**
>
> Always take a manual savepoint before deploying a new Flink JAR, then submit the new job with `--fromSavepoint <path>`. This preserves dedup state across upgrades — without it, a deploy could let through duplicates that arrived during the gap.

---

## § 14 Performance Targets

| Metric                               | Target          | Notes                                                           |
| ------------------------------------ | --------------- | --------------------------------------------------------------- |
| Flink throughput                     | 80k+ events/sec | 4 TaskManagers × 8 slots; bottleneck is Avro deserialization    |
| Flink p95 latency (raw → clean)      | < 200ms         | Dominated by 30s checkpoint barrier; unaligned checkpoints help |
| Druid end-to-end (event → queryable) | < 30s           | Bound by `intermediatePersistPeriod`                            |
| ClickHouse end-to-end                | < 60s           | Bound by Kafka Engine block size + MV materialization           |
| Druid query p95                      | < 500ms         | On 7 days, rollup-aggregated                                    |
| ClickHouse query p95 (1B rows)       | < 2s            | With proper ORDER BY hits                                       |
| Iceberg commit interval              | 60s             | Connect default; tunable                                        |
| Kafka log retention (hub)            | 7 days          | Replay window without Iceberg                                   |

> **🔑 Key Takeaway**
>
> By moving aggregations out of Flink, the streaming job becomes CPU-bound on a single thing — Avro deserialization — which scales linearly with TaskManager count. Throughput targets above are conservative for the simplified design.

---

## § 15 Project Structure & Quick Start

### Repository Layout

```
ecommerce-realtime-pipeline-v3/
├── README.md
├── docker/
│   ├── docker-compose.yml
│   └── elk/
├── flink-app/                       # Java 17 / Maven
│   ├── pom.xml
│   └── src/main/java/com/ecom/
│       ├── EcomEtlJob.java
│       ├── ValidateAndClean.java
│       └── DedupFunction.java
├── clickhouse/
│   ├── 01_storage_tables.sql
│   ├── 02_kafka_engine.sql
│   └── 03_materialized_views.sql
├── druid/
│   └── ingestion-spec.json
├── kafka-connect/
│   └── iceberg-sink.json
├── airflow/dags/
├── elk/
│   ├── logstash-pipelines/
│   ├── ilm-policies/
│   ├── watcher-alerts/
│   └── kibana-dashboards/
└── scripts/
    ├── start_stack.sh
    └── submit_flink_job.sh
```

### Quick Start

```bash
# 1. Bring up the stack
./scripts/start_stack.sh

# 2. Register the Avro schema
curl -X POST http://localhost:8081/subjects/ecommerce.events.raw.v1-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d "{\"schema\":$(jq -Rs . < schemas/clickstream.avsc)}"

# 3. Submit the Flink ETL job
cd flink-app && mvn -DskipTests package
flink run -d -p 8 -c com.ecom.EcomEtlJob target/ecom-etl-3.0.jar

# 4. Apply ClickHouse schemas
clickhouse-client --multiquery < clickhouse/01_storage_tables.sql
clickhouse-client --multiquery < clickhouse/02_kafka_engine.sql
clickhouse-client --multiquery < clickhouse/03_materialized_views.sql

# 5. Configure the Iceberg sink connector
curl -X POST -H 'Content-Type: application/json' \
  -d @kafka-connect/iceberg-sink.json \
  http://localhost:8083/connectors

# 6. Launch the Druid Kafka Indexing supervisor
curl -X POST -H 'Content-Type: application/json' \
  -d @druid/ingestion-spec.json \
  http://localhost:8888/druid/indexer/v1/supervisor

# 7. Import Kibana dashboards
curl -X POST 'http://localhost:5601/api/saved_objects/_import?overwrite=true' \
  -H 'kbn-xsrf: true' --form file=@elk/kibana-dashboards/all.ndjson
```

### UIs

| Service         | URL                        |
| --------------- | -------------------------- |
| Flink Web UI    | http://localhost:8082      |
| Kafka UI        | http://localhost:8085      |
| Druid Console   | http://localhost:8888      |
| ClickHouse Play | http://localhost:8123/play |
| Kafka Connect   | http://localhost:8083      |
| Airflow         | http://localhost:8080      |
| Kibana          | http://localhost:5601      |
| MinIO Console   | http://localhost:9001      |

---

_Real-Time E-Commerce Analytics Pipeline · Engineering Wiki v3.0 · MIT License_

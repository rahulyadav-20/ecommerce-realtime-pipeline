# Architecture Decision Records — ecommerce-realtime-pipeline

This document captures the significant architectural decisions made for this pipeline using the formal ADR (Architecture Decision Record) format. Each ADR records the context in which the decision was made, the decision itself, and the trade-offs accepted.

ADRs are immutable once accepted. Superseded decisions are marked with a link to the superseding ADR rather than edited in place.

---

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](#adr-001-apache-flink-over-apache-spark-streaming) | Apache Flink over Apache Spark Streaming | Accepted |
| [ADR-002](#adr-002-dual-olap-engines-druid--clickhouse) | Dual OLAP Engines (Druid + ClickHouse) | Accepted |
| [ADR-003](#adr-003-kafka-hub-topic-fan-out-pattern) | Kafka Hub-Topic Fan-Out Pattern | Accepted |
| [ADR-004](#adr-004-flink-etl-scope--validate--dedup-only-no-aggregation) | Flink ETL Scope — Validate / Dedup Only, No Aggregation | Accepted |
| [ADR-005](#adr-005-rocksdb-over-hashmapstatebackend-for-flink-dedup) | RocksDB over HashMapStateBackend for Flink Dedup | Accepted |
| [ADR-006](#adr-006-iceberg-as-the-lakehouse-over-delta-lake--hudi) | Iceberg as the Lakehouse over Delta Lake / Hudi | Accepted |
| [ADR-007](#adr-007-localexecutor-for-airflow-over-celeryexecutor) | LocalExecutor for Airflow over CeleryExecutor | Accepted |
| [ADR-008](#adr-008-confluent-schema-registry-with-full_transitive-compatibility) | Confluent Schema Registry with FULL_TRANSITIVE Compatibility | Accepted |

---

## ADR-001: Apache Flink over Apache Spark Streaming

**Date:** 2025-01-15
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

The pipeline must process up to 80,000 events per second with sub-second end-to-end latency (p99 < 1 second from Kafka ingest to sink). The ETL logic requires stateful deduplication with a 2-hour window and guarantees that no event is counted more than once even if the pipeline restarts mid-processing.

Two stream processors were evaluated:

- **Apache Spark Structured Streaming** (3.5): micro-batch engine with configurable trigger intervals. Even at the lowest trigger (`Trigger.ProcessingTime("0 seconds")` continuous mode), Spark introduces at least 100ms of batching overhead per micro-batch. State management is limited to Spark's in-memory StateStore. Exactly-once requires external coordination.
- **Apache Flink** (1.19): true event-at-a-time streaming engine. Native watermark support for event-time processing. Pluggable state backends (RocksDB, HashMap). Transactional Kafka sinks with exactly-once semantics built into the framework.

Both are JVM-based with mature ecosystems and extensive community support.

### Decision

Use **Apache Flink 1.19** with the **RocksDB state backend** as the stream processing engine.

### Rationale

| Factor | Flink | Spark Structured Streaming |
|---|---|---|
| Processing model | True streaming (event-at-a-time) | Micro-batch (min ~100ms/batch) |
| End-to-end latency | Sub-100ms achievable | 100ms–1s typical |
| Exactly-once | Native (Flink checkpoints + Kafka transactions) | Requires external coordination |
| State management | Pluggable backends; RocksDB for large state | In-memory StateStore; limited scalability |
| Watermarks / event time | First-class citizen | Supported but less flexible |
| Dedup window support | Native KeyedProcessFunction with TTL | Requires custom flatMapGroupsWithState |
| JVM ecosystem | Java/Scala only | Java, Scala, Python (PySpark) |

The 2-hour dedup window at 80k events/sec could generate up to 576 million distinct event IDs in state. Flink's RocksDB backend handles this by spilling to disk, while Spark's in-memory StateStore would require proportional JVM heap allocation.

### Consequences

**Positive:**
- True event-at-a-time processing achieves p99 latency well under 1 second.
- Built-in exactly-once semantics via Kafka transactional producer integration with Flink checkpoints.
- RocksDB state backend scales the dedup window to hundreds of millions of keys without proportional heap growth.
- Native watermark support enables correct event-time windowing if aggregations are added in the future.
- Savepoint/restore mechanism enables zero-downtime job upgrades.

**Negative:**
- Job logic must be written in Java or Scala — no Python. This is acceptable because the team is comfortable with Java 17, but it prevents data scientists from directly modifying the ETL logic.
- Flink's programming model (DataStream API, KeyedProcessFunction) has a steeper learning curve than Spark's DataFrame API for engineers familiar with batch processing.
- Operational complexity: Flink requires a running JobManager to manage state and checkpoints, adding one more stateful service to operate.

---

## ADR-002: Dual OLAP Engines (Druid + ClickHouse)

**Date:** 2025-01-15
**Status:** Accepted
**Deciders:** Data Engineering Team, Analytics Team

### Context

Two distinct query patterns emerged from discussions with the Analytics and Product teams:

1. **Real-time dashboards:** product managers need dashboards that refresh every 10–30 seconds showing events-per-minute, conversion funnels, and revenue by category for the last 1–24 hours. Queries are fixed, low-cardinality aggregations over recent data. Speed is the priority; flexible schema is not needed.

2. **Ad-hoc historical analysis:** data analysts run exploratory SQL queries over weeks or months of data, joining event data with dimension tables, computing cohort retention, and investigating anomalies. Queries are unpredictable in structure. Flexibility and full-row access are the priority.

A single OLAP engine was evaluated for both workloads:
- **Druid alone:** excellent for real-time (rollup at ingestion), but rollup permanently discards individual row data, making ad-hoc exploration impossible. Druid's SQL dialect is also more limited than ClickHouse.
- **ClickHouse alone:** can satisfy both if a `AggregatingMergeTree` + materialized view approach is used, but real-time ingestion latency is higher than Druid's (ClickHouse's Kafka Engine batches inserts), and it does not pre-aggregate at ingestion, so real-time dashboard queries require scanning more data.

### Decision

Use **both Apache Druid 36 and ClickHouse 24.3** as complementary OLAP engines, each ingesting from the same clean Kafka topic via independent consumer groups.

- **Druid:** handles real-time dashboards. Rollup at ingestion (count + sum per dimension combination) enables sub-second aggregation queries. Time granularity: second.
- **ClickHouse:** handles ad-hoc historical SQL. Full row storage via Kafka Engine + MergeTree. Materialized views pre-aggregate common patterns for performance.

### Rationale

Each engine is used exclusively for the workload it excels at, rather than stretching one engine to cover both:

| Workload | Winner | Why |
|---|---|---|
| Real-time dashboards (< 100ms on last 1h) | Druid | Pre-aggregated at ingestion; no per-query scan overhead |
| Ad-hoc SQL (flexible, full-row, historical) | ClickHouse | Full-row storage; rich SQL dialect; columnar compression |
| GDPR row-level deletes | Neither (Iceberg) | See ADR-006 |
| Long-term lakehouse analytics (Spark, Trino) | Neither (Iceberg) | See ADR-006 |

### Consequences

**Positive:**
- Real-time dashboard p50 query latency < 100ms on Druid (pre-aggregated data, no scan).
- Ad-hoc queries over 30 days of data complete in < 2 seconds on ClickHouse (columnar, compressed, MaterializedView pre-aggregation).
- Each engine can be independently scaled, upgraded, or replaced without affecting the other.
- Both consumer groups can be independently replayed (see [docs/OPERATIONS.md](OPERATIONS.md)).

**Negative:**
- Two OLAP systems to operate, monitor, upgrade, and tune — doubled operational burden.
- Schema changes (new fields) must be applied to both systems: Druid ingestion spec (dimensionsSpec) and ClickHouse DDL.
- Analysts need to know which system to query for which use case — this requires team education and clear documentation.
- Total infrastructure cost is higher than a single-engine approach.

---

## ADR-003: Kafka Hub-Topic Fan-Out Pattern

**Date:** 2025-01-18
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

Three downstream sinks (Druid, ClickHouse, Iceberg) all need the same clean, validated, deduplicated events. Several delivery patterns were considered:

- **Flink writes directly to each sink:** Flink would have three output operators, each writing to Druid, ClickHouse, and Iceberg directly. This creates tight coupling between Flink and all sinks. Restarting or replaying any single sink requires reprocessing events through Flink.
- **Single Kafka topic, independent consumer groups (hub-topic fan-out):** Flink writes clean events to one Kafka topic. Each sink has its own consumer group and reads independently. Replaying a sink means resetting its consumer group offset — no Flink involvement.
- **Message queue fan-out (SNS/SQS pattern):** not applicable in Kafka; Kafka already provides independent consumption via consumer groups.

### Decision

Use a **single clean Kafka topic** (`ecommerce.events.clean.v1`) with **one independent consumer group per sink**:
- `druid-ecommerce-events` (Druid Kafka supervisor)
- `clickhouse-ecom-consumer` (ClickHouse Kafka Engine)
- `connect-iceberg-sink` (Kafka Connect)

Flink writes to exactly one output: the clean topic. Each sink is responsible for its own consumption.

### Rationale

- **Replay isolation:** if ClickHouse needs to be rebuilt, reset `clickhouse-ecom-consumer` to offset 0. Druid and Iceberg continue consuming from their current positions unaffected.
- **Sink independence:** a Kafka Connect crash does not cause Druid to fall behind. Each sink's consumer group lag is tracked independently.
- **Simplified Flink job:** Flink has one output sink (Kafka producer), making the job graph simpler and checkpoints faster.
- **Schema evolution:** the clean topic is the single source of truth. Adding a new sink requires only creating a new consumer group, not modifying Flink.
- **Kafka retention as a replay buffer:** with 7-day topic retention, any sink can replay up to 7 days of clean events at any time.

### Consequences

**Positive:**
- Any sink can be independently replayed, rebuilt, or replaced by resetting its consumer group.
- Flink's job is simpler and more maintainable — one input, one output.
- Adding a fourth sink in the future requires zero changes to Flink.
- Consumer group lag per sink is independently observable.

**Negative:**
- Kafka is the single point of failure for all sinks. If the clean topic is unavailable, all sinks stall simultaneously. Mitigation: 2-broker cluster with replication factor 2; events are preserved in the raw topic for reprocessing via Flink.
- Kafka topic retention must be long enough to support sink replay. Currently set to 7 days; this must be monitored against disk capacity.
- Each additional sink adds Kafka read throughput on the clean topic. At very high scale, this could require additional topic partitions and brokers.

---

## ADR-004: Flink ETL Scope — Validate / Dedup Only, No Aggregation

**Date:** 2025-01-20
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

During initial design, a proposal was made to compute common aggregations (events per product per hour, revenue per category per day) inside the Flink job and write the results directly to ClickHouse or Druid as pre-computed summary tables. The argument was performance: pre-computing in Flink would reduce query-time computation in OLAP engines.

The counter-argument: aggregation logic is tightly coupled to query patterns. Adding a new dimension (e.g., "events by device type") would require modifying and redeploying the Flink job — a stateful operation requiring a savepoint, an upgrade, and potentially state migration.

### Decision

**Flink does zero aggregation.** The Flink ETL job is strictly limited to:
1. Deserialising Avro events from the raw topic.
2. Validating required fields and routing invalid events to the DLQ.
3. Deduplicating events within a 2-hour window.
4. Publishing valid, deduplicated events to the clean topic.

All aggregation happens at query time (ClickHouse) or at ingestion time (Druid rollup). The Flink job has no knowledge of downstream query patterns.

### Rationale

| Concern | Pre-aggregation in Flink | Aggregation in OLAP |
|---|---|---|
| Adding a new dimension | Requires Flink job change + savepoint + redeploy | Requires only DDL change (ClickHouse) or spec update (Druid) |
| Aggregation logic versioning | Embedded in compiled JAR | Expressed in SQL / ingestion spec; independently versioned |
| Wrong aggregation discovered | Must roll back Flink job state | Drop and recreate MV / Druid table; replay from Kafka |
| Storage cost | Lower (aggregated rows only) | Slightly higher (full rows in ClickHouse) |
| Query flexibility | Fixed to pre-computed dimensions | Arbitrary SQL |

### Consequences

**Positive:**
- Flink job is smaller, simpler, and faster to checkpoint. State is only the dedup window, not aggregation state.
- New aggregations or dashboard requirements can be served by adding ClickHouse materialized views or Druid rollup dimensions — no Flink change, no savepoint.
- If an aggregation is found to be incorrect, it can be corrected by dropping the MV and replaying from the clean Kafka topic. No Flink state is involved.
- The clean Kafka topic contains full-fidelity rows, enabling any future aggregation pattern without reprocessing raw events.

**Negative:**
- Slightly higher storage cost in ClickHouse — full rows are stored rather than pre-aggregated summaries. At 80k events/sec with ~200 bytes per event, this is approximately 1.4 TB per day before ClickHouse compression (which typically achieves 5–10x compression on this data type, yielding 140–280 GB/day).
- Query-time aggregation on ClickHouse requires more CPU per query compared to reading pre-aggregated data. Mitigated by materialized views for common patterns.

---

## ADR-005: RocksDB over HashMapStateBackend for Flink Dedup

**Date:** 2025-01-22
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

The dedup operator (`DedupFunction`) maintains a set of `event_id` values seen within the last 2 hours. At the peak throughput of 80,000 events/second, the number of unique event IDs that must be tracked simultaneously is:

```
80,000 events/sec × 7,200 sec (2 hours) = 576,000,000 event IDs
```

Assuming each event_id is a 36-character UUID string (36 bytes) plus Flink state overhead (~40 bytes per entry), the state size is approximately:

```
576,000,000 × 76 bytes ≈ 43.7 GB
```

Flink offers two primary state backends:

- **HashMapStateBackend:** stores all state in the JVM heap. Requires 43.7 GB of JVM heap per TaskManager that holds this key range. JVM GC at this heap size causes multi-second stop-the-world pauses, which cause checkpoint timeouts and processing stalls.
- **EmbeddedRocksDBStateBackend:** stores state in RocksDB (an LSM-tree on SSD). JVM heap holds only the active working set; state spills to disk. Supports **incremental checkpoints** — only changed state blocks are written to the checkpoint store, not the entire 43 GB.

### Decision

Use **EmbeddedRocksDBStateBackend** with:
- Incremental checkpoints enabled.
- State TTL of 2 hours for the dedup key set.
- SSD-backed Docker volume for RocksDB data directory.

```java
// In JobConfig.java
RocksDBStateBackend backend = new RocksDBStateBackend(checkpointDir, true); // true = incremental
env.setStateBackend(backend);

// In DedupFunction.java — state descriptor with TTL
StateTtlConfig ttlConfig = StateTtlConfig
    .newBuilder(Time.hours(2))
    .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
    .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
    .build();

ValueStateDescriptor<Boolean> descriptor =
    new ValueStateDescriptor<>("seen", Boolean.class);
descriptor.enableTimeToLive(ttlConfig);
```

### Rationale

| Factor | HashMapStateBackend | EmbeddedRocksDB |
|---|---|---|
| State at peak load | ~44 GB in JVM heap | ~44 GB on SSD |
| JVM heap required | 44 GB+ per TaskManager | 2–4 GB per TaskManager |
| GC pause risk | High (multi-second at 44 GB) | None (RocksDB is off-heap) |
| Checkpoint type | Full (entire state each checkpoint) | Incremental (changed blocks only) |
| Checkpoint size | ~44 GB every 60 seconds | ~100 MB incremental per checkpoint |
| Read latency per key | ~100 nanoseconds (in-memory) | ~0.5–2 ms (SSD, RocksDB block cache) |
| State expiry | Manual; no built-in TTL | Built-in TTL with per-entry expiry |

At 80k events/sec with a 2-hour dedup window, the HashMapStateBackend is impractical — it would require 44+ GB JVM heap, causing unpredictable GC pauses that violate the p99 latency SLA. RocksDB's incremental checkpoints also dramatically reduce checkpoint I/O: instead of writing 44 GB every 60 seconds (733 MB/s), only the delta (~100 MB) is written.

### Consequences

**Positive:**
- TaskManager JVM heap can remain at 2–4 GB, eliminating GC pressure.
- Incremental checkpoints complete in seconds rather than minutes.
- RocksDB TTL handles state expiry automatically without custom cleanup logic.
- The SSD volume provides natural persistence — state survives container restarts.

**Negative:**
- RocksDB reads require a disk I/O if the key is not in the block cache (~0.5–2 ms vs ~100 ns for in-memory). At 80k events/sec, dedup lookups consume more CPU due to deserialization. The impact is acceptable: dedup lookup latency is an order of magnitude below the p99 SLA.
- RocksDB requires SSD-backed Docker volumes in production. Spinning-disk volumes would produce unacceptable read latency. This is a **hard infrastructure requirement** documented in SETUP.md.
- RocksDB adds native library dependencies to the Flink fat JAR, increasing its size.

---

## ADR-006: Iceberg as the Lakehouse over Delta Lake / Hudi

**Date:** 2025-01-25
**Status:** Accepted
**Deciders:** Data Engineering Team, Data Platform Team

### Context

The pipeline must maintain a long-term analytical store of all events with the following properties:
- **Schema evolution:** add new event fields over time without rewriting historical data.
- **GDPR compliance:** ability to delete all rows containing a specific `user_id` without rewriting entire partitions.
- **Open format:** data must be readable by Spark, Trino, and future query engines without proprietary connectors.
- **Partitioning:** time-based hidden partitioning to avoid partition skew from high-cardinality event types.

Three open table formats were evaluated:

| Feature | Apache Iceberg | Delta Lake | Apache Hudi |
|---|---|---|---|
| GDPR row deletes | Equality deletes (file-level metadata) | `DELETE` SQL (rewrite) | Record-level deletes |
| Schema evolution | Full support (add, rename, type widen) | Add/drop columns only | Supported |
| Hidden partitioning | Native | Not supported | Partial |
| REST catalog spec | Yes (vendor-neutral) | No | No |
| PyIceberg support | Native library | Delta-rs (Rust binding) | PyHudi (limited) |
| Databricks-native | Partial | Native | Partial |

### Decision

Use **Apache Iceberg 1.x** with the **tabulario/iceberg-rest** REST catalog and **MinIO** as the S3-compatible object store.

### Rationale

- **Equality deletes** are the most efficient GDPR mechanism for high-volume event data. Iceberg's equality delete files mark rows for deletion without rewriting the underlying data files — the actual rewrite is deferred to compaction (the `iceberg_compaction.py` Airflow DAG). Delta Lake requires a full `DELETE` SQL which rewrites affected Parquet files immediately.
- **Hidden partitioning** prevents the `dt=2025-01-15/event_type=page_view` style explicit partition columns that pollute schemas and cause partition explosion with high-cardinality event types. Iceberg derives partition values from the `event_time` column automatically.
- **Vendor-neutral REST catalog spec** means the catalog can be hosted by any compliant implementation (tabulario, AWS Glue Iceberg REST, Databricks Unity Catalog) without changing client code.
- **PyIceberg** provides a first-class Python library for reading and writing Iceberg tables, enabling the GDPR DAG (`gdpr_right_to_forget.py`) and compaction DAG (`iceberg_compaction.py`) to be written in Python without Spark or JVM dependencies.

### Consequences

**Positive:**
- GDPR right-to-forget can be implemented as an Airflow DAG using PyIceberg equality deletes — no Spark cluster required.
- Schema evolution (adding nullable fields) requires zero data rewriting — Iceberg tracks schema evolution in metadata.
- Hidden partitioning automatically optimises query plans without exposing partition details to analysts.
- The Iceberg REST catalog is self-hosted and vendor-neutral; migrating to a managed catalog (AWS Glue, Databricks) requires only a URL change.
- Data is stored as open Parquet files in MinIO — any Parquet-compatible tool can read them directly if needed.

**Negative:**
- Iceberg has less ecosystem support than Delta Lake in organisations heavily invested in Databricks. If the platform later adopts Databricks as the primary compute engine, Delta Lake would be more integrated.
- Equality deletes accumulate until compaction runs, meaning deleted rows are still present in data files (but excluded from query results by the metadata). This is acceptable for GDPR compliance as long as compaction runs within the required deletion window (e.g., 30 days).
- The tabulario/iceberg-rest Docker image is a self-hosted catalog; it must be maintained, backed up, and upgraded independently.

---

## ADR-007: LocalExecutor for Airflow over CeleryExecutor

**Date:** 2025-01-28
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

Apache Airflow supports several executors that determine how tasks are executed:

- **SequentialExecutor:** single-threaded, one task at a time. Only suitable for testing.
- **LocalExecutor:** runs tasks as subprocesses on the same machine as the scheduler. Supports parallelism up to the number of CPU cores on the host.
- **CeleryExecutor:** distributes tasks to a pool of Celery workers via a message broker (Redis or RabbitMQ). Enables horizontal scaling across multiple machines.
- **KubernetesExecutor:** spawns a Kubernetes pod per task. Excellent for isolation and scale but requires a Kubernetes cluster.

The Airflow DAGs in this pipeline are maintenance DAGs:
- `flink_savepoint_rotation` — runs hourly, single task chain, completes in ~30 seconds.
- `iceberg_compaction` — runs daily, 2–3 tasks, completes in ~5 minutes.
- `gdpr_right_to_forget` — triggered on-demand, 1–4 tasks depending on the deletion request.
- `dq_great_expectations` — runs daily, 3–5 tasks, completes in ~3 minutes.

Maximum concurrent tasks at any point: 4–6. All tasks run on the same infrastructure (access to the Docker network). There is no need to distribute tasks to separate worker machines.

### Decision

Use **LocalExecutor** (subprocess-based task execution) with a maximum parallelism of 8 subprocesses.

### Rationale

| Factor | LocalExecutor | CeleryExecutor |
|---|---|---|
| Additional services required | None | Redis + Celery workers (2+ containers) |
| Horizontal scaling | No (single machine) | Yes (add worker nodes) |
| Task concurrency | Up to host CPU cores | Unlimited (add workers) |
| Setup complexity | Minimal | Moderate (Redis config, worker config) |
| Failure recovery | Scheduler restart | Worker restart (independent of scheduler) |
| Suitable for this workload | Yes (<10 concurrent tasks) | Over-engineered for <10 concurrent tasks |

For the current workload — 4 DAGs with at most 6 concurrent tasks, all infrequently scheduled — CeleryExecutor would add two additional containers (Redis + at least one Celery worker) for zero benefit. LocalExecutor is sufficient and keeps the stack simpler.

### Consequences

**Positive:**
- No Redis or Celery worker containers needed — reduces total container count by 2–3.
- Simpler Airflow configuration (`AIRFLOW__CORE__EXECUTOR=LocalExecutor`).
- All tasks run within the same Docker network as the rest of the pipeline — no networking complexity for task workers.
- Easier to debug: task subprocess logs appear directly in the Airflow scheduler container.

**Negative:**
- Cannot run tasks on multiple machines. If the host running the Airflow scheduler is under load (e.g., during a Flink benchmark), Airflow tasks compete for the same CPU resources.
- The Airflow scheduler is a single point of failure. If it crashes, no new tasks are scheduled until it recovers. Mitigation: the scheduler restarts automatically via Docker Compose's `restart: unless-stopped` policy, and all DAGs have `catchup=False` to avoid backfilling missed runs.
- If task concurrency requirements grow significantly (e.g., 50+ concurrent GDPR deletion tasks), migration to CeleryExecutor or KubernetesExecutor will be required. The migration path is straightforward: change the executor setting and add the required services.

---

## ADR-008: Confluent Schema Registry with FULL_TRANSITIVE Compatibility

**Date:** 2025-02-01
**Status:** Accepted
**Deciders:** Data Engineering Team

### Context

The pipeline uses Apache Avro for event serialisation. Avro is a binary format where the schema is not embedded in every message — instead, messages carry a 4-byte schema ID, and consumers look up the schema from a registry. This means that consumers and producers can be upgraded independently only if schemas are mutually compatible.

Confluent Schema Registry supports four compatibility modes:

| Mode | Description |
|---|---|
| `NONE` | No compatibility check. Any schema accepted. |
| `BACKWARD` | New schema can read data written by the previous schema version. |
| `FORWARD` | Old schema can read data written by the new schema version. |
| `FULL` | Both BACKWARD and FORWARD vs the previous version only. |
| `FULL_TRANSITIVE` | Both BACKWARD and FORWARD vs **all** previous versions. |

The pipeline has multiple producers and consumers that are deployed independently:
- **Producer:** microservices / event generator (can be updated at any time)
- **Consumers:** Flink ETL job, Druid (via Kafka supervisor), ClickHouse (via Kafka Engine), Kafka Connect

If a producer deploys a new schema and consumers are not yet updated, the consumers must still be able to read messages written with the new schema. If an old producer continues running after consumers are upgraded, consumers must still read old-schema messages.

### Decision

Enforce **`FULL_TRANSITIVE`** compatibility on the `ecommerce.events.raw.v1-value` subject in Schema Registry.

```bash
# Set compatibility (done in register-schema.sh)
curl -X PUT http://localhost:8081/config/ecommerce.events.raw.v1-value \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"compatibility": "FULL_TRANSITIVE"}'
```

### Rules enforced by FULL_TRANSITIVE

A schema change is **allowed** if:
- Adding an optional (nullable) field with a `null` default.
- Widening a numeric type (e.g., `int` to `long`).

A schema change is **rejected** if:
- Removing any existing field (even an optional one).
- Adding a non-nullable field without a default.
- Renaming a field (treated as delete + add).
- Changing a field type to an incompatible type.

To "rename" a field in practice: add the new name as an alias in the Avro schema, and add the new field as optional. Deprecate the old field in documentation; remove it only if all consumers have migrated and a new major schema version is started.

### Rationale

`FULL_TRANSITIVE` (vs just `FULL`) ensures compatibility is guaranteed against all historical versions, not just the immediately preceding version. This matters because:

1. Kafka topic retention is 7 days. Old messages (written with schema version N-5) may still be on the topic when consumers are processing them. Consumers must be able to read any schema version still present on the topic.
2. Druid and ClickHouse consumers may lag behind producers by hours if there is a replay in progress. During replay, consumers process old messages (potentially from schema version N-10) while the producer is already on version N.
3. `FULL` (without TRANSITIVE) only guarantees compatibility with the immediately previous version. If the schema has evolved through 5 versions, a consumer on version 1 cannot necessarily read version 5 messages under `FULL`.

### Consequences

**Positive:**
- Producers and consumers can be deployed in any order without coordination — the registry rejects any schema that would break any existing consumer.
- Kafka topic can be replayed from any point in retention without schema compatibility errors.
- Schema evolution mistakes are caught at registration time (CI/CD schema check) rather than at runtime.
- All historical Avro messages on the topic remain decodable by any current consumer.

**Negative:**
- **Cannot remove fields** — even fields that are no longer used must remain in the schema permanently (or until a new major schema version is introduced with a new subject name). This leads to schema bloat over time.
- **Cannot rename fields** — renaming is treated as a delete + add, which violates `FULL_TRANSITIVE`. Use Avro field aliases for logical renaming.
- **Cannot make optional fields required** — adding a default to an existing non-nullable field is a breaking backward-compatible change.
- All new fields must be added as optional (`["null", "<type>"]` union with `"default": null`). This is enforced at registration time but can surprise developers who attempt to add a required field.
- Schema evolution becomes more deliberate and requires planning. This is a feature, not a bug — it forces explicit compatibility analysis before deployment.

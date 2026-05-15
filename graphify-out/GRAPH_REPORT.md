# Graph Report - .  (2026-05-15)

## Corpus Check
- 99 files · ~90,233 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1014 nodes · 1665 edges · 70 communities (60 shown, 10 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 130 edges (avg confidence: 0.76)
- Token cost: 88,004 input · 61,101 output

## Community Hubs (Navigation)
- [[_COMMUNITY_React Dashboard Components|React Dashboard Components]]
- [[_COMMUNITY_API Client & UI Hooks|API Client & UI Hooks]]
- [[_COMMUNITY_Flink ETL Job Core|Flink ETL Job Core]]
- [[_COMMUNITY_Dashboard API Services (DruidTests)|Dashboard API Services (Druid/Tests)]]
- [[_COMMUNITY_Flink Validation Tests|Flink Validation Tests]]
- [[_COMMUNITY_ClickHouse Native Client|ClickHouse Native Client]]
- [[_COMMUNITY_FastAPI Routers & Middleware|FastAPI Routers & Middleware]]
- [[_COMMUNITY_WebSocket & Connection Manager|WebSocket & Connection Manager]]
- [[_COMMUNITY_Flink REST Client|Flink REST Client]]
- [[_COMMUNITY_Config & Settings|Config & Settings]]
- [[_COMMUNITY_API Models & Enums|API Models & Enums]]
- [[_COMMUNITY_Dedup Function Tests|Dedup Function Tests]]
- [[_COMMUNITY_Performance Benchmarking|Performance Benchmarking]]
- [[_COMMUNITY_Lakehouse Architecture Decisions|Lakehouse Architecture Decisions]]
- [[_COMMUNITY_Druid OLAP Queries|Druid OLAP Queries]]
- [[_COMMUNITY_End-to-End Integration Tests|End-to-End Integration Tests]]
- [[_COMMUNITY_Client Unit Tests|Client Unit Tests]]
- [[_COMMUNITY_Stack Overview & README|Stack Overview & README]]
- [[_COMMUNITY_Docker Compose Full Stack|Docker Compose Full Stack]]
- [[_COMMUNITY_GDPR & Airflow DAGs|GDPR & Airflow DAGs]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]

## God Nodes (most connected - your core abstractions)
1. `E-Commerce Realtime Analytics Pipeline` - 29 edges
2. `TestFlinkClient` - 21 edges
3. `TestCacheService` - 21 edges
4. `ClickHouseClient` - 19 edges
5. `FlinkClient` - 19 edges
6. `Operations Runbook â€” ecommerce-realtime-pipeline` - 19 edges
7. `EndToEndPipelineTest` - 18 edges
8. `TestClickHouseClient` - 17 edges
9. `DedupFunctionTest` - 16 edges
10. `cn()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `Query 6: Cart Abandonment Rate by Category (Last 24h)` --semantically_similar_to--> `Purchase Funnel State Machine (VIEW → ADD_TO_CART → ORDER_CREATED → PAYMENT)`  [INFERRED] [semantically similar]
  druid/sample-queries.md → src/utils/event_generator.py
- `EcomEtlJob — Flink Job Skeleton (README §6)` --conceptually_related_to--> `Dead Letter Queue Pattern — invalid events routed to side output`  [INFERRED]
  README.md → flink-app/src/test/java/com/ecom/functions/ValidateAndCleanTest.java
- `EcomEtlJob — Flink Job Skeleton (README §6)` --conceptually_related_to--> `Event Time ±48h Freshness Window Validation Rule`  [INFERRED]
  README.md → flink-app/src/test/java/com/ecom/functions/ValidateAndCleanTest.java
- `_build_producer()` --calls--> `SchemaRegistryClient`  [INFERRED]
  src/utils/event_generator.py → dashboard-api/app/routers/websocket.py
- `_build_producer(args, schema_str)` --references--> `confluent-kafka Python Library`  [EXTRACTED]
  src/utils/event_generator.py → requirements.txt

## Hyperedges (group relationships)
- **Clean Topic Fan-Out to All Sinks** — readme_clean_topic, readme_apache_druid, readme_clickhouse, readme_kafka_connect [EXTRACTED 1.00]
- **Flink ETL Processing Pipeline** — readme_raw_topic, readme_validate_and_clean, readme_dedup_function, readme_clean_topic [EXTRACTED 1.00]
- **Observability Stack** — readme_elk_stack, readme_elasticsearch, readme_kibana, readme_apache_airflow [EXTRACTED 1.00]
- **Airflow Maintenance DAGs** — readme_dag_savepoint_rotation, readme_dag_iceberg_compaction, readme_dag_gdpr, readme_dag_dq [EXTRACTED 1.00]
- **Security Control Suite** — security_nginx_proxy, security_jwt_auth, security_kafka_sasl, security_ufw, security_wireguard, security_trivy, security_luks_encryption [EXTRACTED 1.00]
- **Dual OLAP Engines serving real-time and ad-hoc workloads from single clean topic** — architecture_decisions_clean_topic, architecture_decisions_apache_druid, architecture_decisions_clickhouse, architecture_decisions_adr002, architecture_decisions_adr003 [EXTRACTED 1.00]
- **GDPR Compliance Stack: Iceberg Equality Deletes + Airflow DAG + Audit Policy** — architecture_decisions_apache_iceberg, architecture_decisions_equality_deletes, audit_policy_gdpr_deletion_dag, audit_policy_gdpr_art17, architecture_decisions_gdpr_compliance [EXTRACTED 1.00]
- **Flink RocksDB State Management for large-scale deduplication** — architecture_decisions_adr005, architecture_decisions_rocksdb_state, architecture_decisions_dedup_function, architecture_decisions_2hr_dedup_window, architecture_decisions_incremental_checkpoints [EXTRACTED 1.00]
- **Schema Evolution Chain: Avro FULL_TRANSITIVE + Schema Registry + Flink + Sinks** — architecture_decisions_adr008, architecture_decisions_schema_registry, architecture_decisions_full_transitive, development_avro_schema, operations_troubleshoot_iceberg_schema, operations_troubleshoot_avro_deser [INFERRED 0.95]

## Communities (70 total, 10 thin omitted)

### Community 0 - "React Dashboard Components"
Cohesion: 0.05
Nodes (63): BAR_COLORS, CategoryChart(), ConsumerLagGauge(), GROUPS, LagBar(), EventTypeChart(), SLICES, Layout() (+55 more)

### Community 1 - "API Client & UI Hooks"
Cohesion: 0.05
Nodes (44): api, http, STEP_COLORS, STEP_LABELS, STEPS, DOT, ICON, SERVICE_LABELS (+36 more)

### Community 2 - "Flink ETL Job Core"
Cohesion: 0.05
Nodes (14): DedupFunctionTest, EcomEtlJob, EcomEtlJob (com.ecom.etl package), EcomEtlJob (com.ecom root package), EcomEtlJob, JobConfig, DedupFunction, ValidateAndClean (+6 more)

### Community 3 - "Dashboard API Services (Druid/Tests)"
Cohesion: 0.05
Nodes (29): _build_session(), check_health(), DruidClient, _get(), get_realtime_kpis(), get_revenue_timeseries(), DruidClient — synchronous requests-based client with connection pooling and expo, Execute a native Druid timeseries query (JSON-over-HTTP format).          Return (+21 more)

### Community 4 - "Flink Validation Tests"
Cohesion: 0.12
Nodes (6): CleaningOperations, DlqMessageFormat, EventTimeFreshnessValidation, RequiredFieldValidation, ValidateAndCleanTest, ValueRangeValidation

### Community 5 - "ClickHouse Native Client"
Cohesion: 0.08
Nodes (15): acquire(), check_health(), ClickHouseClient, ClickHousePool, _get(), get_funnel(), get_live_events(), get_top_products() (+7 more)

### Community 6 - "FastAPI Routers & Middleware"
Cohesion: 0.08
Nodes (27): lifespan(), get_funnel(), get_live_events(), get_top_products(), Conversion funnel from product view → payment, grouped by the chosen dimension., Top N products by revenue over the given period., Most recent N events ordered by event_time DESC., get_realtime_kpis() (+19 more)

### Community 7 - "WebSocket & Connection Manager"
Cohesion: 0.08
Nodes (27): _check_token(), ConnectionManager, get_manager(), get_registry(), kafka_fanout_task(), WebSocket real-time event stream  —  /ws/events  Architecture ════════════, Synchronous decode of a cached schema.  Returns None if the schema         hasn', Fetch schema if needed, then decode. Returns None on failure. (+19 more)

### Community 8 - "Flink REST Client"
Cohesion: 0.11
Nodes (17): check_health(), close_client(), FlinkClient, _get(), get_checkpoint_stats(), get_flink_metrics(), get_job_metrics(), list_jobs() (+9 more)

### Community 9 - "Config & Settings"
Cohesion: 0.11
Nodes (22): _build_settings(), ClickHouseConfig, DruidConfig, FlinkConfig, _get(), _get_bool(), _get_int(), get_settings() (+14 more)

### Community 10 - "API Models & Enums"
Cohesion: 0.12
Nodes (20): Enum, EventType, Granularity, TimeWindow, str, _advance(), _build_producer(), _load_schema() (+12 more)

### Community 11 - "Dedup Function Tests"
Cohesion: 0.18
Nodes (3): DedupFunctionTest, MetricsCounter, TtlConfiguration

### Community 12 - "Performance Benchmarking"
Cohesion: 0.17
Nodes (22): ascii_checkpoint_chart(), ascii_latency_chart(), ascii_resource_chart(), ascii_throughput_chart(), ascii_timeseries(), _bar(), build_report(), compute_phase_stats() (+14 more)

### Community 13 - "Lakehouse Architecture Decisions"
Cohesion: 0.08
Nodes (24): ADR-006: Iceberg as the Lakehouse over Delta Lake / Hudi, Apache Hudi, Apache Iceberg 1.x, Delta Lake, Iceberg Equality Deletes, GDPR Compliance, Iceberg Hidden Partitioning, tabulario/iceberg-rest REST Catalog (+16 more)

### Community 14 - "Druid OLAP Queries"
Cohesion: 0.1
Nodes (22): Query 7: Average Order Value by Hour of Day (Last 7 Days), Query 6: Cart Abandonment Rate by Category (Last 24h), Query 10: Category Performance Leaderboard (Last 7 Days), Query 8: Event Type Distribution Pie Chart (Last 24h), Query 2: Top 10 Event Types by Count (Last Hour), Query 4: Hourly Event Count Timeseries (Last 3 Days), Query 3: Payment Success Rate by Payment Mode (Last 7 Days), Query 9: Peak Traffic Hours (Last 30 Days) (+14 more)

### Community 15 - "End-to-End Integration Tests"
Cohesion: 0.16
Nodes (3): EndToEndPipelineTest, count(), Size

### Community 16 - "Client Unit Tests"
Cohesion: 0.16
Nodes (5): _mock_http_get(), Inject a mock httpx.AsyncClient into flink_client._client so that     ``client.g, FlinkClient tests inject a mock httpx.AsyncClient directly into     ``flink_clie, Composite method: patch _request directly so the three asyncio.gather         su, TestFlinkClient

### Community 17 - "Stack Overview & README"
Cohesion: 0.17
Nodes (18): fastavro Avro Deserialization, Airflow Orchestration (README §12), Architecture Overview — One Flink ETL, One Hub Topic, Three Sinks, ClickstreamEvent Avro Schema, Design Principle: Aggregations live where queries live (Druid/ClickHouse), Design Principle: Flink does ETL only — no aggregation, Design Principle: One hub topic, three independent consumers, Design Principle: Replay is a consumer-group reset, not a backfill DAG (+10 more)

### Community 18 - "Docker Compose Full Stack"
Cohesion: 0.18
Nodes (17): docker-compose.yml (Full Stack), Airflow 2.9 Services (webserver, scheduler, worker), ClickHouse Service (Full Stack), Druid Services (coordinator, broker, historical, middlemanager, router), ELK Stack (Elasticsearch, Logstash, Kibana, Filebeat, Metricbeat, Heartbeat), Iceberg REST Catalog Service, Kafka 2-Broker Cluster (Full Stack), Kafka Connect Service (Full Stack) (+9 more)

### Community 19 - "GDPR & Airflow DAGs"
Cohesion: 0.18
Nodes (16): _catalog(), _ch_query(), delete_clickhouse(), delete_druid(), delete_iceberg(), _get_param(), Execute a ClickHouse SQL statement via the HTTP interface., Retrieve a DAG param from Airflow context (kwargs['params']) or _STATE.     Fall (+8 more)

### Community 20 - "Community 20"
Cohesion: 0.25
Nodes (14): BaseModel, CheckpointInfo, ComponentHealth, FlinkMetricsResponse, FunnelResponse, FunnelStep, HealthResponse, LiveEvent (+6 more)

### Community 21 - "Community 21"
Cohesion: 0.18
Nodes (15): aiokafka Async Kafka Consumer, EcomEtlJob Flink Main Class, Phase 1: Foundation & Infrastructure Setup, Phase 2: Flink ETL Job, Apache Flink, Apache Kafka, ecommerce.events.clean.v1 Kafka Topic, DedupFunction RocksDB-backed (+7 more)

### Community 22 - "Community 22"
Cohesion: 0.16
Nodes (5): EventConsumer, LatencyTracker, Thread-safe tracker for end-to-end latency.      The producer registers an event, Return and clear all latency samples collected since last call., Background thread that consumes from the clean topic.      For each message it t

### Community 23 - "Community 23"
Cohesion: 0.15
Nodes (14): Nginx Reverse Proxy with TLS Let's Encrypt, Resource Tuning Memory Limits, Deployment Smoke Tests, Systemd Auto-Start Service, VM Deployment Guide Ubuntu 22.04, Druid Kafka Ingestion Supervisor Spec, Phase 3: Storage Layer Druid, Phase 6: ELK Observability (+6 more)

### Community 24 - "Community 24"
Cohesion: 0.14
Nodes (14): Pipeline Alert Thresholds, Airflow Metadata Database Backup, ClickHouse Backup Procedure, Flink Savepoint Backup Procedure, Kibana Monitoring Dashboards, Operations Runbook â€” ecommerce-realtime-pipeline, Flink TaskManager Scaling Guide, Kafka Partition Scaling Guide (+6 more)

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (4): EventProducer, _make_event(), Background thread that produces events to the raw Kafka topic.      Rate is cont, Recovery sequence:           1. Pause the producer (keep events queued in Kafka)

### Community 26 - "Community 26"
Cohesion: 0.23
Nodes (13): clickhouse-driver Python Client, iceberg-sink-events Kafka Connect Config, Phase 5: Iceberg Lakehouse, Apache Iceberg, ClickHouse, gdpr_right_to_forget Airflow DAG, Iceberg Sink Connector, Kafka Connect (+5 more)

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (11): delete_old(), _flink_get(), _flink_post(), list_savepoints(), Trigger a savepoint for every RUNNING Flink job.      Flow:       GET /jobs/over, Enumerate all savepoint directories in MinIO under the configured prefix.      U, Delete savepoint directories older than RETAIN_DAYS, keeping at least     MIN_KE, GET from the Flink REST API, return parsed JSON. (+3 more)

### Community 28 - "Community 28"
Cohesion: 0.17
Nodes (12): FastAPI Dashboard API, Prometheus Client Metrics, Redis Async Caching, Security Architecture Overview, Docker Secrets Management, JWT Authentication, Kafka SASL SCRAM Authentication, LUKS At-Rest Encryption (+4 more)

### Community 29 - "Community 29"
Cohesion: 0.25
Nodes (10): _catalog(), collect_stats(), expire_snapshots(), _load_table(), Bin-pack small Parquet data files into files of ~TARGET_FILE_SIZE_MB.      Strat, Expire Iceberg snapshots older than SNAPSHOT_RETAIN_DAYS.      Why expire?     ─, Collect table statistics: row count, data-file count, total on-disk size.      U, Return an authenticated PyIceberg REST catalog backed by MinIO. (+2 more)

### Community 31 - "Community 31"
Cohesion: 0.31
Nodes (4): BenchmarkConfig, BenchmarkRunner, main(), Orchestrates all phases and writes the results CSV.

### Community 32 - "Community 32"
Cohesion: 0.18
Nodes (11): Airflow DAG Template (Python), Performance Benchmark Script (benchmark.py), CI/CD Checks (PR validation), Conventional Commits Convention, Druid Rollup Ingestion (dimensionsSpec), ecom-streaming-etl.jar (Fat JAR), Flink ETL Job (EcomEtlJob), Development Guide â€” ecommerce-realtime-pipeline (+3 more)

### Community 33 - "Community 33"
Cohesion: 0.29
Nodes (9): _catalog(), load_sample(), publish_results(), Read the last `lookback_hours` of data from the Iceberg table into a     Pandas, Run the Great Expectations suite against the loaded DataFrame.      Uses GE's ep, Write validation results to MinIO and send a Slack alert if the suite failed., run_expectations(), _s3_client() (+1 more)

### Community 34 - "Community 34"
Cohesion: 0.22
Nodes (5): DockerStatsCollector, _parse_mem(), Phase, Collect CPU % and memory for a list of container names., Return {container_name: {cpu_pct, mem_mb}} for all containers.

### Community 35 - "Community 35"
Cohesion: 0.2
Nodes (10): Dead Letter Queue Pattern — invalid events routed to side output, Event Time ±48h Freshness Window Validation Rule, ValidateAndCleanTest, CleaningOperations (Nested Test Class), DlqMessageFormat (Nested Test Class), EventTimeFreshnessValidation (Nested Test Class), RequiredFieldValidation (Nested Test Class), StaticHelpers (Nested Test Class) (+2 more)

### Community 36 - "Community 36"
Cohesion: 0.22
Nodes (9): 2-Hour Deduplication Window, ADR-001: Apache Flink over Apache Spark Streaming, ADR-005: RocksDB over HashMapStateBackend for Flink Dedup, Apache Flink 1.19, HashMapStateBackend, Incremental Checkpoints, EmbeddedRocksDBStateBackend, Apache Spark Structured Streaming (+1 more)

### Community 37 - "Community 37"
Cohesion: 0.25
Nodes (5): KafkaLagMonitor, MetricPoint, One row in the output CSV., Read consumer group lag via kafka-consumer-groups command., Return total lag (sum across all partitions) or -1 on error.

### Community 38 - "Community 38"
Cohesion: 0.25
Nodes (8): Phase 7: Airflow Orchestration, Apache Airflow, dq_great_expectations Airflow DAG, iceberg_compaction Airflow DAG, flink_savepoint_rotation Airflow DAG, Great Expectations Library, PyArrow Library, PyIceberg Library

### Community 39 - "Community 39"
Cohesion: 0.29
Nodes (8): ADR-002: Dual OLAP Engines (Druid + ClickHouse), ADR-004: Flink ETL Scope â€” Validate/Dedup Only, No Aggregation, Apache Druid 36, ClickHouse 24.3, DedupFunction (KeyedProcessFunction), Dashboard UI (E-Commerce Realtime Analytics SPA), React App Entry Point (main.tsx), ClickHouse AggregatingMergeTree Materialized View

### Community 40 - "Community 40"
Cohesion: 0.38
Nodes (7): ecom.events_local (ReplacingMergeTree Storage Table), ecom.events_kafka (Kafka Engine Table), ecom.events_mv (Main Materialized View), ecom.kafka_errors (Dead-Letter MergeTree Table), ecom.kafka_errors_mv (Dead-Letter Materialized View), ClickHouse three-object Kafka ingestion pipeline rationale, ReplacingMergeTree at-least-once deduplication rationale

### Community 41 - "Community 41"
Cohesion: 0.29
Nodes (3): Thread-safe token bucket for rate limiting.      At high rates (>50k/s) the buck, Block until `n` tokens are available., TokenBucket

### Community 42 - "Community 42"
Cohesion: 0.29
Nodes (7): Phase 1 Foundation Kafka Flink, Phase 3 Druid Real-Time OLAP Setup, Phase 4 ClickHouse Historical OLAP Setup, Phase 5 Iceberg Lakehouse Setup, Phase 6 ELK Observability Setup, Phase 7 Airflow Orchestration Setup, Quick Start Full Stack

### Community 43 - "Community 43"
Cohesion: 0.33
Nodes (6): ADR-008: Confluent Schema Registry with FULL_TRANSITIVE Compatibility, FULL_TRANSITIVE Avro Compatibility Mode, Confluent Schema Registry, clickstream-event.avsc (Avro Schema), Troubleshooting: Avro Deserialization Error, Troubleshooting: Iceberg Schema Mismatch

### Community 44 - "Community 44"
Cohesion: 0.53
Nodes (6): ADR-003: Kafka Hub-Topic Fan-Out Pattern, ecommerce.events.clean.v1 (Kafka Clean Topic), Independent Kafka Consumer Groups, Replay Runbook â€” ClickHouse Sink, Replay Runbook â€” Druid Sink, Replay Runbook â€” Iceberg via Kafka Connect

### Community 45 - "Community 45"
Cohesion: 0.5
Nodes (4): Config, get_settings(), Settings, BaseSettings

### Community 46 - "Community 46"
Cohesion: 0.5
Nodes (4): health_check(), _kafka_health(), Approximate Kafka health via Flink job overview (avoids kafka-python dependency), Parallel health check across Druid, ClickHouse, Flink, and Kafka.

### Community 47 - "Community 47"
Cohesion: 0.7
Nodes (5): ecom.events_kafka ClickHouse Kafka Engine Table, ecom.events_local ClickHouse Storage Table, ecom.funnel_5min_mv ClickHouse Materialized View, ecom.kpi_1min_mv ClickHouse Materialized View, Phase 4: Storage Layer ClickHouse

### Community 48 - "Community 48"
Cohesion: 0.67
Nodes (3): Performance Benchmark Script, EndToEndPipelineTest Integration Test, Phase 8: Testing & Optimization

### Community 49 - "Community 49"
Cohesion: 0.67
Nodes (3): ADR-007: LocalExecutor for Airflow over CeleryExecutor, Airflow CeleryExecutor, Airflow LocalExecutor

## Knowledge Gaps
- **271 isolated node(s):** `Create a fresh session with a random product and user profile.`, `Advance the session one step and return the Avro-compatible event dict.     Null`, `Return (producer, avro_serializer) or (None, None) for dry-run.`, `ecom.kafka_errors (Dead-Letter MergeTree Table)`, `JobConfig` (+266 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Size` connect `End-to-End Integration Tests` to `React Dashboard Components`?**
  _High betweenness centrality (0.098) - this node is a cross-community bridge._
- **Why does `CacheService` connect `FastAPI Routers & Middleware` to `Client Unit Tests`, `Dashboard API Services (Druid/Tests)`, `ClickHouse Native Client`, `WebSocket & Connection Manager`?**
  _High betweenness centrality (0.087) - this node is a cross-community bridge._
- **Why does `EndToEndPipelineTest` connect `End-to-End Integration Tests` to `Community 34`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `TestFlinkClient` (e.g. with `ClickHousePool` and `ClickHouseClient`) actually correct?**
  _`TestFlinkClient` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `TestCacheService` (e.g. with `ClickHousePool` and `ClickHouseClient`) actually correct?**
  _`TestCacheService` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `ClickHouseClient` (e.g. with `TestDruidClient` and `TestClickHousePool`) actually correct?**
  _`ClickHouseClient` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `FlinkClient` (e.g. with `TestDruidClient` and `TestClickHousePool`) actually correct?**
  _`FlinkClient` has 5 INFERRED edges - model-reasoned connections that need verification._
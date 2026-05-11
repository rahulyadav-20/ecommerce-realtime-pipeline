# Graph Report - .  (2026-05-11)

## Corpus Check
- Corpus is ~22,038 words - fits in a single context window. You may not need a graph.

## Summary
- 225 nodes · 412 edges · 12 communities (10 shown, 2 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.82)
- Token cost: 21,000 input · 6,000 output

## Community Hubs (Navigation)
- [[_COMMUNITY_ValidateAndClean Test Suite|ValidateAndClean Test Suite]]
- [[_COMMUNITY_DedupFunction Test Suite|DedupFunction Test Suite]]
- [[_COMMUNITY_Pipeline Architecture & Design Principles|Pipeline Architecture & Design Principles]]
- [[_COMMUNITY_Flink ETL Operators & Jobs|Flink ETL Operators & Jobs]]
- [[_COMMUNITY_Druid OLAP Query Cookbook|Druid OLAP Query Cookbook]]
- [[_COMMUNITY_ValidateAndClean Operator|ValidateAndClean Operator]]
- [[_COMMUNITY_Event Generator & Simulation|Event Generator & Simulation]]
- [[_COMMUNITY_Flink Job Config & Entrypoints|Flink Job Config & Entrypoints]]
- [[_COMMUNITY_Infrastructure & Service Orchestration|Infrastructure & Service Orchestration]]
- [[_COMMUNITY_ClickHouse Storage Pipeline|ClickHouse Storage Pipeline]]
- [[_COMMUNITY_Performance Targets|Performance Targets]]
- [[_COMMUNITY_Python Dependencies|Python Dependencies]]

## God Nodes (most connected - your core abstractions)
1. `DedupFunctionTest` - 16 edges
2. `Real-Time E-Commerce Analytics Pipeline (README)` - 13 edges
3. `JobConfig` - 12 edges
4. `ValidateAndClean` - 12 edges
5. `ValidateAndCleanTest` - 12 edges
6. `CleaningOperations` - 11 edges
7. `Druid SQL Query Cookbook` - 11 edges
8. `ValidateAndClean` - 10 edges
9. `StaticHelpers` - 10 edges
10. `DedupFunction` - 9 edges

## Surprising Connections (you probably didn't know these)
- `Purchase Funnel State Machine (VIEW → ADD_TO_CART → ORDER_CREATED → PAYMENT)` --semantically_similar_to--> `Query 6: Cart Abandonment Rate by Category (Last 24h)`  [INFERRED] [semantically similar]
  src/utils/event_generator.py → druid/sample-queries.md
- `Dead Letter Queue Pattern — invalid events routed to side output` --conceptually_related_to--> `EcomEtlJob — Flink Job Skeleton (README §6)`  [INFERRED]
  flink-app/src/test/java/com/ecom/functions/ValidateAndCleanTest.java → README.md
- `Event Time ±48h Freshness Window Validation Rule` --conceptually_related_to--> `EcomEtlJob — Flink Job Skeleton (README §6)`  [INFERRED]
  flink-app/src/test/java/com/ecom/functions/ValidateAndCleanTest.java → README.md
- `confluent-kafka[avro]==2.5.0` --references--> `_build_producer(args, schema_str)`  [EXTRACTED]
  requirements.txt → src/utils/event_generator.py
- `confluent-kafka[avro]==2.5.0` --references--> `_produce(producer, avro_ser, topic, event, metrics, dry_run)`  [EXTRACTED]
  requirements.txt → src/utils/event_generator.py

## Hyperedges (group relationships)
- **ClickHouse Kafka Ingestion Three-Object Pipeline** — 02_kafka_engine_events_kafka, 02_kafka_engine_events_mv, 001_create_events_local_events_local [EXTRACTED 1.00]
- **Flink ETL Validate-Dedup-Sink Processing Chain** — functions_validateandclean_validateandclean, functions_dedupfunction_dedupfunction, ecometljob_root_ecometljob [EXTRACTED 1.00]
- **Dead-Letter Queue Error Routing Pattern** — 02_kafka_engine_kafka_errors_mv, 02_kafka_engine_kafka_errors, functions_validateandclean_validateandclean [INFERRED 0.75]
- **One Kafka Hub Topic Fan-Out to Three Independent OLAP/Lakehouse Sinks** — readme_kafka_hub, compose_full_druid, compose_full_clickhouse, compose_full_iceberg_rest [EXTRACTED 1.00]
- **ELK Observability: Filebeat + Metricbeat + Heartbeat feed Elasticsearch** — filebeat_yml, metricbeat_yml, heartbeat_yml, compose_full_elk [EXTRACTED 1.00]
- **ValidateAndClean Test Harness: Required Fields + Value Range + Event Time Freshness** — validateandcleantest_requiredfieldvalidation, validateandcleantest_valuerangevalidation, validateandcleantest_eventtimefreshnessvalidation [EXTRACTED 1.00]

## Communities (12 total, 2 thin omitted)

### Community 0 - "ValidateAndClean Test Suite"
Cohesion: 0.12
Nodes (6): CleaningOperations, DlqMessageFormat, EventTimeFreshnessValidation, RequiredFieldValidation, ValidateAndCleanTest, ValueRangeValidation

### Community 1 - "DedupFunction Test Suite"
Cohesion: 0.18
Nodes (3): DedupFunctionTest, MetricsCounter, TtlConfiguration

### Community 2 - "Pipeline Architecture & Design Principles"
Cohesion: 0.12
Nodes (24): Dead Letter Queue Pattern — invalid events routed to side output, Event Time ±48h Freshness Window Validation Rule, Airflow Orchestration (README §12), Architecture Overview — One Flink ETL, One Hub Topic, Three Sinks, ClickstreamEvent Avro Schema (README §5), ClickHouse Historical OLAP (README §9), Design Principle: Aggregations live where queries live (Druid/ClickHouse), Design Principle: Flink does ETL only — no aggregation (+16 more)

### Community 3 - "Flink ETL Operators & Jobs"
Cohesion: 0.11
Nodes (8): DedupFunctionTest, EcomEtlJob (com.ecom.etl package), EcomEtlJob (com.ecom root package), DedupFunction, JobConfig, DedupFunction, ValidateAndClean, Flink DedupFunction RocksDB state design rationale

### Community 4 - "Druid OLAP Query Cookbook"
Cohesion: 0.1
Nodes (22): Query 7: Average Order Value by Hour of Day (Last 7 Days), Query 6: Cart Abandonment Rate by Category (Last 24h), Query 10: Category Performance Leaderboard (Last 7 Days), Query 8: Event Type Distribution Pie Chart (Last 24h), Query 2: Top 10 Event Types by Count (Last Hour), Query 4: Hourly Event Count Timeseries (Last 3 Days), Query 3: Payment Success Rate by Payment Mode (Last 7 Days), Query 9: Peak Traffic Hours (Last 30 Days) (+14 more)

### Community 5 - "ValidateAndClean Operator"
Cohesion: 0.15
Nodes (3): ValidateAndClean, StaticHelpers, ValidateAndClean stateless design rationale

### Community 6 - "Event Generator & Simulation"
Cohesion: 0.16
Nodes (14): Enum, str, _advance(), _build_producer(), _load_schema(), Metrics, _produce(), Create a fresh session with a random product and user profile. (+6 more)

### Community 7 - "Flink Job Config & Entrypoints"
Cohesion: 0.21
Nodes (3): EcomEtlJob, EcomEtlJob, JobConfig

### Community 8 - "Infrastructure & Service Orchestration"
Cohesion: 0.18
Nodes (17): docker-compose.yml (Full Stack), Airflow 2.9 Services (webserver, scheduler, worker), ClickHouse Service (Full Stack), Druid Services (coordinator, broker, historical, middlemanager, router), ELK Stack (Elasticsearch, Logstash, Kibana, Filebeat, Metricbeat, Heartbeat), Iceberg REST Catalog Service, Kafka 2-Broker Cluster (Full Stack), Kafka Connect Service (Full Stack) (+9 more)

### Community 9 - "ClickHouse Storage Pipeline"
Cohesion: 0.38
Nodes (7): ecom.events_local (ReplacingMergeTree Storage Table), ecom.events_kafka (Kafka Engine Table), ecom.events_mv (Main Materialized View), ecom.kafka_errors (Dead-Letter MergeTree Table), ecom.kafka_errors_mv (Dead-Letter Materialized View), ClickHouse three-object Kafka ingestion pipeline rationale, ReplacingMergeTree at-least-once deduplication rationale

## Knowledge Gaps
- **32 isolated node(s):** `Create a fresh session with a random product and user profile.`, `Advance the session one step and return the Avro-compatible event dict.     Null`, `Return (producer, avro_serializer) or (None, None) for dry-run.`, `ecom.kafka_errors (Dead-Letter MergeTree Table)`, `JobConfig` (+27 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ValidateAndClean` connect `Flink ETL Operators & Jobs` to `ValidateAndClean Operator`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `StaticHelpers` connect `ValidateAndClean Operator` to `ValidateAndClean Test Suite`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Why does `DedupFunction` connect `Flink ETL Operators & Jobs` to `ValidateAndClean Operator`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **What connects `Create a fresh session with a random product and user profile.`, `Advance the session one step and return the Avro-compatible event dict.     Null`, `Return (producer, avro_serializer) or (None, None) for dry-run.` to the rest of the system?**
  _32 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ValidateAndClean Test Suite` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `Pipeline Architecture & Design Principles` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `Flink ETL Operators & Jobs` be split into smaller, more focused modules?**
  _Cohesion score 0.11 - nodes in this community are weakly interconnected._
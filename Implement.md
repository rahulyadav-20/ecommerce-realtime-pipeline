# Real-Time E-Commerce Analytics Pipeline — Step-by-Step Implementation Plan

> **Personal Project Roadmap**: Build the entire pipeline incrementally with prompts you can use at each stage.

---

## 📋 Project Overview

**Goal**: Build a production-grade streaming analytics pipeline processing e-commerce events through Kafka → Flink (ETL) → Kafka Hub → Druid + ClickHouse + Iceberg, with full ELK observability.

**Timeline**: ~6-8 weeks (working 10-15 hours/week)

**Prerequisites**:

- Docker Desktop with 16GB RAM, 4+ CPUs
- Basic familiarity with Java, Python, SQL
- Access to an AWS account (or MinIO for local S3)

---

## Phase 1: Foundation & Infrastructure Setup (Week 1)

### Step 1.1: Set Up Local Docker Environment

**Prompt to Claude/ChatGPT**:

```
I'm building a real-time e-commerce analytics pipeline locally using Docker Compose.
I need you to create a docker-compose.yml file with the following services:

1. Apache Kafka (KRaft mode, 3 brokers)
2. Confluent Schema Registry
3. Apache Flink (1 JobManager + 2 TaskManagers)
4. Zookeeper (for coordination)
5. Kafka UI (for debugging)

Please provide:
- The complete docker-compose.yml
- Network configuration (single bridge network)
- Volume mounts for persistence
- Environment variables with sensible defaults
- Resource limits: Kafka brokers 2GB each, Flink TMs 4GB each

Include comments explaining key configuration choices.
```

**Deliverables**:

- `docker/docker-compose-phase1.yml`
- `docker/.env` with configurable ports

**Validation**:

```bash
docker-compose -f docker/docker-compose-phase1.yml up -d
docker ps  # All containers running
curl http://localhost:8081  # Flink UI accessible
```

---

### Step 1.2: Create Avro Event Schema

**Prompt**:

```
Create an Avro schema for e-commerce clickstream events with these fields:

Required fields:
- event_id (string, unique per event)
- session_id (string)
- event_time (timestamp-millis logical type)
- event_type (enum: VIEW, ADD_TO_CART, REMOVE_CART, WISHLIST, ORDER_CREATED,
  PAYMENT_SUCCESS, PAYMENT_FAILED, ORDER_CANCELLED, REFUND)
- device (string)
- ip (string)

Optional fields (nullable with null default):
- user_id (string)
- product_id (string)
- category (string)
- quantity (int)
- price (double)
- payment_mode (string)

Namespace: com.ecom.events
Schema name: ClickstreamEvent

Provide the complete .avsc file and a script to register it with Schema Registry.
```

**Deliverables**:

- `schemas/clickstream-event.avsc`
- `scripts/register-schema.sh`

**Validation**:

```bash
./scripts/register-schema.sh
curl http://localhost:8081/subjects  # Should list ecommerce.events.raw.v1-value
```

---

### Step 1.3: Build Synthetic Event Generator

**Prompt**:

```
Create a Python script that generates realistic synthetic e-commerce events and produces them
to Kafka topic 'ecommerce.events.raw.v1' using the Avro schema we defined.

Requirements:
- Use confluent-kafka Python library with AvroSerializer
- Generate events at configurable rate (default: 100 events/sec)
- Simulate realistic user journeys: VIEW → ADD_TO_CART → ORDER_CREATED → PAYMENT_SUCCESS
- 80% payment success rate, 20% failures
- 10 product categories, 100 products
- Include timestamps, session tracking, realistic prices
- Add CLI arguments: --rate, --duration, --topic

Provide:
1. The complete Python script (src/utils/event_generator.py)
2. requirements.txt with dependencies
3. Usage examples in comments
```

**Deliverables**:

- `src/utils/event_generator.py`
- `requirements.txt`

**Validation**:

```bash
python src/utils/event_generator.py --rate 100 --duration 60
# Check Kafka UI: topic ecommerce.events.raw.v1 should have ~6000 messages
```

---

## Phase 2: Flink ETL Job (Week 2)

### Step 2.1: Maven Project Setup

**Prompt**:

```
Create a Maven pom.xml for a Flink 1.19 streaming job with these dependencies:

- flink-streaming-java (1.19.0)
- flink-connector-kafka (1.19.0)
- flink-avro-confluent-registry (1.19.0)
- flink-statebackend-rocksdb (1.19.0)
- avro (1.11.3)
- slf4j-api and slf4j-simple

Project structure:
- groupId: com.ecom
- artifactId: ecom-streaming-etl
- version: 1.0-SNAPSHOT
- Java version: 17

Include maven-shade-plugin to create a fat JAR with all dependencies.
Provide the complete pom.xml with comments.
```

**Deliverables**:

- `flink-app/pom.xml`
- `flink-app/src/main/java/com/ecom/` (directory structure)

---

### Step 2.2: Implement Validation & Cleaning Function

**Prompt**:

```
Implement a Flink ProcessFunction that validates and cleans ClickstreamEvent records.

Class: ValidateAndClean extends ProcessFunction<ClickstreamEvent, ClickstreamEvent>

Validation rules:
1. Required fields non-null: event_id, session_id, event_type, device, ip, event_time
2. Value ranges: quantity >= 0, price >= 0
3. event_time within ±48 hours of processing time

Cleaning operations:
1. Trim whitespace from all string fields
2. Lowercase the category field
3. Normalize device to: "web", "android", "ios", "other"

If validation fails:
- Emit to side output (OutputTag<String> named "dlq")
- JSON format: {"reason": "...", "raw_payload": {...}, "ingest_ts": ...}

Provide:
1. Complete Java class with detailed comments
2. Unit test class using Flink's test harness
```

**Deliverables**:

- `flink-app/src/main/java/com/ecom/functions/ValidateAndClean.java`
- `flink-app/src/test/java/com/ecom/functions/ValidateAndCleanTest.java`

---

### Step 2.3: Implement Deduplication Function

**Prompt**:

```
Implement a Flink KeyedProcessFunction for event deduplication.

Class: DedupFunction extends KeyedProcessFunction<String, ClickstreamEvent, ClickstreamEvent>

Logic:
- Key by event_id
- Use MapState<String, Long> to track seen event_ids with timestamp
- TTL: 2 hours (7200000 ms)
- If event_id exists in state → drop (deduplicate)
- If new → store in state with current timestamp and emit

Include:
- State TTL configuration (UpdateType.OnCreateAndWrite, StateVisibility.NeverReturnExpired)
- Logging for dropped duplicates (at DEBUG level)
- Metrics: counter for duplicates_dropped

Provide complete Java class with comments and unit test.
```

**Deliverables**:

- `flink-app/src/main/java/com/ecom/functions/DedupFunction.java`
- `flink-app/src/test/java/com/ecom/functions/DedupFunctionTest.java`

---

### Step 2.4: Main Flink ETL Job

**Prompt**:

```
Create the main Flink streaming job that ties everything together.

Class: EcomEtlJob

Components:
1. StreamExecutionEnvironment with:
   - Checkpointing every 30 seconds (EXACTLY_ONCE)
   - RocksDB state backend (incremental)
   - Checkpoint storage: file:///tmp/flink-checkpoints (local for now)
   - Unaligned checkpoints enabled

2. KafkaSource:
   - Topic: ecommerce.events.raw.v1
   - Bootstrap servers: kafka:9092
   - Group ID: flink-ecom-etl
   - Avro deserialization via ConfluentRegistryAvroDeserializationSchema
   - Schema Registry: http://schema-registry:8081
   - Starting offsets: committed offsets, EARLIEST if none

3. Processing pipeline:
   - Source → ValidateAndClean (with DLQ side output) → keyBy(event_id) → DedupFunction

4. KafkaSink (clean topic):
   - Topic: ecommerce.events.clean.v1
   - Exactly-once via transactions (prefix: "ecom-etl-")
   - Avro serialization

5. KafkaSink (DLQ):
   - Topic: dlq.events
   - String serialization

Provide the complete main class with comments explaining each section.
```

**Deliverables**:

- `flink-app/src/main/java/com/ecom/EcomEtlJob.java`

**Validation**:

```bash
cd flink-app && mvn clean package
flink run -d -c com.ecom.EcomEtlJob target/ecom-streaming-etl-1.0-SNAPSHOT.jar
# Check Flink UI: job running with 2 operators
```

---

## Phase 3: Storage Layer — Druid (Week 3)

### Step 3.1: Add Druid to Docker Compose

**Prompt**:

```
Extend the docker-compose.yml to add Apache Druid 30.0.0 with the following services:

1. druid-postgres (metadata storage)
2. druid-coordinator
3. druid-broker
4. druid-historical
5. druid-middlemanager
6. druid-router (optional, for unified query endpoint)

Configuration:
- Shared deep storage: local disk /tmp/druid/segments
- Metadata storage: postgres DB
- ZooKeeper for coordination (already running)
- Kafka broker connection: kafka:9092
- All services in same network as Kafka/Flink

Include environment variables for JVM heap sizes and resource limits.
Provide the extended docker-compose.yml section for Druid.
```

**Deliverables**:

- Updated `docker/docker-compose.yml` with Druid services

**Validation**:

```bash
docker-compose up -d
curl http://localhost:8888  # Druid console accessible
```

---

### Step 3.2: Create Druid Ingestion Spec

**Prompt**:

```
Create a Druid Kafka ingestion supervisor spec for consuming from 'ecommerce.events.clean.v1'.

Spec requirements:
- Datasource name: ecommerce_events
- Timestamp column: event_time (ISO format)
- Dimensions: event_type, category, payment_mode, device
- Metrics (with rollup enabled):
  * count (events)
  * longSum on quantity → qty
  * doubleSum on price → revenue
  * HLLSketchBuild on user_id → uniq_users

- Segment granularity: HOUR
- Query granularity: MINUTE (1-minute rollup)
- Rollup: true

Kafka config:
- Consumer group: druid-ecom-gold
- isolation.level: read_committed
- taskCount: 2, replicas: 2
- useEarliestOffset: false

Tuning:
- maxRowsInMemory: 100000
- intermediatePersistPeriod: PT10M

Provide:
1. Complete JSON ingestion spec
2. Bash script to submit it to Druid
```

**Deliverables**:

- `druid/ingestion-spec.json`
- `scripts/submit-druid-supervisor.sh`

**Validation**:

```bash
./scripts/submit-druid-supervisor.sh
# Check Druid console: supervisor running, ingesting data
# Query: SELECT COUNT(*) FROM ecommerce_events WHERE __time > CURRENT_TIMESTAMP - INTERVAL '1' HOUR
```

---

### Step 3.3: Create Druid Query Examples

**Prompt**:

```
Create 10 realistic Druid SQL queries for the ecommerce_events datasource:

1. Revenue by category (last 24 hours)
2. Top 10 event types by count (last hour)
3. Payment success rate by payment_mode (last 7 days)
4. Hourly event count timeseries (last 3 days)
5. Unique users per device type (today)
6. Cart abandonment rate (ADD_TO_CART vs ORDER_CREATED, last 24h)
7. Average order value by hour (last 7 days)
8. Event type distribution (pie chart data, last 24h)
9. Peak traffic hours (events per hour, last 30 days)
10. Category performance leaderboard (revenue + conversion, last 7d)

For each query provide:
- The SQL query
- Expected output format
- Use case / dashboard context

Save as a markdown file with query + explanation for each.
```

**Deliverables**:

- `druid/sample-queries.md`

---

## Phase 4: Storage Layer — ClickHouse (Week 4)

### Step 4.1: Add ClickHouse to Docker Compose

**Prompt**:

```
Extend docker-compose.yml to add ClickHouse 24.x with:

1. clickhouse-server (single-node setup for local dev)
2. clickhouse-client (optional interactive client container)

Configuration:
- Expose ports: 8123 (HTTP), 9000 (native)
- Persistent volume for /var/lib/clickhouse
- users.xml with default user (no password for local dev)
- config.xml with settings:
  * max_memory_usage: 4GB
  * max_thread_pool_size: 8
  * kafka_num_consumers default: 4

Network: same as Kafka/Flink
Provide the docker-compose.yml section for ClickHouse.
```

**Deliverables**:

- Updated `docker/docker-compose.yml` with ClickHouse

**Validation**:

```bash
docker-compose up -d
echo "SELECT version()" | docker exec -i clickhouse-server clickhouse-client
# Should print ClickHouse version
```

---

### Step 4.2: Create ClickHouse Storage Tables

**Prompt**:

```
Create ClickHouse DDL for the events storage table.

Table: ecom.events_local
Engine: ReplacingMergeTree

Schema (matching Avro schema):
- event_id String
- event_time DateTime64(3)
- user_id Nullable(String)
- session_id String
- product_id Nullable(String)
- category LowCardinality(Nullable(String))
- event_type LowCardinality(String)
- quantity Nullable(UInt32)
- price Nullable(Float64)
- payment_mode LowCardinality(Nullable(String))
- device LowCardinality(String)
- ip String
- ingest_ts DateTime64(3) DEFAULT now64(3)

Partitioning: BY toYYYYMMDD(event_time)
Ordering: (event_type, category, event_time, event_id)
TTL: event_time + INTERVAL 2 YEAR

Provide the complete CREATE TABLE statement with comments explaining design choices.
```

**Deliverables**:

- `clickhouse/01_storage_tables.sql`

**Validation**:

```bash
docker exec -i clickhouse-server clickhouse-client --multiquery < clickhouse/01_storage_tables.sql
echo "SHOW TABLES FROM ecom" | docker exec -i clickhouse-server clickhouse-client
```

---

### Step 4.3: Create ClickHouse Kafka Engine

**Prompt**:

```
Create ClickHouse Kafka Engine table to consume from 'ecommerce.events.clean.v1'.

Table: ecom.events_kafka
Engine: Kafka

Settings:
- kafka_broker_list: kafka:9092
- kafka_topic_list: ecommerce.events.clean.v1
- kafka_group_name: clickhouse-ecom-events
- kafka_format: AvroConfluent
- kafka_schema_registry_url: http://schema-registry:8081
- kafka_num_consumers: 4
- kafka_handle_error_mode: stream  (bad records go to system.kafka_errors)

Schema: Match ecom.events_local columns (subset, no ingest_ts)

Also create the glue materialized view:
- Name: ecom.events_mv
- TO ecom.events_local
- AS SELECT * FROM ecom.events_kafka

Provide complete SQL with comments.
```

**Deliverables**:

- `clickhouse/02_kafka_engine.sql`

**Validation**:

```bash
docker exec -i clickhouse-server clickhouse-client --multiquery < clickhouse/02_kafka_engine.sql
# Wait 10 seconds
echo "SELECT COUNT(*) FROM ecom.events_local" | docker exec -i clickhouse-server clickhouse-client
# Should show increasing count
```

---

### Step 4.4: Create ClickHouse Materialized Views

**Prompt**:

```
Create two ClickHouse materialized views for aggregated KPIs:

1. ecom.kpi_1min_mv
   - Engine: AggregatingMergeTree
   - Partition: BY toYYYYMMDD(window_start)
   - Order: (category, event_type, window_start)
   - Aggregations:
     * window_start: toStartOfMinute(event_time)
     * category, event_type
     * countState() AS event_count
     * sumState(quantity) AS qty
     * sumState(price * quantity) AS revenue
     * uniqExactState(user_id) AS unique_users
   - Source: ecom.events_local

2. ecom.funnel_5min_mv
   - Engine: AggregatingMergeTree
   - Partition: BY toYYYYMMDD(window_start)
   - Order: (category, window_start)
   - Aggregations:
     * window_start: toStartOfFiveMinute(event_time)
     * category
     * countIfState(event_type = 'VIEW') AS views
     * countIfState(event_type = 'ADD_TO_CART') AS carts
     * countIfState(event_type = 'ORDER_CREATED') AS orders
     * countIfState(event_type = 'PAYMENT_SUCCESS') AS payments
     * sumIfState(price * quantity, event_type='PAYMENT_SUCCESS') AS revenue
   - Source: ecom.events_local

Provide complete CREATE MATERIALIZED VIEW statements with comments.
```

**Deliverables**:

- `clickhouse/03_materialized_views.sql`

**Validation**:

```bash
docker exec -i clickhouse-server clickhouse-client --multiquery < clickhouse/03_materialized_views.sql
# Wait 1 minute
echo "SELECT window_start, category, countMerge(event_count) FROM ecom.kpi_1min_mv GROUP BY window_start, category ORDER BY window_start DESC LIMIT 10" | docker exec -i clickhouse-server clickhouse-client
```

---

## Phase 5: Iceberg Lakehouse (Week 5)

### Step 5.1: Add MinIO + Iceberg REST Catalog

**Prompt**:

```
Extend docker-compose.yml to add:

1. MinIO (S3-compatible storage)
   - Ports: 9000 (API), 9001 (Console)
   - Create default bucket: warehouse
   - Access key: minio / Secret: minio123

2. Iceberg REST Catalog
   - Use tabular/iceberg-rest:latest image
   - Connect to MinIO for storage
   - PostgreSQL for metadata (can share with Druid's postgres)
   - Expose port: 8181

Provide docker-compose section with volume mounts and environment configuration.
```

**Deliverables**:

- Updated `docker/docker-compose.yml` with MinIO + Iceberg REST

**Validation**:

```bash
docker-compose up -d
curl http://localhost:9001  # MinIO console
curl http://localhost:8181/v1/config  # Iceberg REST catalog
```

---

### Step 5.2: Add Kafka Connect with Iceberg Sink

**Prompt**:

```
Extend docker-compose.yml to add Kafka Connect with the Iceberg Sink Connector.

Service: kafka-connect
- Base image: confluentinc/cp-kafka-connect:7.6.0
- Install Iceberg Sink Connector plugin (io.tabular:iceberg-kafka-connect)
- Expose port: 8083 (REST API)
- Environment:
  * Bootstrap servers: kafka:9092
  * Group ID: connect-cluster
  * Config/offset/status topics: connect-configs, connect-offsets, connect-status
  * Key/value converters: Avro with Schema Registry
  * Plugin path: /usr/share/java,/usr/share/confluent-hub-components

Provide docker-compose section and a script to install the Iceberg connector plugin on startup.
```

**Deliverables**:

- Updated `docker/docker-compose.yml` with Kafka Connect
- `docker/kafka-connect/install-connectors.sh`

**Validation**:

```bash
docker-compose up -d
curl http://localhost:8083/connector-plugins | jq  # Should list IcebergSinkConnector
```

---

### Step 5.3: Create Iceberg Sink Connector Config

**Prompt**:

```
Create a Kafka Connect Iceberg Sink Connector configuration JSON.

Connector name: iceberg-sink-events

Config:
- connector.class: io.tabular.iceberg.connect.IcebergSinkConnector
- tasks.max: 4
- topics: ecommerce.events.clean.v1
- iceberg.tables: warehouse.silver.events_clean
- iceberg.tables.upsert-mode-enabled: true
- iceberg.tables.id-columns: event_id
- iceberg.catalog.type: rest
- iceberg.catalog.uri: http://iceberg-rest:8181
- iceberg.catalog.warehouse: s3://warehouse
- iceberg.catalog.s3.endpoint: http://minio:9000
- iceberg.catalog.s3.access-key-id: minio
- iceberg.catalog.s3.secret-access-key: minio123
- iceberg.catalog.s3.path-style-access: true
- iceberg.control.commit.interval-ms: 60000
- value.converter: io.confluent.connect.avro.AvroConverter
- value.converter.schema.registry.url: http://schema-registry:8081

Provide:
1. Complete JSON connector config
2. Bash script to POST it to Kafka Connect REST API
```

**Deliverables**:

- `kafka-connect/iceberg-sink.json`
- `scripts/submit-iceberg-connector.sh`

**Validation**:

```bash
./scripts/submit-iceberg-connector.sh
curl http://localhost:8083/connectors/iceberg-sink-events/status | jq
# Status should be RUNNING, tasks RUNNING
```

---

## Phase 6: ELK Observability (Week 6)

### Step 6.1: Add ELK Stack to Docker Compose

**Prompt**:

```
Extend docker-compose.yml to add the ELK stack:

1. Elasticsearch (single-node)
   - Version: 8.13.0
   - Port: 9200
   - Environment:
     * discovery.type: single-node
     * xpack.security.enabled: false (for local dev)
     * ES_JAVA_OPTS: -Xms2g -Xmx2g

2. Kibana
   - Version: 8.13.0
   - Port: 5601
   - Connect to Elasticsearch at http://elasticsearch:9200

3. Logstash
   - Version: 8.13.0
   - Ports: 5044 (Beats input), 9600 (monitoring)
   - Pipeline config to be provided separately

4. Filebeat (log shipper)
5. Metricbeat (metrics collector)

Provide the complete ELK section of docker-compose.yml with volumes for persistence.
```

**Deliverables**:

- Updated `docker/docker-compose.yml` with ELK stack

**Validation**:

```bash
docker-compose up -d
curl http://localhost:9200  # ES cluster health
curl http://localhost:5601  # Kibana UI
```

---

### Step 6.2: Create Logstash Pipelines

**Prompt**:

```
Create 4 Logstash pipeline configurations to parse and route logs/metrics:

1. 10-flink.conf
   - Input: beats (from Filebeat/Metricbeat)
   - Filter: if container.name contains "flink"
     * Parse log4j JSON format
     * Extract: job_id, checkpoint_id, task_name
   - Output: Elasticsearch index logs-flink-%{+YYYY.MM.dd}

2. 11-kafka.conf
   - Filter: if container.name contains "kafka"
     * Parse Kafka server.log format
     * Extract: broker_id, topic, partition
   - Output: Elasticsearch index logs-kafka-%{+YYYY.MM.dd}

3. 12-clickhouse.conf
   - Filter: if container.name contains "clickhouse"
     * Parse ClickHouse text logs
     * Multiline for stack traces
   - Output: Elasticsearch index logs-clickhouse-%{+YYYY.MM.dd}

4. 20-metrics.conf
   - Input: beats with metricbeat metadata
   - Output: Elasticsearch index metricbeat-%{+YYYY.MM.dd}

Provide all 4 Logstash config files with full parsing logic and comments.
```

**Deliverables**:

- `docker/elk/logstash/pipeline/10-flink.conf`
- `docker/elk/logstash/pipeline/11-kafka.conf`
- `docker/elk/logstash/pipeline/12-clickhouse.conf`
- `docker/elk/logstash/pipeline/20-metrics.conf`

---

### Step 6.3: Create Kibana Dashboards & Watchers

**Prompt**:

```
Create 3 Kibana dashboards and 3 Elasticsearch Watcher alerts:

Dashboards:
1. Pipeline Health - Flink checkpoint duration, restarts, throughput
2. Sink Lag Watchtower - Consumer group lag gauges (Druid/CH/Connect)
3. DLQ Inspector - Failed events table with reasons

Watchers:
1. druid_consumer_lag_high - Alert if lag > 50k for 2 min
2. flink_checkpoint_failure - Alert if ≥3 failures in 5 min
3. dlq_rate_high - Alert if DLQ rate > 1% of total

For each provide:
- Dashboard: ndjson export + import script
- Watcher: JSON definition + PUT script
```

**Deliverables**:

- `elk/kibana-dashboards/*.ndjson`
- `elk/watcher-alerts/*.json`
- `scripts/import-kibana-dashboards.sh`
- `scripts/create-watchers.sh`

---

## Phase 7: Airflow Orchestration (Week 7)

### Step 7.1: Add Airflow to Docker Compose

**Prompt**:

```
Extend docker-compose.yml to add Apache Airflow 2.9:

Services:
1. airflow-postgres (metadata DB)
2. airflow-webserver (port 8080)
3. airflow-scheduler
4. airflow-init (one-time DB setup)

Configuration:
- Executor: LocalExecutor
- Dags folder: ./airflow/dags (mounted)
- Plugins: ./airflow/plugins
- Admin user: admin / admin

Provide docker-compose section and initialization script.
```

**Deliverables**:

- Updated `docker/docker-compose.yml`
- `scripts/init-airflow.sh`

**Validation**:

```bash
./scripts/init-airflow.sh
docker-compose up -d airflow-webserver
curl http://localhost:8080  # Airflow UI
```

---

### Step 7.2: Create 4 Airflow DAGs

**Prompt**:

```
Create 4 Airflow DAGs:

1. iceberg_compaction (daily 02:00)
   - Task 1: Spark rewrite_data_files (bin-pack small files)
   - Task 2: expire_snapshots (retain 7 days)
   - Task 3: collect_stats (row count, file count)

2. dq_great_expectations (hourly)
   - Run GE suite on Iceberg silver.events_clean
   - Expectations: not null, value ranges, regex patterns
   - On fail: Slack alert + mark as failed

3. gdpr_right_to_forget (manual trigger, param: user_id)
   - Task 1: DELETE from Iceberg via Trino
   - Task 2: ALTER TABLE DELETE in ClickHouse
   - Task 3: Druid kill segments API
   - Task 4: Write audit log to S3

4. flink_savepoint_rotation (daily 03:00)
   - Task 1: POST /jobs/<id>/savepoints (Flink API)
   - Task 2: List all savepoints in S3
   - Task 3: Delete savepoints older than 7 days

Provide complete Python DAG files for all 4 with error handling.
```

**Deliverables**:

- `airflow/dags/iceberg_compaction.py`
- `airflow/dags/dq_great_expectations.py`
- `airflow/dags/gdpr_right_to_forget.py`
- `airflow/dags/flink_savepoint_rotation.py`

---

## Phase 8: Testing & Optimization (Week 8)

### Step 8.1: Integration Test Suite

**Prompt**:

```
Create an end-to-end integration test using Testcontainers.

Test class: EndToEndPipelineTest

Steps:
1. Start containers: Kafka, Schema Registry, Flink MiniCluster, MinIO
2. Produce 1000 test events to raw topic
3. Submit Flink job
4. Poll clean topic until count = 1000 (with timeout)
5. Assertions:
   - No duplicates (check event_id uniqueness)
   - DLQ count = 0
   - Checkpoint completed
6. Teardown

Use: JUnit 5, Testcontainers, Awaitility
Provide complete test class.
```

**Deliverables**:

- `flink-app/src/test/java/com/ecom/integration/EndToEndPipelineTest.java`

---

### Step 8.2: Performance Benchmark

**Prompt**:

```
Create a performance benchmark script for the Flink ETL job.

Script: benchmark.py

Phases:
1. Warmup (1 min at 1k events/sec)
2. Steady state (5 min each at 10k, 50k, 80k events/sec)
3. Burst (30 sec at 150k events/sec)
4. Recovery (restart Flink, measure recovery time)

Metrics:
- End-to-end latency (p50, p95, p99)
- Throughput (events/sec)
- Checkpoint duration
- CPU/memory usage

Output:
- CSV timeseries
- Markdown report with charts

Provide complete Python script.
```

**Deliverables**:

- `tests/performance/benchmark.py`
- `tests/performance/analyze_results.py`

---

### Step 8.3: Documentation

**Prompt**:

```
Create comprehensive project documentation:

1. README.md
   - Project overview
   - Architecture diagram (text or link to diagram)
   - Quick start guide
   - Component descriptions

2. OPERATIONS.md
   - How to restart/scale each component
   - Replay runbooks (Druid, ClickHouse)
   - Troubleshooting guide
   - Monitoring & alerting setup

3. DEVELOPMENT.md
   - Local dev setup
   - How to build/test Flink job
   - How to add new metrics/MVs
   - Code structure & conventions

4. ARCHITECTURE_DECISIONS.md
   - Why Flink over Spark
   - Why dual OLAP (Druid + ClickHouse)
   - Why Kafka hub pattern
   - State management choices

Provide all 4 markdown files with detailed content.
```

**Deliverables**:

- `README.md`
- `docs/OPERATIONS.md`
- `docs/DEVELOPMENT.md`
- `docs/ARCHITECTURE_DECISIONS.md`

---

## 🎓 Learning Path & Resources

### Week-by-Week Focus

| Week | Primary Learning Topics                                        |
| ---- | -------------------------------------------------------------- |
| 1    | Kafka basics, Avro schemas, Docker networking                  |
| 2    | Flink DataStream API, RocksDB state, exactly-once semantics    |
| 3    | Druid architecture, rollup ingestion, time-series queries      |
| 4    | ClickHouse MergeTree engines, materialized views, Kafka engine |
| 5    | Iceberg table format, time travel, Kafka Connect               |
| 6    | ELK stack, Logstash grok patterns, Kibana visualizations       |
| 7    | Airflow operators, XComs, DAG scheduling                       |
| 8    | JUnit testing, performance profiling, technical writing        |

### Recommended Reading

1. **Flink**: "Stream Processing with Apache Flink" (Fabian Hueske)
2. **Kafka**: Confluent Kafka tutorials + "Kafka: The Definitive Guide"
3. **Druid**: Official docs → Design → Architecture
4. **ClickHouse**: Official docs → Engines → MergeTree Family
5. **Iceberg**: "Apache Iceberg: The Definitive Guide"

---

## 📊 Success Metrics

By the end of Week 8, you should have:

✅ A fully functional pipeline processing 10k+ events/sec
✅ Three working OLAP surfaces (Druid, ClickHouse, Iceberg)
✅ Full observability (logs, metrics, alerts)
✅ 4 Airflow automation DAGs
✅ Integration tests with >80% coverage
✅ Complete documentation set
✅ A GitHub repo ready to showcase in interviews

---

## 🚀 Next Steps (Post-Week 8)

1. **Deploy to AWS/GCP** — migrate from Docker Compose to Kubernetes
2. **Add more features**:
   - Real-time anomaly detection (Flink CEP)
   - Session windowing for user journey analysis
   - A/B test framework integration
3. **Scale testing** — benchmark at 100k events/sec
4. **Write blog posts** — document your learnings
5. **Present at meetups** — great resume differentiator

---

_Total estimated effort: 60-80 hours over 8 weeks_
_Difficulty: Intermediate to Advanced_
_Resume impact: Very High (demonstrates full-stack data engineering)_

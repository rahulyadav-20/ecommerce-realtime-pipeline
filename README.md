# ecommerce-realtime-pipeline

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Flink](https://img.shields.io/badge/Apache%20Flink-1.19-E6526F)
![Kafka](https://img.shields.io/badge/Apache%20Kafka-7.6.1-231F20)

A production-grade, fully containerised real-time e-commerce event pipeline that ingests raw clickstream events, validates and deduplicates them in Apache Flink with exactly-once semantics, fans the clean stream out to three independent analytical sinks (Apache Druid for sub-second dashboards, ClickHouse for ad-hoc historical SQL, and Apache Iceberg on MinIO for lakehouse analytics), and ties the entire system together with Airflow-orchestrated maintenance DAGs, Schema Registry-enforced Avro contracts, and end-to-end observability via the ELK stack.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                          ECOMMERCE REAL-TIME PIPELINE                               │
└─────────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐   Avro/Schema    ┌──────────────────────────────────────────────┐
  │Microservices│   Registry       │            KAFKA CLUSTER (2 brokers)         │
  │  / Load Gen │ ─────────────►  │  Topic: ecommerce.events.raw.v1              │
  └─────────────┘                 │  Topic: ecommerce.events.clean.v1            │
                                  │  Topic: dlq.events                           │
                                  └──────────────┬───────────────────────────────┘
                                                 │
                                    ┌────────────▼────────────┐
                                    │    APACHE FLINK 1.19    │
                                    │  ┌─────────────────────┐│
                                    │  │ ValidateAndClean op ││
                                    │  │ DedupFunction (2h)  ││
                                    │  │ RocksDB state bknd  ││
                                    │  │ Exactly-once chkpts ││
                                    │  └─────────────────────┘│
                                    │   1 JobManager + 2 TMs  │
                                    └────────────┬────────────┘
                                                 │ ecommerce.events.clean.v1
                          ┌──────────────────────┼──────────────────────────┐
                          │                      │                          │
               ┌──────────▼──────┐   ┌───────────▼──────────┐  ┌──────────▼──────────┐
               │  APACHE DRUID   │   │    CLICKHOUSE 24.3   │  │   KAFKA CONNECT     │
               │  (real-time)    │   │  (Kafka Engine table)│  │ + Tabular Iceberg   │
               │  Rollup at      │   │  Materialized Views  │  │   connector         │
               │  ingestion      │   │  Ad-hoc SQL          │  └──────────┬──────────┘
               │  Sub-sec query  │   └──────────────────────┘             │
               └─────────────────┘                                        │
                                                                ┌─────────▼──────────┐
                                                                │   APACHE ICEBERG   │
                                                                │ (REST catalog)     │
                                                                │   + MINIO (S3)     │
                                                                │ Lakehouse / GDPR   │
                                                                └────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │                         OBSERVABILITY & ORCHESTRATION                            │
  │                                                                                  │
  │  ┌──────────────────────┐          ┌───────────────────────────────────────┐    │
  │  │      ELK STACK       │          │         APACHE AIRFLOW 2.9            │    │
  │  │  Elasticsearch 8.17  │          │  flink_savepoint_rotation.py          │    │
  │  │  Logstash            │          │  iceberg_compaction.py                │    │
  │  │  Kibana              │          │  gdpr_right_to_forget.py              │    │
  │  │  Metricbeat          │          │  dq_great_expectations.py             │    │
  │  └──────────────────────┘          └───────────────────────────────────────┘    │
  └──────────────────────────────────────────────────────────────────────────────────┘
```

---

## Stack

| Component | Version | Role | Port(s) |
|---|---|---|---|
| Apache Kafka (Confluent) | 7.6.1 | Event backbone — raw and clean topics, DLQ | 9092 (internal) |
| Confluent Schema Registry | 7.6.1 | Avro schema versioning, FULL_TRANSITIVE compatibility | 8081 |
| Apache ZooKeeper | bundled | Kafka cluster coordination | 2181 |
| Kafka UI | latest | Browser-based Kafka topic / consumer-group inspection | 8085 |
| Apache Flink | 1.19 | Stream ETL: validate, dedup, route to clean topic | 8082 (UI) |
| Apache Druid | 36 | Real-time OLAP — rollup at ingestion, sub-second dashboards | 8888 |
| ClickHouse | 24.3 | Historical OLAP — full-row storage, ad-hoc SQL | 8123 (HTTP), 8124 (UI) |
| Apache Iceberg REST | 1.x | Lakehouse table catalog (vendor-neutral REST spec) | 8181 |
| MinIO | latest | S3-compatible object store for Iceberg data files | 9001 (console), 9002 (S3) |
| Kafka Connect | bundled | Sink connector framework — Iceberg sink via Tabular plugin | 8083 |
| Elasticsearch | 8.17 | Log and metric storage | 9200 |
| Kibana | 8.17 | Log visualisation and dashboards | 5601 |
| Apache Airflow | 2.9 | Orchestration — savepoints, compaction, GDPR, DQ | 8080 |

---

## Quick Start

### Prerequisites

Ensure the following are installed and available on your `PATH`:

```bash
docker --version        # Docker 24+
docker compose version  # Compose v2.x
java --version          # Java 17 (for local Flink builds)
mvn --version           # Maven 3.8+
python --version        # Python 3.10+
curl --version
```

Minimum host resources: **16 GB RAM**, **4 CPU cores**, **40 GB free disk**.

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/your-org/ecommerce-realtime-pipeline.git
cd ecommerce-realtime-pipeline
```

---

### Step 2 — Start all services

```bash
docker compose -f docker/docker-compose.yml up -d
```

Wait approximately 90 seconds for all services to reach a healthy state, then verify:

```bash
docker compose -f docker/docker-compose.yml ps
```

All services should show `Up` or `healthy`. Open the Flink UI at http://localhost:8082 to confirm the cluster is ready (1 JobManager, 2 TaskManagers).

---

### Step 3 — Register the Avro schema

```bash
bash scripts/register-schema.sh
```

This registers `schemas/clickstream-event.avsc` with Schema Registry at http://localhost:8081 under subject `ecommerce.events.raw.v1-value` using `FULL_TRANSITIVE` compatibility.

Verify:

```bash
curl -s http://localhost:8081/subjects | python -m json.tool
```

---

### Step 4 — Build and submit the Flink job

```bash
# Build the fat JAR
cd flink-app
mvn clean package -DskipTests
cd ..

# Upload the JAR to the Flink REST API
curl -X POST http://localhost:8082/jars/upload \
  -H "Expect:" \
  -F "jarfile=@flink-app/target/ecom-streaming-etl.jar"

# Get the jar ID and run the job
JAR_ID=$(curl -s http://localhost:8082/jars \
  | python -c "import sys,json; print(json.load(sys.stdin)['files'][0]['id'])")

curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"programArgs": ""}'
```

Confirm the job is RUNNING in the Flink UI at http://localhost:8082.

---

### Step 5 — Submit the Druid supervisor

```bash
bash scripts/submit-druid-supervisor.sh
```

This posts the Druid ingestion spec to the Druid Overlord API. Verify at http://localhost:8888 under Supervisors.

---

### Step 6 — Apply ClickHouse DDL

```bash
# Apply Kafka Engine table definition
curl -s "http://localhost:8123/" \
  --data-binary @clickhouse/02_kafka_engine.sql

# Apply materialized views
curl -s "http://localhost:8123/" \
  --data-binary @clickhouse/03_materialized_views.sql
```

Or connect interactively via the CLI:

```bash
docker exec -it clickhouse clickhouse-client
```

---

### Step 7 — Deploy the Kafka Connect Iceberg sink

```bash
bash scripts/submit-iceberg-connector.sh
```

Verify connector status:

```bash
curl -s http://localhost:8083/connectors/iceberg-sink/status | python -m json.tool
```

The `state` field should read `RUNNING`.

---

### Step 8 — Start the event generator

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run the built-in load generator (1000 events/sec by default)
python src/event_generator.py --rate 1000
```

---

### Step 9 — Verify end-to-end flow

| Check | Command / URL |
|---|---|
| Raw topic receiving events | http://localhost:8085 — topic `ecommerce.events.raw.v1` |
| Flink job processing | http://localhost:8082 — Running Jobs — Records Out |
| Clean topic populated | http://localhost:8085 — topic `ecommerce.events.clean.v1` |
| Druid data visible | http://localhost:8888 — Query — `SELECT COUNT(*) FROM "ecommerce-events"` |
| ClickHouse data visible | `curl "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"` |
| Iceberg files in MinIO | http://localhost:9001 (minio / minio123) — warehouse bucket |
| Kibana logs flowing | http://localhost:5601 — Discover |

---

## Project Structure

```
ecommerce-realtime-pipeline/
├── docker/
│   ├── docker-compose.yml            # Full production-like stack (all services)
│   └── docker-compose-phase1.yml     # Minimal dev stack (Kafka + Flink only)
│
├── flink-app/                        # Apache Flink ETL job (Java 17 / Maven)
│   ├── pom.xml
│   ├── ecom-streaming-etl.jar        # Pre-built fat JAR for quick start
│   └── src/
│       ├── main/
│       │   ├── avro/
│       │   │   └── clickstream-event.avsc
│       │   ├── java/com/ecom/
│       │   │   ├── etl/
│       │   │   │   ├── EcomEtlJob.java       # Main job entry point
│       │   │   │   └── JobConfig.java        # Environment & parameter parsing
│       │   │   ├── functions/
│       │   │   │   ├── DedupFunction.java    # RocksDB-backed 2-hour dedup
│       │   │   │   └── ValidateAndClean.java # Field validation, null handling
│       │   │   └── operators/
│       │   │       ├── DedupFunction.java    # Operator wrapper
│       │   │       └── ValidateAndClean.java # Operator wrapper
│       │   └── resources/
│       │       └── log4j2.properties
│       └── test/
│           └── java/com/ecom/functions/
│               ├── DedupFunctionTest.java
│               └── ValidateAndCleanTest.java
│
├── schemas/
│   └── clickstream-event.avsc        # Canonical Avro schema
│
├── clickhouse/
│   ├── 02_kafka_engine.sql           # Kafka Engine table (raw consumer)
│   ├── 03_materialized_views.sql     # AggregatingMergeTree MVs
│   ├── config/                       # ClickHouse server config overrides
│   └── init/                        # Init SQL scripts
│
├── airflow/
│   ├── dags/
│   │   ├── flink_savepoint_rotation.py   # Hourly savepoint + old cleanup
│   │   ├── iceberg_compaction.py         # Daily small-file compaction
│   │   ├── gdpr_right_to_forget.py       # GDPR equality-delete DAG
│   │   └── dq_great_expectations.py      # Daily data quality checks
│   ├── config/
│   └── plugins/
│
├── kafka-connect/
│   └── iceberg-sink.json             # Iceberg sink connector config
│
├── druid/
│   └── sample-queries.md             # Druid SQL examples
│
├── elk/                              # ELK stack configs (Logstash pipelines, ILM)
│
├── scripts/
│   ├── register-schema.sh            # Register Avro schema with Schema Registry
│   ├── submit-druid-supervisor.sh    # POST Druid ingestion spec
│   ├── submit-iceberg-connector.sh   # POST Kafka Connect connector config
│   └── init-airflow.sh              # Bootstrap Airflow DB + admin user
│
├── tests/
│   └── performance/
│       ├── benchmark.py              # End-to-end throughput/latency benchmark
│       ├── analyze_results.py        # Result visualisation
│       └── results/                  # Benchmark output artefacts
│
├── src/
│   └── event_generator.py            # Synthetic event producer
│
├── requirements.txt
├── README.md                         # This file
├── SETUP.md                          # Detailed environment setup guide
└── docs/
    ├── OPERATIONS.md                 # Runbooks, scaling, troubleshooting
    ├── DEVELOPMENT.md                # Developer guide, conventions, testing
    └── ARCHITECTURE_DECISIONS.md    # Formal ADRs for key design choices
```

---

## Component Descriptions

| Component | Description |
|---|---|
| **Flink ETL Job** | Consumes raw Avro events from Kafka, runs field validation via `ValidateAndClean`, deduplicates within a 2-hour RocksDB-backed window via `DedupFunction`, routes invalid events to `dlq.events`, and publishes clean events to `ecommerce.events.clean.v1` with exactly-once Kafka sink semantics. |
| **Schema Registry** | Central Avro schema store enforcing `FULL_TRANSITIVE` compatibility. All producers and consumers reference schemas by ID, ensuring no breaking schema change is ever deployed undetected. |
| **Apache Druid** | Ingests the clean Kafka topic via a native Kafka supervisor. Applies rollup at ingestion time (count + sum metrics per dimension combination) enabling sub-second aggregation queries over recent data for real-time dashboards. |
| **ClickHouse** | Ingests via a Kafka Engine table that acts as a continuous consumer. Materialized views (`AggregatingMergeTree`) pre-aggregate common query patterns while the raw table preserves full row fidelity for flexible ad-hoc SQL. |
| **Iceberg + MinIO** | Kafka Connect with the Tabular Iceberg sink writes clean events as Parquet files to MinIO (S3-compatible). The Iceberg REST catalog tracks table metadata enabling schema evolution, hidden partitioning, and GDPR equality deletes via Airflow DAGs. |
| **ELK Stack** | Elasticsearch stores application logs and Metricbeat system/container metrics. Logstash pipelines parse and enrich log lines. Kibana provides operational dashboards, log search, and alerting rules. |
| **Apache Airflow** | Runs four scheduled maintenance DAGs: savepoint rotation (protects Flink job state), Iceberg compaction (merges small files for query efficiency), GDPR right-to-forget (equality deletes on user_id), and Great Expectations data quality checks. |

---

## Key Design Principles

| # | Principle | Rationale |
|---|---|---|
| 1 | **Schema-first, contract-enforced** — every event must conform to the registered Avro schema before entering the pipeline. | Prevents malformed data from propagating to sinks and causing cascading failures downstream. |
| 2 | **Exactly-once end-to-end** — Flink checkpoints + Kafka transactions ensure no event is double-counted or silently dropped. | Financial and inventory metrics require accuracy; silent data loss or duplication leads to incorrect business decisions. |
| 3 | **Fan-out via independent consumer groups** — each sink reads the clean topic independently, enabling per-sink replay without reprocessing. | Decouples sink failures from each other; any sink can be replayed by resetting its consumer group offset without affecting others. |
| 4 | **Flink does zero aggregation** — all summarisation happens at query-time (ClickHouse) or at ingestion (Druid rollup). | Aggregation logic is tightly coupled to query patterns; keeping it in OLAP engines makes it independently evolvable without redeploying Flink. |
| 5 | **Stateless sinks, stateful only where necessary** — only Flink's dedup window carries state; all sinks are restartable from Kafka offsets. | Minimises blast radius of failures; any sink can be torn down and rebuilt from the Kafka retention window without data loss. |

---

## Performance Targets

| Metric | Target |
|---|---|
| Sustained throughput | 80,000 events / second |
| End-to-end latency p50 | < 200 ms (raw ingest to ClickHouse queryable) |
| End-to-end latency p95 | < 500 ms |
| End-to-end latency p99 | < 1,000 ms |
| Flink checkpoint duration | < 30 seconds |
| Flink checkpoint interval | 60 seconds |
| Kafka consumer lag (steady state) | < 5,000 messages per partition |
| Druid query latency (recent 1 hour) | < 100 ms |
| ClickHouse ad-hoc query (1 day range) | < 2 seconds |

Run the benchmark suite to validate against these targets:

```bash
python tests/performance/benchmark.py --quick
python tests/performance/analyze_results.py
```

---

## Documentation

| Document | Description |
|---|---|
| [SETUP.md](SETUP.md) | Detailed step-by-step environment setup, including port-conflict resolution, resource tuning, and Windows/WSL notes |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Operational runbooks: health checks, scaling, restart procedures, replay runbooks, backup, and troubleshooting |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer guide: building the Flink job, adding event types, writing DAGs, ClickHouse MVs, code conventions |
| [docs/ARCHITECTURE_DECISIONS.md](docs/ARCHITECTURE_DECISIONS.md) | Formal Architecture Decision Records (ADRs) for all major technology and design choices |

---

## Contributing

1. Fork the repository and create a feature branch: `git checkout -b feat/your-feature`
2. Follow the code conventions described in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
3. Add or update tests. The Flink unit tests must pass: `cd flink-app && mvn test`
4. Commit using the Conventional Commits format: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`
5. Open a pull request against `main`. Describe the motivation, what changed, and how you tested it.
6. All CI checks must pass before merge.

For bug reports and feature requests, open a GitHub Issue with the appropriate label (`bug`, `enhancement`, `question`).

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

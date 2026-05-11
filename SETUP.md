# Setup Guide — Real-Time E-Commerce Analytics Pipeline

End-to-end instructions to get the full stack running on a local development machine.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Repository Layout](#3-repository-layout)
4. [Quick Start (Full Stack)](#4-quick-start-full-stack)
5. [Phase 1 — Foundation (Kafka + Flink)](#5-phase-1--foundation-kafka--flink)
6. [Phase 2 — Schema Registry & Event Generator](#6-phase-2--schema-registry--event-generator)
7. [Phase 3 — Druid Real-Time OLAP](#7-phase-3--druid-real-time-olap)
8. [Phase 4 — ClickHouse Historical OLAP](#8-phase-4--clickhouse-historical-olap)
9. [Phase 5 — Iceberg Lakehouse](#9-phase-5--iceberg-lakehouse)
10. [Phase 6 — ELK Observability](#10-phase-6--elk-observability)
11. [Phase 7 — Airflow Orchestration](#11-phase-7--airflow-orchestration)
12. [Service URLs & Ports](#12-service-urls--ports)
13. [Credentials Reference](#13-credentials-reference)
14. [Common Operations](#14-common-operations)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Architecture Overview

```
                     ┌─────────────────────────────────────────────────┐
                     │              Event Producers                     │
                     │   (web / mobile / order / payment services)     │
                     └──────────────────────┬──────────────────────────┘
                                            │  Avro (Confluent Schema Registry)
                                            ▼
                     ┌─────────────────────────────────────────────────┐
                     │          Kafka  (2 brokers, ZooKeeper)          │
                     │   ecommerce.events.raw.v1   (12 partitions)     │
                     └──────────────────────┬──────────────────────────┘
                                            │
                                            ▼
                     ┌─────────────────────────────────────────────────┐
                     │              Apache Flink 1.19                  │
                     │   ValidateAndClean → DedupFunction → Sink       │
                     │   ecommerce.events.clean.v1   (hub topic)       │
                     └───────┬──────────────┬──────────────┬───────────┘
                             │              │              │
               ┌─────────────┘   ┌──────────┘   ┌─────────┘
               ▼                 ▼              ▼
        ┌────────────┐   ┌─────────────┐  ┌──────────────────┐
        │   Druid 36 │   │ClickHouse 24│  │  Kafka Connect   │
        │  real-time │   │ historical  │  │  Iceberg Sink    │
        │    OLAP    │   │    OLAP     │  │  MinIO / S3      │
        └────────────┘   └─────────────┘  └──────────────────┘
               │                 │              │
               └────────┬────────┘              │
                        ▼                       ▼
              ┌──────────────────┐   ┌────────────────────┐
              │  Kibana / Airflow│   │  Iceberg REST Cat  │
              │  ELK Observability   │  warehouse.silver  │
              └──────────────────┘   └────────────────────┘
```

**Stack versions**

| Component | Version |
|-----------|---------|
| Apache Kafka (Confluent) | 7.6.1 |
| Apache Flink | 1.19 (Java 17) |
| Apache Druid | 36.0.0 |
| ClickHouse | 24.3 |
| Apache Iceberg | via tabulario/iceberg-rest:latest |
| MinIO | latest |
| Kafka Connect | 7.6.1 + tabular/iceberg-kafka-connect 0.6.19 |
| ELK Stack | 8.17.0 |
| Apache Airflow | 2.9.3 |

---

## 2. Prerequisites

### Required software

| Tool | Minimum version | Install |
|------|----------------|---------|
| Docker Desktop | 24.x | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| Docker Compose v2 | 2.20+ | bundled with Docker Desktop |
| Java (JDK) | 17 | for building the Flink JAR locally |
| Maven | 3.8+ | for building the Flink JAR locally |
| Python | 3.10+ | for the event generator and DAG scripts |

### System resources

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 12 GB free | 16 GB free |
| CPU cores | 4 | 8 |
| Disk (SSD) | 20 GB free | 40 GB free |

> **Windows users:** Enable WSL 2 backend in Docker Desktop settings.  
> **macOS Apple Silicon:** all images include `platform: linux/amd64` where needed.

### Increase Docker memory limit

Docker Desktop → Settings → Resources → Memory: set to at least **12 GB**.

### Verify installation

```bash
docker --version          # Docker version 24.x or later
docker compose version    # Docker Compose version v2.x
java -version             # openjdk 17.x
mvn -version              # Apache Maven 3.8.x
python --version          # Python 3.10.x
```

---

## 3. Repository Layout

```
ecommerce-realtime-pipeline/
├── airflow/
│   ├── dags/                         # Airflow DAG Python files
│   │   ├── iceberg_compaction.py
│   │   ├── dq_great_expectations.py
│   │   ├── gdpr_right_to_forget.py
│   │   └── flink_savepoint_rotation.py
│   ├── plugins/                      # Custom operators (add here)
│   └── config/                       # airflow.cfg overrides (optional)
├── clickhouse/
│   ├── config/
│   │   ├── config.xml                # Server settings (memory, threads, Kafka)
│   │   └── users.xml                 # Users, profiles, quotas
│   ├── init/
│   │   └── 001_create_events_local.sql
│   ├── 02_kafka_engine.sql           # Kafka Engine + Materialized Views
│   └── 03_materialized_views.sql     # KPI + funnel aggregation MVs
├── docker/
│   ├── elk/
│   │   └── logstash/pipeline/        # 4 Logstash pipeline configs
│   │       ├── 10-flink.conf
│   │       ├── 11-kafka.conf
│   │       ├── 12-clickhouse.conf
│   │       └── 20-metrics.conf
│   ├── kafka-connect/
│   │   └── install-connectors.sh     # Downloads Iceberg plugin on startup
│   ├── docker-compose.yml            # Full stack (all phases)
│   └── docker-compose-phase1.yml     # Foundation only (Kafka + Flink)
├── druid/
│   ├── ingestion-spec.json           # Kafka supervisor spec
│   └── sample-queries.md            # 10 reference SQL queries
├── elk/
│   ├── filebeat.yml
│   ├── metricbeat.yml
│   └── heartbeat.yml
├── flink-app/
│   ├── src/main/java/com/ecom/      # Flink ETL source code
│   ├── pom.xml
│   └── ecom-streaming-etl.jar       # Pre-built JAR (if present)
├── kafka-connect/
│   └── iceberg-sink.json            # Iceberg sink connector config
├── schemas/
│   └── clickstream-event.avsc       # Avro schema (source of truth)
├── scripts/
│   ├── register-schema.sh
│   ├── submit-druid-supervisor.sh
│   ├── submit-iceberg-connector.sh
│   └── init-airflow.sh
├── src/utils/
│   └── event_generator.py           # Synthetic event producer
├── requirements.txt
├── README.md
└── SETUP.md                         # ← you are here
```

---

## 4. Quick Start (Full Stack)

> Start here if you want every service running with a single command.  
> For a step-by-step walkthrough, follow Phases 1–7 below.

```bash
# 1. Clone / enter the repo
cd ecommerce-realtime-pipeline

# 2. Bring up the full stack (first boot downloads ~4 GB of images)
docker compose -f docker/docker-compose.yml up -d

# 3. Watch startup progress (Ctrl+C to exit logs, services keep running)
docker compose -f docker/docker-compose.yml logs -f

# 4. Wait ~3 minutes for all services to become healthy, then verify
docker compose -f docker/docker-compose.yml ps

# 5. Register the Avro schema
./scripts/register-schema.sh

# 6. Start the Flink ETL job
docker compose -f docker/docker-compose.yml exec flink-jobmanager \
  flink run -d /opt/flink/usrlib/ecom-streaming-etl.jar

# 7. Submit the Druid supervisor (real-time ingestion)
./scripts/submit-druid-supervisor.sh

# 8. Bootstrap ClickHouse tables
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/02_kafka_engine.sql
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/03_materialized_views.sql

# 9. Register the Iceberg sink connector
./scripts/submit-iceberg-connector.sh

# 10. Initialise Airflow
./scripts/init-airflow.sh --start

# 11. Start the event generator (generates ~100 events/s by default)
pip install -r requirements.txt
python src/utils/event_generator.py
```

**All UIs should now be accessible** — see [Section 12](#12-service-urls--ports).

---

## 5. Phase 1 — Foundation (Kafka + Flink)

### 5.1 Start the foundation stack

```bash
docker compose -f docker/docker-compose-phase1.yml up -d
```

Services started: ZooKeeper, Kafka (2 brokers), Schema Registry, Flink (1 JM + 2 TMs), Kafka UI.

### 5.2 Verify Kafka is healthy

```bash
# List topics (should be empty at this point)
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:29092 \
  --list
```

### 5.3 Build the Flink ETL job

```bash
cd flink-app
mvn clean package -DskipTests
# Output: target/ecom-streaming-etl-1.0.0.jar
cd ..
```

### 5.4 Register the Avro schema

```bash
./scripts/register-schema.sh
# Registers schemas/clickstream-event.avsc with the Schema Registry
# Verify: curl -s http://localhost:8081/subjects | jq
```

### 5.5 Submit the Flink job

```bash
docker compose -f docker/docker-compose-phase1.yml exec flink-jobmanager \
  flink run -d \
    --class com.ecom.EcomEtlJob \
    /opt/flink/usrlib/ecom-streaming-etl.jar
```

Flink UI: [http://localhost:8082](http://localhost:8082) — confirm the job shows `RUNNING`.

### 5.6 Send test events

```bash
pip install -r requirements.txt

# Dry run (logs events without sending to Kafka)
python src/utils/event_generator.py --dry-run --events 10

# Send 100 events at 10 events/second
python src/utils/event_generator.py --events 100 --rate 10
```

---

## 6. Phase 2 — Schema Registry & Event Generator

> Covered in Phase 1 above. This section documents the event generator in detail.

### Event generator options

```bash
python src/utils/event_generator.py --help

# Common flags:
#   --events N        Total events to generate (0 = unlimited)
#   --rate N          Events per second (default: 10)
#   --sessions N      Concurrent user sessions (default: 50)
#   --dry-run         Print events to stdout, do not send to Kafka
#   --topic NAME      Override the target topic
```

### Verify events are flowing

```bash
# Kafka UI → Topics → ecommerce.events.raw.v1 → Messages
open http://localhost:8085

# Or via CLI:
docker exec kafka-1 kafka-console-consumer \
  --bootstrap-server kafka-1:29092 \
  --topic ecommerce.events.raw.v1 \
  --from-beginning \
  --max-messages 5
```

---

## 7. Phase 3 — Druid Real-Time OLAP

### 7.1 Start Druid services

```bash
# If not already running (Druid is included in the full stack)
docker compose -f docker/docker-compose.yml up -d \
  druid-postgres zookeeper druid-coordinator druid-broker \
  druid-historical druid-middlemanager druid-router

# Wait for the router to be healthy
docker compose -f docker/docker-compose.yml ps druid-router
```

Druid Console: [http://localhost:8888](http://localhost:8888)

### 7.2 Submit the Kafka supervisor

```bash
./scripts/submit-druid-supervisor.sh

# Verify supervisor is RUNNING:
curl -s http://localhost:8888/druid/indexer/v1/supervisor | jq '.[].id'
```

### 7.3 Run sample queries

Connect via the Druid SQL console ([http://localhost:8888/unified-console.html](http://localhost:8888/unified-console.html)) and run queries from `druid/sample-queries.md`.

```bash
# Quick validation: total event count
curl -s -X POST http://localhost:8888/druid/v2/sql \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT COUNT(*) FROM ecommerce_events"}' | jq
```

---

## 8. Phase 4 — ClickHouse Historical OLAP

### 8.1 Start ClickHouse

```bash
docker compose -f docker/docker-compose.yml up -d clickhouse clickhouse-ui
```

HTTP Play UI: [http://localhost:8123/play](http://localhost:8123/play)  
Management UI: [http://localhost:8124](http://localhost:8124)

### 8.2 Create the storage table

```bash
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/init/001_create_events_local.sql

# Verify:
echo "SHOW TABLES IN ecom" | docker exec -i clickhouse clickhouse-client
```

### 8.3 Create the Kafka Engine pipeline

```bash
# Creates: events_kafka, kafka_errors, kafka_errors_mv, events_mv
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/02_kafka_engine.sql

# Watch row count grow (run every ~10 s):
watch -n 10 'echo "SELECT count(), max(event_time) FROM ecom.events_local" | \
  docker exec -i clickhouse clickhouse-client'
```

### 8.4 Create the aggregation materialized views

```bash
# Creates: kpi_1min, kpi_1min_mv, funnel_5min, funnel_5min_mv
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/03_materialized_views.sql
```

### 8.5 Verify aggregations (after ~1 minute of data)

```bash
echo "
SELECT
  window_start,
  event_type,
  countMerge(event_count) AS events,
  round(sumMerge(revenue), 2) AS revenue
FROM ecom.kpi_1min
WHERE window_start >= now() - INTERVAL 10 MINUTE
GROUP BY window_start, event_type
ORDER BY window_start DESC
LIMIT 20
" | docker exec -i clickhouse clickhouse-client
```

### 8.6 Interactive ClickHouse client

```bash
# Ephemeral client container (tools profile)
docker compose -f docker/docker-compose.yml \
  --profile tools run --rm clickhouse-client

# Or exec into the running server:
docker exec -it clickhouse clickhouse-client
```

---

## 9. Phase 5 — Iceberg Lakehouse

### 9.1 Start MinIO + Iceberg REST Catalog

```bash
docker compose -f docker/docker-compose.yml up -d \
  minio minio-setup iceberg-rest
```

MinIO Console: [http://localhost:9001](http://localhost:9001) — login: `minio / minio123`  
Iceberg REST: [http://localhost:8181/v1/config](http://localhost:8181/v1/config)

### 9.2 Verify MinIO buckets

```bash
# Bucket list (should show: warehouse, flink-ckpt, flink-savepoints)
docker exec minio-setup mc ls local/

# Or via AWS CLI (from host):
AWS_ACCESS_KEY_ID=minio AWS_SECRET_ACCESS_KEY=minio123 \
  aws --endpoint-url http://localhost:9002 s3 ls
```

### 9.3 Start Kafka Connect with the Iceberg plugin

> First boot downloads ~150 MB of JARs. Allow 3–5 minutes.

```bash
docker compose -f docker/docker-compose.yml up -d kafka-connect

# Watch the install progress:
docker logs -f kafka-connect

# Wait for "Handing off to Kafka Connect worker..." then verify plugin:
curl -s http://localhost:8083/connector-plugins | \
  jq '.[].class' | grep -i iceberg
```

### 9.4 Register the Iceberg sink connector

```bash
# Dry run first (no changes):
./scripts/submit-iceberg-connector.sh --dry-run

# Submit:
./scripts/submit-iceberg-connector.sh

# Check status:
curl -s http://localhost:8083/connectors/iceberg-sink-events/status | jq
```

### 9.5 Verify data in MinIO

```bash
# After a few minutes, Iceberg data files appear here:
AWS_ACCESS_KEY_ID=minio AWS_SECRET_ACCESS_KEY=minio123 \
  aws --endpoint-url http://localhost:9002 \
  s3 ls s3://warehouse/warehouse/silver/ --recursive
```

---

## 10. Phase 6 — ELK Observability

### 10.1 Start the ELK stack

```bash
docker compose -f docker/docker-compose.yml up -d \
  elasticsearch kibana logstash filebeat metricbeat heartbeat

# Elasticsearch takes ~60 s; Kibana takes ~120 s on first boot
docker compose -f docker/docker-compose.yml ps elasticsearch kibana
```

Kibana: [http://localhost:5601](http://localhost:5601)  
Elasticsearch: [http://localhost:9200](http://localhost:9200)

### 10.2 Verify Elasticsearch is healthy

```bash
curl -s http://localhost:9200/_cluster/health?pretty
# "status": "yellow" or "green" — either is fine for single-node dev
```

### 10.3 Verify Logstash is receiving Beats

```bash
# Logstash stats (events in/out):
curl -s http://localhost:9600/_node/stats | \
  jq '.pipelines.main.events'

# Indices created by Filebeat:
curl -s http://localhost:9200/_cat/indices/logs-*?v
```

### 10.4 Check Heartbeat monitors in Kibana

Navigate to: Kibana → Observability → Uptime

All pipeline services (Flink, Kafka, ClickHouse, Druid, MinIO, Airflow, Iceberg REST) are monitored by Heartbeat with 30-second intervals.

---

## 11. Phase 7 — Airflow Orchestration

### 11.1 Initialise and start Airflow

```bash
# Creates host directories, migrates DB, creates admin user, starts services
./scripts/init-airflow.sh --start

# Or manually:
docker compose -f docker/docker-compose.yml up -d \
  airflow-postgres airflow-init

# Wait for init to complete:
docker compose -f docker/docker-compose.yml logs airflow-init

# Then start the web server and scheduler:
docker compose -f docker/docker-compose.yml up -d \
  airflow-webserver airflow-scheduler
```

Airflow UI: [http://localhost:8080](http://localhost:8080) — login: `admin / admin`

### 11.2 Verify DAGs are loaded

```bash
# Via REST API:
curl -s http://localhost:8080/api/v1/dags \
  -u admin:admin | jq '.dags[].dag_id'

# Expected DAGs:
# "iceberg_compaction"
# "dq_great_expectations"
# "gdpr_right_to_forget"
# "flink_savepoint_rotation"
```

### 11.3 Run DAGs manually

```bash
# Trigger from CLI:
docker exec airflow-scheduler \
  airflow dags trigger iceberg_compaction

# Or unpause and let the scheduler run on schedule:
docker exec airflow-scheduler \
  airflow dags unpause dq_great_expectations
```

### 11.4 Run DAG scripts directly (without Airflow)

```bash
# Install Python dependencies
pip install "pyiceberg[s3fsspec]>=0.7" great_expectations boto3 requests

# Iceberg compaction (dry run)
ICEBERG_REST_URL=http://localhost:8181 \
MINIO_ENDPOINT=http://localhost:9002 \
  python airflow/dags/iceberg_compaction.py --dry-run

# Data quality check
ICEBERG_REST_URL=http://localhost:8181 \
MINIO_ENDPOINT=http://localhost:9002 \
  python airflow/dags/dq_great_expectations.py

# GDPR erasure (dry run)
ICEBERG_REST_URL=http://localhost:8181 \
MINIO_ENDPOINT=http://localhost:9002 \
CLICKHOUSE_URL=http://localhost:8123 \
DRUID_REST_URL=http://localhost:8888 \
  python airflow/dags/gdpr_right_to_forget.py \
    --user-id u-test123 --dry-run

# Flink savepoint rotation (list only)
FLINK_REST_URL=http://localhost:8082 \
MINIO_ENDPOINT=http://localhost:9002 \
  python airflow/dags/flink_savepoint_rotation.py --task list
```

---

## 12. Service URLs & Ports

### Management UIs

| Service | URL | Credentials |
|---------|-----|-------------|
| **Airflow** | http://localhost:8080 | admin / admin |
| **Kafka UI** | http://localhost:8085 | — |
| **Flink Job Manager** | http://localhost:8082 | — |
| **Druid Console** | http://localhost:8888 | — |
| **ClickHouse Play** | http://localhost:8123/play | default / (none) |
| **ClickHouse UI** | http://localhost:8124 | default / (none) |
| **MinIO Console** | http://localhost:9001 | minio / minio123 |
| **Kibana** | http://localhost:5601 | — |
| **Schema Registry UI** | http://localhost:8086 | — |

### REST APIs (for scripts and external tools)

| Service | URL | Notes |
|---------|-----|-------|
| Schema Registry | http://localhost:8081 | `GET /subjects` |
| Kafka Connect | http://localhost:8083 | `GET /connector-plugins` |
| Iceberg REST Catalog | http://localhost:8181 | `GET /v1/config` |
| Druid SQL | http://localhost:8888/druid/v2/sql | POST JSON |
| ClickHouse HTTP | http://localhost:8123 | POST raw SQL |
| Elasticsearch | http://localhost:9200 | `GET /_cluster/health` |
| Logstash Monitoring | http://localhost:9600 | `GET /_node/stats` |
| Flink REST | http://localhost:8082 | `GET /jobs/overview` |
| Airflow REST | http://localhost:8080/api/v1 | basic auth |

### Internal Docker network hostnames (container-to-container)

| Service | Internal hostname:port |
|---------|----------------------|
| Kafka brokers | `kafka-1:29092`, `kafka-2:29093` |
| Schema Registry | `schema-registry:8081` |
| Kafka Connect | `kafka-connect:8083` |
| Flink Job Manager | `flink-jobmanager:8081` |
| Druid Router | `druid-router:8888` |
| ClickHouse | `clickhouse:8123` (HTTP), `clickhouse:9000` (native) |
| MinIO | `minio:9000` |
| Iceberg REST | `iceberg-rest:8181` |
| Elasticsearch | `elasticsearch:9200` |
| Kibana | `kibana:5601` |
| Airflow Webserver | `airflow-webserver:8080` |

---

## 13. Credentials Reference

| Service | Username | Password | Notes |
|---------|----------|----------|-------|
| MinIO | `minio` | `minio123` | root user |
| ClickHouse | `default` | (none) | no password in dev |
| Airflow | `admin` | `admin` | web UI + REST API |
| Druid PostgreSQL | `druid` | `druid` | metadata DB |
| Airflow PostgreSQL | `airflow` | `airflow` | metadata DB |
| Flink S3 (MinIO) | `minio` | `minio123` | checkpoint storage |
| Iceberg S3 (MinIO) | `minio` | `minio123` | warehouse bucket |

---

## 14. Common Operations

### Stop all services (preserve data volumes)

```bash
docker compose -f docker/docker-compose.yml stop
```

### Stop and remove containers (preserve named volumes)

```bash
docker compose -f docker/docker-compose.yml down
```

### Wipe everything including volumes (full reset)

```bash
docker compose -f docker/docker-compose.yml down -v
```

### Check container health

```bash
docker compose -f docker/docker-compose.yml ps
```

### Tail logs for a specific service

```bash
docker compose -f docker/docker-compose.yml logs -f kafka-1
docker compose -f docker/docker-compose.yml logs -f flink-jobmanager
docker compose -f docker/docker-compose.yml logs -f clickhouse
```

### Restart a single service

```bash
docker compose -f docker/docker-compose.yml restart kafka-connect
```

### Submit a Flink savepoint manually

```bash
# Get the running job ID:
curl -s http://localhost:8082/jobs/overview | jq '.jobs[] | select(.state=="RUNNING") | .jid'

# Trigger savepoint:
curl -s -X POST http://localhost:8082/jobs/<JOB_ID>/savepoints \
  -H "Content-Type: application/json" \
  -d '{"target-directory": "s3://flink-savepoints/ecom/manual/", "cancel-job": false}'
```

### Check Kafka consumer group lag

```bash
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:29092 \
  --describe \
  --group clickhouse-ecom-events
```

### Query ClickHouse from host

```bash
# Via HTTP API:
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events_local"

# Via interactive client:
docker exec -it clickhouse clickhouse-client
```

### Replay Kafka topic from the beginning

```bash
# Reset the ClickHouse consumer group offset to the beginning:
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:29092 \
  --group clickhouse-ecom-events \
  --topic ecommerce.events.clean.v1 \
  --reset-offsets \
  --to-earliest \
  --execute
```

---

## 15. Troubleshooting

### Docker compose file not found

```bash
# Always run compose commands from the repo root, specifying the file path:
docker compose -f docker/docker-compose.yml <command>
# Not: docker compose up  (which looks for docker-compose.yml in the current dir)
```

### Services stuck in "starting" or "unhealthy"

```bash
# Check logs for the failing service:
docker compose -f docker/docker-compose.yml logs <service-name>

# Common cause: not enough RAM — check Docker Desktop memory limit (needs 12 GB+)
docker stats --no-stream
```

### Elasticsearch fails to start — `max virtual memory areas`

On Linux hosts only:

```bash
# Temporary fix (resets on reboot):
sudo sysctl -w vm.max_map_count=262144

# Permanent fix:
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

### ClickHouse `02_kafka_engine.sql` fails — `No connection to Kafka`

Kafka must be healthy before applying the Kafka Engine DDL.

```bash
# Check Kafka health:
docker compose -f docker/docker-compose.yml ps kafka-1 kafka-2

# Retry after Kafka is healthy:
docker exec -i clickhouse clickhouse-client --multiquery \
  < clickhouse/02_kafka_engine.sql
```

### Kafka Connect `iceberg-sink-events` connector FAILED

```bash
# Inspect task error trace:
curl -s http://localhost:8083/connectors/iceberg-sink-events/status | jq '.tasks[].trace'

# Most common cause: Iceberg REST or MinIO not reachable
curl -sf http://localhost:8181/v1/config  # should return 200
curl -sf http://localhost:9001/minio/health/live  # should return 200
```

### Airflow scheduler shows no DAGs

The DAGs directory must be mounted and the scheduler must process it.

```bash
# Check DAG parse errors:
docker exec airflow-scheduler airflow dags list-import-errors

# Force DAG refresh:
docker exec airflow-scheduler airflow dags reserialize
```

### Flink job exits immediately — `ClassNotFoundException`

The JAR must be present in the `flink-app/` directory:

```bash
ls flink-app/ecom-streaming-etl.jar   # pre-built JAR

# Or rebuild:
cd flink-app && mvn clean package -DskipTests && cd ..
```

### MinIO port conflict — `9000 already in use`

Port 9000 on the host is reserved by ClickHouse native protocol.  
MinIO's S3 API is exposed on host port **9002** (not 9000):

```bash
# Correct host-side endpoint for MinIO S3 API:
AWS_ACCESS_KEY_ID=minio AWS_SECRET_ACCESS_KEY=minio123 \
  aws --endpoint-url http://localhost:9002 s3 ls
```

### PyIceberg `rewrite_data_files` not available

```bash
pip install "pyiceberg[s3fsspec]>=0.7"
# Minimum version required for compaction and delete operations
```

---

*For questions or issues, open a GitHub issue or check the `GRAPH_REPORT.md` for an auto-generated architecture audit of the codebase.*

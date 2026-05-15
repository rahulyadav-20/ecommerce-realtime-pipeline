# End-to-End Pipeline Runbook
### E-Commerce Realtime Analytics — `ecommerce-realtime-pipeline`

> **Audience:** On-call engineers, data platform operators  
> **Scope:** Full lifecycle — cold start → steady state → incident → shutdown  
> **Companion docs:** [SETUP.md](SETUP.md) (first-time setup) · [docs/OPERATIONS.md](docs/OPERATIONS.md) (scaling & backups) · [docs/ARCHITECTURE_DECISIONS.md](docs/ARCHITECTURE_DECISIONS.md)

---

## Table of Contents

1. [Pipeline Overview](#1-pipeline-overview)
2. [Prerequisites & Environment](#2-prerequisites--environment)
3. [Cold Start — Full Stack](#3-cold-start--full-stack)
4. [Steady-State Health Checks](#4-steady-state-health-checks)
5. [Sending Events — Event Generator](#5-sending-events--event-generator)
6. [Validating End-to-End Data Flow](#6-validating-end-to-end-data-flow)
7. [Airflow DAGs — Scheduled Maintenance](#7-airflow-dags--scheduled-maintenance)
8. [Incident Response](#8-incident-response)
9. [Replay Procedures](#9-replay-procedures)
10. [Graceful Shutdown](#10-graceful-shutdown)
11. [Quick Reference](#11-quick-reference)

---

## 1. Pipeline Overview

```
Event Producers
     │  (Avro via Schema Registry)
     ▼
Kafka  ──── ecommerce.events.raw.v1 (12 partitions)
     │
     ▼
Apache Flink 1.19
  ├─ ValidateAndClean  (schema enforcement, null checks)
  ├─ DedupFunction     (2-hour RocksDB window, exactly-once)
  └─ KafkaSink ──────► ecommerce.events.clean.v1 (hub topic)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        Apache Druid    ClickHouse 24    Kafka Connect
        (real-time)     (historical)     + Iceberg Sink
        sub-second       Kafka Engine    MinIO / S3
        OLAP + rollup    + Mat. Views    Parquet / REST Cat.

                   ↕ all services ↕
              ELK Stack (logs + metrics + synthetic checks)
              Apache Airflow (savepoints · compaction · GDPR · DQ)
```

**Performance targets:** 80 k events/s sustained · p50 latency < 200 ms · checkpoint < 30 s

---

## 2. Prerequisites & Environment

### Required tools

| Tool | Min version | Check |
|------|-------------|-------|
| Docker Desktop | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Java | 17+ | `java -version` |
| Maven | 3.8+ | `mvn -version` |
| Python | 3.10+ | `python3 --version` |
| `curl` | any | `curl --version` |
| `jq` | 1.6+ | `jq --version` |

**Minimum resources:** 12 GB RAM · 4 CPU cores · 20 GB free disk

### Environment variables

All scripts source `scripts/config.sh` which loads `config/${APP_ENV}.env`.

```bash
# Use Docker-internal hostnames (default for Compose)
export APP_ENV=docker

# Use localhost + exposed ports (for local scripts outside containers)
export APP_ENV=local
```

Key variables and their defaults:

| Variable | Default |
|----------|---------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` |
| `SCHEMA_REGISTRY_URL` | `http://localhost:8081` |
| `FLINK_REST_URL` | `http://localhost:8082` |
| `CLICKHOUSE_URL` | `http://localhost:8123` |
| `DRUID_REST_URL` | `http://localhost:8888` |
| `KAFKA_CONNECT_URL` | `http://localhost:8083` |
| `ICEBERG_REST_URL` | `http://localhost:8181` |
| `MINIO_ENDPOINT` | `http://localhost:9002` |

---

## 3. Cold Start — Full Stack

### Option A — Automated (recommended)

```bash
# Full stack, all 7 phases
./setup.sh

# Partial startup — e.g. Kafka + Flink only (phases 1–2)
./setup.sh --phase 2

# Skip Maven build if JAR already exists
./setup.sh --skip-build
```

`setup.sh` waits for each service to become healthy before moving to the next phase.

---

### Option B — Manual Phase-by-Phase

#### Phase 1 — Foundation (Kafka + Schema Registry)

```bash
docker compose -f docker/docker-compose.yml up -d \
  zookeeper kafka-1 kafka-2 kafka-init \
  schema-registry schema-registry-ui kafka-ui

# Wait for Schema Registry
until curl -sf http://localhost:8081/subjects; do sleep 5; done

# Register Avro schemas
bash scripts/register-schema.sh

# Install Python dependencies
pip3 install -r requirements.txt
```

#### Phase 2 — Flink + MinIO

```bash
# Build fat-JAR
mvn -f flink-app/pom.xml package -DskipTests

docker compose -f docker/docker-compose.yml up -d \
  minio minio-setup flink-jobmanager flink-taskmanager-1 flink-taskmanager-2

# Wait for Flink web UI
until curl -sf http://localhost:8082/overview; do sleep 5; done

# Upload JAR
JAR_PATH=$(ls flink-app/target/ecom-streaming-etl-*.jar | head -1)
JAR_ID=$(curl -s -F "jarfile=@${JAR_PATH}" http://localhost:8082/jars/upload \
  | jq -r '.filename' | xargs basename)

# Submit job
curl -s -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{}' | jq -r '.jobid'
```

#### Phase 3 — Druid (Real-Time OLAP)

```bash
docker compose -f docker/docker-compose.yml up -d \
  druid-postgres druid-coordinator druid-broker \
  druid-historical druid-middlemanager druid-router

until curl -sf http://localhost:8888/status/health; do sleep 10; done
bash scripts/submit-druid-supervisor.sh
```

#### Phase 4 — ClickHouse (Historical OLAP)

```bash
docker compose -f docker/docker-compose.yml up -d clickhouse ch-ui

until curl -sf http://localhost:8123/ping; do sleep 5; done

# Apply DDL (idempotent — uses CREATE IF NOT EXISTS)
CH="docker exec -i $(docker compose -f docker/docker-compose.yml ps -q clickhouse) clickhouse-client --multiquery"
$CH < clickhouse/init/001_create_events_local.sql
$CH < clickhouse/02_kafka_engine.sql
$CH < clickhouse/03_materialized_views.sql
```

#### Phase 5 — Lakehouse (Iceberg + Kafka Connect)

```bash
docker compose -f docker/docker-compose.yml up -d iceberg-rest kafka-connect

until curl -sf http://localhost:8181/v1/config; do sleep 5; done
# Kafka Connect connector plugin download takes 2–4 min on first run
until curl -sf http://localhost:8083/connectors; do sleep 10; done

bash scripts/submit-iceberg-connector.sh
```

#### Phase 6 — Observability (ELK)

```bash
docker compose -f docker/docker-compose.yml up -d \
  elasticsearch logstash kibana filebeat metricbeat heartbeat

until curl -sf "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=60s"; do sleep 10; done
until curl -sf http://localhost:5601/api/status; do sleep 10; done
```

#### Phase 7 — Orchestration (Airflow)

```bash
docker compose -f docker/docker-compose.yml up -d airflow-postgres airflow-init
sleep 30   # allow DB migration to complete

docker compose -f docker/docker-compose.yml up -d airflow-webserver airflow-scheduler

until curl -sf http://localhost:8080/health; do sleep 10; done
```

---

## 4. Steady-State Health Checks

Run after startup and before any on-call shift. All commands run from the project root.

```bash
# ── Kafka ──────────────────────────────────────────────────────────────
# Brokers online, topics present
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 --list

# No under-replicated partitions (output should be empty)
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 \
  --describe --under-replicated-partitions

# ── Flink ──────────────────────────────────────────────────────────────
# 2 TaskManagers, 0 failed jobs
curl -s http://localhost:8082/overview | jq '{taskmanagers, "slots-total", "jobs-running", "jobs-failed"}'

# ETL job state == RUNNING
JOB_ID=$(curl -s http://localhost:8082/jobs \
  | jq -r '.jobs[] | select(.status=="RUNNING") | .id' | head -1)
echo "Job: $JOB_ID"
curl -s "http://localhost:8082/jobs/$JOB_ID" | jq '.state'

# Last successful checkpoint (age should be < 120 seconds)
curl -s "http://localhost:8082/jobs/$JOB_ID/checkpoints" \
  | jq '.latest.completed | {id, "duration_ms": duration, "trigger_timestamp": trigger_timestamp}'

# ── Kafka consumer lag ─────────────────────────────────────────────────
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 --describe --group flink-ecom-etl

# ── Schema Registry ────────────────────────────────────────────────────
curl -s http://localhost:8081/subjects

# ── ClickHouse ─────────────────────────────────────────────────────────
curl -s "http://localhost:8123/ping"                                          # → Ok.
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"       # growing

# ── Druid ──────────────────────────────────────────────────────────────
curl -s http://localhost:8888/status/health | jq .
curl -s http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/status \
  | jq '.payload.state'                                                       # → "RUNNING"

# ── Kafka Connect / Iceberg ────────────────────────────────────────────
curl -s http://localhost:8083/connectors/iceberg-sink/status \
  | jq '.connector.state'                                                     # → "RUNNING"

# ── Elasticsearch / Kibana ────────────────────────────────────────────
curl -s "http://localhost:9200/_cluster/health" | jq '.status'               # → "green" or "yellow"
curl -s http://localhost:5601/api/status | jq '.status.overall.state'        # → "green"

# ── Airflow ────────────────────────────────────────────────────────────
curl -s http://localhost:8080/health | jq .

# ── MinIO ─────────────────────────────────────────────────────────────
curl -s -o /dev/null -w "%{http_code}" http://localhost:9002/minio/health/live  # → 200

# ── Iceberg REST Catalog ───────────────────────────────────────────────
curl -s http://localhost:8181/v1/namespaces | jq .
```

---

## 5. Sending Events — Event Generator

```bash
# Default: 100 events/s, random product & user IDs
python3 src/utils/event_generator.py

# Sustained load for benchmarking (80 k events/s)
python3 src/utils/event_generator.py --rate 80000

# Specific event type (page_view | add_to_cart | purchase | search)
python3 src/utils/event_generator.py --rate 1000 --event-type purchase

# Run for a fixed duration (seconds), then exit
python3 src/utils/event_generator.py --rate 5000 --duration 60
```

Monitor progress in **Kafka UI** → `http://localhost:8085` → Topics → `ecommerce.events.raw.v1`.

---

## 6. Validating End-to-End Data Flow

After sending events, verify each sink received data.

### 6.1 Kafka raw → clean topic

```bash
# Message count on clean topic (should grow)
docker exec kafka-1 kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka-1:9092 \
  --topic ecommerce.events.clean.v1 \
  --time -1

# Peek at a message (Ctrl+C to stop)
docker exec schema-registry \
  kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --topic ecommerce.events.clean.v1 \
  --from-beginning \
  --max-messages 3
```

### 6.2 ClickHouse — historical events

```bash
# Row count
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"

# Events in the last 5 minutes
curl -s "http://localhost:8123/?query=SELECT+event_type,count()+FROM+ecom.events+WHERE+event_time+%3E+now()-300+GROUP+BY+event_type"
```

### 6.3 Druid — real-time OLAP

```bash
# Query via Druid SQL API (events in the last hour)
curl -s -X POST http://localhost:8888/druid/v2/sql \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT event_type, COUNT(*) AS cnt FROM \"ecommerce_events\" WHERE __time > CURRENT_TIMESTAMP - INTERVAL '\''1'\'' HOUR GROUP BY event_type ORDER BY cnt DESC"}' \
  | jq .
```

### 6.4 Iceberg — lakehouse table

```bash
# List Iceberg snapshots (should have at least 1 after ~30 seconds)
curl -s "http://localhost:8181/v1/namespaces/silver/tables/events_clean/snapshots" | jq '.snapshots | length'

# Row count via PyIceberg
python3 - <<'EOF'
from pyiceberg.catalog.rest import RestCatalog
cat = RestCatalog("rest", uri="http://localhost:8181")
tbl = cat.load_table("silver.events_clean")
print("Snapshots:", len(tbl.snapshots()))
print("Files:", tbl.scan().plan_files().__next__() if tbl.snapshots() else "none yet")
EOF
```

### 6.5 Flink dedup validation

```bash
# Confirm dedup window is functioning: raw count > clean count (duplicates filtered)
RAW=$(docker exec kafka-1 kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka-1:9092 --topic ecommerce.events.raw.v1 --time -1 \
  | awk -F: '{sum+=$3} END {print sum}')

CLEAN=$(docker exec kafka-1 kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka-1:9092 --topic ecommerce.events.clean.v1 --time -1 \
  | awk -F: '{sum+=$3} END {print sum}')

echo "Raw: $RAW   Clean: $CLEAN   Deduped: $((RAW - CLEAN))"
```

---

## 7. Airflow DAGs — Scheduled Maintenance

Access the Airflow UI at `http://localhost:8080` (user: `admin` / pass: `admin`).

| DAG | Schedule | What it does |
|-----|----------|-------------|
| `flink_savepoint_rotation` | Hourly | Triggers a Flink savepoint; deletes savepoints older than 5 |
| `iceberg_compaction` | Daily 02:00 | Rewrites small Parquet files into larger ones; expires old snapshots |
| `gdpr_right_to_forget` | On-demand | Deletes Iceberg rows matching a `user_id`; issues positional deletes |
| `dq_great_expectations` | Daily 06:00 | Runs data quality suite against ClickHouse; fails DAG on violations |

### Trigger a DAG manually

```bash
# Via Airflow CLI inside the scheduler container
docker exec airflow-scheduler airflow dags trigger flink_savepoint_rotation

# With configuration JSON
docker exec airflow-scheduler airflow dags trigger gdpr_right_to_forget \
  --conf '{"user_id": "usr-abc123"}'
```

### Check last DAG run status

```bash
docker exec airflow-scheduler airflow dags list-runs -d flink_savepoint_rotation --limit 5
```

---

## 8. Incident Response

### INC-01 — Flink Job FAILED

**Symptoms:** No messages on `ecommerce.events.clean.v1`. Flink UI shows `FAILED`.

```bash
# 1. Identify failure reason
curl -s "http://localhost:8082/jobs/$JOB_ID/exceptions" | jq '.root-exception'
docker logs flink-taskmanager-1 --tail=100

# 2. Resubmit from latest savepoint
SAVEPOINT=$(ls -td /opt/flink/savepoints/*/ 2>/dev/null | head -1)
JAR_ID=$(curl -s http://localhost:8082/jars | jq -r '.files[0].id')

curl -X POST "http://localhost:8082/jars/$JAR_ID/run" \
  -H "Content-Type: application/json" \
  -d "{\"savepointPath\": \"$SAVEPOINT\", \"allowNonRestoredState\": false}"

# 3. If no savepoint exists, resubmit cold (events will replay from Kafka retention)
curl -X POST "http://localhost:8082/jars/$JAR_ID/run" \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

### INC-02 — Checkpoint Timeout / Repeated Failures

**Symptoms:** Flink UI → Checkpoints shows duration ≥ interval (60 s). Lag growing.

```bash
# View checkpoint history
curl -s "http://localhost:8082/jobs/$JOB_ID/checkpoints" | jq '.history[-5:]'

# Option A — increase checkpoint timeout (requires job restart)
# Edit flink-app/src/main/java/com/ecom/etl/JobConfig.java
#   env.getCheckpointConfig().setCheckpointTimeout(120_000L);

# Option B — scale TaskManagers to reduce per-TM load
docker compose -f docker/docker-compose.yml up --scale flink-taskmanager=4 -d
```

---

### INC-03 — Kafka Consumer Lag Growing

**Symptoms:** Lag for group `flink-ecom-etl` increasing. Downstream sinks falling behind.

```bash
# Inspect lag per partition
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 --describe --group flink-ecom-etl

# Scale TaskManagers (no job restart needed for existing parallelism)
docker compose -f docker/docker-compose.yml up --scale flink-taskmanager=4 -d

# Verify new workers registered
curl -s http://localhost:8082/taskmanagers | jq '.taskmanagers | length'
```

---

### INC-04 — ClickHouse Not Consuming

**Symptoms:** `count()` stagnant. ClickHouse consumer group lag growing.

```bash
# Check table attachment
docker exec -it $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "SELECT name, engine FROM system.tables WHERE database='ecom'"

# Check ClickHouse error log
docker exec -it $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client \
  --query "SELECT event_time, level, message FROM system.text_log WHERE level IN ('Error','Warning') ORDER BY event_time DESC LIMIT 20"

# Re-attach Kafka Engine table
docker exec -it $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "DETACH TABLE ecom.events_kafka"
sleep 3
docker exec -it $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "ATTACH TABLE ecom.events_kafka"
```

---

### INC-05 — Iceberg Sink Connector FAILED

**Symptoms:** `GET /connectors/iceberg-sink/status` returns `FAILED`. Iceberg table not updating.

```bash
# Get error detail
curl -s http://localhost:8083/connectors/iceberg-sink/status | jq '.tasks'

# Restart connector tasks (soft restart, preserves offset)
curl -X POST http://localhost:8083/connectors/iceberg-sink/restart?includeTasks=true&onlyFailed=true

# If still failing — delete and redeploy (offset is stored in Kafka)
curl -X DELETE http://localhost:8083/connectors/iceberg-sink
bash scripts/submit-iceberg-connector.sh
```

---

### INC-06 — Druid Supervisor Not Running

**Symptoms:** Druid UI shows supervisor state != `RUNNING`. No new segments.

```bash
# Get supervisor status
curl -s http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/status \
  | jq '.payload'

# Reset and resume
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/reset
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/resume

# If still failing — terminate and resubmit
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/terminate
bash scripts/submit-druid-supervisor.sh
```

---

### INC-07 — Kafka Broker Down

**Symptoms:** Producer errors. One broker missing from `kafka-topics --list`.

```bash
# Identify which broker is down
docker compose -f docker/docker-compose.yml ps | grep kafka

# Graceful restart of a single broker (kafka-2 stays up to maintain quorum)
docker compose -f docker/docker-compose.yml stop kafka-1
sleep 15
docker compose -f docker/docker-compose.yml start kafka-1

# Verify ISR restored (no Leader: -1)
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 \
  --describe --topic ecommerce.events.clean.v1 | grep -v "Leader: -1"
```

---

### INC-08 — Elasticsearch Yellow / Red

**Symptoms:** `GET /_cluster/health` returns `yellow` or `red`. Kibana dashboards empty.

```bash
# Check shard allocation
curl -s "http://localhost:9200/_cluster/allocation/explain?pretty" | jq .

# For single-node dev setup, yellow is normal (replica shards can't be assigned)
# Force green by disabling replicas for the log index
curl -X PUT "http://localhost:9200/flink-logs-*/_settings" \
  -H "Content-Type: application/json" \
  -d '{"index.number_of_replicas": 0}'

# Full restart order: Elasticsearch → Logstash → Kibana
docker compose -f docker/docker-compose.yml restart elasticsearch
sleep 20
docker compose -f docker/docker-compose.yml restart logstash
sleep 10
docker compose -f docker/docker-compose.yml restart kibana
```

---

## 9. Replay Procedures

Each sink has its own independent consumer group — replaying one does not affect the others.

### Replay to Druid

```bash
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/suspend
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/reset
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/resume
```

### Replay to ClickHouse

```bash
CH_CONTAINER=$(docker compose -f docker/docker-compose.yml ps -q clickhouse)

# Detach Kafka Engine, reset offset, truncate, re-attach
docker exec -i $CH_CONTAINER clickhouse-client --query "DETACH TABLE ecom.events_kafka"

docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 \
  --group clickhouse-ecom-consumer \
  --topic ecommerce.events.clean.v1 \
  --reset-offsets --to-earliest --execute

docker exec -i $CH_CONTAINER clickhouse-client --query "TRUNCATE TABLE ecom.events"
docker exec -i $CH_CONTAINER clickhouse-client --query "ATTACH TABLE ecom.events_kafka"
```

### Replay to Iceberg

```bash
curl -X DELETE http://localhost:8083/connectors/iceberg-sink

docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 \
  --group connect-iceberg-sink \
  --topic ecommerce.events.clean.v1 \
  --reset-offsets --to-earliest --execute

bash scripts/submit-iceberg-connector.sh
```

### Replay All Sinks

```bash
# 1. Suspend all sinks
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/suspend
curl -X DELETE http://localhost:8083/connectors/iceberg-sink
docker exec -i $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "DETACH TABLE ecom.events_kafka"

# 2. Reset all consumer groups
for GROUP in druid-ecommerce-events clickhouse-ecom-consumer connect-iceberg-sink; do
  docker exec kafka-1 kafka-consumer-groups \
    --bootstrap-server kafka-1:9092 \
    --group "$GROUP" \
    --topic ecommerce.events.clean.v1 \
    --reset-offsets --to-earliest --execute
done

# 3. Truncate ClickHouse
docker exec -i $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "TRUNCATE TABLE ecom.events"

# 4. Resume all
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/reset
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/resume
docker exec -i $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "ATTACH TABLE ecom.events_kafka"
bash scripts/submit-iceberg-connector.sh
```

---

## 10. Graceful Shutdown

### Save Flink state before stopping

```bash
# 1. Take a savepoint (preserves dedup window state)
JOB_ID=$(curl -s http://localhost:8082/jobs \
  | jq -r '.jobs[] | select(.status=="RUNNING") | .id' | head -1)

SAVEPOINT_RESP=$(curl -s -X POST "http://localhost:8082/jobs/$JOB_ID/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"cancel-job": true, "target-directory": "/opt/flink/savepoints"}')

echo "$SAVEPOINT_RESP" | jq .

# 2. Wait for cancellation (job status → CANCELED)
sleep 15
curl -s http://localhost:8082/jobs | jq '.jobs[] | .status'
```

### Stop all services

```bash
# Ordered shutdown (sinks → processors → brokers → infrastructure)
docker compose -f docker/docker-compose.yml stop \
  airflow-scheduler airflow-webserver \
  kibana logstash filebeat metricbeat heartbeat \
  kafka-connect \
  druid-middlemanager druid-historical druid-broker druid-coordinator druid-overlord \
  clickhouse ch-ui \
  flink-taskmanager-1 flink-taskmanager-2 flink-jobmanager \
  schema-registry schema-registry-ui kafka-ui \
  kafka-1 kafka-2 kafka-init \
  zookeeper \
  iceberg-rest minio \
  elasticsearch \
  airflow-postgres druid-postgres

# Remove containers (keep named volumes for data persistence)
docker compose -f docker/docker-compose.yml down

# Full teardown including all data volumes (DESTRUCTIVE — data lost)
# docker compose -f docker/docker-compose.yml down -v
```

---

## 11. Quick Reference

### Service URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Kafka UI | http://localhost:8085 | — |
| Schema Registry | http://localhost:8081 | — |
| Flink UI | http://localhost:8082 | — |
| Apache Druid | http://localhost:8888 | — |
| ClickHouse HTTP | http://localhost:8123 | default / *(none)* |
| ClickHouse UI | http://localhost:8124 | — |
| MinIO Console | http://localhost:9001 | minio / minio123 |
| MinIO S3 API | http://localhost:9002 | minio / minio123 |
| Iceberg REST | http://localhost:8181 | — |
| Kafka Connect | http://localhost:8083 | — |
| Elasticsearch | http://localhost:9200 | — |
| Kibana | http://localhost:5601 | — |
| Airflow | http://localhost:8080 | admin / admin |

### Kafka Topic Names

| Topic | Purpose |
|-------|---------|
| `ecommerce.events.raw.v1` | Raw events from producers (12 partitions) |
| `ecommerce.events.clean.v1` | Validated + deduped events (hub) |
| `dlq.events` | Dead-letter queue (malformed events) |
| `dlq.iceberg-sink` | Iceberg connector errors |

### Consumer Groups

| Group | Sink |
|-------|------|
| `flink-ecom-etl` | Flink ETL job |
| `clickhouse-ecom-consumer` | ClickHouse Kafka Engine |
| `druid-ecommerce-events` | Druid Kafka supervisor |
| `connect-iceberg-sink` | Kafka Connect Iceberg sink |

### One-Liners

```bash
# Flink job ID (running)
JOB_ID=$(curl -s http://localhost:8082/jobs | jq -r '.jobs[]|select(.status=="RUNNING")|.id' | head -1)

# ClickHouse row count
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"

# Kafka topic offsets (total messages)
docker exec kafka-1 kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka-1:9092 --topic ecommerce.events.clean.v1 --time -1 \
  | awk -F: '{sum+=$3} END {print "Total messages: " sum}'

# All consumer group lags at once
for g in flink-ecom-etl clickhouse-ecom-consumer druid-ecommerce-events connect-iceberg-sink; do
  echo "=== $g ==="; docker exec kafka-1 kafka-consumer-groups \
    --bootstrap-server kafka-1:9092 --describe --group "$g" 2>/dev/null; done

# Tail Flink TaskManager logs
docker logs -f flink-taskmanager-1

# Tail ClickHouse error log
docker exec -it $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query "SELECT event_time, message FROM system.text_log WHERE level='Error' ORDER BY event_time DESC LIMIT 20"
```

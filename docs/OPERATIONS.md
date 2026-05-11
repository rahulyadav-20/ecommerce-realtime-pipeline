# Operations Runbook — ecommerce-realtime-pipeline

This document is the primary reference for operating, scaling, restarting, replaying, backing up, and troubleshooting the pipeline in a running Docker Compose environment. All commands assume you are in the project root directory.

---

## Quick Reference — Service Restart Commands

| Service | Restart Command |
|---|---|
| Flink JobManager | `docker compose -f docker/docker-compose.yml restart flink-jobmanager` |
| Flink TaskManager(s) | `docker compose -f docker/docker-compose.yml restart flink-taskmanager` |
| Kafka broker-1 | `docker compose -f docker/docker-compose.yml restart kafka-1` |
| Kafka broker-2 | `docker compose -f docker/docker-compose.yml restart kafka-2` |
| ZooKeeper | `docker compose -f docker/docker-compose.yml restart zookeeper` |
| Schema Registry | `docker compose -f docker/docker-compose.yml restart schema-registry` |
| ClickHouse | `docker compose -f docker/docker-compose.yml restart clickhouse` |
| Druid Coordinator | `docker compose -f docker/docker-compose.yml restart druid-coordinator` |
| Druid Overlord | `docker compose -f docker/docker-compose.yml restart druid-overlord` |
| Druid Broker | `docker compose -f docker/docker-compose.yml restart druid-broker` |
| Druid MiddleManager | `docker compose -f docker/docker-compose.yml restart druid-middlemanager` |
| Druid Historical | `docker compose -f docker/docker-compose.yml restart druid-historical` |
| Kafka Connect | `docker compose -f docker/docker-compose.yml restart kafka-connect` |
| Airflow Scheduler | `docker compose -f docker/docker-compose.yml restart airflow-scheduler` |
| Airflow Webserver | `docker compose -f docker/docker-compose.yml restart airflow-webserver` |
| Elasticsearch | `docker compose -f docker/docker-compose.yml restart elasticsearch` |
| Kibana | `docker compose -f docker/docker-compose.yml restart kibana` |
| MinIO | `docker compose -f docker/docker-compose.yml restart minio` |

---

## Health Check Commands

Run each one-liner to confirm the named service is healthy.

```bash
# Flink — cluster overview (expect: 2 taskmanagers, 0 failed)
curl -s http://localhost:8082/overview | python -m json.tool

# Flink — confirm ETL job is RUNNING (expect: {"status":"RUNNING"})
JOB_ID=$(curl -s http://localhost:8082/jobs | python -c "import sys,json; print(json.load(sys.stdin)['jobs'][0]['id'])")
curl -s "http://localhost:8082/jobs/${JOB_ID}" | python -c "import sys,json; d=json.load(sys.stdin); print(d['state'])"

# Kafka — list topics (expect: raw, clean, dlq topics present)
docker exec kafka-1 kafka-topics --bootstrap-server kafka-1:9092 --list

# Schema Registry — list subjects
curl -s http://localhost:8081/subjects

# ClickHouse — ping (expect: "Ok.")
curl -s "http://localhost:8123/ping"

# ClickHouse — confirm Kafka engine is consuming
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"

# Druid — service health (expect: {"status":"OK"})
curl -s http://localhost:8888/status/health

# Druid — supervisor running (expect: state=RUNNING)
curl -s http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/status \
  | python -c "import sys,json; d=json.load(sys.stdin); print(d['payload']['state'])"

# Kafka Connect — worker status
curl -s http://localhost:8083/ | python -m json.tool

# Kafka Connect — iceberg sink connector state (expect: RUNNING)
curl -s http://localhost:8083/connectors/iceberg-sink/status \
  | python -c "import sys,json; d=json.load(sys.stdin); print(d['connector']['state'])"

# Elasticsearch — cluster health (expect: green or yellow)
curl -s http://localhost:9200/_cluster/health | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'])"

# Kibana — status (expect: "green")
curl -s http://localhost:5601/api/status | python -c "import sys,json; d=json.load(sys.stdin); print(d['status']['overall']['state'])"

# Airflow — webserver health
curl -s http://localhost:8080/health | python -m json.tool

# MinIO — liveness (expect: HTTP 200)
curl -s -o /dev/null -w "%{http_code}" http://localhost:9002/minio/health/live

# Iceberg REST catalog — list namespaces
curl -s http://localhost:8181/v1/namespaces | python -m json.tool
```

---

## Scaling Guide

### Scale Flink TaskManagers

Add TaskManager replicas without restarting the JobManager or the running job. Flink will automatically register the new workers and the scheduler may rebalance tasks.

```bash
# Scale to 4 TaskManagers
docker compose -f docker/docker-compose.yml up --scale flink-taskmanager=4 -d

# Verify all 4 registered
curl -s http://localhost:8082/taskmanagers | python -c \
  "import sys,json; tms=json.load(sys.stdin)['taskmanagers']; print(f'{len(tms)} TaskManagers registered')"
```

To take effect for a running stateful job, trigger a rescaling via savepoint:

```bash
# Take savepoint
curl -X POST "http://localhost:8082/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"cancel-job": true, "target-directory": "/opt/flink/savepoints"}'

# Resubmit from savepoint with new parallelism
curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"parallelism": 4, "savepointPath": "/opt/flink/savepoints/<savepoint-dir>"}'
```

### Scale Kafka Partitions

Increasing partitions allows more parallel consumers per topic. Note: partition count can only be increased, never decreased.

```bash
# Increase clean topic to 8 partitions
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 \
  --alter \
  --topic ecommerce.events.clean.v1 \
  --partitions 8

# Verify
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 \
  --describe \
  --topic ecommerce.events.clean.v1
```

After increasing partitions, restart all downstream consumers (Druid supervisor, ClickHouse container, Kafka Connect) so they pick up the new partition assignments.

### Scale ClickHouse — Increase Query Threads and Memory

Edit the ClickHouse user config override (typically mounted at `clickhouse/config/users.xml`):

```xml
<profiles>
  <default>
    <max_threads>16</max_threads>
    <max_memory_usage>32000000000</max_memory_usage>  <!-- 32 GB -->
    <max_bytes_before_external_group_by>10000000000</max_bytes_before_external_group_by>
  </default>
</profiles>
```

Apply without restart via the HTTP interface:

```bash
curl -s "http://localhost:8123/?query=SET+max_threads=16"
```

For a persistent change, restart ClickHouse after editing the config file:

```bash
docker compose -f docker/docker-compose.yml restart clickhouse
```

### Scale Druid MiddleManager Worker Capacity

Edit `docker/druid-environment` and increase:

```properties
druid_worker_capacity=8
druid_indexer_runner_javaOptsArray=["-server","-Xms2g","-Xmx4g"]
```

Then restart the MiddleManager:

```bash
docker compose -f docker/docker-compose.yml restart druid-middlemanager
```

Verify the new capacity in the Druid UI under Workers.

---

## Restart Procedures

### Flink — Savepoint-First Restart (Recommended)

Always take a savepoint before restarting Flink to preserve dedup state and avoid reprocessing.

```bash
# Step 1 — get the running job ID
JOB_ID=$(curl -s http://localhost:8082/jobs \
  | python -c "import sys,json; jobs=json.load(sys.stdin)['jobs']; \
    running=[j for j in jobs if j['status']=='RUNNING']; print(running[0]['id'])")

# Step 2 — trigger savepoint (this does NOT cancel the job)
SAVEPOINT_REQ=$(curl -s -X POST "http://localhost:8082/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"cancel-job": false, "target-directory": "/opt/flink/savepoints"}')
echo $SAVEPOINT_REQ

# Step 3 — poll until savepoint completes (status: COMPLETED)
TRIGGER_ID=$(echo $SAVEPOINT_REQ | python -c "import sys,json; print(json.load(sys.stdin)['request-id'])")
curl -s "http://localhost:8082/jobs/${JOB_ID}/savepoints/${TRIGGER_ID}" | python -m json.tool

# Step 4 — note the savepoint path from the response, then restart containers
docker compose -f docker/docker-compose.yml restart flink-jobmanager flink-taskmanager

# Step 5 — wait for TaskManagers to re-register (watch the Flink UI)
sleep 30

# Step 6 — resubmit job from savepoint
JAR_ID=$(curl -s http://localhost:8082/jars | python -c "import sys,json; print(json.load(sys.stdin)['files'][0]['id'])")
curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"savepointPath": "/opt/flink/savepoints/<savepoint-dir>", "allowNonRestoredState": false}'
```

### Kafka Broker — Graceful vs Forced Restart

**Graceful (preferred):** allows the broker to complete in-flight produce/consume operations.

```bash
# Graceful stop
docker compose -f docker/docker-compose.yml stop kafka-1
# Wait for other broker (kafka-2) to assume partition leadership
sleep 15
# Start the stopped broker — it will catch up from replicated logs
docker compose -f docker/docker-compose.yml start kafka-1
```

**Forced (emergency only):**

```bash
docker compose -f docker/docker-compose.yml kill kafka-1
docker compose -f docker/docker-compose.yml start kafka-1
```

After restart, verify all partitions have an in-sync replica:

```bash
docker exec kafka-1 kafka-topics \
  --bootstrap-server kafka-1:9092 \
  --describe \
  --topic ecommerce.events.clean.v1 \
  | grep -v "Leader: -1"
```

### ClickHouse

ClickHouse flushes its Kafka Engine consumer offsets to ZooKeeper/Keeper. A normal restart resumes from the committed offset.

```bash
docker compose -f docker/docker-compose.yml restart clickhouse

# Verify consumption resumed within 60 seconds
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events+WHERE+toDate(event_time)+%3D+today()"
```

### Druid — Coordinator First, Then Others

The Coordinator manages segment assignment. Restart it first and allow 30 seconds before restarting other Druid services.

```bash
docker compose -f docker/docker-compose.yml restart druid-coordinator
sleep 30
docker compose -f docker/docker-compose.yml restart druid-overlord druid-broker druid-historical druid-middlemanager
```

After restart, re-submit any supervisors that did not auto-resume:

```bash
bash scripts/submit-druid-supervisor.sh
```

Verify the supervisor is RUNNING:

```bash
curl -s http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/status \
  | python -c "import sys,json; print(json.load(sys.stdin)['payload']['state'])"
```

### Airflow

```bash
docker compose -f docker/docker-compose.yml restart airflow-scheduler airflow-webserver
```

After restart, confirm the scheduler is healthy:

```bash
curl -s http://localhost:8080/health | python -m json.tool
```

If DAGs are not appearing, force a DAG parse:

```bash
docker exec airflow-scheduler airflow dags reserialize
```

### ELK Stack

Restart in order: Elasticsearch first, then Logstash, then Kibana.

```bash
docker compose -f docker/docker-compose.yml restart elasticsearch
sleep 20
docker compose -f docker/docker-compose.yml restart logstash
sleep 10
docker compose -f docker/docker-compose.yml restart kibana
```

Verify Elasticsearch health after restart:

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

---

## Replay Runbooks

All sinks consume from `ecommerce.events.clean.v1`. Because each sink uses an independent consumer group, replaying one does not affect the others.

### Replay to Druid

```bash
# Step 1 — suspend the supervisor (stops consumption without deleting state)
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/suspend

# Step 2 — reset the supervisor's consumer group to the earliest offset
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/reset

# Step 3 — resume ingestion (supervisor recreates consumer group from offset 0)
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/resume

# Step 4 — monitor lag reduction at the Druid Overlord (http://localhost:8888 > Supervisors)
```

To replay only from a specific timestamp rather than from the beginning, use the `ioConfig.taskStartTime` field in the supervisor spec before resubmitting.

### Replay to ClickHouse

The Kafka Engine table in ClickHouse uses its own consumer group (typically named `clickhouse-ecom-consumer`).

```bash
# Step 1 — stop consumption by detaching the Kafka Engine table
docker exec clickhouse clickhouse-client \
  --query "DETACH TABLE ecom.events_kafka"

# Step 2 — reset the consumer group offset to the beginning
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 \
  --group clickhouse-ecom-consumer \
  --topic ecommerce.events.clean.v1 \
  --reset-offsets \
  --to-earliest \
  --execute

# Step 3 — truncate the target table to avoid duplicates (if doing full replay)
docker exec clickhouse clickhouse-client \
  --query "TRUNCATE TABLE ecom.events"

# Step 4 — re-attach the Kafka Engine table to restart consumption
docker exec clickhouse clickhouse-client \
  --query "ATTACH TABLE ecom.events_kafka"
```

### Replay to Iceberg via Kafka Connect

```bash
# Step 1 — pause the connector
curl -X PUT http://localhost:8083/connectors/iceberg-sink/pause

# Step 2 — delete the connector (this removes the stored offset)
curl -X DELETE http://localhost:8083/connectors/iceberg-sink

# Step 3 — reset the Kafka Connect consumer group offset
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 \
  --group connect-iceberg-sink \
  --topic ecommerce.events.clean.v1 \
  --reset-offsets \
  --to-earliest \
  --execute

# Step 4 — resubmit the connector (it will start from offset 0)
bash scripts/submit-iceberg-connector.sh

# Step 5 — confirm RUNNING state
curl -s http://localhost:8083/connectors/iceberg-sink/status | python -m json.tool
```

### Replay All Sinks Simultaneously

```bash
# Suspend Druid
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/suspend

# Pause and delete Kafka Connect
curl -X PUT http://localhost:8083/connectors/iceberg-sink/pause
curl -X DELETE http://localhost:8083/connectors/iceberg-sink

# Detach ClickHouse Kafka Engine
docker exec clickhouse clickhouse-client --query "DETACH TABLE ecom.events_kafka"

# Reset all consumer groups
for GROUP in druid-ecommerce-events clickhouse-ecom-consumer connect-iceberg-sink; do
  docker exec kafka-1 kafka-consumer-groups \
    --bootstrap-server kafka-1:9092 \
    --group $GROUP \
    --topic ecommerce.events.clean.v1 \
    --reset-offsets --to-earliest --execute
done

# Truncate ClickHouse target table
docker exec clickhouse clickhouse-client --query "TRUNCATE TABLE ecom.events"

# Resume all sinks
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/reset
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/resume
docker exec clickhouse clickhouse-client --query "ATTACH TABLE ecom.events_kafka"
bash scripts/submit-iceberg-connector.sh
```

---

## Backup Procedures

### Flink Savepoints

Savepoints are the primary mechanism for protecting Flink job state. The `flink_savepoint_rotation.py` Airflow DAG runs hourly automatically. To trigger a manual savepoint:

```bash
JOB_ID=$(curl -s http://localhost:8082/jobs \
  | python -c "import sys,json; jobs=json.load(sys.stdin)['jobs']; \
    running=[j for j in jobs if j['status']=='RUNNING']; print(running[0]['id'])")

curl -X POST "http://localhost:8082/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"cancel-job": false, "target-directory": "/opt/flink/savepoints"}'
```

Savepoints are written inside the `flink-taskmanager` container volume. To back up to the host:

```bash
docker cp flink-taskmanager:/opt/flink/savepoints ./backups/flink-savepoints-$(date +%Y%m%d)
```

Retain the last 5 savepoints and delete older ones — the Airflow DAG handles this automatically.

### ClickHouse Backups

```bash
# Full backup via clickhouse-backup (if installed) or native BACKUP command
docker exec clickhouse clickhouse-client \
  --query "BACKUP DATABASE ecom TO Disk('backups', 'ecom-$(date +%Y%m%d).zip')"

# Copy backup file to host
docker cp clickhouse:/var/lib/clickhouse/disks/backups/ecom-$(date +%Y%m%d).zip \
  ./backups/clickhouse/
```

For production, configure `clickhouse/config/backup_storage.xml` to point at S3/MinIO.

### Airflow Metadata Database Backup

Airflow metadata (DAG run history, task instances, variables, connections) is stored in a PostgreSQL or SQLite database depending on your Compose configuration.

```bash
# SQLite (default LocalExecutor setup)
docker exec airflow-scheduler \
  sqlite3 /opt/airflow/airflow.db ".backup /opt/airflow/airflow-backup-$(date +%Y%m%d).db"
docker cp airflow-scheduler:/opt/airflow/airflow-backup-$(date +%Y%m%d).db \
  ./backups/airflow/

# PostgreSQL (if configured)
docker exec airflow-postgres \
  pg_dump -U airflow airflow > ./backups/airflow/airflow-$(date +%Y%m%d).sql
```

---

## Troubleshooting Guide

### Scenario 1 — Flink Job in FAILED State

**Symptoms:** Flink UI shows job state `FAILED`. No events flowing to `ecommerce.events.clean.v1`.

**Cause:** Unhandled exception in an operator, Kafka broker unreachable, or RocksDB state corruption.

**Fix:**
```bash
# 1. View the failure exception in the Flink UI: http://localhost:8082 > Completed Jobs > Exceptions tab

# 2. Check Flink TaskManager logs
docker logs flink-taskmanager --tail=100

# 3. If the failure is transient (network blip), resubmit from last successful savepoint
curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"savepointPath": "/opt/flink/savepoints/<latest>", "allowNonRestoredState": false}'
```

---

### Scenario 2 — Checkpoint Timeout

**Symptoms:** Flink UI shows repeated checkpoint failures with `Checkpoint expired before completing`. Consumer lag grows.

**Cause:** State too large for the checkpoint interval, slow RocksDB writes, or GC pressure.

**Fix:**
```bash
# 1. Check checkpoint statistics in Flink UI: Job > Checkpoints > History
# Look for: checkpoint duration close to or exceeding the interval (default 60s)

# 2. Increase checkpoint timeout in the job config
# Edit flink-app/src/main/java/com/ecom/etl/JobConfig.java:
#   env.getCheckpointConfig().setCheckpointTimeout(120_000L); // 2 minutes

# 3. Enable incremental checkpoints (already on by default with RocksDB)
# Confirm in logs: "Using RocksDB with incremental checkpointing"

# 4. Increase TaskManager memory if GC is the issue
# Edit docker-compose.yml: FLINK_PROPERTIES: taskmanager.memory.process.size: 4096m
docker compose -f docker/docker-compose.yml restart flink-taskmanager
```

---

### Scenario 3 — Kafka Consumer Lag Growing

**Symptoms:** Flink UI shows growing source records-in-flight. Kafka UI shows increasing lag for consumer group `flink-ecom-etl`.

**Cause:** Flink throughput is lower than Kafka producer rate. Common triggers: GC pressure, checkpoint slowness, or insufficient TaskManagers.

**Fix:**
```bash
# 1. Check current lag per partition
docker exec kafka-1 kafka-consumer-groups \
  --bootstrap-server kafka-1:9092 \
  --describe \
  --group flink-ecom-etl

# 2. Scale TaskManagers (see Scaling Guide above)
docker compose -f docker/docker-compose.yml up --scale flink-taskmanager=4 -d

# 3. If GC is the bottleneck, increase heap
# Edit docker-compose.yml: FLINK_PROPERTIES: taskmanager.memory.process.size: 6144m
```

---

### Scenario 4 — ClickHouse Kafka Engine Not Consuming

**Symptoms:** `SELECT count() FROM ecom.events` returns a stagnant value. Kafka consumer group for ClickHouse shows lag growing.

**Cause:** Kafka Engine table detached, schema mismatch between Avro and ClickHouse table columns, or ClickHouse ran out of memory during ingestion.

**Fix:**
```bash
# 1. Check if the table is attached
docker exec clickhouse clickhouse-client \
  --query "SELECT name, engine FROM system.tables WHERE database='ecom'"

# 2. Check ClickHouse system log for errors
docker exec clickhouse clickhouse-client \
  --query "SELECT message FROM system.text_log WHERE level='Error' ORDER BY event_time DESC LIMIT 20"

# 3. Re-attach if detached
docker exec clickhouse clickhouse-client --query "ATTACH TABLE ecom.events_kafka"

# 4. If schema mismatch: drop the Kafka Engine table, fix column types, recreate
docker exec clickhouse clickhouse-client --query "DROP TABLE IF EXISTS ecom.events_kafka"
curl -s "http://localhost:8123/" --data-binary @clickhouse/02_kafka_engine.sql
```

---

### Scenario 5 — Druid Supervisor Lag / Not Consuming

**Symptoms:** Druid Overlord UI shows the supervisor task in a `WAITING` or `PENDING` state. Lag is not decreasing.

**Cause:** MiddleManager out of worker capacity, incorrect Kafka bootstrap server, or ZooKeeper connectivity issue.

**Fix:**
```bash
# 1. Check supervisor status detail
curl -s http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/status \
  | python -m json.tool

# 2. Check MiddleManager capacity
curl -s http://localhost:8888/druid/indexer/v1/worker | python -m json.tool

# 3. If capacity is 0, increase druid_worker_capacity in druid-environment and restart
docker compose -f docker/docker-compose.yml restart druid-middlemanager

# 4. If Kafka bootstrap is wrong, suspend + update + resume the supervisor spec
curl -X POST http://localhost:8888/druid/indexer/v1/supervisor/ecommerce-events/suspend
# Edit the spec's ioConfig.consumerProperties.bootstrap.servers
bash scripts/submit-druid-supervisor.sh
```

---

### Scenario 6 — Kafka Connect Connector in FAILED State

**Symptoms:** `curl http://localhost:8083/connectors/iceberg-sink/status` shows `state: FAILED`. Iceberg files not appearing in MinIO.

**Cause:** MinIO unreachable, Iceberg REST catalog down, schema mismatch, or worker OOM.

**Fix:**
```bash
# 1. Get the error message
curl -s http://localhost:8083/connectors/iceberg-sink/status \
  | python -c "import sys,json; d=json.load(sys.stdin); [print(t['trace']) for t in d['tasks'] if t['state']=='FAILED']"

# 2. Check Kafka Connect worker logs
docker logs kafka-connect --tail=50

# 3. Attempt a connector restart (without deleting offset)
curl -X POST http://localhost:8083/connectors/iceberg-sink/restart

# 4. If error persists, delete and resubmit (will replay from committed offset)
curl -X DELETE http://localhost:8083/connectors/iceberg-sink
bash scripts/submit-iceberg-connector.sh
```

---

### Scenario 7 — Iceberg Schema Mismatch

**Symptoms:** Kafka Connect logs show `SchemaCompatibilityException` or `IncompatibleSchemaException`. Connector enters FAILED state after a schema registry update.

**Cause:** A new Avro schema version was registered that added a non-nullable field or removed a field, violating `FULL_TRANSITIVE` compatibility — or the Iceberg table schema was not evolved before deploying the new schema.

**Fix:**
```bash
# 1. Identify the new schema version
curl -s http://localhost:8081/subjects/ecommerce.events.raw.v1-value/versions/latest \
  | python -m json.tool

# 2. Evolve the Iceberg table schema via PyIceberg before restarting the connector
pip install pyiceberg
python - <<'EOF'
from pyiceberg.catalog import load_catalog
catalog = load_catalog("rest", uri="http://localhost:8181")
table = catalog.load_table("ecom.events")
with table.update_schema() as update:
    update.add_column("new_field", "string")  # adjust to actual new field
EOF

# 3. Restart the connector
curl -X POST http://localhost:8083/connectors/iceberg-sink/restart
```

---

### Scenario 8 — Airflow DAG Not Appearing

**Symptoms:** DAG file is present in `airflow/dags/` but not visible in the Airflow UI.

**Cause:** Python syntax error in the DAG file, missing import, or the scheduler has not yet parsed the file.

**Fix:**
```bash
# 1. Test the DAG file for syntax errors
docker exec airflow-scheduler python /opt/airflow/dags/flink_savepoint_rotation.py

# 2. Force the scheduler to re-parse
docker exec airflow-scheduler airflow dags reserialize

# 3. Check import errors in the Airflow UI: Admin > Import Errors

# 4. Check scheduler logs
docker logs airflow-scheduler --tail=50 | grep ERROR
```

---

### Scenario 9 — MinIO Bucket Access Denied

**Symptoms:** Kafka Connect or Iceberg REST returns `403 Access Denied` or `NoSuchBucket` when writing to MinIO.

**Cause:** MinIO credentials mismatch, bucket does not exist, or bucket policy is too restrictive.

**Fix:**
```bash
# 1. Verify MinIO is up
curl -s -o /dev/null -w "%{http_code}" http://localhost:9002/minio/health/live

# 2. Log into MinIO console and check bucket existence
# http://localhost:9001 (minio / minio123) > Buckets

# 3. Create the warehouse bucket if missing
docker exec minio mc alias set local http://localhost:9000 minio minio123
docker exec minio mc mb local/warehouse

# 4. Verify credentials in kafka-connect/iceberg-sink.json match minio/minio123
# Restart the connector after fixing
curl -X DELETE http://localhost:8083/connectors/iceberg-sink
bash scripts/submit-iceberg-connector.sh
```

---

### Scenario 10 — Elasticsearch Cluster Red

**Symptoms:** `curl http://localhost:9200/_cluster/health` returns `"status":"red"`. Kibana shows "Unable to connect to Elasticsearch".

**Cause:** Unassigned primary shards, typically due to a node restart where the disk was full or the index was corrupted.

**Fix:**
```bash
# 1. Get unassigned shard explanation
curl -s http://localhost:9200/_cluster/allocation/explain?pretty

# 2. If disk full: free space and allow reassignment
curl -X PUT http://localhost:9200/_cluster/settings \
  -H "Content-Type: application/json" \
  -d '{"transient": {"cluster.routing.allocation.disk.threshold_enabled": false}}'

# 3. Force retry of shard allocation
curl -X POST http://localhost:9200/_cluster/reroute?retry_failed=true

# 4. If the index is corrupted, delete and recreate it (data loss — use only if no other option)
curl -X DELETE http://localhost:9200/pipeline-logs-*

# 5. Restore from snapshot if available
curl -X POST http://localhost:9200/_snapshot/backup/snapshot_1/_restore
```

---

### Scenario 11 — OOM in Flink TaskManager

**Symptoms:** Docker shows `flink-taskmanager` container exited with code 137 (OOM kill). Flink job transitions to FAILED or RESTARTING.

**Cause:** RocksDB state growing beyond allocated off-heap memory, or too many parallel tasks per TaskManager.

**Fix:**
```bash
# 1. Check TaskManager memory configuration
docker inspect flink-taskmanager | python -c \
  "import sys,json; d=json.load(sys.stdin); \
   [print(e) for e in d[0]['Config']['Env'] if 'MEMORY' in e or 'memory' in e]"

# 2. Increase TaskManager memory in docker-compose.yml
# FLINK_PROPERTIES: |
#   taskmanager.memory.process.size: 6144m
#   taskmanager.memory.managed.fraction: 0.5

# 3. Reduce the number of slots per TaskManager
# FLINK_PROPERTIES: taskmanager.numberOfTaskSlots: 2

# 4. Rebuild and restart
docker compose -f docker/docker-compose.yml up -d --no-deps flink-taskmanager

# 5. Resubmit job from last savepoint
curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"savepointPath": "/opt/flink/savepoints/<latest>"}'
```

---

### Scenario 12 — Avro Deserialization Error

**Symptoms:** Flink logs show `SerializationException` or `AvroRuntimeException`. Events are routed to `dlq.events` in bulk.

**Cause:** A producer is sending events with an unregistered schema ID, or the schema ID embedded in the Avro wire format does not exist in Schema Registry (e.g., producer was pointed at a different registry).

**Fix:**
```bash
# 1. Check the DLQ topic for error messages
docker exec kafka-1 kafka-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --topic dlq.events \
  --from-beginning \
  --max-messages 5

# 2. List all registered schema versions
curl -s http://localhost:8081/subjects/ecommerce.events.raw.v1-value/versions

# 3. Decode the problematic message's schema ID
# The first 5 bytes of an Avro-encoded Kafka message are: [0x00][schema-id-4-bytes]
# Use kafka-avro-console-consumer to decode:
docker exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --topic ecommerce.events.raw.v1 \
  --from-beginning \
  --max-messages 3 \
  --property schema.registry.url=http://schema-registry:8081

# 4. If producer is using wrong schema, fix the producer config to point at
#    http://localhost:8081 and re-register the schema if needed
bash scripts/register-schema.sh
```

---

## Monitoring and Alerting

### Kibana Dashboards to Create

| Dashboard Name | Data Source | Key Visualisations |
|---|---|---|
| **Pipeline Throughput** | Metricbeat / app logs | Events/sec on raw topic, Events/sec on clean topic, DLQ rate |
| **Flink Job Health** | Flink REST API via Logstash | Checkpoint duration, Checkpoint success rate, Records processed/sec |
| **Kafka Consumer Lag** | Metricbeat Kafka module | Lag per consumer group per topic, Lag trend over 1h |
| **Sink Latency** | App instrumentation logs | p50/p95/p99 end-to-end latency from Kafka ingest to ClickHouse |
| **ClickHouse Performance** | ClickHouse system tables via Logstash | Query duration histogram, Merge queue depth, Replication lag |
| **Druid Ingestion** | Druid Overlord API | Tasks running, Tasks failed, Rows/sec ingested |

### Alert Thresholds

| Alert | Condition | Severity |
|---|---|---|
| Flink job not RUNNING | No RUNNING job for > 2 minutes | Critical |
| Checkpoint failure rate | > 2 consecutive failures | Warning |
| Kafka consumer lag | > 50,000 messages sustained for 5 minutes | Warning |
| Kafka consumer lag | > 200,000 messages sustained for 5 minutes | Critical |
| DLQ events | Any events on `dlq.events` topic | Warning |
| ClickHouse not consuming | Row count delta = 0 for > 5 minutes | Warning |
| Elasticsearch cluster red | `status == "red"` for > 1 minute | Critical |
| Kafka Connect FAILED | Any connector state = FAILED | Critical |
| MinIO disk usage | > 80% capacity | Warning |

### Metricbeat Key Metrics to Watch

```yaml
# In metricbeat.yml, ensure these modules are enabled:
- module: kafka
  metricsets: ["consumergroup", "partition"]
  hosts: ["kafka-1:9092"]

- module: docker
  metricsets: ["container", "memory", "cpu"]

- module: system
  metricsets: ["filesystem", "memory", "cpu"]
```

Key metrics:
- `kafka.consumergroup.consumer_lag` — per group, per topic
- `docker.memory.usage.pct` — flag if any container exceeds 85%
- `system.filesystem.used.pct` — flag if host disk exceeds 80%

### Flink REST API Key Metrics

Poll these endpoints in Kibana via Logstash HTTP poller or a custom Metricbeat module:

```bash
# Overall job metrics
curl -s http://localhost:8082/jobs/${JOB_ID}/metrics?get=numRecordsInPerSecond,numRecordsOutPerSecond

# Checkpoint statistics
curl -s http://localhost:8082/jobs/${JOB_ID}/checkpoints

# TaskManager memory
curl -s http://localhost:8082/taskmanagers | python -m json.tool
```

Set alerts when:
- `numRecordsInPerSecond` drops to 0 while Kafka lag is growing (Flink stalled)
- Last completed checkpoint age exceeds 5 minutes
- Any checkpoint reports `status: FAILED`

# Development Guide — ecommerce-realtime-pipeline

This guide covers everything a new contributor needs to build, test, extend, and debug the pipeline locally. It assumes familiarity with Java, Python, Docker, and basic stream processing concepts.

---

## Prerequisites

| Tool | Minimum Version | Notes |
|---|---|---|
| Java | 17 | Required for Flink job build and IDE debugging |
| Maven | 3.8+ | Flink job dependency management and build |
| Python | 3.10+ | Airflow DAGs, benchmark scripts, event generator |
| Docker Desktop | 24+ with Compose v2 | Runs all infrastructure containers |
| Git | 2.x | Version control |
| IntelliJ IDEA | 2023.1+ | Recommended for Java; Community Edition is sufficient |
| VS Code | 1.80+ | Optional; recommended for Python / DAG editing |
| curl | any | Used in all shell-based verification steps |

Install Python dependencies into a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows PowerShell

pip install -r requirements.txt
```

---

## Local Development Setup

### Step 1 — Clone and verify tools

```bash
git clone https://github.com/your-org/ecommerce-realtime-pipeline.git
cd ecommerce-realtime-pipeline

# Verify all required tools
java -version          # must show openjdk 17
mvn -version           # must show 3.8+
python --version       # must show 3.10+
docker compose version # must show v2.x
```

### Step 2 — Start the minimal dev stack (Phase 1)

For day-to-day Flink development you do not need Druid, ClickHouse, Airflow, or the ELK stack. The Phase 1 Compose file starts only Kafka (2 brokers + ZooKeeper), Schema Registry, Kafka UI, and Flink (JobManager + 2 TaskManagers).

```bash
docker compose -f docker/docker-compose-phase1.yml up -d

# Verify minimal stack is healthy
docker compose -f docker/docker-compose-phase1.yml ps
```

Services available after Phase 1:
- Flink UI: http://localhost:8082
- Schema Registry: http://localhost:8081
- Kafka UI: http://localhost:8085

To start the full stack (all services), use `docker/docker-compose.yml` instead.

### Step 3 — IDE Setup: IntelliJ IDEA

1. Open IntelliJ and choose **File > Open**, then select the `flink-app/` directory (the Maven project root containing `pom.xml`).
2. IntelliJ will detect the Maven project automatically. Click **Trust Project** when prompted.
3. Navigate to **File > Project Structure > Project** and set **SDK** to Java 17. If not listed, click **Add SDK > Download JDK** and select OpenJDK 17.
4. Wait for Maven to finish downloading dependencies (visible in the Build tab).
5. Navigate to `flink-app/src/main/java/com/ecom/etl/EcomEtlJob.java` and verify there are no red underlines.
6. Set up a **Run Configuration** for local execution:
   - **Main class:** `com.ecom.etl.EcomEtlJob`
   - **VM options:** `-Xmx2g -Dlog4j.configurationFile=src/main/resources/log4j2.properties`
   - **Program arguments:** `--kafka.bootstrap.servers localhost:9092 --schema.registry.url http://localhost:8081`

### Step 4 — IDE Setup: VS Code (Python / DAGs)

1. Install extensions: **Python** (ms-python.python), **Docker** (ms-azuretools.vscode-docker), **YAML** (redhat.vscode-yaml).
2. Open the project root in VS Code: `code .`
3. Select the Python interpreter: `Ctrl+Shift+P` > **Python: Select Interpreter** > choose `.venv/bin/python`.
4. The DAG files in `airflow/dags/` are plain Python. You can run them directly for syntax checking:
   ```bash
   python airflow/dags/flink_savepoint_rotation.py
   ```

---

## Building and Testing the Flink Job

### Build the fat JAR

```bash
cd flink-app
mvn clean package -DskipTests
```

The output JAR is at `flink-app/target/ecom-streaming-etl.jar`. This is the artifact submitted to the Flink cluster.

### Run all unit tests

```bash
cd flink-app
mvn test
```

Tests use Flink's `MiniClusterWithClientResource` for integration-level testing without a running cluster.

### Run a specific test class

```bash
cd flink-app
mvn test -Dtest=DedupFunctionTest

# Run a specific test method
mvn test -Dtest=DedupFunctionTest#testDuplicateWithinWindow
```

### Build with local-run profile (IDE execution)

The `local-run` Maven profile includes Flink's `flink-clients` and logging dependencies that are provided by the cluster in production but needed when running from the IDE.

```bash
cd flink-app
mvn clean package -Plocal-run
```

### Submit the built JAR to the local Flink cluster

```bash
# Upload the JAR
curl -X POST http://localhost:8082/jars/upload \
  -H "Expect:" \
  -F "jarfile=@flink-app/target/ecom-streaming-etl.jar"

# Get the jar ID
JAR_ID=$(curl -s http://localhost:8082/jars \
  | python -c "import sys,json; print(json.load(sys.stdin)['files'][0]['id'])")

# Run the job
curl -X POST "http://localhost:8082/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d '{"programArgs": ""}'
```

### Debugging tips — attach a remote debugger

The Flink TaskManager in the Docker Compose file can be configured to listen for remote debugger connections.

Add this to the `flink-taskmanager` service environment in `docker/docker-compose.yml`:

```yaml
JAVA_TOOL_OPTIONS: "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005"
```

Expose port 5005:

```yaml
ports:
  - "5005:5005"
```

Then in IntelliJ: **Run > Edit Configurations > Add > Remote JVM Debug**. Set host to `localhost`, port to `5005`. Start the debug configuration after the Flink job is running.

Set breakpoints in `DedupFunction.java` or `ValidateAndClean.java` and process a few events to trigger them.

---

## How to Add a New Event Type

### Step 1 — Update the Avro schema

Edit `schemas/clickstream-event.avsc`. Add new event_type values to the enum, or add new optional fields using the `["null", "<type>"]` union pattern:

```json
{
  "name": "referrer_url",
  "type": ["null", "string"],
  "default": null,
  "doc": "HTTP referrer URL, present only for page_view events."
}
```

**Rule:** All new fields must be nullable with a `null` default. This preserves `FULL_TRANSITIVE` compatibility — old consumers can still read records written by new producers.

### Step 2 — Register the new schema version

```bash
bash scripts/register-schema.sh
```

This script reads `schemas/clickstream-event.avsc` and POSTs it to Schema Registry. If the change is compatible, it receives a new schema ID. If compatibility is violated, the registration fails with a `409 Conflict` — fix the schema before proceeding.

Verify the new version was accepted:

```bash
curl -s http://localhost:8081/subjects/ecommerce.events.raw.v1-value/versions
```

### Step 3 — Update the Flink ValidateAndClean operator

Edit `flink-app/src/main/java/com/ecom/functions/ValidateAndClean.java`.

Add validation logic for the new field:

```java
// Example: validate referrer_url is a valid URL if present
String referrerUrl = record.get("referrer_url") != null
    ? record.get("referrer_url").toString()
    : null;

if (referrerUrl != null && !referrerUrl.startsWith("http")) {
    collector.collect(buildDlqRecord(record, "invalid_referrer_url"));
    return;
}
```

Run the unit tests after any operator change:

```bash
cd flink-app && mvn test -Dtest=ValidateAndCleanTest
```

### Step 4 — Add the new field as a Druid rollup dimension

Edit the Druid ingestion spec in `scripts/submit-druid-supervisor.sh` (the JSON payload). Add the field to the `dimensionsSpec.dimensions` array:

```json
{
  "name": "referrer_url",
  "type": "string"
}
```

If the field should not be a dimension (too high cardinality), leave it out — Druid rollup will drop fields not listed in dimensionsSpec.

Resubmit the supervisor:

```bash
bash scripts/submit-druid-supervisor.sh
```

### Step 5 — Add the new field to ClickHouse

Add a column to the Kafka Engine table and the target MergeTree table:

```sql
ALTER TABLE ecom.events_kafka ADD COLUMN referrer_url Nullable(String);
ALTER TABLE ecom.events ADD COLUMN referrer_url Nullable(String);
```

Apply:

```bash
curl -s "http://localhost:8123/" \
  --data-binary "ALTER TABLE ecom.events_kafka ADD COLUMN referrer_url Nullable(String)"
curl -s "http://localhost:8123/" \
  --data-binary "ALTER TABLE ecom.events ADD COLUMN referrer_url Nullable(String)"
```

No restart is needed — ClickHouse DDL is online.

---

## How to Add a New ClickHouse Materialized View

### Pattern explanation

ClickHouse materialized views are maintained incrementally: each INSERT into the source table triggers the MV query and merges the result into the target `AggregatingMergeTree` table. This enables sub-second aggregation query performance without a full table scan.

The pattern is:

1. Create a target `AggregatingMergeTree` table (stores partial aggregates).
2. Create a `MATERIALIZED VIEW` that reads from the Kafka Engine table and inserts into the target table.
3. Query the target table using `-Merge` combinator functions (`sumMerge`, `countMerge`, `uniqMerge`).

### Template with annotated example

```sql
-- Step 1: Create the AggregatingMergeTree target table
CREATE TABLE ecom.events_by_category_hourly
(
    hour         DateTime,          -- time bucket for partitioning
    category     String,            -- dimension
    event_count  AggregateFunction(count, UInt64),   -- partial aggregate state
    revenue_sum  AggregateFunction(sum, Float64)     -- partial aggregate state
)
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(hour)        -- monthly partitions
ORDER BY (hour, category);         -- sorting key must include all GROUP BY keys

-- Step 2: Create the Materialized View
CREATE MATERIALIZED VIEW ecom.mv_events_by_category_hourly
TO ecom.events_by_category_hourly  -- "TO" = INSERT into this existing table
AS
SELECT
    toStartOfHour(event_time)      AS hour,
    category,
    countState()                   AS event_count,   -- accumulate partial count
    sumState(revenue)              AS revenue_sum    -- accumulate partial sum
FROM ecom.events_kafka             -- read from the Kafka Engine source
GROUP BY hour, category;
```

### Query the materialized view (using -Merge combinators)

```sql
SELECT
    hour,
    category,
    countMerge(event_count)   AS total_events,
    sumMerge(revenue_sum)     AS total_revenue
FROM ecom.events_by_category_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
GROUP BY hour, category
ORDER BY hour DESC, total_revenue DESC;
```

### Backfill query pattern

If the MV was created after data already exists in the source table, backfill the target using `INSERT INTO ... SELECT`:

```sql
INSERT INTO ecom.events_by_category_hourly
SELECT
    toStartOfHour(event_time) AS hour,
    category,
    countState()              AS event_count,
    sumState(revenue)         AS revenue_sum
FROM ecom.events              -- read from the MergeTree table, not Kafka Engine
WHERE event_time >= '2025-01-01'
GROUP BY hour, category;
```

### Testing the MV

```bash
# 1. Insert a test row directly (bypasses Kafka, useful for MV testing)
curl -s "http://localhost:8123/" \
  --data-binary "INSERT INTO ecom.events_kafka (event_id, event_time, category, revenue) \
    VALUES ('test-001', now(), 'electronics', 99.99)"

# 2. Query the MV target table to confirm the row was aggregated
curl -s "http://localhost:8123/?query=SELECT+countMerge(event_count),sumMerge(revenue_sum)+FROM+ecom.events_by_category_hourly"
```

---

## How to Write a New Airflow DAG

### DAG template with all boilerplate

```python
"""
dag_name.py  — brief description of what this DAG does.

Schedule: daily at 02:00 UTC.
Owner: data-engineering
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
import logging

log = logging.getLogger(__name__)

@dag(
    dag_id="dag_name",
    description="Brief description",
    schedule="0 2 * * *",           # daily at 02:00 UTC (cron expression)
    start_date=pendulum.datetime(2025, 1, 1, tz="UTC"),
    catchup=False,                   # do NOT backfill missed runs
    max_active_runs=1,               # prevent concurrent runs of this DAG
    tags=["maintenance", "ecom"],
    default_args={
        "owner": "data-engineering",
        "retries": 2,
        "retry_delay": pendulum.duration(minutes=5),
        "email_on_failure": False,
    },
)
def dag_name():

    @task()
    def step_one(**context) -> str:
        """First task — returns a value passed to subsequent tasks."""
        log.info("Running step_one for logical_date=%s", context["logical_date"])
        # ... do work ...
        return "result_of_step_one"

    @task()
    def step_two(step_one_result: str) -> None:
        """Second task — receives the return value of step_one."""
        log.info("step_one returned: %s", step_one_result)
        # ... do work ...

    # Wire the task graph
    result = step_one()
    step_two(result)


dag_name()  # required — instantiates the DAG object so Airflow can discover it
```

Save the file to `airflow/dags/dag_name.py`. Airflow's scheduler auto-discovers new DAG files within its `dag_discovery_safe_mode` scan interval (default: 30 seconds).

### Making the DAG runnable as a plain Python script

All DAG files should be runnable with `python dag_name.py` to quickly catch syntax errors without Airflow:

```python
# At the bottom of the file, after dag_name()
if __name__ == "__main__":
    dag_name().test()   # runs all tasks locally using the Airflow local runner
```

```bash
python airflow/dags/flink_savepoint_rotation.py
```

### Testing the DAG in Airflow

```bash
# List all tasks in the DAG
docker exec airflow-scheduler airflow tasks list dag_name

# Test a single task (does not write to the metadata database)
docker exec airflow-scheduler airflow tasks test dag_name step_one 2025-01-01

# Trigger a full DAG run manually
docker exec airflow-scheduler airflow dags trigger dag_name
```

Monitor the run in the Airflow UI at http://localhost:8080 (admin / admin).

---

## How to Add a New Druid SQL Query

### SUM(events) vs COUNT(*) gotcha

Druid applies **rollup** at ingestion time. Multiple raw events with the same dimension combination are merged into a single row with a `count` metric. This means:

```sql
-- WRONG: undercounts — counts rolled-up rows, not original events
SELECT COUNT(*) FROM "ecommerce-events" WHERE ...

-- CORRECT: sums the pre-aggregated event count
SELECT SUM("count") FROM "ecommerce-events" WHERE ...
```

Always use `SUM("count")` for event counting in Druid. Use `COUNT(*)` only to count the number of rolled-up dimension combinations.

### Time bucketing helpers

Druid's `__time` column is always in milliseconds UTC. Use Druid's time functions:

```sql
-- Events per hour for the last 24 hours
SELECT
    TIME_FLOOR(__time, 'PT1H')  AS hour,
    category,
    SUM("count")                AS event_count
FROM "ecommerce-events"
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1, 2
ORDER BY 1 DESC;
```

Common intervals: `'PT1M'` (1 min), `'PT1H'` (1 hour), `'P1D'` (1 day).

### Testing via the REST API

```bash
# POST a Druid SQL query
curl -X POST http://localhost:8888/druid/v2/sql \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT SUM(\"count\") FROM \"ecommerce-events\" WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '\''1'\'' HOUR"
  }'
```

Or use the Druid console query editor at http://localhost:8888 > Query.

Save useful queries to `druid/sample-queries.md` for team reference.

---

## Code Structure and Conventions

### Java Package Structure

```
com.ecom
├── etl/
│   ├── EcomEtlJob.java      # Main entry point. Contains main(), builds StreamExecutionEnvironment.
│   └── JobConfig.java       # Reads ParameterTool and exposes typed getters for all config values.
├── functions/
│   ├── DedupFunction.java   # RichKeyedProcessFunction — RocksDB state, TTL, dedup logic.
│   └── ValidateAndClean.java # RichFlatMapFunction — field validation, null-checks, DLQ routing.
└── operators/
    ├── DedupFunction.java   # Thin wrapper / alias — can hold operator-level configuration.
    └── ValidateAndClean.java # Thin wrapper / alias.
```

**Rules for Java code:**
- One class per file, package-private helpers only when tightly coupled.
- All Flink operators must be serializable. Do not hold non-serializable references as instance fields.
- Use `transient` for any field that should not be checkpointed.
- `open()` and `close()` lifecycle methods must call `super.open()` / `super.close()`.
- Log at `DEBUG` for per-event data; `INFO` for per-checkpoint summaries; `WARN`/`ERROR` for recoverable/unrecoverable issues.

### Python Naming Conventions

| Entity | Convention | Example |
|---|---|---|
| DAG ID | `snake_case` | `flink_savepoint_rotation` |
| Task ID | `snake_case` | `trigger_savepoint` |
| Python function | `snake_case` | `def get_job_id():` |
| Constants | `UPPER_SNAKE_CASE` | `FLINK_BASE_URL = "http://..."` |
| DAG file name | matches dag_id | `flink_savepoint_rotation.py` |

All DAG files must be runnable as plain Python scripts (see DAG template above).

### SQL Formatting Rules

- Keywords in `UPPER CASE`: `SELECT`, `FROM`, `WHERE`, `GROUP BY`, `ORDER BY`
- Table and column names in `lower_case` or `"quoted_lower_case"` (Druid requires quoting)
- One clause per line for queries longer than 3 lines
- Include a comment block at the top of each SQL file explaining what it creates and why
- Use explicit column lists in `INSERT INTO ... SELECT` — never `SELECT *`

### Git Branch and Commit Message Convention

**Branch naming:**

```
feat/<short-description>     # new feature
fix/<short-description>      # bug fix
chore/<short-description>    # dependency updates, CI, tooling
docs/<short-description>     # documentation only
refactor/<short-description> # no behaviour change
```

**Commit message format (Conventional Commits):**

```
<type>(<scope>): <short summary in present tense, max 72 chars>

<optional body: motivation, what changed, how to test>

<optional footer: BREAKING CHANGE: ..., Closes #123>
```

Examples:

```
feat(flink): add referrer_url field to ValidateAndClean operator

fix(clickhouse): handle NULL revenue values in Kafka Engine table

chore(deps): bump Flink version to 1.19.1

docs(ops): add replay runbook for ClickHouse sink
```

---

## Running the Performance Benchmark

### Quick run (reduced load, ~5 minutes)

```bash
python tests/performance/benchmark.py --quick
```

This runs a 5-minute load test at 10,000 events/sec and reports throughput and latency percentiles.

### Full run (sustained load, ~30 minutes)

```bash
python tests/performance/benchmark.py --duration 1800 --rate 80000
```

### Analyse results

```bash
python tests/performance/analyze_results.py
```

Output is saved to `tests/performance/results/`. The analyser prints a summary table and generates matplotlib charts.

### Interpreting results

| Metric | Healthy | Investigate if |
|---|---|---|
| Throughput (events/sec) | Within 5% of target rate | > 10% below target rate |
| p50 latency (ms) | < 200 | > 300 |
| p95 latency (ms) | < 500 | > 800 |
| p99 latency (ms) | < 1000 | > 1500 |
| Flink checkpoint duration (s) | < 30 | > 45 |
| Kafka consumer lag (messages) | < 5,000 | > 50,000 |

If p99 latency exceeds the target, first check Flink checkpoint duration (checkpoint barriers can block processing). If checkpoint duration is fine, check GC logs in the TaskManager container.

---

## CI/CD Notes

The following checks run automatically on every pull request push:

| Check | Tool | What it validates |
|---|---|---|
| Flink unit tests | `mvn test` (Java 17) | `DedupFunctionTest`, `ValidateAndCleanTest` |
| Python syntax check | `python -m py_compile` | All files under `airflow/dags/` and `scripts/` |
| Avro schema lint | `avro-tools` | `schemas/clickstream-event.avsc` is valid Avro JSON |
| Docker Compose validate | `docker compose config` | `docker/docker-compose.yml` is syntactically valid |
| Commit message lint | `commitlint` | Enforces Conventional Commits format |

All checks must pass before a PR can be merged. The CI pipeline does **not** run integration tests or spin up the full Docker stack — those are run manually before merging significant changes.

To run the CI checks locally before pushing:

```bash
# Java tests
cd flink-app && mvn test && cd ..

# Python syntax
python -m py_compile airflow/dags/flink_savepoint_rotation.py
python -m py_compile airflow/dags/iceberg_compaction.py
python -m py_compile airflow/dags/gdpr_right_to_forget.py
python -m py_compile airflow/dags/dq_great_expectations.py

# Docker Compose validation
docker compose -f docker/docker-compose.yml config --quiet && echo "Compose OK"
```

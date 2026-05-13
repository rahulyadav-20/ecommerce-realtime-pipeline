#!/usr/bin/env bash
# E-Commerce Realtime Pipeline - Full Stack Setup Script
# Usage: ./setup.sh [--phase <1-7>] [--skip-build] [--env <local|docker|prod>]
set -euo pipefail

# ─── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

# ─── Defaults ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_PHASE=7
SKIP_BUILD=false
APP_ENV=docker
COMPOSE_FILE="${SCRIPT_DIR}/docker/docker-compose.yml"

# ─── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)    TARGET_PHASE="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    --env)      APP_ENV="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--phase 1-7] [--skip-build] [--env local|docker|prod]"
      echo ""
      echo "  --phase N       Run only up to phase N (default: 7 = full stack)"
      echo "  --skip-build    Skip Maven build of Flink job"
      echo "  --env ENV       Config environment: local | docker | prod (default: docker)"
      echo ""
      echo "Phases:"
      echo "  1  Foundation       — Kafka, Zookeeper, Schema Registry"
      echo "  2  Stream Processor — Flink JobManager + TaskManagers"
      echo "  3  Real-Time OLAP   — Apache Druid + supervisor"
      echo "  4  Historical OLAP  — ClickHouse + Kafka Engine tables"
      echo "  5  Data Lakehouse   — MinIO, Iceberg REST Catalog, Kafka Connect"
      echo "  6  Observability    — ELK Stack (Elasticsearch, Logstash, Kibana, Beats)"
      echo "  7  Orchestration    — Apache Airflow DAGs"
      exit 0
      ;;
    *) die "Unknown argument: $1. Use --help for usage." ;;
  esac
done

export APP_ENV

# ─── Prerequisites check ─────────────────────────────────────────────────────
check_prerequisites() {
  log "Checking prerequisites..."

  local missing=()

  command -v docker    &>/dev/null || missing+=("docker")
  command -v python3   &>/dev/null || command -v python &>/dev/null || missing+=("python3")
  command -v curl      &>/dev/null || missing+=("curl")
  command -v jq        &>/dev/null || missing+=("jq")

  if [[ "$SKIP_BUILD" == false ]]; then
    command -v mvn &>/dev/null || missing+=("maven")
    command -v java &>/dev/null || missing+=("java 17+")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    die "Missing required tools: ${missing[*]}"
  fi

  # Docker daemon running?
  docker info &>/dev/null || die "Docker daemon is not running. Please start Docker Desktop."

  # docker compose (v2) or docker-compose (v1)
  if docker compose version &>/dev/null 2>&1; then
    DOCKER_COMPOSE="docker compose"
  elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE="docker-compose"
  else
    die "Docker Compose not found. Install Docker Desktop >= 24 or docker-compose v2."
  fi

  # Java version check (17+)
  if [[ "$SKIP_BUILD" == false ]] && command -v java &>/dev/null; then
    local java_ver
    java_ver=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d. -f1)
    [[ "$java_ver" -ge 17 ]] 2>/dev/null || warn "Java 17+ is recommended; found version $java_ver"
  fi

  # Available memory check (warn if < 12 GB)
  if command -v free &>/dev/null; then
    local mem_gb
    mem_gb=$(free -g | awk '/Mem:/ {print $2}')
    [[ "$mem_gb" -ge 12 ]] || warn "System has ${mem_gb} GB RAM — 12 GB is the recommended minimum."
  fi

  ok "Prerequisites satisfied."
}

# ─── Wait helper ─────────────────────────────────────────────────────────────
wait_for_http() {
  local name="$1" url="$2" max_secs="${3:-120}" interval=5
  log "Waiting for ${name} at ${url} (timeout ${max_secs}s)..."
  local elapsed=0
  until curl -sf "$url" &>/dev/null; do
    if [[ $elapsed -ge $max_secs ]]; then
      err "${name} did not become ready within ${max_secs}s."
      return 1
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
    echo -n "."
  done
  echo ""
  ok "${name} is ready."
}

# ─── Phase 1: Foundation (Kafka + Schema Registry) ───────────────────────────
phase_1_foundation() {
  echo -e "\n${CYAN}══ Phase 1: Foundation — Kafka + Schema Registry ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d zookeeper kafka-1 kafka-2 kafka-init \
    schema-registry schema-registry-ui kafka-ui

  wait_for_http "Schema Registry" "http://localhost:8081/subjects" 120

  # Register Avro schemas
  log "Registering Avro schemas..."
  bash "${SCRIPT_DIR}/scripts/register-schema.sh"
  ok "Schemas registered."

  # Install Python dependencies for event generator
  log "Installing Python dependencies..."
  local pip_cmd
  pip_cmd=$(command -v pip3 || command -v pip)
  $pip_cmd install -q -r "${SCRIPT_DIR}/requirements.txt"
  ok "Python dependencies installed."

  echo -e "${GREEN}Phase 1 complete.${NC}"
  echo "  Kafka UI:         http://localhost:8085"
  echo "  Schema Registry:  http://localhost:8081"
}

# ─── Phase 2: Stream Processor (Flink) ───────────────────────────────────────
phase_2_flink() {
  echo -e "\n${CYAN}══ Phase 2: Stream Processor — Apache Flink ══${NC}"

  # Build Flink fat-JAR unless skipped or already built
  local jar="${SCRIPT_DIR}/flink-app/target/ecom-streaming-etl-1.0-SNAPSHOT.jar"
  local prebuilt="${SCRIPT_DIR}/flink-app/ecom-streaming-etl.jar"

  if [[ "$SKIP_BUILD" == false ]]; then
    if [[ ! -f "$jar" ]]; then
      log "Building Flink job (this may take a few minutes)..."
      mvn -f "${SCRIPT_DIR}/flink-app/pom.xml" package -DskipTests -q
      ok "Flink JAR built: $jar"
    else
      ok "Flink JAR already built — skipping Maven build."
    fi
  elif [[ -f "$prebuilt" ]]; then
    log "Using pre-built JAR: $prebuilt"
    jar="$prebuilt"
  else
    warn "--skip-build specified but no JAR found. Flink job will not be submitted."
    jar=""
  fi

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d flink-jobmanager flink-taskmanager-1 flink-taskmanager-2 minio minio-setup

  wait_for_http "Flink JobManager" "http://localhost:8082/overview" 120

  # Submit Flink job
  if [[ -n "$jar" && -f "$jar" ]]; then
    log "Submitting Flink ETL job..."
    curl -sf \
      -F "jarfile=@${jar}" \
      http://localhost:8082/jars/upload | jq -r '.filename' > /tmp/flink_jar_id.txt
    local jar_id
    jar_id=$(cat /tmp/flink_jar_id.txt | xargs basename | sed 's/_/\//')
    curl -sf -X POST \
      "http://localhost:8082/jars/${jar_id}/run" \
      -H 'Content-Type: application/json' \
      -d '{"programArgsList": []}' | jq -r '.jobid' > /tmp/flink_job_id.txt
    ok "Flink job submitted: $(cat /tmp/flink_job_id.txt)"
  fi

  echo -e "${GREEN}Phase 2 complete.${NC}"
  echo "  Flink UI:    http://localhost:8082"
  echo "  MinIO UI:    http://localhost:9001  (minio / minio123)"
}

# ─── Phase 3: Real-Time OLAP (Druid) ─────────────────────────────────────────
phase_3_druid() {
  echo -e "\n${CYAN}══ Phase 3: Real-Time OLAP — Apache Druid ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d druid-postgres druid-coordinator druid-broker \
    druid-historical druid-middlemanager druid-router

  wait_for_http "Druid Router" "http://localhost:8888/status/health" 180

  log "Submitting Druid Kafka supervisor..."
  bash "${SCRIPT_DIR}/scripts/submit-druid-supervisor.sh"

  echo -e "${GREEN}Phase 3 complete.${NC}"
  echo "  Druid UI:    http://localhost:8888"
}

# ─── Phase 4: Historical OLAP (ClickHouse) ───────────────────────────────────
phase_4_clickhouse() {
  echo -e "\n${CYAN}══ Phase 4: Historical OLAP — ClickHouse ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d clickhouse ch-ui

  wait_for_http "ClickHouse" "http://localhost:8123/ping" 120

  log "Applying ClickHouse DDL..."
  for sql_file in \
    "${SCRIPT_DIR}/clickhouse/init/001_create_events_local.sql" \
    "${SCRIPT_DIR}/clickhouse/02_kafka_engine.sql" \
    "${SCRIPT_DIR}/clickhouse/03_materialized_views.sql"; do
    if [[ -f "$sql_file" ]]; then
      docker exec -i \
        "$(${DOCKER_COMPOSE} -f "${COMPOSE_FILE}" ps -q clickhouse 2>/dev/null | head -1)" \
        clickhouse-client --multiquery < "$sql_file" \
        && ok "Applied: $(basename "$sql_file")" \
        || warn "Could not apply $sql_file — may already exist."
    fi
  done

  echo -e "${GREEN}Phase 4 complete.${NC}"
  echo "  ClickHouse UI:  http://localhost:8124"
  echo "  HTTP API:       http://localhost:8123"
}

# ─── Phase 5: Data Lakehouse (Iceberg + Kafka Connect) ───────────────────────
phase_5_lakehouse() {
  echo -e "\n${CYAN}══ Phase 5: Data Lakehouse — Iceberg + Kafka Connect ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d iceberg-rest kafka-connect

  wait_for_http "Iceberg REST Catalog" "http://localhost:8181/v1/config" 120
  wait_for_http "Kafka Connect"        "http://localhost:8083/connectors" 180

  log "Deploying Iceberg sink connector..."
  bash "${SCRIPT_DIR}/scripts/submit-iceberg-connector.sh"

  echo -e "${GREEN}Phase 5 complete.${NC}"
  echo "  Iceberg REST:   http://localhost:8181"
  echo "  Kafka Connect:  http://localhost:8083"
}

# ─── Phase 6: Observability (ELK) ────────────────────────────────────────────
phase_6_elk() {
  echo -e "\n${CYAN}══ Phase 6: Observability — ELK Stack ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d elasticsearch logstash kibana filebeat metricbeat heartbeat

  wait_for_http "Elasticsearch" "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=60s" 180
  wait_for_http "Kibana"        "http://localhost:5601/api/status" 180

  echo -e "${GREEN}Phase 6 complete.${NC}"
  echo "  Kibana:          http://localhost:5601"
  echo "  Elasticsearch:   http://localhost:9200"
}

# ─── Phase 7: Orchestration (Airflow) ────────────────────────────────────────
phase_7_airflow() {
  echo -e "\n${CYAN}══ Phase 7: Orchestration — Apache Airflow ══${NC}"

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d airflow-postgres airflow-init

  log "Waiting for Airflow DB initialisation..."
  sleep 20

  $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d airflow-webserver airflow-scheduler

  wait_for_http "Airflow" "http://localhost:8080/health" 180

  echo -e "${GREEN}Phase 7 complete.${NC}"
  echo "  Airflow UI:  http://localhost:8080  (admin / admin)"
}

# ─── Summary ─────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║        E-Commerce Realtime Pipeline — Ready!         ║${NC}"
  echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo "  Service              URL"
  echo "  ─────────────────────────────────────────────────────"
  echo "  Kafka UI             http://localhost:8085"
  echo "  Schema Registry      http://localhost:8081"
  echo "  Flink UI             http://localhost:8082"
  echo "  Apache Druid         http://localhost:8888"
  echo "  ClickHouse UI        http://localhost:8124"
  echo "  MinIO Console        http://localhost:9001  (minio/minio123)"
  echo "  Iceberg REST         http://localhost:8181"
  echo "  Kafka Connect        http://localhost:8083"
  echo "  Kibana               http://localhost:5601"
  echo "  Elasticsearch        http://localhost:9200"
  echo "  Airflow              http://localhost:8080  (admin/admin)"
  echo ""
  echo "Start sending test events:"
  echo "  python3 src/utils/event_generator.py --rate 1000"
  echo ""
  echo "To tear down:"
  echo "  docker compose -f docker/docker-compose.yml down -v"
  echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "  ╔══════════════════════════════════════════════════╗"
  echo "  ║   E-Commerce Realtime Pipeline — Setup Script    ║"
  echo "  ║   Target phase: ${TARGET_PHASE} / 7  |  Env: ${APP_ENV}              ║"
  echo "  ╚══════════════════════════════════════════════════╝"
  echo -e "${NC}"

  check_prerequisites

  [[ $TARGET_PHASE -ge 1 ]] && phase_1_foundation
  [[ $TARGET_PHASE -ge 2 ]] && phase_2_flink
  [[ $TARGET_PHASE -ge 3 ]] && phase_3_druid
  [[ $TARGET_PHASE -ge 4 ]] && phase_4_clickhouse
  [[ $TARGET_PHASE -ge 5 ]] && phase_5_lakehouse
  [[ $TARGET_PHASE -ge 6 ]] && phase_6_elk
  [[ $TARGET_PHASE -ge 7 ]] && phase_7_airflow

  [[ $TARGET_PHASE -eq 7 ]] && print_summary
}

main "$@"

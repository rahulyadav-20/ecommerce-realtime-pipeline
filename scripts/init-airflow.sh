#!/usr/bin/env bash
# =============================================================================
# init-airflow.sh
# Bootstrap Apache Airflow 2.9 for the ecom real-time pipeline.
#
# What this script does:
#   1. Validates prerequisites (Docker, Compose, required ports)
#   2. Creates host directories mounted into the Airflow containers
#   3. Starts airflow-postgres and waits for it to be healthy
#   4. Runs airflow-init (DB migration + admin user creation)
#   5. Optionally starts airflow-webserver + airflow-scheduler
#   6. Verifies the stack is up and prints access URLs
#
# Usage:
#   ./scripts/init-airflow.sh [OPTIONS]
#
# Options:
#   --start          Also start webserver + scheduler after init (default: init only)
#   --down           Tear down Airflow services and remove named volumes
#   --reset          --down then re-run full initialisation
#   --compose-file F Path to docker-compose.yml  (default: docker/docker-compose.yml)
#   --airflow-dir D  Path to Airflow host dirs   (default: ./airflow)
#   -h, --help       Show this help message
#
# Examples:
#   ./scripts/init-airflow.sh                    # init only
#   ./scripts/init-airflow.sh --start            # init + start services
#   ./scripts/init-airflow.sh --reset --start    # wipe + reinitialise + start
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${REPO_ROOT}/docker/docker-compose.yml"
AIRFLOW_DIR="${REPO_ROOT}/airflow"
AIRFLOW_URL="${AIRFLOW_URL:-http://localhost:8080}"

DO_START=false
DO_DOWN=false
DO_RESET=false
INIT_TIMEOUT=300   # seconds to wait for airflow-init to complete
HEALTH_TIMEOUT=120 # seconds to wait for webserver to be healthy

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)        DO_START=true;         shift ;;
        --down)         DO_DOWN=true;          shift ;;
        --reset)        DO_RESET=true;         shift ;;
        --compose-file) COMPOSE_FILE="$2";     shift 2 ;;
        --airflow-dir)  AIRFLOW_DIR="$2";      shift 2 ;;
        -h|--help)
            head -35 "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

COMPOSE="docker compose -f ${COMPOSE_FILE}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()     { printf '\033[0;32m[%s] %s\033[0m\n'     "$(date +%H:%M:%S)" "$*"; }
warn()    { printf '\033[0;33m[%s] WARN: %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()     { printf '\033[0;31m[%s] ERROR: %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
section() { printf '\n\033[1;34m══ %s ══\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Wait until a command exits 0 or the timeout is reached
wait_for() {
    local description="$1"; shift
    local timeout="$1"; shift
    local deadline=$(( $(date +%s) + timeout ))
    until "$@" 2>/dev/null; do
        [[ $(date +%s) -lt $deadline ]] || die "Timed out waiting for: ${description}"
        sleep 3
        log "Still waiting for ${description}..."
    done
    log "${description} ✓"
}

# Check if a host port is already bound (to avoid silent failures)
check_port_free() {
    local port="$1"
    if command -v lsof &>/dev/null; then
        lsof -iTCP:"${port}" -sTCP:LISTEN -n -P &>/dev/null && \
            warn "Port ${port} is already in use — Airflow may fail to bind"
    fi
}

# ---------------------------------------------------------------------------
# STEP 0: Prerequisites
# ---------------------------------------------------------------------------
section "Pre-flight checks"

command -v docker >/dev/null  || die "docker is not installed"
docker info >/dev/null 2>&1   || die "Docker daemon is not running"

# Prefer 'docker compose' (v2 plugin) over 'docker-compose' (v1 standalone)
if ! docker compose version >/dev/null 2>&1; then
    COMPOSE="docker-compose -f ${COMPOSE_FILE}"
    command -v docker-compose >/dev/null || die "Neither 'docker compose' nor 'docker-compose' is available"
fi

[[ -f "${COMPOSE_FILE}" ]] || die "Compose file not found: ${COMPOSE_FILE}"

log "Docker: $(docker --version)"
log "Compose: $(${COMPOSE} version 2>/dev/null || echo 'v1')"
log "Compose file: ${COMPOSE_FILE}"

check_port_free 8080   # Airflow webserver
check_port_free 5432   # Postgres (if exposed externally)

# ---------------------------------------------------------------------------
# STEP 1: --down / --reset
# ---------------------------------------------------------------------------
if $DO_DOWN || $DO_RESET; then
    section "Tearing down Airflow services"
    log "Stopping and removing Airflow containers + named volumes..."
    ${COMPOSE} rm -fsv airflow-init airflow-webserver airflow-scheduler airflow-postgres 2>/dev/null || true
    # Remove the named volume for logs (postgres data is a separate volume)
    docker volume rm "$(basename "${REPO_ROOT}")_airflow-logs" 2>/dev/null && log "Removed airflow-logs volume" || true
    docker volume rm "$(basename "${REPO_ROOT}")_airflow-postgres-data" 2>/dev/null && log "Removed airflow-postgres-data volume" || true
    log "Teardown complete"
    $DO_DOWN && { log "Done."; exit 0; }
fi

# ---------------------------------------------------------------------------
# STEP 2: Create host directories
# Compose bind-mounts require the source directories to exist on the host.
# Docker will auto-create them as root if missing, which causes permission issues.
# ---------------------------------------------------------------------------
section "Creating Airflow host directories"

for d in dags plugins config logs; do
    mkdir -p "${AIRFLOW_DIR}/${d}"
    log "  ${AIRFLOW_DIR}/${d}"
done

# Airflow container runs as UID 50000 (airflow user). The mounted directories
# must be owned or writable by that UID so the scheduler can create log files.
# On Linux, set ownership explicitly. On macOS/Windows with Docker Desktop
# the fs permission model is more permissive; this chmod is a safe no-op.
chmod 755 "${AIRFLOW_DIR}/dags" "${AIRFLOW_DIR}/plugins" "${AIRFLOW_DIR}/config"
chmod 777 "${AIRFLOW_DIR}/logs" 2>/dev/null || warn "Could not chmod logs dir (may need sudo on Linux)"

# ---------------------------------------------------------------------------
# STEP 3: Start airflow-postgres
# ---------------------------------------------------------------------------
section "Starting airflow-postgres"

${COMPOSE} up -d airflow-postgres
wait_for "airflow-postgres to be healthy" 60 \
    ${COMPOSE} ps airflow-postgres --format json | grep -q '"Health":"healthy"'

# ---------------------------------------------------------------------------
# STEP 4: Run airflow-init
# Performs: airflow db migrate + users create + connections create
# ---------------------------------------------------------------------------
section "Running airflow-init (DB migration + user setup)"

log "Starting airflow-init container..."
${COMPOSE} up airflow-init

# Capture exit code of the init container
INIT_EXIT=$(${COMPOSE} ps airflow-init --format json 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0].get('ExitCode',0))" 2>/dev/null || echo "0")

if [[ "${INIT_EXIT}" != "0" ]]; then
    log "airflow-init logs:"
    ${COMPOSE} logs airflow-init | tail -30
    die "airflow-init exited with code ${INIT_EXIT}"
fi

log "airflow-init completed successfully ✓"

# ---------------------------------------------------------------------------
# STEP 5: Optionally start webserver + scheduler
# ---------------------------------------------------------------------------
if $DO_START; then
    section "Starting airflow-webserver and airflow-scheduler"

    ${COMPOSE} up -d airflow-webserver airflow-scheduler

    log "Waiting for webserver to become healthy..."
    wait_for "Airflow webserver" "${HEALTH_TIMEOUT}" \
        curl -sf "${AIRFLOW_URL}/health"

    log "Waiting for scheduler heartbeat..."
    # Scheduler writes a heartbeat to the DB every 5 s; wait up to 30 s
    sleep 15
    if ${COMPOSE} ps airflow-scheduler --format json 2>/dev/null | grep -q '"Status":"running"'; then
        log "airflow-scheduler is running ✓"
    else
        warn "airflow-scheduler may not have started cleanly; check: ${COMPOSE} logs airflow-scheduler"
    fi
fi

# ---------------------------------------------------------------------------
# STEP 6: Status summary
# ---------------------------------------------------------------------------
section "Status"

${COMPOSE} ps airflow-postgres airflow-init \
    $( $DO_START && echo "airflow-webserver airflow-scheduler" ) \
    --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || \
    ${COMPOSE} ps airflow-postgres airflow-init

printf '\n'
log "Airflow initialised."
log ""
log "  Web UI   : ${AIRFLOW_URL}  (admin / admin)"
log "  REST API : ${AIRFLOW_URL}/api/v1/dags"
log ""
log "Useful commands:"
log "  Start all  : docker compose -f ${COMPOSE_FILE} up -d airflow-webserver airflow-scheduler"
log "  Logs WS    : docker compose -f ${COMPOSE_FILE} logs -f airflow-webserver"
log "  Logs sched : docker compose -f ${COMPOSE_FILE} logs -f airflow-scheduler"
log "  Stop all   : docker compose -f ${COMPOSE_FILE} stop airflow-webserver airflow-scheduler"
log "  Full reset : $0 --reset --start"
log ""
log "DAGs directory  : ${AIRFLOW_DIR}/dags"
log "Plugins directory: ${AIRFLOW_DIR}/plugins"

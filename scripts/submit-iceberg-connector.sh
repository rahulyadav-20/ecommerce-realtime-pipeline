#!/usr/bin/env bash
# =============================================================================
# submit-iceberg-connector.sh
# Submits (or updates) the Iceberg sink connector to Kafka Connect.
#
# Usage:
#   ./scripts/submit-iceberg-connector.sh [OPTIONS]
#
# Options:
#   --connect-url URL     Kafka Connect REST base URL  (default: http://localhost:8083)
#   --catalog-url URL     Iceberg REST catalog URL     (default: http://localhost:8181)
#   --config FILE         Path to connector JSON config
#                         (default: kafka-connect/iceberg-sink.json, relative to repo root)
#   --dry-run             Print curl commands without executing
#   -h, --help            Show this help
#
# Examples:
#   ./scripts/submit-iceberg-connector.sh
#   ./scripts/submit-iceberg-connector.sh --connect-url http://localhost:8083
#   ./scripts/submit-iceberg-connector.sh --dry-run
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Load environment config (APP_ENV=local|docker|prod)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=scripts/config.sh
source "${SCRIPT_DIR}/config.sh"

# ---------------------------------------------------------------------------
# Defaults (config.sh already set KAFKA_CONNECT_URL and ICEBERG_REST_URL)
# ---------------------------------------------------------------------------
CONNECT_URL="${KAFKA_CONNECT_URL}"
CATALOG_URL="${ICEBERG_REST_URL}"
CONFIG_FILE="${REPO_ROOT}/kafka-connect/iceberg-sink.json"
DRY_RUN=false

CONNECT_TIMEOUT=300   # seconds to wait for Connect REST API to be ready
STATUS_TIMEOUT=120    # seconds to wait for connector to reach RUNNING
POLL_INTERVAL=5       # seconds between status polls

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --connect-url)  CONNECT_URL="$2";  shift 2 ;;
        --catalog-url)  CATALOG_URL="$2";  shift 2 ;;
        --config)       CONFIG_FILE="$2";  shift 2 ;;
        --dry-run)      DRY_RUN=true;      shift   ;;
        -h|--help)
            head -30 "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()     { printf '\033[0;32m[%s] %s\033[0m\n'  "$(date +%H:%M:%S)" "$*"; }
warn()    { printf '\033[0;33m[%s] WARN: %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()     { printf '\033[0;31m[%s] ERROR: %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
section() { printf '\n\033[1;34m=== %s ===\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# dry_run wrapper — prints the command instead of executing it
# ---------------------------------------------------------------------------
run() {
    if [ "${DRY_RUN}" = "true" ]; then
        printf '\033[0;90m[dry-run] %s\033[0m\n' "$*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
section "Pre-flight checks"

[ -f "${CONFIG_FILE}" ] || die "Config file not found: ${CONFIG_FILE}"

# Extract connector name from JSON (requires jq or python3)
if command -v jq &>/dev/null; then
    CONNECTOR_NAME=$(jq -r '.name' "${CONFIG_FILE}")
else
    CONNECTOR_NAME=$(python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" < "${CONFIG_FILE}")
fi
[ -n "${CONNECTOR_NAME}" ] || die "Could not parse connector name from ${CONFIG_FILE}"

log "Connector  : ${CONNECTOR_NAME}"
log "Config     : ${CONFIG_FILE}"
log "Connect URL: ${CONNECT_URL}"
log "Catalog URL: ${CATALOG_URL}"
[ "${DRY_RUN}" = "true" ] && warn "DRY-RUN MODE — no changes will be made"

# ---------------------------------------------------------------------------
# Step 1: Wait for Kafka Connect REST API
# ---------------------------------------------------------------------------
section "Step 1/4 — Wait for Kafka Connect"

deadline=$(( $(date +%s) + CONNECT_TIMEOUT ))
until curl -sf "${CONNECT_URL}/" -o /dev/null 2>/dev/null; do
    now=$(date +%s)
    if [ "${now}" -ge "${deadline}" ]; then
        die "Kafka Connect did not become ready within ${CONNECT_TIMEOUT}s. Is the container running?"
    fi
    remaining=$(( deadline - now ))
    log "Waiting for Connect... (${remaining}s remaining)"
    sleep "${POLL_INTERVAL}"
done
log "Kafka Connect is ready ✓"

# ---------------------------------------------------------------------------
# Step 2: Ensure Iceberg namespaces exist
# The table identifier warehouse.silver.events_clean requires two namespace levels:
#   Level 1: warehouse   (top-level namespace)
#   Level 2: silver      (child namespace under warehouse)
# The Iceberg REST API returns 409 Conflict if a namespace already exists —
# we treat 409 as success (idempotent create).
# ---------------------------------------------------------------------------
section "Step 2/4 — Ensure Iceberg namespaces"

# Helper: create a namespace, treat 409 (already exists) as success
create_namespace() {
    local ns_json="$1"
    local http_code
    http_code=$(
        run curl -s -o /dev/null -w "%{http_code}" \
            -X POST "${CATALOG_URL}/v1/namespaces" \
            -H "Content-Type: application/json" \
            -d "${ns_json}"
    )
    case "${http_code}" in
        200|201) log "Namespace created: ${ns_json}" ;;
        409)     log "Namespace already exists (409) — skipping: ${ns_json}" ;;
        *)       warn "Unexpected HTTP ${http_code} creating namespace: ${ns_json}" ;;
    esac
}

# Check catalog is reachable before creating namespaces
if curl -sf "${CATALOG_URL}/v1/config" -o /dev/null 2>/dev/null; then
    log "Iceberg REST catalog is reachable ✓"
    create_namespace '{"namespace": ["warehouse"]}'
    create_namespace '{"namespace": ["warehouse", "silver"]}'
else
    warn "Iceberg REST catalog unreachable at ${CATALOG_URL} — skipping namespace creation."
    warn "The connector will attempt to create namespaces itself (requires auto-create-enabled=true)."
fi

# ---------------------------------------------------------------------------
# Step 3: Create or update the connector
# POST /connectors             → create (expects full {"name":..., "config":{...}} body)
# PUT  /connectors/{name}/config → update (expects only the "config" object)
# ---------------------------------------------------------------------------
section "Step 3/4 — Submit connector config"

HTTP_STATUS=$(
    curl -s -o /dev/null -w "%{http_code}" \
        "${CONNECT_URL}/connectors/${CONNECTOR_NAME}"
)

if [ "${HTTP_STATUS}" = "200" ]; then
    # Connector exists — update its config via PUT
    log "Connector '${CONNECTOR_NAME}' already exists — updating config"

    if command -v jq &>/dev/null; then
        CONFIG_BODY=$(jq '.config' "${CONFIG_FILE}")
    else
        CONFIG_BODY=$(python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['config']))" < "${CONFIG_FILE}")
    fi

    http_code=$(
        run curl -s -o /dev/null -w "%{http_code}" \
            -X PUT "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/config" \
            -H "Content-Type: application/json" \
            -d "${CONFIG_BODY}"
    )
    log "PUT /connectors/${CONNECTOR_NAME}/config → HTTP ${http_code}"
    [[ "${http_code}" =~ ^2 ]] || die "Update failed with HTTP ${http_code}"

else
    # Connector does not exist — create it via POST
    log "Connector '${CONNECTOR_NAME}' not found (HTTP ${HTTP_STATUS}) — creating"

    # Strip _comment_* keys before posting (Kafka Connect rejects unknown properties)
    if command -v jq &>/dev/null; then
        CLEAN_JSON=$(jq 'del(.config | to_entries[] | select(.key | startswith("_comment")) | .key) | {name, config: (.config | with_entries(select(.key | startswith("_comment") | not)))}' "${CONFIG_FILE}")
    else
        CLEAN_JSON=$(python3 - <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
data['config'] = {k: v for k, v in data['config'].items() if not k.startswith('_comment')}
print(json.dumps(data))
PYEOF
        "${CONFIG_FILE}")
    fi

    http_code=$(
        run curl -s -o /dev/null -w "%{http_code}" \
            -X POST "${CONNECT_URL}/connectors" \
            -H "Content-Type: application/json" \
            -d "${CLEAN_JSON}"
    )
    log "POST /connectors → HTTP ${http_code}"
    [[ "${http_code}" =~ ^2 ]] || die "Create failed with HTTP ${http_code}"
fi

# ---------------------------------------------------------------------------
# Step 4: Poll until connector and all tasks reach RUNNING
# ---------------------------------------------------------------------------
section "Step 4/4 — Wait for RUNNING status"

if [ "${DRY_RUN}" = "true" ]; then
    log "[dry-run] Skipping status poll"
    exit 0
fi

deadline=$(( $(date +%s) + STATUS_TIMEOUT ))

while true; do
    now=$(date +%s)
    [ "${now}" -lt "${deadline}" ] || die "Connector did not reach RUNNING within ${STATUS_TIMEOUT}s"

    STATUS_JSON=$(curl -sf "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status" 2>/dev/null || true)
    [ -n "${STATUS_JSON}" ] || { sleep "${POLL_INTERVAL}"; continue; }

    if command -v jq &>/dev/null; then
        CONN_STATE=$(echo "${STATUS_JSON}" | jq -r '.connector.state')
        TASK_STATES=$(echo "${STATUS_JSON}" | jq -r '.tasks[].state' 2>/dev/null || echo "PENDING")
        TASK_ERRORS=$(echo "${STATUS_JSON}" | jq -r '.tasks[] | select(.state == "FAILED") | .trace' 2>/dev/null || true)
    else
        CONN_STATE=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['connector']['state'])" <<< "${STATUS_JSON}")
        TASK_STATES="UNKNOWN"
    fi

    log "Connector state: ${CONN_STATE}  |  Task states: $(echo "${TASK_STATES}" | tr '\n' ' ')"

    if [ "${CONN_STATE}" = "FAILED" ]; then
        die "Connector FAILED. Check logs: docker logs kafka-connect"
    fi

    if [ -n "${TASK_ERRORS}" ]; then
        warn "One or more tasks FAILED. Error trace:"
        echo "${TASK_ERRORS}" | head -20
        die "Task failure detected — see above. Run: docker logs kafka-connect"
    fi

    all_running=true
    while IFS= read -r state; do
        [ "${state}" = "RUNNING" ] || { all_running=false; break; }
    done <<< "${TASK_STATES}"

    if [ "${CONN_STATE}" = "RUNNING" ] && [ "${all_running}" = "true" ]; then
        log "All tasks RUNNING ✓"
        break
    fi

    remaining=$(( deadline - $(date +%s) ))
    log "Waiting for tasks to start... (${remaining}s remaining)"
    sleep "${POLL_INTERVAL}"
done

# ---------------------------------------------------------------------------
# Final status display
# ---------------------------------------------------------------------------
section "Final Status"

FINAL_STATUS=$(curl -sf "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status")

if command -v jq &>/dev/null; then
    echo "${FINAL_STATUS}" | jq '{
        connector: .connector.state,
        tasks: [ .tasks[] | {id: .id, state: .state, worker: .worker_id} ],
        offsets: "run: curl -s '"${CONNECT_URL}"'/connectors/'"${CONNECTOR_NAME}"'/offsets | jq"
    }'
else
    echo "${FINAL_STATUS}"
fi

log ""
log "Connector '${CONNECTOR_NAME}' is RUNNING."
log ""
log "Useful commands:"
log "  Status  : curl -s ${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status | jq"
log "  Offsets : curl -s ${CONNECT_URL}/connectors/${CONNECTOR_NAME}/offsets | jq"
log "  Pause   : curl -X PUT ${CONNECT_URL}/connectors/${CONNECTOR_NAME}/pause"
log "  Resume  : curl -X PUT ${CONNECT_URL}/connectors/${CONNECTOR_NAME}/resume"
log "  Restart : curl -X POST ${CONNECT_URL}/connectors/${CONNECTOR_NAME}/restart"
log "  Delete  : curl -X DELETE ${CONNECT_URL}/connectors/${CONNECTOR_NAME}"
log ""
log "Iceberg table: warehouse.silver.events_clean"
log "  MinIO       : http://localhost:9001  → bucket: warehouse → silver/events_clean/"
log "  REST catalog: ${CATALOG_URL}/v1/namespaces/warehouse%1Fsilver/tables/events_clean"

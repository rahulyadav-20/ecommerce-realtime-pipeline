#!/usr/bin/env bash
# =============================================================================
# submit-druid-supervisor.sh — Submit (or update) the ecommerce_events Kafka
#                               Indexing Supervisor to Apache Druid.
#
# Usage:
#   ./scripts/submit-druid-supervisor.sh [DRUID_ROUTER_URL]
#
# Default DRUID_ROUTER_URL: http://localhost:8888
#
# What this script does:
#   1. Waits for the Druid Router to be reachable.
#   2. (Optional) Suspends an existing supervisor so offset state is preserved.
#   3. POSTs the ingestion spec — creates the supervisor if absent, replaces it
#      if it already exists (Druid upsert semantics on /supervisor endpoint).
#   4. Verifies the supervisor reached RUNNING status.
#   5. Prints the Druid console URL and a sample validation query.
#
# Re-running the script after a spec change is safe: Druid will apply the new
# config to the next set of tasks without losing consumer offsets.
# =============================================================================
set -euo pipefail

# ── Load environment config (APP_ENV=local|docker|prod) ───────────────────────
# shellcheck source=scripts/config.sh
source "$(dirname "$0")/config.sh"

ROUTER="${1:-${DRUID_REST_URL}}"
SPEC_FILE="$(cd "$(dirname "$0")/.." && pwd)/druid/ingestion-spec.json"
DATASOURCE="ecommerce_events"
SUPERVISOR_ID="$DATASOURCE"

# Colours
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v curl >/dev/null 2>&1 || error "curl is required."
[[ -f "$SPEC_FILE" ]]           || error "Spec file not found: $SPEC_FILE"

# Strip JSON comments (lines starting with "_comment") so Druid doesn't reject them.
# Druid's JSON parser is strict and does not accept non-standard comment keys,
# but it does ignore unknown fields — so _comment fields are actually fine.
info "Spec file: $SPEC_FILE"

# ── Wait for Druid Router ─────────────────────────────────────────────────────
info "Waiting for Druid Router at $ROUTER ..."
MAX_WAIT=120
WAITED=0
until curl -sf "$ROUTER/status" > /dev/null 2>&1; do
    (( WAITED += 5 ))
    if (( WAITED >= MAX_WAIT )); then
        error "Druid Router did not respond within ${MAX_WAIT}s. Is it running?
        Check: docker compose ps druid-router
        Logs:  docker compose logs druid-router"
    fi
    echo -n "."
    sleep 5
done
echo ""
info "Druid Router is reachable."

# ── Check Druid cluster health ────────────────────────────────────────────────
HEALTH=$(curl -sf "$ROUTER/status/health" 2>/dev/null || echo "unknown")
info "Cluster health: $HEALTH"

# ── Suspend existing supervisor if running (preserves offsets) ────────────────
EXISTING=$(curl -sf "$ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/status" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('payload',{}).get('state','NONE'))" \
    2>/dev/null || echo "NONE")

if [[ "$EXISTING" != "NONE" && "$EXISTING" != "" ]]; then
    warn "Existing supervisor found (state=$EXISTING). Suspending before update..."
    curl -sf -X POST "$ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/suspend" \
        -H "Content-Type: application/json" > /dev/null
    sleep 3
    info "Supervisor suspended."
fi

# ── Submit the ingestion spec ─────────────────────────────────────────────────
info "Submitting ingestion spec..."
RESPONSE=$(curl -sf -X POST "$ROUTER/druid/indexer/v1/supervisor" \
    -H "Content-Type: application/json" \
    -d @"$SPEC_FILE")

# Druid returns {"id":"<datasource>"} on success
SUPERVISOR_CREATED=$(echo "$RESPONSE" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('id','ERROR'))" 2>/dev/null \
    || echo "ERROR")

if [[ "$SUPERVISOR_CREATED" == "ERROR" || -z "$SUPERVISOR_CREATED" ]]; then
    error "Unexpected response from Druid:\n$RESPONSE"
fi
info "Supervisor created/updated: $SUPERVISOR_CREATED"

# ── Wait for supervisor to reach RUNNING state ────────────────────────────────
info "Waiting for supervisor to reach RUNNING state..."
MAX_WAIT_RUNNING=120
WAITED_RUNNING=0
while true; do
    STATE=$(curl -sf "$ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/status" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('payload',{}).get('state','UNKNOWN'))" \
        2>/dev/null || echo "UNKNOWN")

    case "$STATE" in
        RUNNING)
            info "Supervisor is RUNNING."
            break
            ;;
        SUSPENDED)
            info "Resuming suspended supervisor..."
            curl -sf -X POST "$ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/resume" \
                -H "Content-Type: application/json" > /dev/null
            ;;
        PENDING|CONNECTING|IDLE)
            echo -n "."
            ;;
        ERROR)
            error "Supervisor entered ERROR state. Check Druid console."
            ;;
    esac

    (( WAITED_RUNNING += 5 ))
    if (( WAITED_RUNNING >= MAX_WAIT_RUNNING )); then
        warn "Supervisor did not reach RUNNING within ${MAX_WAIT_RUNNING}s (current: $STATE)."
        warn "This is normal on first run — Druid needs time to allocate tasks."
        break
    fi
    sleep 5
done
echo ""

# ── Print active tasks ────────────────────────────────────────────────────────
info "=== Active Supervisor Tasks ==="
curl -sf "$ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/stats" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    tasks = d.get('payload', {}).get('activeTasks', [])
    print(f'  Active tasks: {len(tasks)}')
    for t in tasks[:4]:
        print(f'    {t.get(\"id\",\"?\")} → lag={t.get(\"lag\",\"?\")}')
except Exception:
    print('  (stats not yet available)')
" 2>/dev/null || echo "  (stats not yet available)"

# ── Validation instructions ───────────────────────────────────────────────────
echo ""
info "=== Validation ==="
echo ""
echo "  1. Druid Console"
echo "     $ROUTER/unified-console.html"
echo ""
echo "  2. Check supervisor"
echo "     curl $ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/status | python3 -m json.tool"
echo ""
echo "  3. Sample SQL query (run in Druid console → Query tab):"
echo ""
echo '     SELECT'
echo '       TIME_FLOOR(__time, '"'"'PT1M'"'"') AS minute,'
echo '       event_type,'
echo '       category,'
echo '       SUM(events)   AS event_count,'
echo '       SUM(qty)      AS total_qty,'
echo '       ROUND(SUM(revenue), 2) AS total_revenue,'
echo '       HLL_SKETCH_ESTIMATE(DS_HLL(uniq_users)) AS approx_users'
echo '     FROM ecommerce_events'
echo '     WHERE __time > CURRENT_TIMESTAMP - INTERVAL '"'"'1'"'"' HOUR'
echo '     GROUP BY 1, 2, 3'
echo '     ORDER BY 1 DESC, event_count DESC'
echo '     LIMIT 20'
echo ""
echo "  4. Count last hour:"
echo '     SELECT COUNT(*) FROM ecommerce_events'
echo '     WHERE __time > CURRENT_TIMESTAMP - INTERVAL '"'"'1'"'"' HOUR'
echo ""
echo "  5. Reset supervisor offsets (replay from earliest):"
echo "     curl -X POST $ROUTER/druid/indexer/v1/supervisor/$SUPERVISOR_ID/reset"
echo ""
info "Done. Supervisor '$SUPERVISOR_ID' submitted successfully."

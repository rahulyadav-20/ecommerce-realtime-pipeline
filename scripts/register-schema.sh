#!/usr/bin/env bash
# =============================================================================
# register-schema.sh — Register the ClickstreamEvent Avro schema with
#                       Confluent Schema Registry and verify the result.
#
# Usage:
#   ./scripts/register-schema.sh [SCHEMA_REGISTRY_URL]
#
# Defaults:
#   SCHEMA_REGISTRY_URL=http://localhost:8081
#
# The script registers the schema under two subjects:
#   ecommerce.events.raw.v1-value   (source topic)
#   ecommerce.events.clean.v1-value (hub / sink topic — same schema)
#
# Schema compatibility is set to FULL_TRANSITIVE before registration so that
# future schema changes are blocked at the registry level if they break either
# old producers or old consumers.
# =============================================================================
set -euo pipefail

SR_URL="${1:-${SCHEMA_REGISTRY_URL:-http://localhost:8081}}"
SCHEMA_FILE="$(dirname "$0")/../schemas/clickstream-event.avsc"

# Colours for output
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. Validate pre-conditions
# ---------------------------------------------------------------------------
command -v curl >/dev/null 2>&1 || error "curl is required but not installed."
command -v jq   >/dev/null 2>&1 || error "jq is required but not installed."

[[ -f "$SCHEMA_FILE" ]] || error "Schema file not found: $SCHEMA_FILE"

# ---------------------------------------------------------------------------
# 1. Wait for Schema Registry to be reachable
# ---------------------------------------------------------------------------
info "Waiting for Schema Registry at $SR_URL ..."
MAX_WAIT=60
WAITED=0
until curl -sf "$SR_URL/subjects" > /dev/null 2>&1; do
  if (( WAITED >= MAX_WAIT )); then
    error "Schema Registry did not respond within ${MAX_WAIT}s. Is it running?"
  fi
  sleep 5
  WAITED=$(( WAITED + 5 ))
done
info "Schema Registry is reachable."

# ---------------------------------------------------------------------------
# 2. Inline the .avsc file into a JSON Schema Registry payload
#    Schema Registry expects: {"schema": "<escaped-json-string>"}
# ---------------------------------------------------------------------------
SCHEMA_JSON=$(jq -Rs . < "$SCHEMA_FILE")   # escapes the whole file as a JSON string
PAYLOAD="{\"schemaType\": \"AVRO\", \"schema\": ${SCHEMA_JSON}}"

# ---------------------------------------------------------------------------
# 3. Set FULL_TRANSITIVE compatibility on both subjects before registering
#    This means: new schema must be compatible with ALL previous versions,
#    in both forward and backward directions.
# ---------------------------------------------------------------------------
for SUBJECT in \
    "ecommerce.events.raw.v1-value" \
    "ecommerce.events.clean.v1-value"; do

  info "Setting FULL_TRANSITIVE compatibility on subject: $SUBJECT"
  COMPAT_RESP=$(curl -sf -X PUT "$SR_URL/config/$SUBJECT" \
    -H "Content-Type: application/vnd.schemaregistry.v1+json" \
    -d '{"compatibility": "FULL_TRANSITIVE"}') || error "Failed to set compatibility for $SUBJECT"
  echo "  → $COMPAT_RESP"
done

# ---------------------------------------------------------------------------
# 4. Register the schema under each subject
# ---------------------------------------------------------------------------
for SUBJECT in \
    "ecommerce.events.raw.v1-value" \
    "ecommerce.events.clean.v1-value"; do

  info "Registering schema under subject: $SUBJECT"
  RESP=$(curl -sf -X POST "$SR_URL/subjects/$SUBJECT/versions" \
    -H "Content-Type: application/vnd.schemaregistry.v1+json" \
    -d "$PAYLOAD") || error "Failed to register schema for $SUBJECT"

  ID=$(echo "$RESP" | jq -r '.id // empty')
  if [[ -z "$ID" ]]; then
    error "Unexpected response from Schema Registry for $SUBJECT: $RESP"
  fi
  info "Registered $SUBJECT → schema id = $ID"
done

# ---------------------------------------------------------------------------
# 5. Verify — list subjects and fetch the registered schema
# ---------------------------------------------------------------------------
echo ""
info "=== Registered subjects ==="
curl -sf "$SR_URL/subjects" | jq -r '.[]' | sort

echo ""
info "=== Schema details (raw topic) ==="
curl -sf "$SR_URL/subjects/ecommerce.events.raw.v1-value/versions/latest" \
  | jq '{subject, version, id, schemaType}'

echo ""
info "=== Compatibility config ==="
curl -sf "$SR_URL/config/ecommerce.events.raw.v1-value" | jq .

echo ""
info "All schemas registered successfully. ✓"
echo ""
echo "  Subjects registered:"
echo "    • ecommerce.events.raw.v1-value"
echo "    • ecommerce.events.clean.v1-value"
echo ""
echo "  Validate:"
echo "    curl $SR_URL/subjects"
echo "    curl $SR_URL/subjects/ecommerce.events.raw.v1-value/versions/latest | jq ."

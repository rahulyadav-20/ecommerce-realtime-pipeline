#!/usr/bin/env bash
# =============================================================================
# scripts/config.sh — Shared configuration loader for bash scripts
# =============================================================================
#
# Source this file at the top of every bash script:
#   source "$(dirname "$0")/config.sh"
#
# Environment selection:
#   APP_ENV=local   (default) — localhost + exposed Docker ports
#   APP_ENV=docker             — internal Docker hostnames
#   APP_ENV=prod               — production endpoints
#
# Priority (highest wins):
#   1. Variables already set in the shell environment
#   2. config/${APP_ENV}.env file
#   3. Hard-coded defaults below
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ENV="${APP_ENV:-local}"
ENV_FILE="${PROJECT_ROOT}/config/${APP_ENV}.env"

# ── Load the .env file (if it exists) ─────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    # set -a exports every variable defined after this point
    # set +a restores the previous export behavior
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# ── Apply defaults for any variable not yet set ───────────────────────────────
# These mirror the defaults in config/settings.py so bash and Python agree.

# Kafka
: "${KAFKA_BOOTSTRAP_SERVERS:=localhost:9092}"
: "${SCHEMA_REGISTRY_URL:=http://localhost:8081}"
: "${KAFKA_RAW_TOPIC:=ecommerce.events.raw.v1}"
: "${KAFKA_CLEAN_TOPIC:=ecommerce.events.clean.v1}"
: "${KAFKA_DLQ_TOPIC:=dlq.events}"
: "${KAFKA_REPLICATION_FACTOR:=2}"
: "${KAFKA_CONSUMER_GROUP_PREFIX:=ecom-}"

# Flink
: "${FLINK_REST_URL:=http://localhost:8082}"
: "${FLINK_CHECKPOINT_DIR:=s3://flink-ckpt/ecom/}"
: "${FLINK_SAVEPOINT_DIR:=s3://flink-savepoints/ecom/}"
: "${FLINK_ETL_JOB_ID:=}"

# MinIO / S3
: "${MINIO_ENDPOINT:=http://localhost:9002}"
: "${MINIO_ACCESS_KEY:=minio}"
: "${MINIO_SECRET_KEY:=minio123}"
: "${MINIO_WAREHOUSE_BUCKET:=warehouse}"
: "${MINIO_FLINK_CKPT_BUCKET:=flink-ckpt}"
: "${MINIO_FLINK_SAVEPOINTS_BUCKET:=flink-savepoints}"

# ClickHouse
: "${CLICKHOUSE_URL:=http://localhost:8123}"
: "${CLICKHOUSE_USER:=default}"
: "${CLICKHOUSE_PASSWORD:=}"
: "${CLICKHOUSE_DATABASE:=ecom}"

# Iceberg REST Catalog
: "${ICEBERG_REST_URL:=http://localhost:8181}"
: "${ICEBERG_WAREHOUSE:=s3://warehouse/}"
: "${ICEBERG_EVENTS_TABLE:=warehouse.silver.events_clean}"

# Druid
: "${DRUID_REST_URL:=http://localhost:8888}"
: "${DRUID_DATASOURCE:=ecommerce_events}"

# Kafka Connect
: "${KAFKA_CONNECT_URL:=http://localhost:8083}"
: "${ICEBERG_PLUGIN_VERSION:=0.6.19}"
: "${ICEBERG_CONNECTOR_NAME:=iceberg-sink-events}"

# Notifications
: "${SLACK_WEBHOOK_URL:=}"

# ── Print active environment (useful for debugging) ───────────────────────────
_cfg_print_env() {
    echo "[config.sh] APP_ENV=${APP_ENV}  file=${ENV_FILE}"
    echo "  kafka   : ${KAFKA_BOOTSTRAP_SERVERS}"
    echo "  flink   : ${FLINK_REST_URL}"
    echo "  minio   : ${MINIO_ENDPOINT}"
    echo "  ch      : ${CLICKHOUSE_URL}"
    echo "  iceberg : ${ICEBERG_REST_URL}"
    echo "  druid   : ${DRUID_REST_URL}"
}

# Uncomment the line below to print config on every script startup:
# _cfg_print_env

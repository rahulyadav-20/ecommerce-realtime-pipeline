#!/usr/bin/env bash
# =============================================================================
# install-connectors.sh
# Installs Kafka Connect plugins before the worker starts.
#
# Called by the docker-compose command override:
#   command: ["bash", "/scripts/install-connectors.sh"]
# The script ends with `exec /etc/confluent/docker/run` so it replaces itself
# with the Connect worker process (correct PID-1 behaviour, signal forwarding).
#
# Environment variables (all optional — defaults shown):
#   ICEBERG_PLUGIN_VERSION   Tabular Iceberg connector version  (default: 0.6.19)
#   PLUGIN_DIR               Target directory for Confluent Hub  (default: /usr/share/confluent-hub-components)
#   CONNECT_SKIP_HUB_INSTALL Set to "true" to skip download      (default: false)
#                            Useful when building a custom image that pre-bakes plugins.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (override via docker-compose environment:)
# ---------------------------------------------------------------------------
ICEBERG_PLUGIN_VERSION="${ICEBERG_PLUGIN_VERSION:-0.6.19}"
PLUGIN_DIR="${PLUGIN_DIR:-/usr/share/confluent-hub-components}"
SKIP_INSTALL="${CONNECT_SKIP_HUB_INSTALL:-false}"

# Maven fallback coordinates (used when Confluent Hub is unreachable)
MAVEN_BASE="https://repo1.maven.org/maven2/io/tabular"
ICEBERG_RUNTIME_JAR="iceberg-kafka-connect-runtime-${ICEBERG_PLUGIN_VERSION}.jar"
ICEBERG_RUNTIME_URL="${MAVEN_BASE}/iceberg-kafka-connect-runtime/${ICEBERG_PLUGIN_VERSION}/${ICEBERG_RUNTIME_JAR}"

# Marker directory written by confluent-hub install — presence means already installed
ICEBERG_MARKER="${PLUGIN_DIR}/tabular-iceberg-kafka-connect"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()  { printf '\033[0;32m[install-connectors] %s\033[0m\n' "$*" >&2; }
warn() { printf '\033[0;33m[install-connectors] WARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[0;31m[install-connectors] ERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Retry helper  (retries n times with exponential back-off)
# Usage: retry <max_attempts> <command...>
# ---------------------------------------------------------------------------
retry() {
    local attempts="$1"; shift
    local delay=5
    local count=0
    until "$@"; do
        count=$((count + 1))
        if [ "$count" -ge "$attempts" ]; then
            die "Command failed after $attempts attempts: $*"
        fi
        warn "Attempt $count/$attempts failed — retrying in ${delay}s..."
        sleep "$delay"
        delay=$((delay * 2))   # exponential back-off: 5 → 10 → 20 → 40 s
    done
}

# ---------------------------------------------------------------------------
# Install via Confluent Hub (primary path)
# ---------------------------------------------------------------------------
install_from_hub() {
    local plugin="$1"
    log "Installing ${plugin} from Confluent Hub..."
    confluent-hub install \
        --no-prompt \
        --component-dir "${PLUGIN_DIR}" \
        --worker-configs /dev/null \
        "${plugin}"
}

# ---------------------------------------------------------------------------
# Install via direct Maven download (fallback path)
# Used when Confluent Hub is unreachable (airgapped envs, CI without internet).
# Downloads only the fat/runtime JAR; no manifest.json — Connect discovers it
# via the plugin.path scan of all JARs in PLUGIN_DIR subdirectories.
# ---------------------------------------------------------------------------
install_from_maven() {
    local jar_url="$1"
    local jar_name
    jar_name="$(basename "${jar_url}")"
    local target_dir="${PLUGIN_DIR}/tabular-iceberg-kafka-connect/lib"

    log "Falling back to direct Maven download..."
    log "  URL: ${jar_url}"

    mkdir -p "${target_dir}"
    retry 3 curl --fail --silent --show-error --location \
        --output "${target_dir}/${jar_name}" \
        "${jar_url}"

    log "Downloaded: ${target_dir}/${jar_name} ($(du -sh "${target_dir}/${jar_name}" | cut -f1))"
}

# ---------------------------------------------------------------------------
# Main install logic
# ---------------------------------------------------------------------------
if [ "${SKIP_INSTALL}" = "true" ]; then
    log "CONNECT_SKIP_HUB_INSTALL=true — skipping plugin installation"

elif [ -d "${ICEBERG_MARKER}" ]; then
    log "Iceberg plugin already installed at ${ICEBERG_MARKER} — skipping download"
    log "  (Delete the Docker volume or set CONNECT_SKIP_HUB_INSTALL=false to force reinstall)"

else
    log "=========================================================="
    log "Installing Tabular Iceberg Kafka Connect ${ICEBERG_PLUGIN_VERSION}"
    log "  Publisher : tabular"
    log "  Plugin    : iceberg-kafka-connect"
    log "  Target    : ${PLUGIN_DIR}"
    log "=========================================================="

    # Primary: Confluent Hub
    # The hub packages everything needed: connector JAR, Iceberg libraries,
    # AWS SDK v2 (for MinIO/S3 I/O), and Hadoop minimal runtime.
    if confluent-hub install \
            --no-prompt \
            --component-dir "${PLUGIN_DIR}" \
            --worker-configs /dev/null \
            "tabular/iceberg-kafka-connect:${ICEBERG_PLUGIN_VERSION}" 2>/dev/null; then
        log "Confluent Hub install succeeded"
    else
        warn "Confluent Hub unreachable or install failed — trying Maven fallback"
        install_from_maven "${ICEBERG_RUNTIME_URL}"
    fi
fi

# ---------------------------------------------------------------------------
# Diagnostics — show what's in the plugin directory
# ---------------------------------------------------------------------------
log "=== Plugin directory contents (${PLUGIN_DIR}) ==="
if [ -d "${PLUGIN_DIR}" ]; then
    ls -1 "${PLUGIN_DIR}" >&2 || true
else
    warn "Plugin directory does not exist: ${PLUGIN_DIR}"
fi

# ---------------------------------------------------------------------------
# Verify the Iceberg connector class is discoverable
# (Runs after the Connect worker starts its first discovery scan)
# ---------------------------------------------------------------------------
log "=== Iceberg connector JARs ==="
if [ -d "${ICEBERG_MARKER}" ]; then
    find "${ICEBERG_MARKER}" -name "*.jar" | sort >&2 || true
else
    warn "Iceberg marker dir not found — plugin may not be loaded correctly"
fi

# ---------------------------------------------------------------------------
# Hand off to the standard Kafka Connect entrypoint
# `exec` replaces this process so the Connect worker becomes PID 1,
# ensuring SIGTERM from Docker stops the worker gracefully.
# ---------------------------------------------------------------------------
log "Handing off to Kafka Connect worker..."
exec /etc/confluent/docker/run

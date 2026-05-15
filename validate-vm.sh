#!/usr/bin/env bash
# ==============================================================================
# validate-vm.sh — Post-setup validation for the E-Commerce Pipeline VM
# ==============================================================================
#
# Verifies every prerequisite installed by setup-vm.sh is correctly configured.
# Safe to run at any time — read-only, no changes made.
#
# Usage:
#   bash validate-vm.sh [--json] [--quiet]
#
# Output:
#   --json    Machine-readable JSON output
#   --quiet   Only print FAIL lines (useful for CI / monitoring)
# ==============================================================================
set -uo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── Flags ─────────────────────────────────────────────────────────────────────
JSON_OUT=false
QUIET=false
[[ "${1:-}" == "--json"  ]] && JSON_OUT=true
[[ "${1:-}" == "--quiet" ]] && QUIET=true

# ── State ─────────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN_COUNT=0
declare -a RESULTS=()

# ── Helpers ───────────────────────────────────────────────────────────────────
pass() {
  ((PASS++))
  RESULTS+=("{\"status\":\"pass\",\"check\":\"$1\",\"value\":\"${2:-ok}\"}")
  [[ "$QUIET" == "false" ]] && echo -e "  ${GREEN}✓${NC}  $1${2:+  ${BLUE}($2)${NC}}"
}

fail() {
  ((FAIL++))
  RESULTS+=("{\"status\":\"fail\",\"check\":\"$1\",\"expected\":\"${2:-}\",\"actual\":\"${3:-}\"}")
  echo -e "  ${RED}✗${NC}  $1${2:+  — expected: ${YELLOW}$2${NC}${3:+, got: ${RED}$3${NC}}}"
}

warn() {
  ((WARN_COUNT++))
  RESULTS+=("{\"status\":\"warn\",\"check\":\"$1\",\"note\":\"$2\"}")
  [[ "$QUIET" == "false" ]] && echo -e "  ${YELLOW}⚠${NC}  $1  ${YELLOW}($2)${NC}"
}

section() {
  [[ "$QUIET" == "false" ]] && echo -e "\n${CYAN}${BOLD}── $* ─────────────────────────${NC}"
}

# Get a sysctl value
sc() { sysctl -n "$1" 2>/dev/null || echo "N/A"; }

# ══════════════════════════════════════════════════════════════════════════════
# CHECKS
# ══════════════════════════════════════════════════════════════════════════════

[[ "$QUIET" == "false" ]] && {
  echo -e "\n${CYAN}${BOLD}══ VM Validation — $(date) ══${NC}\n"
}

# ── OS ────────────────────────────────────────────────────────────────────────
section "Operating System"
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "$ID" == "ubuntu" && "$VERSION_ID" == "22.04" ]]; then
    pass "Ubuntu 22.04 LTS" "$PRETTY_NAME"
  else
    warn "OS version" "Expected Ubuntu 22.04, found: ${PRETTY_NAME:-unknown}"
  fi
fi

# Kernel version
KERNEL=$(uname -r)
pass "Kernel" "$KERNEL"

# ── Docker ────────────────────────────────────────────────────────────────────
section "Docker"

if command -v docker &>/dev/null; then
  DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
  pass "Docker Engine" "$DOCKER_VER"
else
  fail "Docker Engine" "installed" "not found"
fi

if systemctl is-active docker &>/dev/null; then
  pass "Docker daemon" "active"
else
  fail "Docker daemon" "active" "$(systemctl is-active docker 2>/dev/null)"
fi

if docker compose version &>/dev/null 2>&1; then
  DC_VER=$(docker compose version 2>/dev/null | awk '{print $4}')
  pass "Docker Compose V2" "$DC_VER"
else
  fail "Docker Compose V2" "installed" "not found"
fi

DOCKER_LIVE=$(docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null || echo "false")
[[ "$DOCKER_LIVE" == "true" ]] && pass "Docker live-restore" "enabled" \
                                || fail "Docker live-restore" "true" "$DOCKER_LIVE"

DOCKER_STORAGE=$(docker info --format '{{.Driver}}' 2>/dev/null || echo "unknown")
[[ "$DOCKER_STORAGE" == "overlay2" ]] && pass "Docker storage driver" "overlay2" \
                                       || warn "Docker storage driver" "expected overlay2, got $DOCKER_STORAGE"

# ── Kernel parameters ─────────────────────────────────────────────────────────
section "Kernel Parameters (sysctl)"

check_sysctl() {
  local key="$1" expected="$2"
  local actual; actual=$(sc "$key")
  if [[ "$actual" -ge "$expected" ]] 2>/dev/null; then
    pass "$key" "$actual"
  else
    fail "$key" ">= $expected" "$actual"
  fi
}

check_sysctl "vm.max_map_count"          262144
check_sysctl "net.core.somaxconn"        4096
check_sysctl "fs.file-max"               2097152
check_sysctl "fs.inotify.max_user_watches" 524288
check_sysctl "net.core.rmem_max"         16777216
check_sysctl "net.core.wmem_max"         16777216

SWAPPINESS=$(sc vm.swappiness)
[[ "$SWAPPINESS" -le 1 ]] && pass "vm.swappiness" "$SWAPPINESS (≤1)" \
                           || warn "vm.swappiness" "$SWAPPINESS — recommend ≤1 for Kafka/Druid"

IP_FWD=$(sc net.ipv4.ip_forward)
[[ "$IP_FWD" == "1" ]] && pass "net.ipv4.ip_forward" "1 (Docker networking OK)" \
                        || fail "net.ipv4.ip_forward" "1" "$IP_FWD"

# ── File descriptors ──────────────────────────────────────────────────────────
section "File Descriptors"

SOFT_NOFILE=$(ulimit -Sn 2>/dev/null || echo "0")
HARD_NOFILE=$(ulimit -Hn 2>/dev/null || echo "0")

[[ "$SOFT_NOFILE" -ge 65535 ]] 2>/dev/null \
  && pass "ulimit -Sn (soft)" "$SOFT_NOFILE" \
  || fail "ulimit -Sn (soft)" ">= 65535" "$SOFT_NOFILE"

[[ "$HARD_NOFILE" -ge 65535 ]] 2>/dev/null \
  && pass "ulimit -Hn (hard)" "$HARD_NOFILE" \
  || fail "ulimit -Hn (hard)" ">= 65535" "$HARD_NOFILE"

[[ -f /etc/security/limits.d/99-ecom-pipeline.conf ]] \
  && pass "limits.d config file" "/etc/security/limits.d/99-ecom-pipeline.conf" \
  || fail "limits.d config file" "present" "missing"

# ── Swap ──────────────────────────────────────────────────────────────────────
section "Swap"

SWAP_TOTAL=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if [[ "$SWAP_TOTAL" -eq 0 ]]; then
  pass "Swap" "disabled"
else
  warn "Swap" "${SWAP_TOTAL} kB active — disable for Kafka/Druid performance"
fi

# Check /etc/fstab
if grep -qE '\bswap\b' /etc/fstab 2>/dev/null; then
  fail "fstab swap entry" "absent" "present — swap will re-enable on reboot"
else
  pass "fstab swap entry" "not present (will stay off after reboot)"
fi

# ── System timezone ───────────────────────────────────────────────────────────
section "Timezone & Time"

TZ_ACTIVE=$(timedatectl show --property=Timezone --value 2>/dev/null || date +%Z)
pass "Timezone" "$TZ_ACTIVE"

NTP_STATUS=$(timedatectl show --property=NTPSynchronized --value 2>/dev/null || echo "unknown")
[[ "$NTP_STATUS" == "yes" ]] && pass "NTP sync" "yes" \
                              || warn "NTP sync" "not synced — run: timedatectl set-ntp true"

# ── App user ──────────────────────────────────────────────────────────────────
section "App User"

APP_USER="${ECOM_USER:-ecomuser}"
if id "$APP_USER" &>/dev/null; then
  pass "User '$APP_USER'" "exists"
  id -nG "$APP_USER" | grep -qw docker \
    && pass "User in docker group" "yes" \
    || fail "User in docker group" "yes" "no — run: usermod -aG docker $APP_USER"
else
  fail "User '$APP_USER'" "exists" "not found"
fi

# ── Directories ───────────────────────────────────────────────────────────────
section "Directories"

for dir in "/opt/ecom-pipeline" "/data" "/logs"; do
  if [[ -d "$dir" ]]; then
    OWNER=$(stat -c '%U' "$dir" 2>/dev/null || echo "?")
    pass "Directory $dir" "exists (owner: $OWNER)"
  else
    fail "Directory $dir" "exists" "missing"
  fi
done

# ── System utilities ──────────────────────────────────────────────────────────
section "System Utilities"

for pkg in htop iotop nethogs jq vim git curl wget rsync; do
  command -v "$pkg" &>/dev/null \
    && pass "Command: $pkg" "$(command -v "$pkg")" \
    || fail "Command: $pkg" "installed" "not found"
done

# ── Resources ─────────────────────────────────────────────────────────────────
section "System Resources"

CPU_CORES=$(nproc)
MEM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
DISK_FREE=$(df -BG / | awk 'NR==2 {gsub("G",""); print $4}')

[[ "$CPU_CORES" -ge 4 ]] && pass "CPU cores" "$CPU_CORES" \
                          || warn "CPU cores" "$CPU_CORES — 4+ recommended"

[[ "$MEM_GB" -ge 12 ]] && pass "RAM" "${MEM_GB} GB" \
                        || warn "RAM" "${MEM_GB} GB — 12 GB recommended for full stack"

[[ "$DISK_FREE" -ge 20 ]] && pass "Free disk (/))" "${DISK_FREE} GB" \
                           || warn "Free disk (/)" "${DISK_FREE} GB — 20 GB recommended"

# ── Docker Swarm ──────────────────────────────────────────────────────────────
section "Docker Swarm"
SWARM_STATE=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo "unknown")
if [[ "$SWARM_STATE" == "active" ]]; then
  SWARM_ROLE=$(docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null && echo "manager" || echo "worker")
  pass "Docker Swarm" "$SWARM_STATE ($SWARM_ROLE)"
else
  warn "Docker Swarm" "inactive — run setup-vm.sh --prod to initialise"
fi

# ── systemd service ───────────────────────────────────────────────────────────
section "Systemd Service"
if systemctl list-unit-files ecom-pipeline.service &>/dev/null 2>&1 | grep -q ecom-pipeline; then
  SVC_STATE=$(systemctl is-enabled ecom-pipeline.service 2>/dev/null || echo "disabled")
  pass "ecom-pipeline.service" "registered ($SVC_STATE)"
else
  fail "ecom-pipeline.service" "registered" "not found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

echo ""
if [[ "$JSON_OUT" == "true" ]]; then
  printf '{"timestamp":"%s","pass":%d,"fail":%d,"warn":%d,"checks":[' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PASS" "$FAIL" "$WARN_COUNT"
  IFS=','
  echo "${RESULTS[*]}]}"
  IFS=$'\n\t'
else
  echo -e "${BOLD}═══════════════════════════════════════════${NC}"
  echo -e "  ${GREEN}${BOLD}PASS${NC}  ${PASS}"
  echo -e "  ${RED}${BOLD}FAIL${NC}  ${FAIL}"
  echo -e "  ${YELLOW}${BOLD}WARN${NC}  ${WARN_COUNT}"
  echo -e "${BOLD}═══════════════════════════════════════════${NC}"

  if [[ $FAIL -eq 0 ]]; then
    echo -e "\n  ${GREEN}${BOLD}✓ VM is ready for the pipeline!${NC}"
    echo -e "  Run:  ${CYAN}cd /opt/ecom-pipeline && bash setup.sh${NC}\n"
  else
    echo -e "\n  ${RED}${BOLD}✗ ${FAIL} check(s) failed — fix before deploying.${NC}\n"
  fi
fi

exit $FAIL

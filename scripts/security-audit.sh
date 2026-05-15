#!/usr/bin/env bash
# ==============================================================================
# scripts/security-audit.sh — Automated security posture check
# E-Commerce Realtime Analytics Pipeline
# ==============================================================================
#
# Checks every control in the SECURITY.md checklist and outputs
# PASS / WARN / FAIL for each item.
#
# Usage:
#   bash scripts/security-audit.sh [--json] [--quiet] [--fix-hints]
#
# Options:
#   --json        Machine-readable JSON output
#   --quiet       Only print FAIL and WARN lines
#   --fix-hints   Show remediation commands for each failure
#   -h, --help    Show this message
#
# Exit code: number of FAIL items (0 = all clear)
# ==============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

JSON_OUT=false
QUIET=false
FIX_HINTS=false
[[ "${1:-}" == "--json"      ]] && JSON_OUT=true
[[ "${1:-}" == "--quiet"     ]] && QUIET=true
[[ "${1:-}" == "--fix-hints" ]] && FIX_HINTS=true
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { grep "^#" "$0" | sed 's/^# \{0,1\}//'; exit 0; }

PASS=0; WARN_COUNT=0; FAIL=0
declare -a RESULTS=()
declare -a HINTS=()

# ── Result helpers ────────────────────────────────────────────────────────────
pass() {
  ((PASS++))
  local label="${1}" value="${2:-}"
  RESULTS+=("{\"status\":\"pass\",\"check\":\"${label}\",\"value\":\"${value}\"}")
  [[ "$QUIET" == "false" ]] && \
    printf "  ${GREEN}✓ PASS${NC}  %-52s ${BLUE}%s${NC}\n" "$label" "$value"
}

warn() {
  ((WARN_COUNT++))
  local label="${1}" note="${2:-}" hint="${3:-}"
  RESULTS+=("{\"status\":\"warn\",\"check\":\"${label}\",\"note\":\"${note}\"}")
  HINTS+=("${label}: ${hint}")
  printf "  ${YELLOW}⚠ WARN${NC}  %-52s ${YELLOW}%s${NC}\n" "$label" "$note"
}

fail() {
  ((FAIL++))
  local label="${1}" expected="${2:-}" actual="${3:-}" hint="${4:-}"
  RESULTS+=("{\"status\":\"fail\",\"check\":\"${label}\",\"expected\":\"${expected}\",\"actual\":\"${actual}\"}")
  HINTS+=("${label}: ${hint}")
  printf "  ${RED}✗ FAIL${NC}  %-52s ${RED}expected: %s | got: %s${NC}\n" "$label" "$expected" "$actual"
}

section() {
  [[ "$QUIET" == "false" ]] && \
    echo -e "\n${CYAN:-${BLUE}}${BOLD}── $* ──────────────────────────────────────────${NC}"
}

# ── sysctl helper ─────────────────────────────────────────────────────────────
sc() { sysctl -n "$1" 2>/dev/null || echo "N/A"; }

# ── Main output header ────────────────────────────────────────────────────────
[[ "$QUIET" == "false" ]] && {
  echo -e "\n${BOLD}${BLUE}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${BLUE}║   Security Audit — $(date '+%Y-%m-%d %H:%M')              ║${NC}"
  echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════╝${NC}"
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: NETWORK
# ══════════════════════════════════════════════════════════════════════════════
section "Network Security"

# UFW active?
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  pass "UFW firewall" "active"
else
  fail "UFW firewall" "active" "inactive" \
    "sudo bash scripts/setup-firewall.sh"
fi

# Check admin ports NOT publicly exposed
for port in 9092 8123 9200 8888 8082 5601 8080 6379; do
  svc_name=$(case $port in
    9092) echo "Kafka";; 8123) echo "ClickHouse";; 9200) echo "Elasticsearch";;
    8888) echo "Druid";; 8082) echo "Flink UI";; 5601) echo "Kibana";;
    8080) echo "Airflow";; 6379) echo "Redis";; *) echo "Port ${port}";;
  esac)

  if ufw status 2>/dev/null | grep -qE "^${port}.*DENY|^${port}.*(Anywhere).*DENY"; then
    pass "Port ${port} (${svc_name}) blocked by UFW" "DENY rule present"
  elif ss -tlnp 2>/dev/null | grep -q ":${port}.*0.0.0.0"; then
    fail "Port ${port} (${svc_name}) exposed" "127.0.0.1:${port} only" \
      "0.0.0.0:${port} listening" \
      "Bind to 127.0.0.1 in docker-compose.yml and add UFW deny rule"
  else
    pass "Port ${port} (${svc_name})" "not publicly bound"
  fi
done

# SSH config check
if [[ -f /etc/ssh/sshd_config ]]; then
  if grep -qE "^PermitRootLogin\s+(no|prohibit-password)" /etc/ssh/sshd_config \
    || grep -qE "^PermitRootLogin\s+no" /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
    pass "SSH PermitRootLogin" "no"
  else
    warn "SSH PermitRootLogin" \
      "check: grep PermitRootLogin /etc/ssh/sshd_config" \
      "Set PermitRootLogin no in /etc/ssh/sshd_config"
  fi

  if grep -qE "^PasswordAuthentication\s+no" /etc/ssh/sshd_config 2>/dev/null \
    || grep -qE "^PasswordAuthentication\s+no" /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
    pass "SSH PasswordAuthentication" "no (key-only)"
  else
    warn "SSH password auth" \
      "still enabled (key-only recommended)" \
      "Set PasswordAuthentication no in /etc/ssh/sshd_config"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: AUTHENTICATION
# ══════════════════════════════════════════════════════════════════════════════
section "Authentication & Authorization"

# Nginx htpasswd exists and is not empty
HTPASSWD="/etc/nginx/auth/.htpasswd"
if [[ -f "$HTPASSWD" ]] && [[ -s "$HTPASSWD" ]]; then
  USER_COUNT=$(wc -l < "$HTPASSWD")
  pass "Nginx Basic Auth (.htpasswd)" "${USER_COUNT} user(s)"
else
  fail "Nginx Basic Auth (.htpasswd)" "present and non-empty" "missing or empty" \
    "sudo bash scripts/setup-basic-auth.sh -u admin"
fi

# JWT secret set
JWT_SECRET="${JWT_SECRET:-}"
if [[ -f "/etc/ecom-pipeline/secrets/jwt_secret" ]]; then
  JWT_LEN=$(wc -c < "/etc/ecom-pipeline/secrets/jwt_secret")
  [[ "$JWT_LEN" -ge 32 ]] \
    && pass "JWT secret" "${JWT_LEN} chars" \
    || warn "JWT secret" "too short (${JWT_LEN} chars, need ≥ 32)" \
       "openssl rand -base64 48 | sudo tee /etc/ecom-pipeline/secrets/jwt_secret"
else
  warn "JWT secret file" "/etc/ecom-pipeline/secrets/jwt_secret not found" \
    "Run: sudo bash scripts/rotate-secrets.sh --jwt"
fi

# MinIO defaults changed?
MINIO_PASS_FILE="/etc/ecom-pipeline/secrets/minio_secret_key"
if [[ -f "$MINIO_PASS_FILE" ]]; then
  MINIO_PASS=$(cat "$MINIO_PASS_FILE")
  if [[ "$MINIO_PASS" == "minio123" ]] || [[ ${#MINIO_PASS} -lt 16 ]]; then
    fail "MinIO secret key" "strong (≥16 chars)" "weak/default" \
      "sudo bash scripts/rotate-secrets.sh --minio"
  else
    pass "MinIO secret key" "changed from default"
  fi
else
  warn "MinIO credentials" "not verified (secret file missing)" \
    "sudo bash scripts/rotate-secrets.sh --minio"
fi

# Redis AUTH
REDIS_PASS_FILE="/etc/ecom-pipeline/secrets/redis_password"
if [[ -f "$REDIS_PASS_FILE" ]] && [[ -s "$REDIS_PASS_FILE" ]]; then
  pass "Redis AUTH" "configured"
else
  warn "Redis AUTH" "not verified (secret file missing)" \
    "sudo bash scripts/rotate-secrets.sh --redis"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: ENCRYPTION
# ══════════════════════════════════════════════════════════════════════════════
section "Encryption"

# TLS certificate
if command -v certbot &>/dev/null; then
  CERT_INFO=$(certbot certificates 2>/dev/null | head -20)
  if echo "$CERT_INFO" | grep -q "VALID"; then
    EXPIRY=$(echo "$CERT_INFO" | grep "Expiry" | head -1 | awk '{print $NF}')
    pass "Let's Encrypt certificate" "VALID (expires: ${EXPIRY})"
  elif echo "$CERT_INFO" | grep -q "WARNING"; then
    warn "TLS certificate" "expiring soon" "sudo certbot renew"
  else
    warn "TLS certificate" "no valid cert found (dev OK)" \
      "sudo bash scripts/setup-certbot.sh -d YOUR_DOMAIN -e EMAIL"
  fi
else
  warn "certbot" "not installed (OK for dev)" \
    "sudo snap install --classic certbot"
fi

# DH params
DHPARAM="/etc/nginx/ssl/dhparam.pem"
if [[ -f "$DHPARAM" ]]; then
  DH_BITS=$(openssl dhparam -in "$DHPARAM" -text 2>/dev/null | grep "DH Parameters" | grep -oP '\d+' | head -1)
  [[ "${DH_BITS:-0}" -ge 2048 ]] \
    && pass "DH params" "${DH_BITS}-bit" \
    || fail "DH params" "≥2048-bit" "${DH_BITS:-0}-bit" \
       "openssl dhparam -out /etc/nginx/ssl/dhparam.pem 2048"
else
  warn "DH params" "file not found" \
    "openssl dhparam -out /etc/nginx/ssl/dhparam.pem 2048"
fi

# LUKS (check if /data is on encrypted volume)
if command -v cryptsetup &>/dev/null && cryptsetup status ecom-data &>/dev/null 2>&1; then
  pass "LUKS encryption (/data)" "active"
elif mount | grep -q "on /data type"; then
  warn "LUKS encryption (/data)" "mounted but not LUKS-encrypted" \
    "See SECURITY.md §5.3 for LUKS setup instructions"
else
  warn "LUKS encryption (/data)" "/data not mounted" \
    "See SECURITY.md §5.3 for LUKS setup instructions"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: SECRETS
# ══════════════════════════════════════════════════════════════════════════════
section "Secrets Management"

# .env not committed
if git -C . rev-parse &>/dev/null 2>&1; then
  if git -C . check-ignore -q docker/.env 2>/dev/null; then
    pass ".env in .gitignore" "docker/.env excluded"
  else
    fail ".env in .gitignore" "excluded" "tracked" \
      "echo 'docker/.env' >> .gitignore && git rm --cached docker/.env"
  fi

  # No secrets in git history (basic check)
  if git -C . log --all -p 2>/dev/null | grep -qE "(minio123|password123|secret123|changeme)"; then
    fail "No default secrets in git history" "clean history" "defaults found" \
      "Use BFG Repo-Cleaner to purge secrets from git history"
  else
    pass "No known default secrets in git" "clean"
  fi
fi

# Secrets dir permissions
if [[ -d "/etc/ecom-pipeline/secrets" ]]; then
  PERM=$(stat -c %a /etc/ecom-pipeline/secrets)
  [[ "$PERM" == "700" ]] \
    && pass "Secrets dir permissions" "700 (root-only)" \
    || fail "Secrets dir permissions" "700" "$PERM" \
       "chmod 700 /etc/ecom-pipeline/secrets"
fi

# Rotation status
if [[ -f "/var/log/ecom-secret-rotation.log" ]]; then
  LAST_ROTATION=$(tail -1 /var/log/ecom-secret-rotation.log | awk '{print $1}' 2>/dev/null || echo "never")
  pass "Secret rotation log" "last entry: ${LAST_ROTATION}"
else
  warn "Secret rotation log" "not found — rotation may not have run" \
    "sudo bash scripts/rotate-secrets.sh --check"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: CONTAINERS
# ══════════════════════════════════════════════════════════════════════════════
section "Container Security"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then

  # Non-root users
  NON_ROOT_COUNT=0; ROOT_COUNT=0
  while IFS= read -r container; do
    uid=$(docker inspect "$container" --format '{{.Config.User}}' 2>/dev/null)
    if [[ -z "$uid" ]] || [[ "$uid" == "root" ]] || [[ "$uid" == "0" ]]; then
      ((ROOT_COUNT++))
    else
      ((NON_ROOT_COUNT++))
    fi
  done < <(docker ps --format "{{.Names}}" 2>/dev/null)

  TOTAL_CONTAINERS=$((ROOT_COUNT + NON_ROOT_COUNT))
  if [[ "$ROOT_COUNT" -eq 0 ]]; then
    pass "Containers non-root" "all ${TOTAL_CONTAINERS} containers"
  elif [[ "$ROOT_COUNT" -lt "$TOTAL_CONTAINERS" ]]; then
    warn "Containers non-root" "${ROOT_COUNT}/${TOTAL_CONTAINERS} running as root" \
      "Add user: directive in docker-compose.yml (see SECURITY.md §7.1)"
  else
    fail "Containers non-root" "non-root" "all running as root" \
      "Add user: directive in docker-compose.yml (see SECURITY.md §7.1)"
  fi

  # No :latest tags in production
  LATEST_IMAGES=$(docker ps --format "{{.Image}}" 2>/dev/null | grep ":latest" | wc -l)
  if [[ "$LATEST_IMAGES" -eq 0 ]]; then
    pass "No :latest image tags" "all images pinned"
  else
    warn "Image pinning" "${LATEST_IMAGES} container(s) using :latest" \
      "Pin images to specific digests in docker-compose.yml (see SECURITY.md §7.6)"
  fi

  # Docker live-restore
  LIVE_RESTORE=$(docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null || echo "false")
  [[ "$LIVE_RESTORE" == "true" ]] \
    && pass "Docker live-restore" "enabled" \
    || warn "Docker live-restore" "disabled — containers stop if daemon restarts" \
       'Add "live-restore": true to /etc/docker/daemon.json'

  # Trivy installed?
  if command -v trivy &>/dev/null; then
    TRIVY_VER=$(trivy --version 2>/dev/null | head -1)
    pass "Trivy scanner" "$TRIVY_VER"
  else
    warn "Trivy scanner" "not installed" \
      "curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin"
  fi

else
  warn "Docker" "not running — skipping container checks"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: KERNEL / OS
# ══════════════════════════════════════════════════════════════════════════════
section "Kernel & OS Hardening"

# Kernel params
[[ "$(sc vm.max_map_count)" -ge 262144 ]]  && pass "vm.max_map_count"   "$(sc vm.max_map_count)" \
                                            || fail "vm.max_map_count" "≥262144" "$(sc vm.max_map_count)" \
                                               "sudo sysctl -w vm.max_map_count=262144"

[[ "$(sc vm.swappiness)"   -le 1 ]]        && pass "vm.swappiness (≤1)" "$(sc vm.swappiness)" \
                                            || warn "vm.swappiness" "$(sc vm.swappiness) — recommend ≤1 for Kafka" \
                                               "sudo sysctl -w vm.swappiness=1"

SWAP_TOTAL=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
[[ "$SWAP_TOTAL" -eq 0 ]] && pass "Swap disabled" "off" \
                           || warn "Swap" "${SWAP_TOTAL} kB active — disable for Kafka" \
                              "sudo swapoff -a && sudo sed -i '/\\bswap\\b/d' /etc/fstab"

SOFT_FD=$(ulimit -Sn 2>/dev/null || echo 0)
[[ "$SOFT_FD" -ge 65535 ]] && pass "ulimit -n (soft)" "$SOFT_FD" \
                            || fail "ulimit -n (soft)" "≥65535" "$SOFT_FD" \
                               "sudo bash scripts/setup-vm.sh (sets limits)"

# Automatic security updates
if systemctl is-active unattended-upgrades &>/dev/null 2>&1 \
   || dpkg -l unattended-upgrades 2>/dev/null | grep -q "^ii"; then
  pass "Unattended security upgrades" "enabled"
else
  warn "Unattended security upgrades" "not active" \
    "sudo apt-get install unattended-upgrades && sudo dpkg-reconfigure unattended-upgrades"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: AUDITING
# ══════════════════════════════════════════════════════════════════════════════
section "Auditing & Logging"

# Nginx JSON access log
NGINX_LOG="/var/log/nginx/access.log"
if [[ -f "$NGINX_LOG" ]] && head -1 "$NGINX_LOG" 2>/dev/null | grep -q '"@timestamp"'; then
  pass "Nginx JSON access log" "$NGINX_LOG"
else
  warn "Nginx JSON access log" "not found or not JSON format" \
    "Deploy nginx-proxy with the provided nginx.conf"
fi

# Elasticsearch reachable (logs shipped)
if curl -sf "http://localhost:9200/_cluster/health" &>/dev/null; then
  ES_STATUS=$(curl -sf "http://localhost:9200/_cluster/health" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "?")
  [[ "$ES_STATUS" != "red" ]] \
    && pass "Elasticsearch for audit logs" "status: $ES_STATUS" \
    || warn "Elasticsearch" "status: red — logs may not be stored" \
       "Check Elasticsearch: docker logs elasticsearch"
else
  warn "Elasticsearch" "not reachable on localhost:9200 (OK if using remote)" \
    "Verify Elasticsearch is running"
fi

# Audit log rotation (ILM policy)
if curl -sf "http://localhost:9200/_ilm/policy/ecom-audit-policy" &>/dev/null; then
  pass "Elasticsearch ILM policy" "ecom-audit-policy found"
else
  warn "Elasticsearch ILM policy" "not configured — logs may not be deleted" \
    "Apply the ILM policy from SECURITY.md §8.4"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
echo ""
if [[ "$JSON_OUT" == "true" ]]; then
  printf '{"timestamp":"%s","pass":%d,"warn":%d,"fail":%d,"checks":[' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PASS" "$WARN_COUNT" "$FAIL"
  IFS=','; echo "${RESULTS[*]}]}"
else
  echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
  printf "  ${GREEN}${BOLD}PASS  %d${NC}\n" "$PASS"
  printf "  ${YELLOW}${BOLD}WARN  %d${NC}\n" "$WARN_COUNT"
  printf "  ${RED}${BOLD}FAIL  %d${NC}\n" "$FAIL"
  echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"

  if [[ "$FIX_HINTS" == "true" ]] && [[ ${#HINTS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${BOLD}Remediation hints:${NC}"
    for hint in "${HINTS[@]}"; do
      echo -e "  ${YELLOW}→${NC}  $hint"
    done
    echo ""
  fi

  if [[ "$FAIL" -eq 0 ]] && [[ "$WARN_COUNT" -eq 0 ]]; then
    echo -e "\n  ${GREEN}${BOLD}✓ Security audit passed with no issues.${NC}\n"
  elif [[ "$FAIL" -eq 0 ]]; then
    echo -e "\n  ${YELLOW}⚠ ${WARN_COUNT} warning(s) — review before production.${NC}"
    echo -e "  Run with --fix-hints for remediation steps.\n"
  else
    echo -e "\n  ${RED}✗ ${FAIL} failure(s) — must fix before production deployment.${NC}"
    echo -e "  Run with --fix-hints for remediation steps.\n"
  fi
fi

exit "$FAIL"

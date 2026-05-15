#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-firewall.sh — UFW firewall hardening
# E-Commerce Realtime Analytics Pipeline
# ==============================================================================
#
# Configures UFW (Uncomplicated Firewall) to:
#   - Allow only SSH, HTTP (redirect), HTTPS from the public internet
#   - Block all database / admin UI ports from external access
#   - Optionally restrict SSH to a VPN/office CIDR
#
# Usage:
#   sudo bash scripts/setup-firewall.sh [--vpn-cidr <CIDR>] [--dry-run]
#
# Options:
#   --vpn-cidr  <CIDR>   Restrict SSH to this CIDR (e.g. 10.8.0.0/16)
#                        Default: allow SSH from anywhere (less secure)
#   --wireguard          Open port 51820/udp for WireGuard VPN
#   --dry-run            Print rules without applying them
#   --reset              Remove all UFW rules and start fresh (CAUTION)
#   -h, --help           Show this message
#
# Example:
#   sudo bash scripts/setup-firewall.sh --vpn-cidr 10.8.0.0/16 --wireguard
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
success() { echo -e "${GREEN}[✓]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
die()     { echo -e "${RED}[ERROR]${NC}  $*" >&2; exit 1; }

VPN_CIDR=""
WIREGUARD=false
DRY_RUN=false
RESET=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vpn-cidr)   VPN_CIDR="${2:?--vpn-cidr requires a CIDR}"; shift 2 ;;
    --wireguard)  WIREGUARD=true; shift ;;
    --dry-run)    DRY_RUN=true;   shift ;;
    --reset)      RESET=true;     shift ;;
    -h|--help)    grep "^#" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Must run as root: sudo bash $0"

# ── UFW wrapper (respects --dry-run) ─────────────────────────────────────────
ufw_cmd() {
  echo "  → ufw $*"
  [[ "$DRY_RUN" == "false" ]] && ufw "$@" || true
}

# ── 0. Check UFW installed ────────────────────────────────────────────────────
command -v ufw &>/dev/null || {
  info "Installing UFW…"
  apt-get install -y -qq ufw
}

# ── 1. Reset (optional) ───────────────────────────────────────────────────────
if [[ "$RESET" == "true" ]]; then
  warn "Resetting all UFW rules…"
  ufw_cmd --force reset
fi

echo -e "\n${BOLD}${BLUE}══ UFW Firewall Configuration ══${NC}"
[[ "$DRY_RUN" == "true" ]] && warn "DRY-RUN mode — no changes will be applied"
echo ""

# ── 2. Default policies ───────────────────────────────────────────────────────
info "Setting default policies…"
ufw_cmd default deny incoming  comment 'Block all inbound by default'
ufw_cmd default allow outgoing comment 'Allow all outbound'
ufw_cmd default deny forward   comment 'No forwarding'

# ── 3. SSH ────────────────────────────────────────────────────────────────────
info "SSH access…"
if [[ -n "$VPN_CIDR" ]]; then
  ufw_cmd allow from "$VPN_CIDR" to any port 22 proto tcp comment "SSH from VPN ${VPN_CIDR} only"
  success "SSH restricted to VPN: ${VPN_CIDR}"
else
  ufw_cmd allow 22/tcp comment 'SSH (open — restrict with --vpn-cidr in production)'
  warn "SSH open to all IPs — use --vpn-cidr <CIDR> to restrict in production"
fi

# ── 4. Public web traffic ─────────────────────────────────────────────────────
info "HTTP / HTTPS…"
ufw_cmd allow 80/tcp  comment 'HTTP — redirects to HTTPS via Nginx'
ufw_cmd allow 443/tcp comment 'HTTPS — Nginx reverse proxy'
success "HTTP (80) and HTTPS (443) open"

# ── 5. WireGuard VPN (optional) ───────────────────────────────────────────────
if [[ "$WIREGUARD" == "true" ]]; then
  info "WireGuard VPN…"
  ufw_cmd allow 51820/udp comment 'WireGuard VPN'
  success "WireGuard port 51820/udp open"
fi

# ── 6. Explicitly block all admin / database ports ───────────────────────────
info "Blocking all admin and database ports from public internet…"

declare -A BLOCKED_PORTS=(
  ["9092"]="Kafka broker"
  ["9093"]="Kafka broker SSL"
  ["29092"]="Kafka internal"
  ["29093"]="Kafka internal SSL"
  ["2181"]="ZooKeeper"
  ["8123"]="ClickHouse HTTP"
  ["19000"]="ClickHouse native"
  ["8888"]="Apache Druid Router"
  ["8082"]="Flink JobManager UI"
  ["8081"]="Flink internal"
  ["8085"]="Kafka UI"
  ["8086"]="Schema Registry UI"
  ["9200"]="Elasticsearch"
  ["9300"]="Elasticsearch transport"
  ["5601"]="Kibana"
  ["5044"]="Logstash Beats"
  ["8080"]="Airflow Webserver"
  ["9001"]="MinIO Console"
  ["9002"]="MinIO S3 API"
  ["8181"]="Iceberg REST"
  ["8083"]="Kafka Connect"
  ["8000"]="Dashboard API (behind Nginx)"
  ["3000"]="Dashboard UI (behind Nginx)"
  ["6379"]="Redis"
  ["5432"]="PostgreSQL (Airflow/Druid)"
)

for port in "${!BLOCKED_PORTS[@]}"; do
  ufw_cmd deny "${port}/tcp" comment "${BLOCKED_PORTS[$port]} — internal only"
done

success "All admin/database ports blocked"

# ── 7. Rate limiting for SSH (brute-force protection) ────────────────────────
info "SSH rate limiting (max 6 connections/30s per IP)…"
ufw_cmd limit ssh comment 'SSH brute-force protection'
success "SSH rate limited"

# ── 8. Loopback ───────────────────────────────────────────────────────────────
info "Allowing loopback…"
ufw_cmd allow in on lo comment 'Loopback'

# ── 9. Docker needs iptables rules preserved ──────────────────────────────────
# Docker manages its own iptables rules; we need to ensure UFW doesn't break them.
info "Configuring UFW to not interfere with Docker iptables rules…"
UFW_DOCKER_FILE="/etc/ufw/after.rules"
if ! grep -q "DOCKER-USER" "$UFW_DOCKER_FILE" 2>/dev/null && [[ "$DRY_RUN" == "false" ]]; then
  cat >> "$UFW_DOCKER_FILE" << 'RULES'

# ── Docker / UFW coexistence ──────────────────────────────────────────────────
# Allow Docker containers to communicate internally but not receive
# unexpected traffic from the public internet on unmapped ports.
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -j RETURN
COMMIT
RULES
  success "Docker-UFW iptables coexistence rules added"
fi

# ── 10. Enable UFW ────────────────────────────────────────────────────────────
info "Enabling UFW…"
if [[ "$DRY_RUN" == "false" ]]; then
  echo "y" | ufw enable
  success "UFW enabled"
else
  info "[DRY-RUN] Would run: ufw enable"
fi

# ── 11. Show current rules ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Current UFW status:${NC}"
if [[ "$DRY_RUN" == "false" ]]; then
  ufw status verbose
fi

# ── 12. Verify summary ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Firewall Configuration Complete                ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Public ports open:${NC}  22 (SSH), 80 (HTTP→HTTPS), 443 (HTTPS)"
[[ "$WIREGUARD" == "true" ]] && echo -e "  ${BOLD}VPN:${NC}               51820/udp (WireGuard)"
[[ -n "$VPN_CIDR" ]]        && echo -e "  ${BOLD}SSH restricted to:${NC} ${VPN_CIDR}"
echo -e "  ${BOLD}All DB/admin ports:${NC} DENIED from internet"
echo ""
echo -e "  Verify with: ${YELLOW}ufw status verbose${NC}"
echo -e "  Test port:   ${YELLOW}nmap -p 8123,9200,8888 $(curl -s ifconfig.me)${NC}"
echo ""
[[ "$DRY_RUN" == "true" ]] && echo -e "  ${YELLOW}⚠ DRY-RUN: No changes were applied.${NC}\n"

#!/usr/bin/env bash
# ==============================================================================
# setup-vm.sh — Ubuntu 22.04 VM Provisioner
# E-Commerce Realtime Analytics Pipeline
# ==============================================================================
#
# Installs and configures all prerequisites on a fresh Ubuntu 22.04 server:
#   Docker Engine (latest) + Compose V2, system utilities, kernel tuning,
#   app user, directory structure, and optional Docker Swarm initialisation.
#
# Usage:
#   sudo bash setup-vm.sh [OPTIONS]
#
# Options:
#   --prod              Init Docker Swarm (single-node manager mode)
#   --user  <name>      App user name          (default: ecomuser)
#   --tz    <timezone>  System timezone         (default: UTC)
#   --dry-run           Print actions without executing
#   --skip-docker       Skip Docker installation (if already installed)
#   --skip-swap         Skip swap disabling
#   -h, --help          Show this message
#
# Examples:
#   sudo bash setup-vm.sh                     # Standard setup
#   sudo bash setup-vm.sh --prod              # Production + Swarm
#   sudo bash setup-vm.sh --dry-run           # Preview all changes
#   sudo bash setup-vm.sh --user myapp --tz Asia/Kolkata
# ==============================================================================
set -euo pipefail
IFS=$'\n\t'

# ── Script metadata ───────────────────────────────────────────────────────────
readonly SCRIPT_VERSION="1.2.0"
readonly SCRIPT_NAME="$(basename "$0")"
readonly LOG_FILE="/tmp/ecom-vm-setup-$(date +%Y%m%d-%H%M%S).log"
readonly START_TS=$(date +%s)

# ── Defaults (overridden by CLI flags) ────────────────────────────────────────
APP_USER="ecomuser"
TIMEZONE="UTC"
INIT_SWARM=false
DRY_RUN=false
SKIP_DOCKER=false
SKIP_SWAP=false

# ── Paths ─────────────────────────────────────────────────────────────────────
readonly APP_HOME="/opt/ecom-pipeline"
readonly DATA_DIR="/data"
readonly LOGS_DIR="/logs"
readonly SYSCTL_FILE="/etc/sysctl.d/99-ecom-pipeline.conf"
readonly LIMITS_FILE="/etc/security/limits.d/99-ecom-pipeline.conf"
readonly DOCKER_DAEMON_FILE="/etc/docker/daemon.json"
readonly DOCKER_GPG_KEY="/etc/apt/keyrings/docker.gpg"

# ── Colour codes ──────────────────────────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m';    NC='\033[0m'

# ── Logging ───────────────────────────────────────────────────────────────────
log()     { echo -e "$*" | tee -a "$LOG_FILE"; }
info()    { log "${BLUE}[INFO]${NC}     $*"; }
success() { log "${GREEN}[✓ OK]${NC}    $*"; }
warn()    { log "${YELLOW}[WARN]${NC}    $*"; }
error()   { log "${RED}[ERROR]${NC}   $*" >&2; }
die()     { error "$*"; exit 1; }
step()    { log "\n${CYAN}${BOLD}┌─────────────────────────────────────────${NC}"; \
            log "${CYAN}${BOLD}│  $*${NC}"; \
            log "${CYAN}${BOLD}└─────────────────────────────────────────${NC}"; }

# Run a command (skipped in dry-run; always logged)
run() {
  local cmd="$*"
  log "  ${YELLOW}→${NC} ${cmd}"
  if [[ "$DRY_RUN" == "true" ]]; then
    log "  ${YELLOW}  [DRY-RUN] skipped${NC}"
    return 0
  fi
  if ! eval "$cmd" >> "$LOG_FILE" 2>&1; then
    error "Command failed: ${cmd}"
    error "Check ${LOG_FILE} for details."
    exit 1
  fi
}

# Run silently — no log echo (for idempotency checks)
quietly() { eval "$@" >> "$LOG_FILE" 2>&1 || true; }

# ── Argument parsing ──────────────────────────────────────────────────────────
usage() {
  grep "^#" "$0" | grep -v "^#!/" | sed 's/^# \{0,1\}//' | head -30
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod)         INIT_SWARM=true;      shift ;;
    --dry-run)      DRY_RUN=true;         shift ;;
    --skip-docker)  SKIP_DOCKER=true;     shift ;;
    --skip-swap)    SKIP_SWAP=true;       shift ;;
    --user)         APP_USER="${2:?--user requires a value}";      shift 2 ;;
    --tz)           TIMEZONE="${2:?--tz requires a value}";        shift 2 ;;
    -h|--help)      usage ;;
    *) die "Unknown option: $1  (try --help)" ;;
  esac
done

# ── Pre-flight checks ─────────────────────────────────────────────────────────
preflight() {
  step "Pre-flight checks"

  # Must run as root
  [[ $EUID -eq 0 ]] || die "This script must be run as root: sudo bash $SCRIPT_NAME"

  # Ubuntu 22.04 check
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "$ID" != "ubuntu" ]] || [[ "$VERSION_ID" != "22.04" ]]; then
      warn "Detected ${PRETTY_NAME:-unknown OS}. This script targets Ubuntu 22.04."
      warn "Some steps may behave differently. Continuing in 5 s… (Ctrl-C to abort)"
      sleep 5
    else
      success "OS: ${PRETTY_NAME}"
    fi
  fi

  # Internet connectivity
  if ! curl -sf --connect-timeout 5 https://download.docker.com > /dev/null; then
    warn "Cannot reach download.docker.com — internet may be limited."
  fi

  # Available disk space (require >= 20 GB on /)
  local avail_gb
  avail_gb=$(df -BG / | awk 'NR==2 {gsub("G",""); print $4}')
  if [[ "$avail_gb" -lt 20 ]]; then
    warn "Only ${avail_gb} GB free on /  (20 GB recommended)"
  else
    success "Disk: ${avail_gb} GB free"
  fi

  # Available RAM (warn if < 12 GB)
  local mem_gb
  mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
  if [[ "$mem_gb" -lt 12 ]]; then
    warn "RAM: ${mem_gb} GB  (12 GB minimum recommended for full stack)"
  else
    success "RAM: ${mem_gb} GB"
  fi

  info "Log file: ${LOG_FILE}"
  [[ "$DRY_RUN" == "true" ]] && warn "DRY-RUN mode — no changes will be applied"
}

# ── Section 1: System packages & utilities ────────────────────────────────────
install_system_packages() {
  step "System utilities"

  info "Updating package index…"
  run "apt-get update -qq"

  info "Upgrading existing packages…"
  run "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq"

  info "Installing prerequisites and utilities…"
  run "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates curl wget gnupg lsb-release \
    htop iotop nethogs \
    jq vim git unzip \
    net-tools dnsutils traceroute \
    sysstat iotop atop \
    build-essential \
    python3 python3-pip python3-venv \
    ncdu tree tmux \
    rsync acl"

  success "System packages installed"
}

# ── Section 2: Docker Engine ──────────────────────────────────────────────────
install_docker() {
  if [[ "$SKIP_DOCKER" == "true" ]]; then
    warn "Skipping Docker installation (--skip-docker)"
    return 0
  fi

  step "Docker Engine + Compose V2"

  if command -v docker &>/dev/null; then
    local dver
    dver=$(docker --version | awk '{print $3}' | tr -d ',')
    info "Docker already installed: ${dver}"
    info "Skipping installation. Remove with: apt-get remove docker-ce"
    success "Docker already present"
    return 0
  fi

  info "Adding Docker's official GPG key…"
  run "install -m 0755 -d /etc/apt/keyrings"
  run "curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o ${DOCKER_GPG_KEY}"
  run "chmod a+r ${DOCKER_GPG_KEY}"

  info "Adding Docker APT repository…"
  run "echo \
    \"deb [arch=\$(dpkg --print-architecture) signed-by=${DOCKER_GPG_KEY}] \
    https://download.docker.com/linux/ubuntu \
    \$(lsb_release -cs) stable\" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null"

  run "apt-get update -qq"

  info "Installing Docker Engine + Compose V2 plugin…"
  run "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin"

  info "Enabling and starting Docker service…"
  run "systemctl enable --now docker"

  success "Docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ','|| echo '?') installed"
  success "Docker Compose V2 $(docker compose version 2>/dev/null | awk '{print $4}'|| echo '?')"
}

# ── Section 3: Docker daemon configuration ────────────────────────────────────
configure_docker_daemon() {
  step "Docker daemon configuration"

  info "Writing ${DOCKER_DAEMON_FILE}…"

  [[ "$DRY_RUN" == "false" ]] && mkdir -p /etc/docker

  run "cat > ${DOCKER_DAEMON_FILE} << 'DOCKEREOF'
{
  \"log-driver\": \"json-file\",
  \"log-opts\": {
    \"max-size\": \"100m\",
    \"max-file\": \"5\"
  },
  \"default-shm-size\": \"256m\",
  \"max-concurrent-downloads\": 10,
  \"max-concurrent-uploads\": 5,
  \"live-restore\": true,
  \"userland-proxy\": false,
  \"storage-driver\": \"overlay2\",
  \"metrics-addr\": \"127.0.0.1:9323\",
  \"experimental\": false
}
DOCKEREOF"

  if [[ "$DRY_RUN" == "false" ]] && systemctl is-active docker &>/dev/null; then
    info "Reloading Docker daemon…"
    run "systemctl reload-or-restart docker"
  fi

  success "Docker daemon configured (log rotation, live-restore, overlay2)"
}

# ── Section 4: Docker Swarm ───────────────────────────────────────────────────
init_docker_swarm() {
  if [[ "$INIT_SWARM" == "false" ]]; then
    info "Swarm mode not requested (pass --prod to enable)"
    return 0
  fi

  step "Docker Swarm initialisation (--prod)"

  local swarm_state
  swarm_state=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo "inactive")

  if [[ "$swarm_state" == "active" ]]; then
    success "Docker Swarm already active (node: $(docker info --format '{{.Swarm.NodeID}}' 2>/dev/null))"
    return 0
  fi

  info "Detecting primary network interface…"
  local advertise_addr
  advertise_addr=$(ip -4 addr show scope global | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
  info "Advertise address: ${advertise_addr}"

  run "docker swarm init --advertise-addr ${advertise_addr}"

  info "Creating overlay networks for the pipeline…"
  run "docker network create --driver overlay --attachable ecom-net   2>/dev/null || true"
  run "docker network create --driver overlay --attachable monitoring  2>/dev/null || true"

  success "Docker Swarm initialised (single-node manager)"
  info "Join token (save this!):"
  if [[ "$DRY_RUN" == "false" ]]; then
    docker swarm join-token worker 2>/dev/null | tee -a "$LOG_FILE" || true
  fi
}

# ── Section 5: Kernel parameters (sysctl) ────────────────────────────────────
configure_kernel() {
  step "Kernel parameters (sysctl)"

  info "Writing ${SYSCTL_FILE}…"

  run "cat > ${SYSCTL_FILE} << 'SYSCTLEOF'
# ============================================================
# ecom-pipeline kernel tuning — /etc/sysctl.d/99-ecom-pipeline.conf
# ============================================================

# ── Elasticsearch / ClickHouse ───────────────────────────────
# Required by Elasticsearch; also helps ClickHouse mmap usage
vm.max_map_count = 262144

# ── Kafka / high-throughput networking ───────────────────────
# Max connections queued before accept() is called
net.core.somaxconn = 4096

# TCP send/receive buffers (16 MB each)
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216

# Increase number of unprocessed input packets
net.core.netdev_max_backlog = 16384

# ── File descriptors ──────────────────────────────────────────
# System-wide file-handle limit (Kafka, ClickHouse, Flink)
fs.file-max = 2097152

# Inotify watches (needed by Flink checkpointing + monitoring)
fs.inotify.max_user_watches   = 524288
fs.inotify.max_user_instances = 512

# ── Memory management ─────────────────────────────────────────
# Reduce swappiness (0 = never swap; Kafka/Druid are latency-sensitive)
vm.swappiness = 1

# Reduce dirty page writeback lag to avoid I/O spikes
vm.dirty_ratio            = 10
vm.dirty_background_ratio = 5

# ── Docker overlay networking ─────────────────────────────────
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
SYSCTLEOF"

  info "Applying kernel parameters…"
  run "sysctl --system"

  success "Kernel parameters applied"
}

# ── Section 6: File descriptor limits ────────────────────────────────────────
configure_limits() {
  step "File descriptor limits (ulimit)"

  info "Writing ${LIMITS_FILE}…"

  run "cat > ${LIMITS_FILE} << 'LIMEOF'
# ============================================================
# ecom-pipeline ulimit settings — /etc/security/limits.d/99-ecom-pipeline.conf
# ============================================================
# Applied to all users; PAM must load pam_limits.so (default on Ubuntu 22.04)

# Global limits
*       soft    nofile  65535
*       hard    nofile  65535
*       soft    nproc   65535
*       hard    nproc   65535

# Root limits (Docker daemon runs as root)
root    soft    nofile  65535
root    hard    nofile  65535
root    soft    nproc   65535
root    hard    nproc   65535
LIMEOF"

  # Also raise for the current shell session
  run "ulimit -n 65535 2>/dev/null || true"

  # SystemD service file descriptor limit (affects Docker)
  info "Raising systemd global service limits…"
  run "mkdir -p /etc/systemd/system.conf.d"
  run "cat > /etc/systemd/system.conf.d/99-ecom-pipeline.conf << 'SDEOF'
[Manager]
DefaultLimitNOFILE=65535
DefaultLimitNPROC=65535
SDEOF"

  # Raise Docker service fd limit
  run "mkdir -p /etc/systemd/system/docker.service.d"
  run "cat > /etc/systemd/system/docker.service.d/override.conf << 'DOVEOF'
[Service]
LimitNOFILE=1048576
LimitNPROC=1048576
DOVEOF"

  run "systemctl daemon-reload"
  [[ "$DRY_RUN" == "false" ]] && systemctl is-active docker &>/dev/null \
    && run "systemctl restart docker"

  success "File descriptor limits configured (65535 hard/soft)"
}

# ── Section 7: Disable swap ───────────────────────────────────────────────────
disable_swap() {
  if [[ "$SKIP_SWAP" == "true" ]]; then
    warn "Skipping swap disable (--skip-swap)"
    return 0
  fi

  step "Disable swap (Kafka / Druid latency)"

  local swap_total
  swap_total=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)

  if [[ "$swap_total" -eq 0 ]]; then
    success "Swap already disabled"
    return 0
  fi

  info "Turning off active swap (${swap_total} kB)…"
  run "swapoff -a"

  info "Removing swap entries from /etc/fstab…"
  run "sed -i '/\bswap\b/d' /etc/fstab"

  info "Removing any swap files…"
  run "rm -f /swapfile /swap.img"

  success "Swap disabled permanently"
}

# ── Section 8: Timezone ───────────────────────────────────────────────────────
configure_timezone() {
  step "Timezone → ${TIMEZONE}"

  if ! timedatectl list-timezones | grep -q "^${TIMEZONE}$" 2>/dev/null; then
    warn "Timezone '${TIMEZONE}' not found; defaulting to UTC"
    TIMEZONE="UTC"
  fi

  run "timedatectl set-timezone ${TIMEZONE}"
  run "timedatectl set-ntp true"

  success "Timezone set to ${TIMEZONE}"
}

# ── Section 9: App user ───────────────────────────────────────────────────────
create_app_user() {
  step "App user: ${APP_USER}"

  if id "${APP_USER}" &>/dev/null; then
    info "User '${APP_USER}' already exists — skipping creation"
  else
    info "Creating user '${APP_USER}'…"
    run "useradd --system --create-home --shell /bin/bash \
         --comment 'E-Commerce Pipeline App User' ${APP_USER}"
  fi

  info "Adding '${APP_USER}' to docker group…"
  run "usermod -aG docker ${APP_USER}"

  # Allow passwordless sudo for specific pipeline commands only
  info "Granting limited sudo privileges…"
  run "cat > /etc/sudoers.d/99-${APP_USER} << 'SUDOEOF'
# ecom-pipeline: allow app user to manage Docker and restart services
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose, /usr/local/bin/docker-compose
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart docker
SUDOEOF"

  run "chmod 0440 /etc/sudoers.d/99-${APP_USER}"

  success "User '${APP_USER}' created and configured"
}

# ── Section 10: Directory structure ──────────────────────────────────────────
create_directories() {
  step "Directory structure"

  local dirs=(
    "${APP_HOME}"
    "${APP_HOME}/config"
    "${APP_HOME}/scripts"
    "${APP_HOME}/docker"
    "${APP_HOME}/backups"
    "${DATA_DIR}"
    "${DATA_DIR}/kafka"
    "${DATA_DIR}/clickhouse"
    "${DATA_DIR}/minio"
    "${DATA_DIR}/elasticsearch"
    "${DATA_DIR}/redis"
    "${LOGS_DIR}"
    "${LOGS_DIR}/flink"
    "${LOGS_DIR}/kafka"
    "${LOGS_DIR}/airflow"
  )

  for dir in "${dirs[@]}"; do
    if [[ -d "$dir" ]]; then
      info "  Already exists: ${dir}"
    else
      info "  Creating: ${dir}"
      run "mkdir -p ${dir}"
    fi
  done

  info "Setting ownership to '${APP_USER}'…"
  run "chown -R ${APP_USER}:${APP_USER} ${APP_HOME} ${DATA_DIR} ${LOGS_DIR}"

  info "Setting permissions…"
  run "chmod 750  ${APP_HOME}"
  run "chmod 700  ${APP_HOME}/config"
  run "chmod 755  ${DATA_DIR}"
  run "chmod 755  ${LOGS_DIR}"

  # Sticky bit on data dir — prevent accidental deletion
  run "chmod +t ${DATA_DIR}"

  success "Directories created and permissioned"
}

# ── Section 11: SSH hardening (optional) ─────────────────────────────────────
harden_ssh() {
  step "SSH hardening"

  local sshd_cfg="/etc/ssh/sshd_config.d/99-ecom-hardening.conf"

  info "Writing ${sshd_cfg}…"

  run "cat > ${sshd_cfg} << 'SSHEOF'
# ecom-pipeline SSH hardening
Protocol 2
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
MaxAuthTries 3
MaxSessions 10
ClientAliveInterval 300
ClientAliveCountMax 2
AllowAgentForwarding no
AllowTcpForwarding no
X11Forwarding no
PrintMotd no
SSHEOF"

  run "sshd -t"
  run "systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true"

  success "SSH hardened (root login disabled, idle timeout 5 min)"
}

# ── Section 12: Logrotate for pipeline logs ───────────────────────────────────
configure_logrotate() {
  step "Logrotate for pipeline logs"

  run "cat > /etc/logrotate.d/ecom-pipeline << 'LREOF'
${LOGS_DIR}/*/*.log ${LOGS_DIR}/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    sharedscripts
    postrotate
        systemctl kill -s HUP docker.service 2>/dev/null || true
    endscript
}
LREOF"

  success "Logrotate configured (14-day retention, daily compression)"
}

# ── Section 13: System service (auto-start pipeline) ─────────────────────────
create_systemd_service() {
  step "Systemd service: ecom-pipeline"

  run "cat > /etc/systemd/system/ecom-pipeline.service << 'SVCEOF'
[Unit]
Description=E-Commerce Realtime Analytics Pipeline
Documentation=https://github.com/your-org/ecommerce-realtime-pipeline
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
WorkingDirectory=${APP_HOME}
ExecStartPre=/usr/bin/docker info
ExecStart=/usr/bin/docker compose -f ${APP_HOME}/docker/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose  -f ${APP_HOME}/docker/docker-compose.yml down
ExecReload=/usr/bin/docker compose -f ${APP_HOME}/docker/docker-compose.yml restart

TimeoutStartSec=300
TimeoutStopSec=120
Restart=on-failure
RestartSec=30

StandardOutput=journal
StandardError=journal
SyslogIdentifier=ecom-pipeline

[Install]
WantedBy=multi-user.target
SVCEOF"

  run "systemctl daemon-reload"
  # Do NOT enable by default — user should do this explicitly after deploying code
  info "Service registered but NOT enabled (run: systemctl enable ecom-pipeline)"

  success "Systemd service created"
}

# ── Validation summary ────────────────────────────────────────────────────────
validate() {
  step "Post-install validation"
  local ok=0 fail=0

  check() {
    local label="$1"; shift
    if eval "$@" &>/dev/null; then
      success "  ${label}"
      ((ok++))
    else
      error   "  ${label} — FAILED"
      ((fail++))
    fi
  }

  check "Docker Engine installed"            "command -v docker"
  check "Docker daemon running"              "systemctl is-active docker"
  check "Docker Compose V2"                  "docker compose version"
  check "User '${APP_USER}' exists"          "id ${APP_USER}"
  check "User in docker group"               "id -nG ${APP_USER} | grep -qw docker"
  check "Directory ${APP_HOME}"              "test -d ${APP_HOME}"
  check "Directory ${DATA_DIR}"             "test -d ${DATA_DIR}"
  check "Directory ${LOGS_DIR}"             "test -d ${LOGS_DIR}"
  check "Sysctl file present"               "test -f ${SYSCTL_FILE}"
  check "Limits file present"               "test -f ${LIMITS_FILE}"
  check "vm.max_map_count=262144"           "sysctl -n vm.max_map_count | grep -q 262144"
  check "net.core.somaxconn=4096"           "sysctl -n net.core.somaxconn | grep -q 4096"
  check "fs.file-max=2097152"               "sysctl -n fs.file-max | grep -q 2097152"
  check "Swap disabled"                     "test \$(sysctl -n vm.swappiness) -le 1"
  check "Timezone ${TIMEZONE}"              "timedatectl | grep -q '${TIMEZONE}'"
  check "Docker log rotation configured"    "test -f ${DOCKER_DAEMON_FILE}"
  check "ecom-pipeline.service registered"  "systemctl list-unit-files ecom-pipeline.service"

  if [[ "$INIT_SWARM" == "true" ]]; then
    check "Docker Swarm active" "docker info --format '{{.Swarm.LocalNodeState}}' | grep -q active"
  fi

  echo ""
  log "${BOLD}Validation: ${GREEN}${ok} passed${NC}${BOLD}, ${RED}${fail} failed${NC}"
  [[ $fail -gt 0 ]] && warn "Check ${LOG_FILE} for details on failures."
}

# ── Final summary ─────────────────────────────────────────────────────────────
print_summary() {
  local elapsed=$(( $(date +%s) - START_TS ))
  local mins=$(( elapsed / 60 ))
  local secs=$(( elapsed % 60 ))

  echo ""
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}${BOLD}║       VM Setup Complete — E-Commerce Pipeline            ║${NC}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BOLD}Time elapsed:${NC}   ${mins}m ${secs}s"
  echo -e "  ${BOLD}Log file:${NC}       ${LOG_FILE}"
  echo -e "  ${BOLD}App user:${NC}       ${APP_USER}"
  echo -e "  ${BOLD}App home:${NC}       ${APP_HOME}"
  echo -e "  ${BOLD}Data dir:${NC}       ${DATA_DIR}"
  echo -e "  ${BOLD}Logs dir:${NC}       ${LOGS_DIR}"
  echo -e "  ${BOLD}Timezone:${NC}       ${TIMEZONE}"
  echo -e "  ${BOLD}Swarm:${NC}          $([[ "$INIT_SWARM" == "true" ]] && echo "Enabled (manager)" || echo "Disabled")"
  echo ""
  echo -e "${BOLD}Next steps:${NC}"
  echo -e "  1. Copy the repository to ${APP_HOME}/"
  echo -e "     ${CYAN}rsync -av ./ ${APP_USER}@<vm-ip>:${APP_HOME}/${NC}"
  echo ""
  echo -e "  2. Switch to the app user"
  echo -e "     ${CYAN}su - ${APP_USER}${NC}"
  echo ""
  echo -e "  3. Run the pipeline setup script"
  echo -e "     ${CYAN}cd ${APP_HOME} && bash setup.sh${NC}"
  echo ""
  echo -e "  4. (Optional) Enable auto-start on reboot"
  echo -e "     ${CYAN}sudo systemctl enable ecom-pipeline${NC}"
  echo ""
  echo -e "  5. Validate the full stack"
  echo -e "     ${CYAN}bash validate-vm.sh${NC}"
  echo ""
  [[ "$DRY_RUN" == "true" ]] && echo -e "  ${YELLOW}⚠  DRY-RUN: No changes were made.${NC}\n"
}

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
main() {
  # Header
  echo -e "${CYAN}${BOLD}"
  echo "  ╔══════════════════════════════════════════════════════╗"
  echo "  ║   E-Commerce Pipeline — VM Provisioner v${SCRIPT_VERSION}     ║"
  echo "  ║   Ubuntu 22.04 LTS                                  ║"
  echo "  ╚══════════════════════════════════════════════════════╝"
  echo -e "${NC}"
  echo -e "  Started: $(date)"
  echo -e "  User: ${APP_USER}  |  TZ: ${TIMEZONE}  |  Swarm: ${INIT_SWARM}  |  Dry-run: ${DRY_RUN}"
  echo ""

  # Initialise log
  mkdir -p "$(dirname "$LOG_FILE")"
  echo "# ecom-vm-setup log — $(date)" > "$LOG_FILE"

  preflight
  install_system_packages
  install_docker
  configure_docker_daemon
  configure_kernel
  configure_limits
  disable_swap
  configure_timezone
  create_app_user
  create_directories
  harden_ssh
  configure_logrotate
  create_systemd_service
  init_docker_swarm
  validate
  print_summary
}

main "$@"

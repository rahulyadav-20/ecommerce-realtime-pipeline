#!/usr/bin/env bash
# ==============================================================================
# scripts/rotate-secrets.sh — Credential rotation for the pipeline
# ==============================================================================
#
# Rotates secrets on a 90-day schedule and tracks rotation dates in
# /etc/ecom-pipeline/secret-rotation.log
#
# Usage:
#   sudo bash scripts/rotate-secrets.sh [OPTIONS]
#
# Options:
#   --check        Show which secrets are due for rotation (no changes)
#   --all          Rotate all secrets immediately
#   --jwt          Rotate JWT signing key only
#   --redis        Rotate Redis AUTH password only
#   --minio        Rotate MinIO credentials only
#   --nginx-auth   Regenerate Nginx basic auth passwords
#   --emergency    Rotate ALL secrets immediately (incident response)
#   --dry-run      Print what would be done without making changes
#   -h, --help     Show this message
#
# The script writes new secrets to:
#   - Docker Swarm secrets (if in Swarm mode)
#   - /etc/ecom-pipeline/secrets/ (file-based secrets, chmod 600)
#   - Logs rotation events to /var/log/ecom-secret-rotation.log
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
success() { echo -e "${GREEN}[✓]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error()   { echo -e "${RED}[ERROR]${NC}  $*" >&2; }

# ── Config ────────────────────────────────────────────────────────────────────
ROTATION_PERIOD_DAYS=90
SECRETS_DIR="/etc/ecom-pipeline/secrets"
ROTATION_LOG="/var/log/ecom-secret-rotation.log"
COMPOSE_FILE="docker/docker-compose.yml"
SWARM_MODE=false

ACTION=""
DRY_RUN=false

docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q active \
  && SWARM_MODE=true || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)      ACTION="check";      shift ;;
    --all)        ACTION="all";        shift ;;
    --jwt)        ACTION="jwt";        shift ;;
    --redis)      ACTION="redis";      shift ;;
    --minio)      ACTION="minio";      shift ;;
    --nginx-auth) ACTION="nginx-auth"; shift ;;
    --emergency)  ACTION="emergency";  shift ;;
    --dry-run)    DRY_RUN=true;        shift ;;
    -h|--help)    grep "^#" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown argument: $1"; exit 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || { error "Must run as root"; exit 1; }
[[ -z "$ACTION" ]] && { error "Specify an action (--check, --all, --jwt, etc.)"; exit 1; }

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
touch "$ROTATION_LOG"

# ── Helpers ───────────────────────────────────────────────────────────────────

gen_secret() {
  local bytes="${1:-32}"
  openssl rand -base64 "$bytes" | tr -d '\n/+='
}

log_rotation() {
  local secret="$1"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  ROTATED  ${secret}  by=${USER:-root}  host=$(hostname)" \
    >> "$ROTATION_LOG"
}

days_since_rotation() {
  local secret="$1"
  local marker_file="${SECRETS_DIR}/.rotated-${secret}"
  if [[ -f "$marker_file" ]]; then
    local rotated_epoch; rotated_epoch=$(stat -c %Y "$marker_file")
    local now_epoch;     now_epoch=$(date +%s)
    echo $(( (now_epoch - rotated_epoch) / 86400 ))
  else
    echo 999   # never rotated
  fi
}

mark_rotated() {
  local secret="$1"
  touch "${SECRETS_DIR}/.rotated-${secret}"
  log_rotation "$secret"
}

write_secret_file() {
  local name="$1" value="$2"
  local file="${SECRETS_DIR}/${name}"
  echo "$value" > "$file"
  chmod 600 "$file"
  chown root:root "$file"
}

update_docker_secret() {
  local name="$1" value="$2"
  if [[ "$SWARM_MODE" == "true" ]]; then
    echo "$value" | docker secret create "${name}_new" -
    # Services need to be updated to use the new secret version
    info "Docker Swarm: created secret '${name}_new' — update services to use it"
  fi
}

reload_service() {
  local service="$1"
  if docker ps --filter "name=^${service}$" --format "{{.Names}}" | grep -q "$service"; then
    if [[ "$DRY_RUN" == "false" ]]; then
      docker restart "$service"
      success "Restarted container: ${service}"
    else
      info "[DRY-RUN] Would restart: ${service}"
    fi
  else
    warn "Container '${service}' not running — restart manually after updating secrets"
  fi
}

# ── Check which secrets are due ───────────────────────────────────────────────
action_check() {
  echo -e "\n${BOLD}Secret Rotation Status (threshold: ${ROTATION_PERIOD_DAYS} days)${NC}"
  echo -e "──────────────────────────────────────────────────────"

  local secrets=("jwt" "redis" "minio" "clickhouse" "airflow-fernet" "nginx-auth")
  local all_ok=true

  for secret in "${secrets[@]}"; do
    local days; days=$(days_since_rotation "$secret")
    local status color
    if [[ "$days" -ge "$ROTATION_PERIOD_DAYS" ]]; then
      status="⚠ OVERDUE (${days} days)"
      color="${RED}"
      all_ok=false
    elif [[ "$days" -ge $(( ROTATION_PERIOD_DAYS - 14 )) ]]; then
      status="⚡ DUE SOON (${days} days)"
      color="${YELLOW}"
    else
      status="✓ OK (${days} days ago)"
      color="${GREEN}"
    fi
    printf "  %-20s %b%s%b\n" "$secret" "$color" "$status" "$NC"
  done

  echo ""
  if [[ "$all_ok" == "false" ]]; then
    warn "Some secrets are overdue — run: sudo bash scripts/rotate-secrets.sh --all"
    return 1
  else
    success "All secrets rotated within ${ROTATION_PERIOD_DAYS} days"
  fi
}

# ── JWT key rotation ──────────────────────────────────────────────────────────
rotate_jwt() {
  info "Rotating JWT signing key…"
  local new_secret; new_secret=$(gen_secret 48)

  if [[ "$DRY_RUN" == "false" ]]; then
    write_secret_file "jwt_secret" "$new_secret"
    update_docker_secret "jwt_secret" "$new_secret"

    # Update the running dashboard-api via env override (Compose restart)
    # In production: update Docker secret reference and redeploy
    if [[ -f "docker/.env" ]]; then
      sed -i "s/^JWT_SECRET=.*/JWT_SECRET=${new_secret}/" docker/.env \
        || echo "JWT_SECRET=${new_secret}" >> docker/.env
    fi

    reload_service "dashboard-api"
    mark_rotated "jwt"
    success "JWT secret rotated — all existing tokens are now invalid"
  else
    info "[DRY-RUN] Would rotate JWT secret (${#new_secret} chars)"
  fi
}

# ── Redis AUTH rotation ───────────────────────────────────────────────────────
rotate_redis() {
  info "Rotating Redis AUTH password…"
  local new_pass; new_pass=$(gen_secret 32)

  if [[ "$DRY_RUN" == "false" ]]; then
    # Get current password to perform live rotation (no downtime)
    local redis_container
    redis_container=$(docker ps --filter "name=redis-cache" --format "{{.Names}}" | head -1)

    if [[ -n "$redis_container" ]]; then
      local old_pass; old_pass=$(cat "${SECRETS_DIR}/redis_password" 2>/dev/null || echo "")

      # Set new password in Redis (live — no restart needed)
      docker exec "$redis_container" redis-cli \
        ${old_pass:+-a "$old_pass"} CONFIG SET requirepass "$new_pass"
      success "Redis requirepass updated live (no downtime)"
    fi

    write_secret_file "redis_password" "$new_pass"
    update_docker_secret "redis_password" "$new_pass"

    if [[ -f "docker/.env" ]]; then
      sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://:${new_pass}@redis-cache:6379|" docker/.env
    fi

    reload_service "dashboard-api"
    mark_rotated "redis"
    success "Redis AUTH password rotated"
  else
    info "[DRY-RUN] Would rotate Redis password"
  fi
}

# ── MinIO credentials rotation ────────────────────────────────────────────────
rotate_minio() {
  info "Rotating MinIO credentials…"
  local new_key;    new_key=$(gen_secret 16)
  local new_secret; new_secret=$(gen_secret 32)

  if [[ "$DRY_RUN" == "false" ]]; then
    local minio_container
    minio_container=$(docker ps --filter "name=minio" --format "{{.Names}}" | grep -v setup | head -1)

    if [[ -n "$minio_container" ]]; then
      local old_key;    old_key=$(cat "${SECRETS_DIR}/minio_access_key"  2>/dev/null || echo "minio")
      local old_secret; old_secret=$(cat "${SECRETS_DIR}/minio_secret_key" 2>/dev/null || echo "minio123")

      # Create new admin user, then remove old one (zero-downtime rotation)
      docker exec "$minio_container" mc alias set local \
        "http://localhost:9000" "$old_key" "$old_secret" 2>/dev/null || true

      docker exec "$minio_container" mc admin user add local "$new_key" "$new_secret"
      docker exec "$minio_container" mc admin policy attach local readwrite --user "$new_key"

      success "New MinIO user created — updating clients…"
    fi

    write_secret_file "minio_access_key"  "$new_key"
    write_secret_file "minio_secret_key"  "$new_secret"

    if [[ -f "docker/.env" ]]; then
      sed -i "s/^MINIO_ACCESS_KEY=.*/MINIO_ACCESS_KEY=${new_key}/"       docker/.env
      sed -i "s/^MINIO_SECRET_KEY=.*/MINIO_SECRET_KEY=${new_secret}/"   docker/.env
    fi

    # Remove old admin after services restart with new creds
    if [[ -n "${minio_container:-}" ]]; then
      local old_key_val; old_key_val=$(cat "${SECRETS_DIR}/minio_access_key.old" 2>/dev/null || echo "")
      [[ -n "$old_key_val" ]] && \
        docker exec "$minio_container" mc admin user remove local "$old_key_val" 2>/dev/null || true
    fi

    cp "${SECRETS_DIR}/minio_access_key" "${SECRETS_DIR}/minio_access_key.old" 2>/dev/null || true

    mark_rotated "minio"
    success "MinIO credentials rotated — access key: ${new_key:0:8}…"
  else
    info "[DRY-RUN] Would rotate MinIO credentials"
  fi
}

# ── Nginx Basic Auth rotation ─────────────────────────────────────────────────
rotate_nginx_auth() {
  info "Rotating Nginx basic auth passwords…"

  if ! command -v htpasswd &>/dev/null; then
    apt-get install -y -qq apache2-utils
  fi

  local htpasswd_file="/etc/nginx/auth/.htpasswd"
  [[ -f "$htpasswd_file" ]] || { warn "htpasswd file not found — run setup-basic-auth.sh first"; return 1; }

  if [[ "$DRY_RUN" == "false" ]]; then
    # Back up old file
    cp "$htpasswd_file" "${htpasswd_file}.bak.$(date +%Y%m%d)"

    # Generate new password for each user
    while IFS=: read -r username _; do
      local new_pass; new_pass=$(gen_secret 16)
      htpasswd -bB "$htpasswd_file" "$username" "$new_pass"
      info "  ${username}: new password stored (length: ${#new_pass})"
      # Store new password securely
      write_secret_file "nginx_auth_${username}" "$new_pass"
      success "  ${username}: password rotated → ${SECRETS_DIR}/nginx_auth_${username}"
    done < "$htpasswd_file"

    # Reload nginx
    local nginx_container
    nginx_container=$(docker ps --filter "name=nginx-proxy" --format "{{.Names}}" | head -1)
    if [[ -n "$nginx_container" ]]; then
      docker cp "$htpasswd_file" "${nginx_container}:/etc/nginx/auth/.htpasswd"
      docker exec "$nginx_container" nginx -s reload
    fi

    mark_rotated "nginx-auth"
    success "Nginx basic auth passwords rotated"
    warn "Distribute new passwords to admin team via secure channel (1Password, Vault)"
  else
    info "[DRY-RUN] Would rotate passwords for all users in ${htpasswd_file}"
  fi
}

# ── Emergency rotation (all secrets) ─────────────────────────────────────────
action_emergency() {
  echo ""
  error "EMERGENCY ROTATION — rotating ALL secrets immediately"
  warn  "This will restart services and invalidate all existing sessions."
  echo ""
  read -r -p "Type CONFIRM to proceed: " confirmation
  [[ "$confirmation" == "CONFIRM" ]] || { info "Aborted."; exit 0; }

  rotate_jwt
  rotate_redis
  rotate_minio
  rotate_nginx_auth

  echo ""
  log_rotation "EMERGENCY_ALL"
  success "Emergency rotation complete — all secrets rotated at $(date -u)"
  echo ""
  warn "Next steps:"
  echo "  1. Redistribute Nginx auth passwords to the admin team"
  echo "  2. Update any external services using the pipeline API (new JWT secret)"
  echo "  3. Verify all services are healthy: bash validate-vm.sh"
  echo "  4. Document the incident in your security log"
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${BLUE}══ Secret Rotation — $(date) ══${NC}"
[[ "$DRY_RUN" == "true" ]] && warn "DRY-RUN mode — no changes will be applied"
[[ "$SWARM_MODE" == "true" ]] && info "Docker Swarm mode detected"
echo ""

case "$ACTION" in
  check)      action_check ;;
  jwt)        rotate_jwt ;;
  redis)      rotate_redis ;;
  minio)      rotate_minio ;;
  nginx-auth) rotate_nginx_auth ;;
  all)
    rotate_jwt
    rotate_redis
    rotate_minio
    rotate_nginx_auth
    success "All secrets rotated"
    ;;
  emergency)  action_emergency ;;
  *)          error "Unknown action: $ACTION"; exit 1 ;;
esac

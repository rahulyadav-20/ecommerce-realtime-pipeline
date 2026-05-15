#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-basic-auth.sh — HTTP Basic Authentication Setup
# Protects admin routes: /druid, /flink, /kibana
# ==============================================================================
#
# Usage:
#   sudo bash scripts/setup-basic-auth.sh [OPTIONS]
#
# Options:
#   -u <username>   Add or update a user (can repeat: -u alice -u bob)
#   -p <password>   Password for the last -u (prompted if omitted)
#   -f <file>       htpasswd file path (default: /etc/nginx/auth/.htpasswd)
#   --list          List all users in the htpasswd file
#   --remove <user> Remove a user from the htpasswd file
#   --docker        Update the htpasswd inside the running nginx-proxy container
#   -h, --help      Show this message
#
# Examples:
#   # Add users interactively (password prompt)
#   sudo bash scripts/setup-basic-auth.sh -u admin -u operator
#
#   # Add user with password on CLI (avoid in production — shows in history)
#   sudo bash scripts/setup-basic-auth.sh -u admin -p "S3cur3P@ss!"
#
#   # List current users
#   sudo bash scripts/setup-basic-auth.sh --list
#
#   # Remove a user
#   sudo bash scripts/setup-basic-auth.sh --remove operator
#
#   # Apply to running Docker container
#   sudo bash scripts/setup-basic-auth.sh -u admin --docker
# ==============================================================================
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
success() { echo -e "${GREEN}[✓]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
die()     { echo -e "${RED}[ERROR]${NC}  $*" >&2; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────────────────
HTPASSWD_FILE="/etc/nginx/auth/.htpasswd"
USERS=()
PASSWORDS=()
ACTION="add"       # add | list | remove
REMOVE_USER=""
UPDATE_DOCKER=false

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u)           USERS+=("${2:?-u requires a username}"); PASSWORDS+=(""); shift 2 ;;
    -p)           # Assign password to the last added user
                  if [[ ${#USERS[@]} -eq 0 ]]; then die "-p must follow a -u flag"; fi
                  PASSWORDS[-1]="${2:?-p requires a password}"; shift 2 ;;
    -f)           HTPASSWD_FILE="${2:?-f requires a file path}"; shift 2 ;;
    --list)       ACTION="list";   shift ;;
    --remove)     ACTION="remove"; REMOVE_USER="${2:?--remove requires a username}"; shift 2 ;;
    --docker)     UPDATE_DOCKER=true; shift ;;
    -h|--help)    grep "^#" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1 (try --help)" ;;
  esac
done

# ── Ensure apache2-utils (provides htpasswd) ──────────────────────────────────
ensure_htpasswd() {
  if command -v htpasswd &>/dev/null; then return 0; fi
  info "Installing apache2-utils (provides htpasswd)…"
  if command -v apt-get &>/dev/null; then
    apt-get install -y -qq apache2-utils
  elif command -v apk &>/dev/null; then
    apk add --no-cache apache2-utils
  else
    die "Cannot install htpasswd — install apache2-utils manually"
  fi
  success "htpasswd available"
}

# ── Create/protect htpasswd file ─────────────────────────────────────────────
init_htpasswd_file() {
  local dir; dir="$(dirname "$HTPASSWD_FILE")"
  mkdir -p "$dir"
  [[ ! -f "$HTPASSWD_FILE" ]] && touch "$HTPASSWD_FILE"
  chmod 640 "$HTPASSWD_FILE"
  # Try to set ownership to nginx/www-data
  chown root:nginx       "$HTPASSWD_FILE" 2>/dev/null \
  || chown root:www-data "$HTPASSWD_FILE" 2>/dev/null \
  || true
}

# ── List users ────────────────────────────────────────────────────────────────
action_list() {
  if [[ ! -f "$HTPASSWD_FILE" ]] || [[ ! -s "$HTPASSWD_FILE" ]]; then
    warn "No users found in ${HTPASSWD_FILE}"
    return 0
  fi
  echo ""
  echo -e "${BOLD}Users with access to /druid, /flink, /kibana:${NC}"
  echo -e "${BOLD}─────────────────────────────────────────────${NC}"
  while IFS= read -r line; do
    user="${line%%:*}"
    echo -e "  • ${user}"
  done < "$HTPASSWD_FILE"
  echo ""
  echo -e "  htpasswd file: ${HTPASSWD_FILE}"
  echo ""
}

# ── Remove user ───────────────────────────────────────────────────────────────
action_remove() {
  local user="$1"
  if ! grep -q "^${user}:" "$HTPASSWD_FILE" 2>/dev/null; then
    warn "User '${user}' not found in ${HTPASSWD_FILE}"
    return 0
  fi
  htpasswd -D "$HTPASSWD_FILE" "$user"
  success "User '${user}' removed from ${HTPASSWD_FILE}"
}

# ── Add/update user ───────────────────────────────────────────────────────────
action_add() {
  local username="$1"
  local password="$2"

  # Validate username
  if [[ ! "$username" =~ ^[a-zA-Z0-9._-]+$ ]]; then
    die "Invalid username '${username}' — use letters, digits, . _ - only"
  fi

  # Determine if adding or updating
  local verb="Added"
  grep -q "^${username}:" "$HTPASSWD_FILE" 2>/dev/null && verb="Updated"

  if [[ -n "$password" ]]; then
    # Non-interactive (password supplied on CLI)
    htpasswd -bB "$HTPASSWD_FILE" "$username" "$password"
  else
    # Interactive — prompt twice and confirm
    echo ""
    echo -e "${BOLD}Setting password for user: ${username}${NC}"

    local pw1 pw2
    while true; do
      read -r -s -p "  Password:        " pw1; echo
      [[ ${#pw1} -ge 8 ]] || { warn "Password must be at least 8 characters"; continue; }
      read -r -s -p "  Confirm password: " pw2; echo
      [[ "$pw1" == "$pw2" ]] && break
      warn "Passwords do not match — try again"
    done
    password="$pw1"
    htpasswd -bB "$HTPASSWD_FILE" "$username" "$password"
  fi

  success "${verb} user '${username}' in ${HTPASSWD_FILE}"
}

# ── Reload nginx (host or Docker) ─────────────────────────────────────────────
reload_nginx() {
  if [[ "$UPDATE_DOCKER" == "true" ]]; then
    local container
    container=$(docker ps --filter "name=nginx-proxy" --format "{{.Names}}" 2>/dev/null | head -1)
    if [[ -n "$container" ]]; then
      info "Copying htpasswd to container '${container}'…"
      docker cp "$HTPASSWD_FILE" "${container}:/etc/nginx/auth/.htpasswd"
      docker exec "$container" nginx -s reload
      success "nginx-proxy container updated and reloaded"
    else
      warn "Container 'nginx-proxy' not running — copy htpasswd manually"
    fi
  elif systemctl is-active nginx &>/dev/null 2>&1; then
    nginx -t && systemctl reload nginx
    success "nginx reloaded"
  else
    warn "nginx not running — start nginx to apply changes"
  fi
}

# ── Verification test ─────────────────────────────────────────────────────────
verify_auth() {
  local user="$1"
  # Test that htpasswd can authenticate the user
  # (We don't store the plaintext password so we just check the entry exists)
  if grep -q "^${user}:" "$HTPASSWD_FILE" 2>/dev/null; then
    success "Authentication entry verified for '${user}'"
  else
    warn "Entry for '${user}' not found — something went wrong"
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
  ensure_htpasswd
  init_htpasswd_file

  case "$ACTION" in
    list)
      action_list
      exit 0
      ;;

    remove)
      action_remove "$REMOVE_USER"
      reload_nginx
      exit 0
      ;;

    add)
      if [[ ${#USERS[@]} -eq 0 ]]; then
        die "No users specified. Use: -u <username>"
      fi

      for i in "${!USERS[@]}"; do
        action_add "${USERS[$i]}" "${PASSWORDS[$i]:-}"
        verify_auth "${USERS[$i]}"
      done

      echo ""
      echo -e "${BOLD}Protected routes:${NC}"
      echo -e "  ${YELLOW}https://YOUR_DOMAIN/druid${NC}  — Druid Console"
      echo -e "  ${YELLOW}https://YOUR_DOMAIN/flink${NC}  — Flink Web UI"
      echo -e "  ${YELLOW}https://YOUR_DOMAIN/kibana${NC} — Kibana"
      echo ""

      reload_nginx

      echo ""
      echo -e "${BOLD}Test basic auth:${NC}"
      echo -e "  curl -u ${USERS[0]}:<password> https://YOUR_DOMAIN/druid/"
      echo -e "  curl -u ${USERS[0]}:<password> https://YOUR_DOMAIN/flink/"
      echo -e "  curl -u ${USERS[0]}:<password> https://YOUR_DOMAIN/kibana/"
      echo ""
      ;;
  esac
}

main "$@"

#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-certbot.sh — Let's Encrypt SSL certificate setup
# E-Commerce Realtime Analytics Pipeline
# ==============================================================================
#
# Obtains a TLS certificate from Let's Encrypt using certbot (the --nginx
# plugin), generates a 2048-bit Diffie-Hellman parameter file for PFS, and
# sets up automatic renewal via systemd timer.
#
# Usage:
#   sudo bash scripts/setup-certbot.sh -d analytics.example.com -e admin@example.com
#
# Options:
#   -d <domain>      Domain name (required, repeat for SANs: -d a.com -d b.com)
#   -e <email>       Email for Let's Encrypt expiry notices (required)
#   --staging        Use Let's Encrypt staging server (test without rate limits)
#   --dry-run        Simulate certificate request without saving
#   --force-renew    Force renewal even if certificate is valid
#   -h, --help       Show this message
#
# Pre-requisites:
#   - DNS A record pointing domain → this server's IP
#   - Port 80 open and reachable from the internet
#   - Nginx running (certbot --nginx plugin modifies nginx config)
#   - setup-vm.sh has been run (installs all system packages)
# ==============================================================================
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
success() { echo -e "${GREEN}[✓]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error()   { echo -e "${RED}[ERROR]${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────────────────
DOMAINS=()
EMAIL=""
STAGING=false
DRY_RUN=false
FORCE_RENEW=false
WEBROOT_DIR="/var/www/certbot"
NGINX_CONF_DIR="/etc/nginx/conf.d"
DHPARAM_FILE="/etc/nginx/ssl/dhparam.pem"
DHPARAM_BITS=2048

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d)          DOMAINS+=("$2"); shift 2 ;;
    -e)          EMAIL="$2";      shift 2 ;;
    --staging)   STAGING=true;    shift   ;;
    --dry-run)   DRY_RUN=true;    shift   ;;
    --force-renew) FORCE_RENEW=true; shift ;;
    -h|--help)   grep "^#" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ ${#DOMAINS[@]} -eq 0 ]] && die "At least one domain is required: -d your-domain.com"
[[ -z "$EMAIL" ]]          && die "Email address is required: -e admin@example.com"

PRIMARY_DOMAIN="${DOMAINS[0]}"

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root: sudo bash $0"

echo -e "\n${CYAN:-}${BOLD}══ Let's Encrypt SSL Setup ══${NC}\n" 2>/dev/null || true
info "Domain(s):  ${DOMAINS[*]}"
info "Email:      ${EMAIL}"
info "Staging:    ${STAGING}"

# ── Step 1: Install certbot ───────────────────────────────────────────────────
install_certbot() {
  if command -v certbot &>/dev/null; then
    success "certbot already installed: $(certbot --version 2>&1)"
    return 0
  fi

  info "Installing certbot via snap…"
  snap install core 2>/dev/null || true
  snap refresh core  2>/dev/null || true
  snap install --classic certbot

  # Ensure certbot is on PATH
  ln -sf /snap/bin/certbot /usr/bin/certbot 2>/dev/null || true
  success "certbot installed: $(certbot --version 2>&1)"
}

# ── Step 2: Prepare webroot directory ─────────────────────────────────────────
prepare_webroot() {
  info "Creating ACME challenge directory: ${WEBROOT_DIR}"
  mkdir -p "${WEBROOT_DIR}/.well-known/acme-challenge"
  chown -R www-data:www-data "${WEBROOT_DIR}" 2>/dev/null || \
  chown -R nginx:nginx       "${WEBROOT_DIR}" 2>/dev/null || true
  chmod -R 755 "${WEBROOT_DIR}"
}

# ── Step 3: Obtain certificate ────────────────────────────────────────────────
obtain_certificate() {
  info "Requesting TLS certificate from Let's Encrypt…"

  local staging_flag=""
  [[ "$STAGING"     == "true" ]] && staging_flag="--staging"
  [[ "$DRY_RUN"     == "true" ]] && staging_flag="$staging_flag --dry-run"
  [[ "$FORCE_RENEW" == "true" ]] && staging_flag="$staging_flag --force-renewal"

  # Build domain flags
  local domain_flags=""
  for d in "${DOMAINS[@]}"; do domain_flags="$domain_flags -d $d"; done

  # Use webroot plugin so nginx stays running during verification
  certbot certonly \
    --webroot \
    --webroot-path "${WEBROOT_DIR}" \
    --email "${EMAIL}" \
    --agree-tos \
    --no-eff-email \
    --non-interactive \
    $staging_flag \
    $domain_flags

  success "Certificate obtained for: ${DOMAINS[*]}"
  info "Certificate path: /etc/letsencrypt/live/${PRIMARY_DOMAIN}/fullchain.pem"
  info "Private key path: /etc/letsencrypt/live/${PRIMARY_DOMAIN}/privkey.pem"
}

# ── Step 4: Generate Diffie-Hellman parameters ────────────────────────────────
generate_dhparam() {
  if [[ -f "$DHPARAM_FILE" ]] && [[ $(wc -c < "$DHPARAM_FILE") -gt 100 ]]; then
    success "DH params already exist: ${DHPARAM_FILE}"
    return 0
  fi

  info "Generating ${DHPARAM_BITS}-bit DH params (this takes 1-3 minutes)…"
  mkdir -p "$(dirname "$DHPARAM_FILE")"
  openssl dhparam -out "${DHPARAM_FILE}" ${DHPARAM_BITS}
  chmod 600 "${DHPARAM_FILE}"
  success "DH params generated: ${DHPARAM_FILE}"
}

# ── Step 5: Update nginx configuration ────────────────────────────────────────
patch_nginx_config() {
  local conf_file="${NGINX_CONF_DIR}/ecom-pipeline.conf"

  if [[ ! -f "$conf_file" ]]; then
    warn "nginx config not found at ${conf_file} — skipping auto-patch"
    info "Manually replace 'YOUR_DOMAIN' with '${PRIMARY_DOMAIN}' in your nginx config"
    return 0
  fi

  info "Patching nginx config: ${conf_file}"
  sed -i "s/YOUR_DOMAIN/${PRIMARY_DOMAIN}/g" "${conf_file}"
  success "Domain '${PRIMARY_DOMAIN}' substituted in nginx config"
}

# ── Step 6: Test and reload nginx ─────────────────────────────────────────────
reload_nginx() {
  info "Testing nginx configuration…"
  nginx -t || die "nginx config test failed — fix errors before reloading"

  info "Reloading nginx…"
  if systemctl is-active nginx &>/dev/null; then
    systemctl reload nginx
  elif docker ps --filter "name=nginx-proxy" --format "{{.Names}}" | grep -q nginx; then
    docker exec nginx-proxy nginx -s reload
  else
    warn "Could not detect running nginx — reload manually: nginx -s reload"
  fi
  success "nginx reloaded"
}

# ── Step 7: Set up automatic renewal ─────────────────────────────────────────
setup_auto_renewal() {
  info "Configuring automatic certificate renewal…"

  # Certbot installs a systemd timer automatically on Ubuntu 22.04
  # Verify it's running:
  if systemctl list-timers snap.certbot.renew.timer &>/dev/null 2>&1; then
    success "Auto-renewal timer: snap.certbot.renew.timer (already active)"
  else
    # Fallback: cron job for non-snap installs
    local cron_line="0 3 * * * root certbot renew --quiet --post-hook 'nginx -s reload'"
    local cron_file="/etc/cron.d/certbot-renew"

    if [[ ! -f "$cron_file" ]] || ! grep -q "certbot renew" "$cron_file"; then
      echo "$cron_line" > "$cron_file"
      chmod 644 "$cron_file"
      success "Cron renewal job created: ${cron_file}"
    else
      success "Cron renewal already configured: ${cron_file}"
    fi
  fi

  # Deploy hook: reload nginx after successful renewal
  local deploy_hook="/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh"
  mkdir -p "$(dirname "$deploy_hook")"
  cat > "$deploy_hook" << 'HOOK'
#!/bin/bash
# Reload nginx after certificate renewal
if systemctl is-active nginx &>/dev/null; then
    systemctl reload nginx
    echo "[certbot] nginx reloaded after renewal at $(date)"
elif docker ps --filter "name=nginx-proxy" --format "{{.Names}}" | grep -q nginx; then
    docker exec nginx-proxy nginx -s reload
    echo "[certbot] Docker nginx reloaded after renewal at $(date)"
fi
HOOK
  chmod +x "$deploy_hook"
  success "Renewal deploy hook: ${deploy_hook}"
}

# ── Step 8: Test renewal (dry-run) ────────────────────────────────────────────
test_renewal() {
  info "Testing certificate renewal (dry-run)…"
  certbot renew --dry-run --quiet && success "Renewal dry-run: OK" \
                                  || warn "Renewal dry-run failed — check certbot logs"
}

# ── Summary ───────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}${BOLD}║   SSL Setup Complete                              ║${NC}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BOLD}Domain:${NC}      ${PRIMARY_DOMAIN}"
  echo -e "  ${BOLD}Full chain:${NC}  /etc/letsencrypt/live/${PRIMARY_DOMAIN}/fullchain.pem"
  echo -e "  ${BOLD}Private key:${NC} /etc/letsencrypt/live/${PRIMARY_DOMAIN}/privkey.pem"
  echo -e "  ${BOLD}DH params:${NC}   ${DHPARAM_FILE}"
  echo -e "  ${BOLD}Auto-renew:${NC}  every 60 days (systemd timer or cron)"
  echo ""
  echo -e "  Verify HTTPS:  ${YELLOW}curl -I https://${PRIMARY_DOMAIN}/health${NC}"
  echo -e "  SSL grade:     ${YELLOW}https://www.ssllabs.com/ssltest/analyze.html?d=${PRIMARY_DOMAIN}${NC}"
  echo ""
  [[ "$STAGING" == "true" ]] && echo -e "  ${YELLOW}⚠  STAGING cert: browsers will show a warning. Re-run without --staging for production.${NC}\n"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
  install_certbot
  prepare_webroot
  obtain_certificate
  generate_dhparam
  patch_nginx_config
  reload_nginx
  setup_auto_renewal
  test_renewal
  print_summary
}

main "$@"

# ==============================================================================
# nginx/Dockerfile — E-Commerce Analytics Reverse Proxy
#
# Build:
#   docker build -t ecom/nginx-proxy:latest nginx/
#
# Run (local dev with self-signed cert):
#   docker compose -f docker/docker-compose.nginx.yml up -d nginx-proxy
#
# Notes:
#   - SSL certificates are MOUNTED as volumes (not baked into the image)
#     so you can rotate certs without rebuilding the image.
#   - The certbot companion container auto-renews and places certs at
#     /etc/letsencrypt (shared volume with this container).
# ==============================================================================

FROM nginx:1.27-alpine AS base

# ── System packages ───────────────────────────────────────────────────────────
RUN apk add --no-cache \
    apache2-utils \
    openssl \
    curl \
    tzdata

# ── Create directories ────────────────────────────────────────────────────────
RUN mkdir -p \
    /etc/nginx/ssl \
    /etc/nginx/auth \
    /etc/nginx/snippets \
    /var/www/certbot \
    /var/log/nginx \
    /usr/share/nginx/html

# ── Copy configuration ────────────────────────────────────────────────────────
COPY nginx.conf                  /etc/nginx/nginx.conf
COPY conf.d/                     /etc/nginx/conf.d/
COPY snippets/                   /etc/nginx/snippets/

# ── Copy custom error pages ───────────────────────────────────────────────────
COPY html/                       /usr/share/nginx/html/

# ── Generate self-signed cert for the catch-all server block (port 443) ──────
# This lets nginx start before Let's Encrypt certs are available.
# Real certs are mounted as volumes at runtime.
RUN openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/dummy.key \
    -out    /etc/nginx/ssl/dummy.crt \
    -subj   "/CN=localhost/O=ecom-pipeline/C=US" \
    2>/dev/null

# ── Generate a placeholder DH params file (replaced by setup-certbot.sh) ─────
RUN openssl dhparam -out /etc/nginx/ssl/dhparam.pem 2048 2>/dev/null

# ── Create an empty htpasswd file (populated by setup-basic-auth.sh) ─────────
RUN touch /etc/nginx/auth/.htpasswd \
 && chmod 640 /etc/nginx/auth/.htpasswd

# ── Permissions ───────────────────────────────────────────────────────────────
RUN chown -R nginx:nginx /var/log/nginx /var/www/certbot

# ── Validate config at build time ─────────────────────────────────────────────
# Commented out because ssl_certificate paths won't exist until runtime.
# Uncomment in CI if you mount dummy certs during the build stage.
# RUN nginx -t

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1/health || exit 1

EXPOSE 80 443

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Run nginx config test before starting
CMD ["sh", "-c", "nginx -t && exec nginx -g 'daemon off;'"]

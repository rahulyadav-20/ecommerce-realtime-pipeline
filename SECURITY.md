# Security Hardening Guide
### E-Commerce Realtime Analytics Pipeline

> **Classification:** Internal — Engineering  
> **Last reviewed:** 2026-05-15  
> **Owner:** Platform Engineering  
> **Review cycle:** Quarterly

---

## Table of Contents

1. [Security Architecture Overview](#1-security-architecture-overview)
2. [Security Checklist](#2-security-checklist)
3. [Network Security](#3-network-security)
4. [Authentication & Authorization](#4-authentication--authorization)
5. [Data Encryption](#5-data-encryption)
6. [Secrets Management](#6-secrets-management)
7. [Container Security](#7-container-security)
8. [Auditing & Logging](#8-auditing--logging)
9. [Compliance](#9-compliance)
10. [Incident Response](#10-incident-response)
11. [Implementation Scripts](#11-implementation-scripts)

---

## 1. Security Architecture Overview

```
Internet
   │
   │  443 (HTTPS only)
   ▼
┌──────────────────────────────────────┐
│          Nginx Reverse Proxy         │  ← TLS termination, rate limiting,
│       (SSL/TLS termination)          │    basic auth for admin routes
└──────────────┬───────────────────────┘
               │  Private Docker network (ecom-net)
               │  No service exposed on host directly
   ┌───────────┼──────────────────────────┐
   ▼           ▼              ▼           ▼
dashboard-api  dashboard-ui  druid   flink
(JWT auth)    (static)      (basic) (basic)
   │
   ├── ClickHouse (native port internal only)
   ├── Apache Druid  (internal only)
   ├── Apache Kafka  (internal only, SASL in prod)
   ├── Redis (AUTH password)
   └── Elasticsearch (internal only, X-Pack in prod)

VPN / Bastion Host
   │
   └── SSH access only — no direct port access to admin UIs
```

**Threat model summary:**

| Threat | Control |
|---|---|
| Unauthenticated API access | JWT on dashboard-api, Basic Auth on admin UIs |
| Data exfiltration via exposed ports | All DB ports hidden behind Docker network |
| Man-in-the-middle | TLS 1.2+ on all external traffic |
| Brute-force login | Rate limiting (30 req/min), account lockout |
| Privilege escalation | Non-root containers, dropped capabilities |
| Supply-chain attack | Image pinning, Trivy scanning in CI |
| Secret leakage | Docker secrets, no env vars in images |
| Kafka message tampering | SASL/SCRAM + SSL in production |

---

## 2. Security Checklist

Use this checklist before every production deployment.  
`scripts/security-audit.sh` automates the verifiable items.

### 2.1 Network

- [ ] **UFW enabled** — default deny inbound, deny outbound
- [ ] **Public ports** — only 22 (SSH), 80 (HTTP→redirect), 443 (HTTPS) open
- [ ] **Admin UIs** — Kafka (9092), ClickHouse (8123/19000), Druid (8888), Flink (8082), Kibana (5601), Airflow (8080), Elasticsearch (9200) **NOT** exposed to internet
- [ ] **SSH** — restricted to VPN/office CIDR only (not `0.0.0.0/0`)
- [ ] **Docker network** — all services on `ecom-net` private bridge; no `network_mode: host`
- [ ] **IPv6** — disabled or explicitly firewalled

### 2.2 Authentication

- [ ] **Nginx Basic Auth** — `.htpasswd` created for `/druid`, `/flink`, `/kibana` routes
- [ ] **Dashboard API** — `WS_SECRET_TOKEN` set and non-empty
- [ ] **Airflow** — admin password changed from default `admin`
- [ ] **MinIO** — `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` changed from `minio / minio123`
- [ ] **Elasticsearch** — X-Pack security enabled in production (not disabled)
- [ ] **Redis** — `requirepass` set in `redis.conf`
- [ ] **ClickHouse** — non-empty `CLICKHOUSE_PASSWORD` for `default` user
- [ ] **Druid** — metadata store password changed from `druid`
- [ ] **Airflow Fernet key** — unique, not the default sample key

### 2.3 Encryption

- [ ] **TLS certificate** — Let's Encrypt cert active; auto-renewal tested
- [ ] **TLS grade** — A or A+ on SSL Labs
- [ ] **DH params** — `/etc/nginx/ssl/dhparam.pem` generated (2048-bit minimum)
- [ ] **HSTS** — `Strict-Transport-Security` header set with `max-age=31536000`
- [ ] **Kafka SSL** — listener security protocol set to `SASL_SSL` in production
- [ ] **Data volume** — `/data` partition encrypted with LUKS

### 2.4 Secrets

- [ ] **No plaintext secrets in `.env` files committed to git**
- [ ] **`.gitignore`** — `docker/.env`, `config/prod.env`, `*.key`, `*.pem` excluded
- [ ] **Docker secrets** — used for database passwords in production Swarm mode
- [ ] **Secret rotation** — all credentials rotated within last 90 days
- [ ] **AWS Secrets Manager / Vault** — integrated (or documented plan to integrate)

### 2.5 Containers

- [ ] **No `:latest` tags** — all images pinned to a specific digest or semver
- [ ] **Non-root users** — containers run as non-root (`user:` directive in Compose)
- [ ] **Read-only filesystem** — enabled where possible (`read_only: true`)
- [ ] **Resource limits** — CPU and memory limits set on all services
- [ ] **Trivy scan** — all images scanned; **zero CRITICAL** vulnerabilities
- [ ] **Capabilities** — `cap_drop: [ALL]` + only required `cap_add`
- [ ] **Seccomp profile** — default Docker seccomp profile applied

### 2.6 Auditing

- [ ] **Access logs** — Nginx access logs in JSON format, shipping to Elasticsearch
- [ ] **Audit logs** — Admin actions (user creation, secret rotation) logged
- [ ] **Log retention** — logs retained for **1 year** minimum
- [ ] **Log integrity** — logs stored on write-once storage or SIEM
- [ ] **Alerting** — automated alerts for 5xx spikes, auth failures, unusual volume

### 2.7 Compliance

- [ ] **GDPR right-to-forget** — `gdpr_right_to_forget` Airflow DAG tested
- [ ] **Data retention** — Kafka topic retention ≤ 7 days; Iceberg snapshots expired
- [ ] **PII masking** — `user_id` hashed before storage in analytics tables
- [ ] **Access log PII** — IP addresses anonymised in long-term storage

---

## 3. Network Security

### 3.1 UFW Firewall Rules

Run `sudo bash scripts/setup-firewall.sh` on every VM. Key rules:

```bash
# ── Public interface ────────────────────────────────────
ufw allow 22/tcp    comment 'SSH (restrict to VPN CIDR in production)'
ufw allow 80/tcp    comment 'HTTP → redirects to HTTPS'
ufw allow 443/tcp   comment 'HTTPS via Nginx'

# ── Deny all admin/database ports from the internet ─────
# These services are accessed through Nginx proxy or VPN only.
ufw deny 9092/tcp   comment 'Kafka — internal only'
ufw deny 8123/tcp   comment 'ClickHouse HTTP — internal only'
ufw deny 19000/tcp  comment 'ClickHouse native — internal only'
ufw deny 8888/tcp   comment 'Druid — internal only'
ufw deny 8082/tcp   comment 'Flink UI — internal only'
ufw deny 9200/tcp   comment 'Elasticsearch — internal only'
ufw deny 5601/tcp   comment 'Kibana — internal only'
ufw deny 8080/tcp   comment 'Airflow — internal only'
ufw deny 9001/tcp   comment 'MinIO Console — internal only'
ufw deny 6379/tcp   comment 'Redis — internal only'

ufw default deny incoming
ufw default allow outgoing
ufw enable
```

**Restrict SSH to VPN/office CIDR** (replace `10.8.0.0/16` with your VPN subnet):

```bash
ufw delete allow 22/tcp
ufw allow from 10.8.0.0/16 to any port 22 proto tcp comment 'SSH from VPN only'
```

### 3.2 Docker Network Isolation

All services communicate on the `ecom-net` private bridge network. No service should use `network_mode: host` in production.

```yaml
# docker/docker-compose.yml — verify these settings
networks:
  ecom-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16   # private; not routable externally

# ── Each service ────────────────────────────────────────
services:
  clickhouse:
    ports:
      # ✅ GOOD: only expose on localhost for direct access during dev
      - "127.0.0.1:8123:8123"
      - "127.0.0.1:19000:9000"
      # ❌ BAD (never in production):
      # - "0.0.0.0:8123:8123"
```

### 3.3 VPN for Admin Access

Use WireGuard or OpenVPN for accessing admin UIs directly (bypassing Nginx basic auth):

```bash
# Install WireGuard on Ubuntu 22.04
apt-get install -y wireguard

# Generate server key pair
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key

# /etc/wireguard/wg0.conf — minimal config
cat > /etc/wireguard/wg0.conf << 'EOF'
[Interface]
Address    = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <server_private_key>
PostUp     = ufw allow 51820/udp
PostDown   = ufw delete allow 51820/udp

[Peer]
# Add one [Peer] block per operator
PublicKey  = <client_public_key>
AllowedIPs = 10.8.0.2/32
EOF

systemctl enable --now wg-quick@wg0

# Add UFW rule for WireGuard
ufw allow 51820/udp comment 'WireGuard VPN'
```

Once connected, operators access admin UIs at their Docker host IPs directly:
- Flink: `http://10.8.0.1:8082`
- Druid: `http://10.8.0.1:8888`
- Kibana: `http://10.8.0.1:5601`

---

## 4. Authentication & Authorization

### 4.1 Nginx Basic Auth (Druid, Flink, Kibana)

Already implemented via `/etc/nginx/auth/.htpasswd`.  
Setup: `sudo bash scripts/setup-basic-auth.sh -u admin -u ops-readonly`

Password policy:
- Minimum 16 characters
- Rotate every 90 days
- Use a password manager (1Password, Bitwarden, AWS Secrets Manager)

### 4.2 Dashboard API — JWT Authentication

Add JWT middleware to `dashboard-api/app/main.py`:

```python
# dashboard-api/app/middleware/auth.py
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os

security  = HTTPBearer(auto_error=False)
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGO   = "HS256"

async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Apply to protected routes in routers:
# @router.get("/kpis/realtime", dependencies=[Depends(verify_token)])
```

Generate tokens (valid 24 h):

```python
import jwt, datetime, secrets
payload = {
    "sub":  "dashboard-user",
    "role": "read-only",
    "exp":  datetime.datetime.utcnow() + datetime.timedelta(hours=24),
    "jti":  secrets.token_hex(16),   # unique token ID for revocation
}
token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
```

### 4.3 Kafka SASL/SCRAM Authentication (Production)

```bash
# Create SCRAM credentials (run on Kafka broker)
docker exec kafka-1 kafka-configs \
  --bootstrap-server kafka-1:9092 \
  --alter \
  --add-config 'SCRAM-SHA-512=[password=flink-secret]' \
  --entity-type users --entity-name flink-etl

docker exec kafka-1 kafka-configs \
  --bootstrap-server kafka-1:9092 \
  --alter \
  --add-config 'SCRAM-SHA-512=[password=clickhouse-secret]' \
  --entity-type users --entity-name clickhouse-consumer
```

Flink SASL config in `JobConfig.java`:

```java
properties.setProperty("security.protocol",        "SASL_SSL");
properties.setProperty("sasl.mechanism",            "SCRAM-SHA-512");
properties.setProperty("sasl.jaas.config",
    "org.apache.kafka.common.security.scram.ScramLoginModule required "
  + "username=\"flink-etl\" "
  + "password=\"" + System.getenv("KAFKA_PASSWORD") + "\";");
```

### 4.4 Airflow

```bash
# Change default admin password
docker exec airflow-webserver airflow users create \
  --username admin \
  --firstname Ops \
  --lastname Team \
  --role Admin \
  --email ops@company.com \
  --password "$(openssl rand -base64 32)"

# Rotate Fernet key (re-encrypts all stored connections)
# 1. Generate new key:
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 2. Add both keys to AIRFLOW__CORE__FERNET_KEY (comma-separated) for rotation
# 3. Run: airflow rotate-fernet-key
# 4. Remove old key
```

### 4.5 MinIO

```bash
# Change default credentials
docker exec minio mc alias set local http://localhost:9000 minio minio123
docker exec minio mc admin user add local new-admin "$(openssl rand -base64 32)"
docker exec minio mc admin policy attach local readwrite --user new-admin
docker exec minio mc admin user disable local minio   # disable default user
```

---

## 5. Data Encryption

### 5.1 TLS — All HTTP Traffic

Managed by Nginx (see `nginx/conf.d/ecom-pipeline.conf` and `DEPLOYMENT_VM.md`).

Verify certificate grade:

```bash
# SSL Labs test (automated)
curl -s "https://api.ssllabs.com/api/v3/analyze?host=YOUR_DOMAIN&all=done" \
  | jq '.endpoints[0].grade'
# Expected: "A" or "A+"

# Local certificate check
openssl s_client -connect YOUR_DOMAIN:443 -servername YOUR_DOMAIN 2>/dev/null \
  | openssl x509 -noout -dates -subject
```

### 5.2 Kafka SSL (Broker ↔ Client)

```bash
# Generate CA + broker + client certificates
mkdir -p kafka-ssl && cd kafka-ssl

# 1. Create CA
openssl req -new -x509 -keyout ca-key.pem -out ca-cert.pem \
  -days 365 -passout pass:ca-password \
  -subj "/CN=kafka-ca/O=ecom/C=IN"

# 2. Generate broker keystore for each broker
for broker in kafka-1 kafka-2; do
  keytool -genkey -keystore "${broker}.keystore.jks" \
    -alias "${broker}" -validity 365 \
    -storepass keystore-password \
    -dname "CN=${broker},O=ecom,C=IN"

  # Sign with CA
  keytool -certreq -keystore "${broker}.keystore.jks" \
    -alias "${broker}" -storepass keystore-password \
    -file "${broker}.csr"

  openssl x509 -req -CA ca-cert.pem -CAkey ca-key.pem \
    -in "${broker}.csr" -out "${broker}-signed.crt" \
    -days 365 -CAcreateserial -passin pass:ca-password

  keytool -importcert -keystore "${broker}.keystore.jks" \
    -alias CARoot -file ca-cert.pem -storepass keystore-password -noprompt

  keytool -importcert -keystore "${broker}.keystore.jks" \
    -alias "${broker}" -file "${broker}-signed.crt" \
    -storepass keystore-password -noprompt
done

# 3. Broker docker-compose.yml environment additions
# KAFKA_SSL_KEYSTORE_FILENAME: kafka-1.keystore.jks
# KAFKA_SSL_KEYSTORE_CREDENTIALS: keystore-password
# KAFKA_SSL_KEY_CREDENTIALS: key-password
# KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,SSL:SSL,SASL_SSL:SASL_SSL
```

### 5.3 At-Rest Encryption — LUKS

Encrypt the `/data` volume before deploying any services:

```bash
# !! Do this on a fresh disk — data will be erased !!

DATA_DEVICE="/dev/sdb"     # adjust to your device
MAPPER_NAME="ecom-data"

# Install cryptsetup
apt-get install -y cryptsetup

# Create LUKS container
cryptsetup luksFormat --type luks2 \
  --hash sha256 --cipher aes-xts-plain64 \
  --key-size 512 --pbkdf argon2id \
  "$DATA_DEVICE"

# Open the encrypted volume
cryptsetup open "$DATA_DEVICE" "$MAPPER_NAME"

# Create filesystem
mkfs.ext4 -L ecom-data /dev/mapper/"$MAPPER_NAME"

# Mount
mkdir -p /data
mount /dev/mapper/"$MAPPER_NAME" /data

# Auto-mount on boot (via keyfile — more secure than passphrase for servers)
dd if=/dev/urandom bs=512 count=4 > /etc/luks-keys/ecom-data.key
chmod 600 /etc/luks-keys/ecom-data.key
cryptsetup luksAddKey "$DATA_DEVICE" /etc/luks-keys/ecom-data.key

echo "$MAPPER_NAME  $DATA_DEVICE  /etc/luks-keys/ecom-data.key  luks" \
  >> /etc/crypttab
echo "/dev/mapper/$MAPPER_NAME  /data  ext4  defaults  0 2" >> /etc/fstab
```

### 5.4 Redis AUTH

```yaml
# docker/docker-compose.yml — update redis-cache service
redis-cache:
  command: >
    redis-server
      --requirepass "${REDIS_PASSWORD}"
      --maxmemory 512mb
      --maxmemory-policy volatile-lru
```

```bash
# In dashboard-api .env:
REDIS_URL=redis://:${REDIS_PASSWORD}@redis-cache:6379
```

---

## 6. Secrets Management

### 6.1 What NOT to do ❌

```bash
# Never do this:
MINIO_SECRET_KEY=minio123               # weak default
CLICKHOUSE_PASSWORD=                     # empty password
JWT_SECRET=my-secret                    # hardcoded in source
docker run -e DB_PASS=password123 ...   # visible in process list
```

### 6.2 Docker Secrets (Swarm Mode) ✅

```bash
# Create secrets from random values
openssl rand -base64 32 | docker secret create minio_secret_key -
openssl rand -base64 32 | docker secret create redis_password -
openssl rand -base64 48 | docker secret create jwt_secret -
openssl rand -base64 32 | docker secret create clickhouse_password -
openssl rand -base64 32 | docker secret create airflow_fernet_key -

# Reference in docker-compose.yml (Swarm):
services:
  dashboard-api:
    secrets:
      - jwt_secret
      - redis_password
    environment:
      JWT_SECRET_FILE:    /run/secrets/jwt_secret
      REDIS_PASSWORD_FILE: /run/secrets/redis_password

secrets:
  jwt_secret:       { external: true }
  redis_password:   { external: true }
```

Application reads secret from file:

```python
# dashboard-api/app/config.py
import os

def _read_secret(env_var: str, file_var: str) -> str:
    """Read from Docker secret file or fall back to env var."""
    file_path = os.getenv(file_var)
    if file_path and os.path.exists(file_path):
        return open(file_path).read().strip()
    return os.getenv(env_var, "")

JWT_SECRET = _read_secret("JWT_SECRET", "JWT_SECRET_FILE")
```

### 6.3 HashiCorp Vault Integration (Recommended for Production)

```bash
# Install Vault (Ubuntu)
wget -qO- https://apt.releases.hashicorp.com/gpg | gpg --dearmor \
  | tee /usr/share/keyrings/hashicorp.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] \
  https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | tee /etc/apt/sources.list.d/hashicorp.list
apt-get update && apt-get install -y vault

# Store pipeline secrets
vault kv put secret/ecom-pipeline/prod \
  minio_access_key="$(openssl rand -hex 16)" \
  minio_secret_key="$(openssl rand -base64 32)" \
  redis_password="$(openssl rand -base64 32)" \
  jwt_secret="$(openssl rand -base64 48)" \
  clickhouse_password="$(openssl rand -base64 32)"

# Retrieve at deploy time
MINIO_SECRET_KEY=$(vault kv get -field=minio_secret_key secret/ecom-pipeline/prod)
export MINIO_SECRET_KEY
```

### 6.4 AWS Secrets Manager

```bash
# Store
aws secretsmanager create-secret \
  --name "ecom-pipeline/prod" \
  --secret-string '{
    "MINIO_SECRET_KEY":    "'"$(openssl rand -base64 32)"'",
    "REDIS_PASSWORD":      "'"$(openssl rand -base64 32)"'",
    "JWT_SECRET":          "'"$(openssl rand -base64 48)"'",
    "CLICKHOUSE_PASSWORD": "'"$(openssl rand -base64 32)"'"
  }'

# Retrieve at startup
aws secretsmanager get-secret-value \
  --secret-id ecom-pipeline/prod \
  --query SecretString --output text \
  | jq -r 'to_entries[] | "export \(.key)=\(.value)"' \
  | source /dev/stdin
```

### 6.5 Secret Rotation Policy

| Secret | Rotation Period | Automation |
|---|---|---|
| TLS certificates | 90 days (auto via certbot) | Certbot timer |
| JWT signing key | 90 days | `scripts/rotate-secrets.sh` |
| MinIO credentials | 90 days | Manual + Vault rotation |
| Redis AUTH password | 90 days | `scripts/rotate-secrets.sh` |
| Database passwords | 90 days | Manual + Vault rotation |
| Kafka SASL passwords | 180 days | Manual |
| SSH host keys | Never (unless compromised) | — |
| SSH user keys | 365 days | Manual |

Run `bash scripts/rotate-secrets.sh --check` to see which secrets are overdue.

---

## 7. Container Security

### 7.1 Non-Root Users

```yaml
# docker/docker-compose.yml — add user: directive
services:
  dashboard-api:
    user: "1001:1001"           # must match USER in Dockerfile

  dashboard-ui:
    user: "nginx:nginx"

  flink-jobmanager:
    user: "flink:flink"         # flink image supports this
```

```dockerfile
# dashboard-api/Dockerfile — create non-root user
FROM python:3.12-slim
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ ./app/
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 7.2 Read-Only Root Filesystem

```yaml
# docker/docker-compose.yml
services:
  dashboard-api:
    read_only: true
    tmpfs:
      - /tmp              # allow writing to /tmp only
    volumes:
      - /app/logs:/app/logs   # allow writing logs

  dashboard-ui:
    read_only: true
    tmpfs:
      - /var/cache/nginx
      - /var/run
```

### 7.3 Drop Capabilities

```yaml
services:
  dashboard-api:
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE   # only if binding port < 1024
    security_opt:
      - no-new-privileges:true
```

### 7.4 Resource Limits

```yaml
services:
  dashboard-api:
    deploy:
      resources:
        limits:
          cpus:    "1.0"
          memory:  512M
        reservations:
          cpus:    "0.25"
          memory:  256M

  flink-jobmanager:
    deploy:
      resources:
        limits:
          cpus:    "2.0"
          memory:  4G

  clickhouse:
    deploy:
      resources:
        limits:
          cpus:    "4.0"
          memory:  8G
```

### 7.5 Image Security Scanning (Trivy)

```bash
# Install Trivy
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
  | sh -s -- -b /usr/local/bin v0.50.0

# Scan all pipeline images — fail on CRITICAL
for image in \
  "ecom/dashboard-api:latest" \
  "ecom/dashboard-ui:latest" \
  "ecom/nginx-proxy:latest" \
  "flink:1.19-java17" \
  "clickhouse/clickhouse-server:24.3" \
  "confluentinc/cp-kafka:7.6.1"; do
    echo "=== Scanning: $image ==="
    trivy image \
      --severity CRITICAL,HIGH \
      --exit-code 1 \
      --ignore-unfixed \
      "$image" || echo "VULNERABILITIES FOUND in $image"
done
```

Add to CI/CD pipeline (GitHub Actions):

```yaml
# .github/workflows/security-scan.yml
- name: Trivy scan
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: ecom/dashboard-api:${{ github.sha }}
    format: sarif
    output: trivy-results.sarif
    severity: CRITICAL,HIGH
    exit-code: 1
```

### 7.6 Image Pinning (No `:latest` in Production)

```yaml
# ❌ Development only — never use :latest in production
image: flink:latest

# ✅ Production — pin to digest for immutability
image: flink:1.19.1-java17@sha256:abc123...

# Get digest:
docker image inspect flink:1.19-java17 \
  --format '{{index .RepoDigests 0}}'
```

---

## 8. Auditing & Logging

### 8.1 What to Audit

| Event | Source | Retention |
|---|---|---|
| HTTP requests | Nginx access log (JSON) | 1 year |
| HTTP errors (4xx, 5xx) | Nginx error log | 1 year |
| Admin UI logins | Nginx auth log | 1 year |
| API calls with JWT | dashboard-api | 1 year |
| Kafka consumer group changes | Kafka audit log | 90 days |
| Docker daemon events | journald | 90 days |
| System auth (SSH login, sudo) | `/var/log/auth.log` | 1 year |
| Secret access | Vault / AWS CloudTrail | 1 year |
| Container start/stop | Docker events | 90 days |
| Flink job submissions | Flink history server | 6 months |

### 8.2 Centralized Logging to ELK

Filebeat ships all logs to Elasticsearch. Index pattern: `ecom-audit-*`.

```yaml
# elk/filebeat.yml — additional inputs for audit
filebeat.inputs:
  - type: log
    paths: ["/var/log/auth.log", "/var/log/syslog"]
    fields: { log_type: system, service: host }

  - type: journald
    id: docker-daemon
    include_matches:
      - "_SYSTEMD_UNIT=docker.service"
    fields: { log_type: docker }
```

### 8.3 Alerting Rules (Kibana)

Create these detection rules in Kibana → Security → Rules:

```json
// Alert: More than 10 auth failures in 5 minutes from one IP
{
  "name": "Nginx Auth Failure Spike",
  "query": "status:401 AND uri:(\\/druid OR \\/flink OR \\/kibana)",
  "threshold": { "field": "remote_addr", "value": 10 },
  "window": "5m",
  "severity": "high"
}

// Alert: Any SSH login as root
{
  "name": "Root SSH Login",
  "query": "log_type:system AND message:\"Accepted * for root\"",
  "severity": "critical"
}

// Alert: Container stopped unexpectedly
{
  "name": "Container Crash",
  "query": "log_type:docker AND message:\"die\" AND NOT message:\"exited with code 0\"",
  "severity": "medium"
}
```

### 8.4 Log Retention

```bash
# Elasticsearch ILM policy — 1-year retention
curl -X PUT "http://elasticsearch:9200/_ilm/policy/ecom-audit-policy" \
  -H "Content-Type: application/json" -d '{
  "policy": {
    "phases": {
      "hot":    { "min_age": "0ms",  "actions": { "rollover": { "max_size": "50GB", "max_age": "30d" } } },
      "warm":   { "min_age": "30d",  "actions": { "shrink":   { "number_of_shards": 1 } } },
      "cold":   { "min_age": "90d",  "actions": { "freeze":   {} } },
      "delete": { "min_age": "365d", "actions": { "delete":   {} } }
    }
  }
}'
```

---

## 9. Compliance

### 9.1 GDPR

**Data inventory:**

| Data | Location | Retention | Legal basis |
|---|---|---|---|
| `user_id` (hashed) | ClickHouse `events` table | 2 years | Legitimate interest |
| Raw events | Kafka topic | 7 days | Legitimate interest |
| Iceberg events_clean | MinIO S3 | 1 year | Legitimate interest |
| Access logs (IP) | Elasticsearch | 1 year | Legal obligation |

**Right to Forget:**
The `gdpr_right_to_forget` Airflow DAG (`airflow/dags/gdpr_right_to_forget.py`) handles deletion:

```bash
# Trigger deletion for a user (Airflow UI or CLI)
docker exec airflow-scheduler airflow dags trigger gdpr_right_to_forget \
  --conf '{"user_id": "usr-abc123", "reason": "user_request", "ticket": "GDPR-2026-042"}'
```

This DAG:
1. Issues positional deletes in Iceberg (format-v2, no rewrite needed)
2. Deletes rows from ClickHouse `events` table
3. Resets Kafka consumer offset does NOT help (events already consumed)
4. Logs the deletion with timestamp and ticket reference

**Data minimisation:**
- `user_id` is hashed (SHA-256) before storage in ClickHouse
- `ip_address` is anonymised in access logs after 30 days
- No PII stored in Elasticsearch event indices (only `user_id` hash)

### 9.2 PCI-DSS (if handling card data)

If the pipeline processes payment card data, the following additional controls apply:

- [ ] **PAN never stored** — `price` field must never contain full card numbers
- [ ] **Tokenisation** — use Stripe/Adyen tokenisation; store token only
- [ ] **Encryption in transit** — TLS 1.2+ (already implemented)
- [ ] **Access control** — least-privilege: only payment team has access to payment logs
- [ ] **Vulnerability scans** — quarterly ASV scans (Qualys, Rapid7)
- [ ] **Penetration testing** — annual pen test required
- [ ] **Cardholder data environment** — segment payment services from analytics services

**Payment data exclusion in Flink:**

```java
// ValidateAndClean.java — strip sensitive payment fields
private static ClickstreamEvent sanitisePaymentFields(ClickstreamEvent event) {
    if (event.getEventType().equals("PAYMENT_SUCCESS")) {
        event.setCardNumber(null);   // never propagate PAN
        event.setCvv(null);
    }
    return event;
}
```

### 9.3 SOC 2 Type II

For SOC 2 audit readiness:

**CC6 — Logical Access Controls:**
- [ ] All access requires authentication (JWT, Basic Auth, SSH key)
- [ ] MFA enabled for all human admin accounts
- [ ] Access review performed quarterly — document in JIRA/Linear
- [ ] Onboarding/offboarding process documented
- [ ] Service accounts use separate credentials

**CC7 — System Operations:**
- [ ] Change management: all infrastructure changes via PR + approval
- [ ] Deployment pipeline with automated security scanning
- [ ] Incident response plan documented and tested annually

**CC8 — Change Management:**
- [ ] All changes to production require PR review (at least one approver)
- [ ] Security checklist (this document) reviewed before each release
- [ ] Rollback plan documented for each deployment

**A1 — Availability:**
- [ ] RTO ≤ 4 hours, RPO ≤ 1 hour
- [ ] Monitoring and alerting for all critical services
- [ ] Disaster recovery runbook tested semi-annually

---

## 10. Incident Response

### Response Levels

| Severity | Description | Response Time | Escalation |
|---|---|---|---|
| P1 — Critical | Data breach, service unavailable | 15 minutes | CISO + CTO |
| P2 — High | Auth bypass, secret exposure | 1 hour | Security lead + Engineering manager |
| P3 — Medium | Unusual access pattern, cert expiry | 4 hours | On-call engineer |
| P4 — Low | Failed login spike, performance anomaly | 24 hours | Next business day |

### P1 Playbook — Suspected Data Breach

```bash
# 1. IMMEDIATE: Isolate affected services
docker compose -f docker/docker-compose.yml stop kafka-1 kafka-2

# 2. Preserve evidence (do not wipe logs)
tar -czf /tmp/incident-$(date +%Y%m%d-%H%M%S).tar.gz \
  /var/log/nginx/ /var/log/auth.log

# 3. Rotate ALL secrets immediately
bash scripts/rotate-secrets.sh --emergency --all

# 4. Block suspicious IPs
ufw insert 1 deny from <suspicious-ip>

# 5. Notify (within 72 hours for GDPR)
# - Internal: security@company.com
# - GDPR DPA if EU user data affected: reportable within 72h of discovery

# 6. Revoke and reissue all JWT tokens
# Update JWT_SECRET → all existing tokens immediately invalid

# 7. Document in incident log
echo "$(date): Incident started. Affected: <services>. Action: <taken>" \
  >> /var/log/security-incidents.log
```

---

## 11. Implementation Scripts

| Script | Purpose |
|---|---|
| `scripts/setup-firewall.sh` | Configure UFW rules |
| `scripts/setup-basic-auth.sh` | Create/manage htpasswd users |
| `scripts/setup-certbot.sh` | Obtain and auto-renew TLS certificates |
| `scripts/rotate-secrets.sh` | Rotate credentials on schedule |
| `scripts/security-audit.sh` | Automated security posture check |

Run the full security audit at any time:

```bash
bash scripts/security-audit.sh
# Outputs: PASS / FAIL / WARN for each control
# Exit code: 0 = all pass, N = number of failures
```

---

## Document History

| Date | Version | Author | Change |
|---|---|---|---|
| 2026-05-15 | 1.0 | Platform Engineering | Initial release |

*Next review: 2026-08-15*

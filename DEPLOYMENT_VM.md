# VM Deployment Guide
### E-Commerce Realtime Analytics Pipeline — Ubuntu 22.04 LTS

> **Target:** Single VM deployment (16 GB RAM, 8 vCPU, 100 GB SSD).  
> **Time:** ~45 minutes end-to-end including Docker image pulls.

---

## Table of Contents

1. [VM Provisioning & OS Setup](#1-vm-provisioning--os-setup)
2. [Install Docker + Docker Compose](#2-install-docker--docker-compose)
3. [Install Java 17 (Flink build)](#3-install-java-17-flink-build)
4. [Install Python 3.11 + Dependencies](#4-install-python-311--dependencies)
5. [Clone Repository & Configure Environment](#5-clone-repository--configure-environment)
6. [Build Flink JAR](#6-build-flink-jar)
7. [Start Core Pipeline Stack](#7-start-core-pipeline-stack)
8. [Start Dashboard Layer](#8-start-dashboard-layer)
9. [Firewall & Port Exposure](#9-firewall--port-exposure)
10. [Nginx Reverse Proxy + TLS (optional)](#10-nginx-reverse-proxy--tls-optional)
11. [Systemd Auto-Start](#11-systemd-auto-start)
12. [Smoke Tests](#12-smoke-tests)
13. [Resource Tuning](#13-resource-tuning)
14. [Maintenance Cheatsheet](#14-maintenance-cheatsheet)

---

## 1. VM Provisioning & OS Setup

### Recommended specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU      | 4 vCPU  | 8 vCPU      |
| RAM      | 12 GB   | 16–32 GB    |
| Disk     | 40 GB SSD | 100 GB SSD (NVMe) |
| OS       | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| Network  | 100 Mbps | 1 Gbps |

### Initial setup (run as root or with sudo)

```bash
# Update package index
sudo apt-get update && sudo apt-get upgrade -y

# Install essential tools
sudo apt-get install -y \
  curl wget git unzip jq \
  ca-certificates gnupg lsb-release \
  htop ncdu net-tools

# Set timezone
sudo timedatectl set-timezone Asia/Kolkata

# Increase file descriptor limits (required by Elasticsearch and Kafka)
sudo tee -a /etc/security/limits.conf <<'EOF'
*       soft    nofile  65536
*       hard    nofile  65536
root    soft    nofile  65536
root    hard    nofile  65536
EOF

# Increase virtual memory map count (required by Elasticsearch)
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Disable swap (recommended for Kafka and Flink performance)
sudo swapoff -a
sudo sed -i '/swap/d' /etc/fstab
```

---

## 2. Install Docker + Docker Compose

```bash
# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Start and enable Docker
sudo systemctl enable docker
sudo systemctl start docker

# Add your user to the docker group (log out and back in after this)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version            # Docker version 27.x
docker compose version      # Docker Compose version v2.x
```

---

## 3. Install Java 17 (Flink build)

```bash
sudo apt-get install -y openjdk-17-jdk-headless

# Verify
java -version   # openjdk 17.x

# Set JAVA_HOME
echo 'export JAVA_HOME=$(readlink -f /usr/bin/java | sed "s:/bin/java::")' >> ~/.bashrc
source ~/.bashrc
```

---

## 4. Install Python 3.11 + Dependencies

```bash
sudo apt-get install -y python3.11 python3.11-venv python3-pip

# Create a project-level venv
python3.11 -m venv ~/venv/ecom
source ~/venv/ecom/bin/activate

# Verify
python3 --version   # Python 3.11.x
```

---

## 5. Clone Repository & Configure Environment

```bash
# Clone
git clone https://github.com/your-org/ecommerce-realtime-pipeline.git
cd ecommerce-realtime-pipeline

# Install Python dependencies (event generator, Airflow DAGs)
source ~/venv/ecom/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Copy and edit the Docker env file
cp docker/.env.example docker/.env   # if an example exists, otherwise edit docker/.env directly
```

### Key variables to review in `docker/.env`

```bash
# Open and adjust these if needed:
nano docker/.env
```

| Variable | Default | Notes |
|----------|---------|-------|
| `KAFKA_REPLICATION_FACTOR` | `2` | Reduce to `1` on single-node VM |
| `KAFKA_MIN_ISR` | `1` | Keep at `1` for single-broker |
| `FLINK_TASK_SLOTS` | `4` | Tune to (vCPUs / 2) |
| `MINIO_ACCESS_KEY` | `minio` | Change in production |
| `MINIO_SECRET_KEY` | `minio123` | **Change in production** |
| `AIRFLOW__CORE__FERNET_KEY` | (generated) | Keep this secret |

---

## 6. Build Flink JAR

```bash
# Install Maven
sudo apt-get install -y maven

# Build (skip tests for speed)
mvn -f flink-app/pom.xml package -DskipTests -q

# Verify JAR
ls -lh flink-app/target/ecom-streaming-etl-*.jar
```

> **Skip this step** if you are using the pre-built `flink-app/ecom-streaming-etl.jar`.  
> Pass `--skip-build` to `setup.sh` in that case.

---

## 7. Start Core Pipeline Stack

```bash
# Full automated setup (all 7 phases)
chmod +x setup.sh
./setup.sh

# ─── OR phase-by-phase ────────────────────────────────────────────────────────

# Phase 1: Kafka + Schema Registry
docker compose -f docker/docker-compose.yml up -d \
  zookeeper kafka-1 kafka-2 kafka-init schema-registry schema-registry-ui kafka-ui
bash scripts/register-schema.sh

# Phase 2: Flink + MinIO
docker compose -f docker/docker-compose.yml up -d \
  minio minio-setup flink-jobmanager flink-taskmanager-1 flink-taskmanager-2

# Phase 3: Druid
docker compose -f docker/docker-compose.yml up -d \
  druid-postgres druid-coordinator druid-broker druid-historical \
  druid-middlemanager druid-router
sleep 60
bash scripts/submit-druid-supervisor.sh

# Phase 4: ClickHouse
docker compose -f docker/docker-compose.yml up -d clickhouse ch-ui

# Phase 5: Iceberg + Kafka Connect
docker compose -f docker/docker-compose.yml up -d iceberg-rest kafka-connect
sleep 90
bash scripts/submit-iceberg-connector.sh

# Phase 6: ELK
docker compose -f docker/docker-compose.yml up -d \
  elasticsearch logstash kibana filebeat metricbeat heartbeat

# Phase 7: Airflow
docker compose -f docker/docker-compose.yml up -d \
  airflow-postgres airflow-init
sleep 30
docker compose -f docker/docker-compose.yml up -d airflow-webserver airflow-scheduler
```

**Wait for all containers to be healthy (~10 minutes on first pull):**

```bash
docker compose -f docker/docker-compose.yml ps
```

---

## 8. Start Dashboard Layer

```bash
# Build and start Redis + FastAPI + React UI
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dashboard.yml \
  up -d --build redis dashboard-api dashboard-ui

# Verify
curl -sf http://localhost:8000/api/v1/health | jq .status   # → "healthy"
curl -sf -o /dev/null -w "%{http_code}" http://localhost:3000  # → 200
```

---

## 9. Firewall & Port Exposure

### Using `ufw` (Ubuntu Firewall)

```bash
sudo ufw allow OpenSSH

# Expose only the dashboard to the internet (recommended)
sudo ufw allow 3000/tcp comment "React Dashboard"
sudo ufw allow 8000/tcp comment "FastAPI API"

# Internal services — restrict to your IP or VPN (replace YOUR_IP)
sudo ufw allow from YOUR_IP to any port 8082 comment "Flink UI"
sudo ufw allow from YOUR_IP to any port 8085 comment "Kafka UI"
sudo ufw allow from YOUR_IP to any port 8888 comment "Druid UI"
sudo ufw allow from YOUR_IP to any port 8123 comment "ClickHouse"
sudo ufw allow from YOUR_IP to any port 8124 comment "ClickHouse UI"
sudo ufw allow from YOUR_IP to any port 8181 comment "Iceberg REST"
sudo ufw allow from YOUR_IP to any port 9001 comment "MinIO Console"
sudo ufw allow from YOUR_IP to any port 5601 comment "Kibana"
sudo ufw allow from YOUR_IP to any port 8080 comment "Airflow"

sudo ufw enable
sudo ufw status verbose
```

### Port reference

| Port | Service | Public? |
|------|---------|---------|
| 3000 | React Dashboard | Yes (or via Nginx) |
| 8000 | FastAPI Backend | Yes (or via Nginx) |
| 8080 | Airflow UI | Private |
| 8082 | Flink UI | Private |
| 8085 | Kafka UI | Private |
| 8088 | Druid Router | Private |
| 8123 | ClickHouse HTTP | Private |
| 8124 | ClickHouse UI | Private |
| 8181 | Iceberg REST | Private |
| 9001 | MinIO Console | Private |
| 5601 | Kibana | Private |
| 9200 | Elasticsearch | Private |

---

## 10. Nginx Reverse Proxy + TLS (optional)

Use Nginx to serve the dashboard on port 443 with a Let's Encrypt certificate.

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Replace dashboard.example.com with your domain
DOMAIN="dashboard.example.com"

sudo tee /etc/nginx/sites-available/ecom-dashboard <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass         http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host \$host;
        proxy_cache_bypass \$http_upgrade;
    }

    location /api/ {
        proxy_pass         http://localhost:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/ecom-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Issue Let's Encrypt certificate
sudo certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m admin@example.com

# Certbot auto-renewal is handled by a systemd timer — verify:
sudo systemctl status certbot.timer
```

---

## 11. Systemd Auto-Start

Create a systemd service so the pipeline restarts automatically after VM reboots.

```bash
PROJECT_DIR="$(pwd)"

sudo tee /etc/systemd/system/ecom-pipeline.service <<EOF
[Unit]
Description=E-Commerce Realtime Analytics Pipeline
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/docker compose -f docker/docker-compose.yml -f docker/docker-compose.dashboard.yml up -d
ExecStop=/usr/bin/docker compose -f docker/docker-compose.yml -f docker/docker-compose.dashboard.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ecom-pipeline.service
sudo systemctl start  ecom-pipeline.service

# Check status
sudo systemctl status ecom-pipeline.service
```

---

## 12. Smoke Tests

Run after deployment to confirm end-to-end data flow.

```bash
# 1. All containers healthy
docker compose -f docker/docker-compose.yml ps | grep -v healthy

# 2. API health
curl -sf http://localhost:8000/api/v1/health | jq '{status, components: (.components | to_entries | map({(.key): .value.status}) | add)}'

# 3. Send test events
source ~/venv/ecom/bin/activate
python3 src/utils/event_generator.py --rate 1000 --duration 30

# 4. Confirm events reach Kafka (offsets should increase)
docker exec kafka-1 kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka-1:9092 --topic ecommerce.events.clean.v1 --time -1

# 5. Confirm ClickHouse is consuming
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+ecom.events"

# 6. Confirm Druid has data
curl -sf -X POST http://localhost:8888/druid/v2/sql \
  -H "Content-Type: application/json" \
  -d '{"query":"SELECT COUNT(*) AS cnt FROM \"ecommerce_events\""}' | jq .

# 7. Dashboard API KPIs
curl -sf http://localhost:8000/api/v1/kpis/realtime | jq .

# 8. React dashboard reachable
curl -sf -o /dev/null -w "HTTP %{http_code}\n" http://localhost:3000
```

---

## 13. Resource Tuning

### Docker memory limits

Edit `docker/.env` to constrain container memory (important on 16 GB VMs):

```bash
# Elasticsearch and Kibana heap
ES_JAVA_OPTS=-Xms1g -Xmx2g
LS_JAVA_OPTS=-Xms512m -Xmx1g

# Flink TaskManager memory
FLINK_TASKMANAGER_MEMORY=3072m

# ClickHouse memory cap
CLICKHOUSE_MAX_MEMORY=4000000000
```

### Swap (only if necessary)

```bash
# Create 4 GB swap file if workloads exceed available RAM
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Disk I/O scheduling (SSD optimisation)

```bash
# Set I/O scheduler to none for NVMe/SSD
echo none | sudo tee /sys/block/sda/queue/scheduler
```

---

## 14. Maintenance Cheatsheet

```bash
# View all container resource usage
docker stats --no-stream

# Tail logs of a specific service
docker logs -f dashboard-api --tail=100

# Update images and restart
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.dashboard.yml \
               pull && \
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.dashboard.yml \
               up -d

# Rebuild dashboard without downtime
docker compose -f docker/docker-compose.dashboard.yml \
  build dashboard-api dashboard-ui && \
docker compose -f docker/docker-compose.dashboard.yml \
  up -d --no-deps dashboard-api dashboard-ui

# Prune unused images / volumes (reclaim disk)
docker system prune -f
docker volume prune -f   # WARNING: removes volumes not attached to containers

# Backup ClickHouse
docker exec -i $(docker compose -f docker/docker-compose.yml ps -q clickhouse) \
  clickhouse-client --query \
  "BACKUP DATABASE ecom TO Disk('backups', 'ecom-$(date +%Y%m%d).zip')"

# Manual Flink savepoint
JOB_ID=$(curl -s http://localhost:8082/jobs \
  | jq -r '.jobs[]|select(.status=="RUNNING")|.id' | head -1)
curl -X POST "http://localhost:8082/jobs/$JOB_ID/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"cancel-job": false, "target-directory": "/opt/flink/savepoints"}'

# Check VM disk usage
df -h
du -sh /var/lib/docker/volumes/*

# Monitor VM CPU and memory
htop
```

---

## Post-Deployment URL Summary

| Service | URL | Notes |
|---------|-----|-------|
| **React Dashboard** | `http://VM_IP:3000` | Main user interface |
| **FastAPI Docs** | `http://VM_IP:8000/docs` | Interactive API explorer |
| **Prometheus Metrics** | `http://VM_IP:8000/metrics` | Scrape target |
| Kafka UI | `http://VM_IP:8085` | Restrict to VPN |
| Flink UI | `http://VM_IP:8082` | Restrict to VPN |
| Druid UI | `http://VM_IP:8888` | Restrict to VPN |
| ClickHouse UI | `http://VM_IP:8124` | Restrict to VPN |
| MinIO Console | `http://VM_IP:9001` | minio / minio123 |
| Kibana | `http://VM_IP:5601` | Restrict to VPN |
| Airflow | `http://VM_IP:8080` | admin / admin |

#!/bin/bash

# ============================================================================
# E-Commerce Real-Time Pipeline Setup Script
# ============================================================================

set -e  # Exit on error

echo "=========================================="
echo "E-Commerce Pipeline Setup"
echo "=========================================="

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

# Check Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}Docker Compose is not installed. Please install Docker Compose first.${NC}"
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is not installed. Please install Python 3.9+ first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites check passed${NC}"

# Create necessary directories
echo -e "${YELLOW}Creating project directories...${NC}"

mkdir -p config
mkdir -p airflow/{dags,logs,plugins}
mkdir -p src/{streaming,druid,utils}
mkdir -p tests/{unit,integration}
mkdir -p scripts
mkdir -p docker

echo -e "${GREEN}✓ Directories created${NC}"

# Create virtual environment
echo -e "${YELLOW}Creating Python virtual environment...${NC}"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${YELLOW}Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
#source venv/bin/activate

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install --upgrade pip
#pip install -r requirements.txt

echo -e "${GREEN}✓ Python dependencies installed${NC}"

# Create environment file for Docker
echo -e "${YELLOW}Creating Docker environment files...${NC}"

cat > docker/.env << EOF
# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=ecommerce-events

# Storage Configuration
STORAGE_TYPE=hdfs
HDFS_NAMENODE=localhost:9000

# Spark Configuration
SPARK_MASTER=spark://localhost:7077

# Druid Configuration
DRUID_COORDINATOR_URL=http://localhost:8081
DRUID_BROKER_URL=http://localhost:8082
DRUID_ROUTER_URL=http://localhost:8888

# Monitoring
LOG_LEVEL=INFO
EOF

# Create Hadoop environment file
cat > docker/config/hadoop.env << EOF
CORE_CONF_fs_defaultFS=hdfs://namenode:9000
CORE_CONF_hadoop_http_staticuser_user=root
CORE_CONF_hadoop_proxyuser_hue_hosts=*
CORE_CONF_hadoop_proxyuser_hue_groups=*
CORE_CONF_io_compression_codecs=org.apache.hadoop.io.compress.SnappyCodec

HDFS_CONF_dfs_webhdfs_enabled=true
HDFS_CONF_dfs_permissions_enabled=false
HDFS_CONF_dfs_namenode_datanode_registration_ip___hostname___check=false

YARN_CONF_yarn_log___aggregation___enable=true
YARN_CONF_yarn_log_server_url=http://historyserver:8188/applicationhistory/logs/
YARN_CONF_yarn_resourcemanager_recovery_enabled=true
YARN_CONF_yarn_resourcemanager_store_class=org.apache.hadoop.yarn.server.resourcemanager.recovery.FileSystemRMStateStore
YARN_CONF_yarn_resourcemanager_scheduler_class=org.apache.hadoop.yarn.server.resourcemanager.scheduler.capacity.CapacityScheduler
YARN_CONF_yarn_scheduler_capacity_root_default_maximum___allocation___mb=8192
YARN_CONF_yarn_scheduler_capacity_root_default_maximum___allocation___vcores=4
YARN_CONF_yarn_resourcemanager_fs_state___store_uri=/rmstate
YARN_CONF_yarn_resourcemanager_system___metrics___publisher_enabled=true
YARN_CONF_yarn_resourcemanager_hostname=resourcemanager
YARN_CONF_yarn_resourcemanager_address=resourcemanager:8032
YARN_CONF_yarn_resourcemanager_scheduler_address=resourcemanager:8030
YARN_CONF_yarn_resourcemanager_resource__tracker_address=resourcemanager:8031
YARN_CONF_yarn_timeline___service_enabled=true
YARN_CONF_yarn_timeline___service_generic___application___history_enabled=true
YARN_CONF_yarn_timeline___service_hostname=historyserver
YARN_CONF_mapreduce_map_output_compress=true
YARN_CONF_mapred_map_output_compress_codec=org.apache.hadoop.io.compress.SnappyCodec
YARN_CONF_yarn_nodemanager_resource_memory___mb=16384
YARN_CONF_yarn_nodemanager_resource_cpu___vcores=8
YARN_CONF_yarn_nodemanager_disk___health___checker_max___disk___utilization___per___disk___percentage=98.5
YARN_CONF_yarn_nodemanager_remote___app___log___dir=/app-logs
YARN_CONF_yarn_nodemanager_aux___services=mapreduce_shuffle

MAPRED_CONF_mapreduce_framework_name=yarn
MAPRED_CONF_mapred_child_java_opts=-Xmx4096m
MAPRED_CONF_mapreduce_map_memory_mb=4096
MAPRED_CONF_mapreduce_reduce_memory_mb=8192
MAPRED_CONF_mapreduce_map_java_opts=-Xmx3072m
MAPRED_CONF_mapreduce_reduce_java_opts=-Xmx6144m
MAPRED_CONF_yarn_app_mapreduce_am_env=HADOOP_MAPRED_HOME=/opt/hadoop-3.2.1/
MAPRED_CONF_mapreduce_map_env=HADOOP_MAPRED_HOME=/opt/hadoop-3.2.1/
MAPRED_CONF_mapreduce_reduce_env=HADOOP_MAPRED_HOME=/opt/hadoop-3.2.1/
EOF

echo -e "${GREEN}✓ Environment files created${NC}"

# Create sample configuration YAML
cat > config/pipeline_config.yaml << EOF
kafka:
  bootstrap_servers: "localhost:9092"
  topic: "ecommerce-events"
  druid_topic: "druid-ecommerce-metrics"

storage:
  storage_type: "hdfs"
  bronze_path: "hdfs://localhost:9000/data/bronze/ecommerce_events"
  silver_path: "hdfs://localhost:9000/data/silver/ecommerce"
  checkpoint_location: "hdfs://localhost:9000/checkpoints/ecommerce_pipeline"

streaming:
  trigger_interval: "30 seconds"
  watermark_delay: "10 minutes"

spark:
  app_name: "EcommerceRealtimePipeline"
  master: "spark://localhost:7077"
  shuffle_partitions: 6

monitoring:
  log_level: "INFO"
  max_kafka_lag: 10000
EOF

echo -e "${GREEN}✓ Configuration files created${NC}"

# Start Docker services
echo -e "${YELLOW}Starting Docker services...${NC}"
cd docker
docker-compose up -d

echo -e "${YELLOW}Waiting for services to be healthy (this may take 2-3 minutes)...${NC}"
sleep 120

# Check service health
echo -e "${YELLOW}Checking service health...${NC}"

services=("zookeeper" "kafka" "namenode" "postgres" "spark-master")
all_healthy=true

for service in "${services[@]}"; do
    if docker ps --filter "name=$service" --filter "status=running" | grep -q "$service"; then
        echo -e "${GREEN}✓ $service is running${NC}"
    else
        echo -e "${RED}✗ $service is not running${NC}"
        all_healthy=false
    fi
done

if [ "$all_healthy" = false ]; then
    echo -e "${RED}Some services are not running. Please check docker logs.${NC}"
    exit 1
fi

# Create Kafka topics
echo -e "${YELLOW}Creating Kafka topics...${NC}"

docker exec kafka kafka-topics --create \
    --topic ecommerce-events \
    --partitions 3 \
    --replication-factor 1 \
    --bootstrap-server localhost:9092 \
    --if-not-exists

docker exec kafka kafka-topics --create \
    --topic druid-ecommerce-metrics \
    --partitions 3 \
    --replication-factor 1 \
    --bootstrap-server localhost:9092 \
    --if-not-exists

echo -e "${GREEN}✓ Kafka topics created${NC}"

# Create HDFS directories
echo -e "${YELLOW}Creating HDFS directories...${NC}"

docker exec namenode hdfs dfs -mkdir -p /data/bronze/ecommerce_events
docker exec namenode hdfs dfs -mkdir -p /data/silver/ecommerce
docker exec namenode hdfs dfs -mkdir -p /data/gold/ecommerce
docker exec namenode hdfs dfs -mkdir -p /checkpoints/ecommerce_pipeline
docker exec namenode hdfs dfs -mkdir -p /reports

docker exec namenode hdfs dfs -chmod -R 777 /data
docker exec namenode hdfs dfs -chmod -R 777 /checkpoints
docker exec namenode hdfs dfs -chmod -R 777 /reports

echo -e "${GREEN}✓ HDFS directories created${NC}"

# Initialize Airflow database
echo -e "${YELLOW}Initializing Airflow database...${NC}"

docker exec airflow-webserver airflow db init || true
docker exec airflow-webserver airflow users create \
    --username admin \
    --password admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@example.com || true

echo -e "${GREEN}✓ Airflow initialized${NC}"

cd ..

echo ""
echo "=========================================="
echo -e "${GREEN}Setup completed successfully!${NC}"
echo "=========================================="
echo ""
echo "Service URLs:"
echo "  - Spark Master UI:     http://localhost:8080"
echo "  - HDFS NameNode UI:    http://localhost:9870"
echo "  - Druid Router:        http://localhost:8888"
echo "  - Airflow UI:          http://localhost:8081 (admin/admin)"
echo ""
echo "Next steps:"
echo "  1. Activate venv:      source venv/bin/activate"
echo "  2. Generate test data: python src/utils/kafka_producer.py"
echo "  3. Start pipeline:     ./scripts/start_pipeline.sh"
echo ""

#!/bin/bash

# ============================================================================
# Start E-Commerce Real-Time Pipeline
# ============================================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "=========================================="
echo "Starting E-Commerce Pipeline"
echo "=========================================="

# Check if services are running
echo -e "${YELLOW}Checking services...${NC}"

if ! docker ps | grep -q kafka; then
    echo -e "${RED}Kafka is not running. Please run setup.sh first.${NC}"
    exit 1
fi

if ! docker ps | grep -q spark-master; then
    echo -e "${RED}Spark is not running. Please run setup.sh first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All required services are running${NC}"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo -e "${RED}Virtual environment not found. Please run setup.sh first.${NC}"
    exit 1
fi

# Submit Spark streaming job
echo -e "${YELLOW}Submitting Spark streaming job...${NC}"

spark-submit \
    --master spark://localhost:7077 \
    --deploy-mode client \
    --name "EcommerceRealtimePipeline" \
    --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.2 \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    --conf spark.sql.catalog.spark_catalog=org.apache.iceberg.spark.SparkSessionCatalog \
    --conf spark.sql.catalog.spark_catalog.type=hive \
    --conf spark.sql.catalog.local=org.apache.iceberg.spark.SparkCatalog \
    --conf spark.sql.catalog.local.type=hadoop \
    --conf spark.sql.catalog.local.warehouse=hdfs://localhost:9000/data/silver \
    --conf spark.sql.shuffle.partitions=6 \
    --conf spark.sql.adaptive.enabled=true \
    --conf spark.executor.memory=2g \
    --conf spark.driver.memory=1g \
    --conf spark.executor.cores=2 \
    --driver-class-path /opt/spark/jars/postgresql-42.6.0.jar \
    src/streaming/main.py

echo -e "${GREEN}Pipeline started!${NC}"
echo ""
echo "Monitor the pipeline:"
echo "  - Spark UI:     http://localhost:4040"
echo "  - Spark Master: http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop the pipeline"

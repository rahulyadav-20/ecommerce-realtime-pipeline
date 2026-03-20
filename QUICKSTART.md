# Quick Start Guide

## Prerequisites

- **Docker Desktop**: 16GB RAM, 4+ CPUs, 20GB free disk
- **Python**: 3.9 or higher
- **Operating System**: Linux, macOS, or Windows with WSL2

## 5-Minute Setup

### Step 1: Clone and Setup
```bash
cd ecommerce-realtime-pipeline
chmod +x scripts/*.sh
./scripts/setup.sh
```

This will:
- Create virtual environment
- Install Python dependencies
- Start all Docker services (Kafka, Spark, Druid, HDFS, Airflow)
- Create Kafka topics
- Initialize HDFS directories
- Setup Airflow

**Wait time**: ~3-5 minutes

### Step 2: Verify Services

Check that all services are running:
```bash
docker-compose -f docker/docker-compose.yml ps
```

You should see 16 containers running.

Access service UIs:
- **Spark Master**: http://localhost:8080
- **HDFS NameNode**: http://localhost:9870
- **Druid Console**: http://localhost:8888
- **Airflow**: http://localhost:8081 (admin/admin)

### Step 3: Generate Test Data

In a new terminal:
```bash
source venv/bin/activate
./scripts/generate_data.sh
```

This starts generating 100 events/second. Keep it running.

### Step 4: Start Streaming Pipeline

In another terminal:
```bash
source venv/bin/activate
./scripts/start_pipeline.sh
```

Monitor the pipeline at http://localhost:4040 (Spark Streaming UI)

### Step 5: Verify Data Flow

**Check Bronze Layer (HDFS)**:
```bash
docker exec namenode hdfs dfs -ls /data/bronze/ecommerce_events
```

**Check Silver Layer (Iceberg)**:
```bash
docker exec namenode hdfs dfs -ls /data/silver/ecommerce
```

**Query Druid**:
```bash
curl -X POST http://localhost:8888/druid/v2/sql \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "SELECT COUNT(*) as event_count FROM ecommerce_events_realtime WHERE __time > CURRENT_TIMESTAMP - INTERVAL '\''1'\'' HOUR"
  }'
```

## Common Commands

### Stop Pipeline
```bash
# Press Ctrl+C in the terminal running start_pipeline.sh
```

### Stop All Services
```bash
cd docker
docker-compose down
```

### View Logs
```bash
# Spark logs
docker logs spark-master

# Kafka logs
docker logs kafka

# Druid logs
docker logs druid-coordinator
```

### Restart Services
```bash
cd docker
docker-compose restart
```

### Clean Everything
```bash
cd docker
docker-compose down -v  # Warning: This deletes all data!
```

## Troubleshooting

### Services Not Starting
```bash
# Check Docker resources
docker stats

# Increase Docker memory to 16GB in Docker Desktop settings
```

### Kafka Connection Issues
```bash
# Check Kafka is running
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Recreate topics
docker exec kafka kafka-topics --delete --topic ecommerce-events --bootstrap-server localhost:9092
docker exec kafka kafka-topics --create --topic ecommerce-events --partitions 3 --replication-factor 1 --bootstrap-server localhost:9092
```

### HDFS Permission Issues
```bash
docker exec namenode hdfs dfs -chmod -R 777 /data
docker exec namenode hdfs dfs -chmod -R 777 /checkpoints
```

### Spark Job Fails
```bash
# Check Spark master is accessible
curl http://localhost:8080

# Check worker registration
docker logs spark-worker-1
```

## Next Steps

1. **Explore Druid Queries**: See `src/druid/sample_queries.sql`
2. **Customize Configuration**: Edit `config/pipeline_config.yaml`
3. **Add Custom Metrics**: Modify `src/streaming/aggregations.py`
4. **Setup Monitoring**: Configure `airflow/dags/ecommerce_pipeline_dags.py`
5. **Run Backfill**: Trigger the backfill DAG in Airflow UI

## Architecture Diagram

```
┌─────────────┐
│   Kafka     │  Events: 100/sec
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   PySpark   │  Processing: 30s trigger
│  Streaming  │
└──┬─────┬─┬──┘
   │     │ │
   │     │ └──────┐
   │     │        │
   ▼     ▼        ▼
┌──────┐ ┌─────┐ ┌─────┐
│Bronze│ │Silver│ │Druid│
│ HDFS │ │Iceberg│ │OLAP│
└──────┘ └─────┘ └─────┘
```

## Key Metrics to Monitor

1. **Kafka Lag**: Should be < 10,000
2. **Processing Latency**: Should be < 5 minutes
3. **Data Freshness**: Should be < 10 minutes
4. **Checkpoint Success Rate**: Should be 100%

Monitor these in Airflow at http://localhost:8081

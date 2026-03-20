#!/bin/bash
set -e

# Check if already initialized
if [ -f /opt/airflow/.airflow_initialized ]; then
  echo "Airflow already initialized. Skipping..."
  exit 0
fi

echo "Initializing Airflow database..."
airflow db init

echo "Creating admin user..."
airflow users create \
  --username admin \
  --password admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com 2>/dev/null || true

# Mark as initialized
touch /opt/airflow/.airflow_initialized

echo "Airflow initialization complete!"

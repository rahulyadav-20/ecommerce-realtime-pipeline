#!/bin/bash

# ============================================================================
# Generate Test Data for E-Commerce Pipeline
# ============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================="
echo "Starting Test Data Generation"
echo "=========================================="

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found. Please run setup.sh first."
    exit 1
fi

# Check if Kafka is running
if ! docker ps | grep -q kafka; then
    echo "Kafka is not running. Please start Docker services first."
    exit 1
fi

echo -e "${YELLOW}Starting event generator...${NC}"
echo ""
echo "Target rate: 100 events/second"
echo "Press Ctrl+C to stop"
echo ""

# Run the Kafka producer
python src/utils/kafka_producer.py

echo -e "${GREEN}Event generator stopped${NC}"

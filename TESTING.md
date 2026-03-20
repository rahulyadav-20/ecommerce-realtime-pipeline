# Testing Guide

## Overview
This guide covers unit testing, integration testing, and end-to-end testing for the E-Commerce Real-Time Pipeline.

## Test Structure

```
tests/
├── unit/
│   ├── test_schema.py
│   ├── test_transformations.py
│   └── test_aggregations.py
├── integration/
│   ├── test_kafka_integration.py
│   ├── test_druid_integration.py
│   └── test_end_to_end.py
└── conftest.py
```

## Unit Tests

### Testing Schema Validation

```python
# tests/unit/test_schema.py
import pytest
from src.streaming.schema import validate_event, VALID_EVENT_TYPES

def test_valid_payment_success():
    is_valid, error = validate_event(
        event_type='payment_success',
        quantity=2,
        price=599.99,
        order_id='order_123',
        payment_mode='credit_card'
    )
    assert is_valid == True
    assert error is None

def test_invalid_payment_no_mode():
    is_valid, error = validate_event(
        event_type='payment_success',
        quantity=2,
        price=599.99,
        order_id='order_123',
        payment_mode=None
    )
    assert is_valid == False
    assert 'Missing payment_mode' in error

def test_invalid_event_type():
    is_valid, error = validate_event(
        event_type='invalid_type',
        quantity=1,
        price=100.0,
        order_id=None,
        payment_mode=None
    )
    assert is_valid == False
    assert 'Invalid event_type' in error
```

### Testing Transformations

```python
# tests/unit/test_transformations.py
import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import *
from src.streaming.transformations import clean_and_standardize

@pytest.fixture(scope="module")
def spark():
    return SparkSession.builder \
        .master("local[2]") \
        .appName("test") \
        .getOrCreate()

def test_price_rounding(spark):
    data = [
        ("evt_1", "user_1", "prod_1", "Electronics", None, "view_product", 
         1, 99.999, None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, 0, "2026-01-01T00:00:00Z")
    ]
    
    schema = StructType([
        StructField("event_id", StringType()),
        StructField("user_id", StringType()),
        StructField("product_id", StringType()),
        StructField("category", StringType()),
        StructField("order_id", StringType()),
        StructField("event_type", StringType()),
        StructField("quantity", IntegerType()),
        StructField("price", DoubleType()),
        StructField("payment_mode", StringType()),
        StructField("event_time", StringType()),
        StructField("ingestion_time", StringType()),
        StructField("kafka_partition", IntegerType()),
        StructField("kafka_offset", LongType()),
        StructField("processing_time", StringType())
    ])
    
    df = spark.createDataFrame(data, schema)
    result = clean_and_standardize(df)
    
    price = result.select("price").collect()[0][0]
    assert price == 100.0

def test_category_capitalization(spark):
    data = [
        ("evt_1", "user_1", "prod_1", "electronics", None, "view_product", 
         None, None, None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, 0, "2026-01-01T00:00:00Z")
    ]
    
    schema = StructType([...])  # Same as above
    
    df = spark.createDataFrame(data, schema)
    result = clean_and_standardize(df)
    
    category = result.select("category").collect()[0][0]
    assert category == "Electronics"
```

### Testing Aggregations

```python
# tests/unit/test_aggregations.py
import pytest
from pyspark.sql import SparkSession
from datetime import datetime
from src.streaming.aggregations import create_5min_aggregations

@pytest.fixture(scope="module")
def spark():
    return SparkSession.builder \
        .master("local[2]") \
        .appName("test") \
        .getOrCreate()

def test_revenue_calculation(spark):
    # Create test data
    data = [
        ("2026-01-01T10:00:00Z", "payment_success", "Electronics", 2, 100.0),
        ("2026-01-01T10:01:00Z", "payment_success", "Electronics", 1, 50.0),
        ("2026-01-01T10:02:00Z", "view_product", "Electronics", None, None),
    ]
    
    df = spark.createDataFrame(data, ["event_time", "event_type", "category", "quantity", "price"])
    df = df.withColumn("event_time", df.event_time.cast("timestamp"))
    
    # No watermark in test (would cause issues with batch processing)
    result = create_5min_aggregations(df, watermark_delay="0 seconds")
    
    # Check results
    result_data = result.collect()
    
    # Should have revenue of 250.0 for Electronics payment_success
    electronics_payment = [r for r in result_data 
                          if r.event_type == "payment_success" 
                          and r.category == "Electronics"][0]
    
    assert electronics_payment.total_revenue == 250.0
    assert electronics_payment.event_count == 2
```

## Integration Tests

### Testing Kafka Integration

```python
# tests/integration/test_kafka_integration.py
import pytest
import json
from kafka import KafkaProducer, KafkaConsumer
from datetime import datetime

@pytest.fixture
def kafka_producer():
    return KafkaProducer(
        bootstrap_servers='localhost:9092',
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )

@pytest.fixture
def kafka_consumer():
    return KafkaConsumer(
        'ecommerce-events',
        bootstrap_servers='localhost:9092',
        auto_offset_reset='earliest',
        value_deserializer=lambda v: json.loads(v.decode('utf-8'))
    )

def test_produce_and_consume_event(kafka_producer, kafka_consumer):
    # Produce test event
    test_event = {
        'event_id': 'test_evt_001',
        'user_id': 'test_user_001',
        'product_id': 'test_prod_001',
        'category': 'Electronics',
        'event_type': 'view_product',
        'event_time': datetime.utcnow().isoformat() + 'Z'
    }
    
    kafka_producer.send('ecommerce-events', value=test_event)
    kafka_producer.flush()
    
    # Consume and verify
    for message in kafka_consumer:
        if message.value['event_id'] == 'test_evt_001':
            assert message.value['user_id'] == 'test_user_001'
            assert message.value['category'] == 'Electronics'
            break
```

### Testing Druid Integration

```python
# tests/integration/test_druid_integration.py
import pytest
import requests
import time

def test_druid_datasource_exists():
    # Check if datasource exists
    response = requests.get('http://localhost:8888/druid/coordinator/v1/datasources')
    assert response.status_code == 200
    
    datasources = response.json()
    assert 'ecommerce_events_realtime' in datasources

def test_druid_query():
    # Simple aggregation query
    query = {
        "queryType": "timeseries",
        "dataSource": "ecommerce_events_realtime",
        "granularity": "minute",
        "intervals": ["2026-01-01/2026-12-31"],
        "aggregations": [
            {"type": "count", "name": "count"}
        ]
    }
    
    response = requests.post(
        'http://localhost:8888/druid/v2/',
        json=query
    )
    
    assert response.status_code == 200
    result = response.json()
    assert isinstance(result, list)
```

## End-to-End Test

```python
# tests/integration/test_end_to_end.py
import pytest
import json
import time
from kafka import KafkaProducer
from pyspark.sql import SparkSession
import requests

def test_full_pipeline():
    """
    Test complete pipeline flow:
    1. Send event to Kafka
    2. Wait for processing
    3. Verify in HDFS Bronze layer
    4. Verify in Iceberg Silver layer
    5. Verify in Druid
    """
    
    # 1. Send test event
    producer = KafkaProducer(
        bootstrap_servers='localhost:9092',
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    test_event_id = f'e2e_test_{int(time.time())}'
    event = {
        'event_id': test_event_id,
        'user_id': 'e2e_user',
        'product_id': 'e2e_prod',
        'category': 'Electronics',
        'event_type': 'payment_success',
        'quantity': 1,
        'price': 999.99,
        'payment_mode': 'credit_card',
        'order_id': 'e2e_order',
        'event_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    }
    
    producer.send('ecommerce-events', value=event)
    producer.flush()
    
    # 2. Wait for processing (adjust based on trigger interval)
    time.sleep(60)
    
    # 3. Verify in Bronze layer (HDFS)
    spark = SparkSession.builder.getOrCreate()
    bronze_df = spark.read.parquet('hdfs://localhost:9000/data/bronze/ecommerce_events')
    
    bronze_count = bronze_df.filter(bronze_df.event_id == test_event_id).count()
    assert bronze_count >= 1, "Event not found in Bronze layer"
    
    # 4. Verify in Silver layer (Iceberg)
    silver_df = spark.read.format('iceberg').load('hdfs://localhost:9000/data/silver/ecommerce/ecommerce_events')
    
    silver_count = silver_df.filter(silver_df.event_id == test_event_id).count()
    assert silver_count >= 1, "Event not found in Silver layer"
    
    # 5. Verify in Druid (may take longer due to segment publishing)
    time.sleep(120)  # Wait for Druid ingestion
    
    druid_query = {
        "queryType": "scan",
        "dataSource": "ecommerce_events_realtime",
        "intervals": ["2026-01-01/2026-12-31"],
        "filter": {
            "type": "selector",
            "dimension": "event_type",
            "value": "payment_success"
        },
        "columns": ["event_count", "total_revenue"],
        "limit": 100
    }
    
    response = requests.post('http://localhost:8888/druid/v2/', json=druid_query)
    assert response.status_code == 200
```

## Running Tests

### Run All Tests
```bash
pytest tests/ -v
```

### Run Unit Tests Only
```bash
pytest tests/unit/ -v
```

### Run Integration Tests Only
```bash
pytest tests/integration/ -v
```

### Run with Coverage
```bash
pytest tests/ --cov=src --cov-report=html
```

## Performance Testing

### Load Testing Script
```python
# tests/performance/load_test.py
import time
from src.utils.kafka_producer import EcommerceEventGenerator, KafkaEventProducer

def run_load_test(target_rate=1000, duration_seconds=300):
    """
    Run load test at specified event rate
    """
    generator = EcommerceEventGenerator()
    producer = KafkaEventProducer('localhost:9092', 'ecommerce-events')
    
    events_sent = 0
    start_time = time.time()
    
    while time.time() - start_time < duration_seconds:
        batch_start = time.time()
        
        # Generate batch
        batch_size = 100
        events = generator.generate_batch(batch_size)
        producer.send_batch(events)
        
        events_sent += batch_size
        
        # Sleep to maintain rate
        elapsed = time.time() - batch_start
        target_duration = batch_size / target_rate
        if elapsed < target_duration:
            time.sleep(target_duration - elapsed)
    
    total_time = time.time() - start_time
    actual_rate = events_sent / total_time
    
    print(f"Load test complete:")
    print(f"  Events sent: {events_sent}")
    print(f"  Duration: {total_time:.2f}s")
    print(f"  Target rate: {target_rate} events/s")
    print(f"  Actual rate: {actual_rate:.2f} events/s")
```

## Test Data Fixtures

```python
# tests/conftest.py
import pytest
from pyspark.sql import SparkSession

@pytest.fixture(scope="session")
def spark_session():
    spark = SparkSession.builder \
        .master("local[2]") \
        .appName("test") \
        .config("spark.sql.shuffle.partitions", "2") \
        .getOrCreate()
    
    yield spark
    
    spark.stop()

@pytest.fixture
def sample_events():
    return [
        {
            'event_id': 'evt_001',
            'user_id': 'user_001',
            'product_id': 'prod_001',
            'category': 'Electronics',
            'event_type': 'view_product',
            'event_time': '2026-01-01T10:00:00Z'
        },
        {
            'event_id': 'evt_002',
            'user_id': 'user_001',
            'product_id': 'prod_001',
            'category': 'Electronics',
            'event_type': 'add_to_cart',
            'quantity': 1,
            'price': 599.99,
            'event_time': '2026-01-01T10:01:00Z'
        }
    ]
```

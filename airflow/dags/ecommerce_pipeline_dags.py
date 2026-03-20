"""
Airflow DAG for E-Commerce Pipeline Backfill
Handles historical data reprocessing and batch jobs
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.sensors.external_task import ExternalTaskSensor
import logging

logger = logging.getLogger(__name__)


# Default arguments
default_args = {
    'owner': 'data-engineering',
    'depends_on_past': False,
    'email': ['data-team@company.com'],
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
    'execution_timeout': timedelta(hours=2),
}


# DAG definition
dag = DAG(
    'ecommerce_backfill_pipeline',
    default_args=default_args,
    description='Backfill historical e-commerce data',
    schedule_interval=None,  # Manual trigger only
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['ecommerce', 'backfill', 'batch'],
    params={
        'start_date': '2026-01-01',
        'end_date': '2026-01-31',
        'reprocess_mode': 'append'  # 'append' or 'overwrite'
    }
)


def validate_date_range(**context):
    """Validate input date range"""
    params = context['params']
    start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
    end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')
    
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")
    
    if (end_date - start_date).days > 365:
        raise ValueError("Date range cannot exceed 365 days")
    
    logger.info(f"Validated date range: {start_date} to {end_date}")
    return True


def check_source_data_availability(**context):
    """Check if source data exists for the date range"""
    from pyspark.sql import SparkSession
    
    params = context['params']
    start_date = params['start_date']
    end_date = params['end_date']
    
    spark = SparkSession.builder.appName("SourceDataCheck").getOrCreate()
    
    try:
        # Check Bronze layer for data
        bronze_path = f"hdfs://localhost:9000/data/bronze/ecommerce_events"
        df = spark.read.parquet(bronze_path)
        
        # Filter by date range
        count = df.filter(
            (df.event_date >= start_date) & (df.event_date <= end_date)
        ).count()
        
        logger.info(f"Found {count} events in date range")
        
        if count == 0:
            raise ValueError(f"No data found for date range {start_date} to {end_date}")
        
        return count
        
    finally:
        spark.stop()


def create_temp_views(**context):
    """Create temporary views for processing"""
    from pyspark.sql import SparkSession
    
    params = context['params']
    
    spark = SparkSession.builder \
        .appName("CreateTempViews") \
        .config("spark.sql.adaptive.enabled", "true") \
        .getOrCreate()
    
    try:
        bronze_path = "hdfs://localhost:9000/data/bronze/ecommerce_events"
        df = spark.read.parquet(bronze_path)
        
        df.filter(
            (df.event_date >= params['start_date']) & 
            (df.event_date <= params['end_date'])
        ).createOrReplaceTempView("backfill_events")
        
        logger.info("Created temporary view: backfill_events")
        
    finally:
        spark.stop()


def generate_metrics_report(**context):
    """Generate summary metrics report"""
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import count, sum, avg, countDistinct
    
    params = context['params']
    
    spark = SparkSession.builder.appName("MetricsReport").getOrCreate()
    
    try:
        silver_path = "hdfs://localhost:9000/data/silver/ecommerce/ecommerce_events"
        df = spark.read.format("iceberg").load(silver_path)
        
        # Generate summary metrics
        metrics = df.filter(
            (df.event_date >= params['start_date']) & 
            (df.event_date <= params['end_date'])
        ).groupBy("event_date").agg(
            count("*").alias("total_events"),
            countDistinct("user_id").alias("unique_users"),
            countDistinct("product_id").alias("unique_products"),
            sum("revenue").alias("total_revenue")
        ).orderBy("event_date")
        
        # Write to report location
        report_path = f"hdfs://localhost:9000/reports/backfill_{params['start_date']}_{params['end_date']}"
        metrics.coalesce(1).write.mode("overwrite").parquet(report_path)
        
        logger.info(f"Metrics report written to {report_path}")
        
        # Log summary
        total_events = metrics.agg(sum("total_events")).collect()[0][0]
        logger.info(f"Backfill completed: {total_events} events processed")
        
    finally:
        spark.stop()


def cleanup_temp_data(**context):
    """Clean up temporary processing data"""
    import subprocess
    
    logger.info("Cleaning up temporary data...")
    
    # Clean up Spark temporary files
    subprocess.run([
        "hdfs", "dfs", "-rm", "-r", "-skipTrash",
        f"/tmp/spark-backfill-{context['run_id']}"
    ], check=False)
    
    logger.info("Cleanup completed")


# Task 1: Validate inputs
validate_task = PythonOperator(
    task_id='validate_date_range',
    python_callable=validate_date_range,
    provide_context=True,
    dag=dag
)

# Task 2: Check source data
check_data_task = PythonOperator(
    task_id='check_source_data',
    python_callable=check_source_data_availability,
    provide_context=True,
    dag=dag
)

# Task 3: Create temp views
temp_views_task = PythonOperator(
    task_id='create_temp_views',
    python_callable=create_temp_views,
    provide_context=True,
    dag=dag
)

# Task 4: Run Spark backfill job
backfill_task = SparkSubmitOperator(
    task_id='run_backfill',
    application='/opt/airflow/dags/scripts/backfill_job.py',
    name='ecommerce-backfill',
    conf={
        'spark.sql.adaptive.enabled': 'true',
        'spark.sql.adaptive.coalescePartitions.enabled': 'true',
        'spark.dynamicAllocation.enabled': 'true',
    },
    application_args=[
        '--start-date', '{{ params.start_date }}',
        '--end-date', '{{ params.end_date }}',
        '--mode', '{{ params.reprocess_mode }}'
    ],
    verbose=True,
    dag=dag
)

# Task 5: Regenerate aggregations
regenerate_agg_task = SparkSubmitOperator(
    task_id='regenerate_aggregations',
    application='/opt/airflow/dags/scripts/regenerate_aggregations.py',
    name='ecommerce-aggregations',
    application_args=[
        '--start-date', '{{ params.start_date }}',
        '--end-date', '{{ params.end_date }}'
    ],
    verbose=True,
    dag=dag
)

# Task 6: Update Druid segments
update_druid_task = BashOperator(
    task_id='update_druid_segments',
    bash_command="""
    curl -X POST http://localhost:8888/druid/indexer/v1/task \
        -H 'Content-Type: application/json' \
        -d @/opt/airflow/dags/druid/batch_ingestion_spec.json
    """,
    dag=dag
)

# Task 7: Generate report
report_task = PythonOperator(
    task_id='generate_metrics_report',
    python_callable=generate_metrics_report,
    provide_context=True,
    dag=dag
)

# Task 8: Cleanup
cleanup_task = PythonOperator(
    task_id='cleanup_temp_data',
    python_callable=cleanup_temp_data,
    provide_context=True,
    trigger_rule='all_done',  # Run even if upstream fails
    dag=dag
)

# Task 9: Data quality checks
quality_check_task = BashOperator(
    task_id='run_quality_checks',
    bash_command="""
    python /opt/airflow/dags/scripts/data_quality_checks.py \
        --start-date {{ params.start_date }} \
        --end-date {{ params.end_date }}
    """,
    dag=dag
)

# Define task dependencies
validate_task >> check_data_task >> temp_views_task
temp_views_task >> backfill_task >> regenerate_agg_task
regenerate_agg_task >> update_druid_task >> quality_check_task
quality_check_task >> report_task >> cleanup_task


# ============================================================================
# MONITORING DAG
# ============================================================================

monitoring_dag = DAG(
    'ecommerce_pipeline_monitoring',
    default_args=default_args,
    description='Monitor streaming pipeline health',
    schedule_interval='*/5 * * * *',  # Every 5 minutes
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['ecommerce', 'monitoring'],
)


def check_kafka_lag(**context):
    """Check Kafka consumer lag"""
    from kafka import KafkaConsumer
    from kafka.admin import KafkaAdminClient
    
    admin_client = KafkaAdminClient(bootstrap_servers='localhost:9092')
    
    # Get consumer group lag
    # This is a simplified version - production would use proper monitoring
    logger.info("Checking Kafka consumer lag...")
    
    # Alert if lag > threshold
    max_lag_threshold = 10000
    # Implementation would check actual lag here
    
    return True


def check_streaming_queries(**context):
    """Check if streaming queries are running"""
    import requests
    
    # Check Spark streaming UI
    response = requests.get('http://localhost:4040/api/v1/applications')
    
    if response.status_code == 200:
        apps = response.json()
        running_apps = [app for app in apps if app['attempts'][0]['completed'] == False]
        
        if len(running_apps) == 0:
            logger.error("No streaming applications running!")
            raise ValueError("Streaming pipeline is down")
        
        logger.info(f"Found {len(running_apps)} running streaming applications")
    
    return True


def check_druid_ingestion(**context):
    """Check Druid ingestion health"""
    import requests
    
    # Check Druid supervisor status
    response = requests.get('http://localhost:8888/druid/indexer/v1/supervisor')
    
    if response.status_code == 200:
        supervisors = response.json()
        logger.info(f"Active supervisors: {len(supervisors)}")
        
        for supervisor in supervisors:
            if supervisor.get('state') != 'RUNNING':
                logger.warning(f"Supervisor {supervisor['id']} is not running")
    
    return True


def check_data_freshness(**context):
    """Check if data is being ingested (freshness check)"""
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import max as spark_max
    from datetime import datetime, timedelta
    
    spark = SparkSession.builder.appName("FreshnessCheck").getOrCreate()
    
    try:
        silver_path = "hdfs://localhost:9000/data/silver/ecommerce/ecommerce_events"
        df = spark.read.format("iceberg").load(silver_path)
        
        latest_event = df.select(spark_max("event_time")).collect()[0][0]
        
        if latest_event:
            age = datetime.now() - latest_event
            
            if age > timedelta(minutes=10):
                logger.error(f"Data is stale! Latest event: {latest_event}")
                raise ValueError("Data freshness check failed")
            
            logger.info(f"Data is fresh. Latest event: {latest_event}")
        
    finally:
        spark.stop()
    
    return True


# Monitoring tasks
kafka_lag_task = PythonOperator(
    task_id='check_kafka_lag',
    python_callable=check_kafka_lag,
    provide_context=True,
    dag=monitoring_dag
)

streaming_health_task = PythonOperator(
    task_id='check_streaming_queries',
    python_callable=check_streaming_queries,
    provide_context=True,
    dag=monitoring_dag
)

druid_health_task = PythonOperator(
    task_id='check_druid_ingestion',
    python_callable=check_druid_ingestion,
    provide_context=True,
    dag=monitoring_dag
)

freshness_task = PythonOperator(
    task_id='check_data_freshness',
    python_callable=check_data_freshness,
    provide_context=True,
    dag=monitoring_dag
)

# All monitoring checks run in parallel
[kafka_lag_task, streaming_health_task, druid_health_task, freshness_task]

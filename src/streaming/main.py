"""
Main PySpark Structured Streaming Application
End-to-End Real-Time E-Commerce Analytics Pipeline
"""

import sys
import logging
from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col, expr

from schema import EVENT_SCHEMA
from transformations import (
    apply_bronze_transformations,
    apply_silver_transformations
)
from aggregations import (
    create_5min_aggregations,
    create_product_metrics,
    create_payment_metrics,
    create_category_metrics,
    create_realtime_kpi_summary
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EcommerceStreamingPipeline:
    """Main streaming pipeline class"""
    
    def __init__(self, config):
        self.config = config
        self.spark = self._create_spark_session()
        
    def _create_spark_session(self):
        """Create and configure Spark session"""
        logger.info("Creating Spark session...")
        
        return SparkSession.builder \
            .appName("EcommerceRealtimePipeline") \
            .config("spark.sql.shuffle.partitions", "6") \
            .config("spark.sql.streaming.checkpointLocation", 
                   self.config['checkpoint_location']) \
            .config("spark.sql.streaming.schemaInference", "false") \
            .config("spark.sql.streaming.metricsEnabled", "true") \
            .config("spark.sql.adaptive.enabled", "true") \
            .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
            .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer") \
            .config("spark.streaming.kafka.consumer.cache.enabled", "false") \
            .getOrCreate()
    
    def read_from_kafka(self):
        """Read streaming data from Kafka"""
        logger.info(f"Reading from Kafka topic: {self.config['kafka_topic']}")
        
        kafka_df = self.spark \
            .readStream \
            .format("kafka") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("subscribe", self.config['kafka_topic']) \
            .option("startingOffsets", self.config.get('starting_offsets', 'latest')) \
            .option("maxOffsetsPerTrigger", self.config.get('max_offsets_per_trigger', 10000)) \
            .option("failOnDataLoss", "false") \
            .option("kafka.session.timeout.ms", "30000") \
            .option("kafka.request.timeout.ms", "40000") \
            .load()
        
        # Parse JSON and apply schema
        parsed_df = kafka_df.select(
            col("key").cast("string"),
            from_json(col("value").cast("string"), EVENT_SCHEMA).alias("value"),
            col("topic"),
            col("partition"),
            col("offset"),
            col("timestamp")
        )
        
        return parsed_df
    
    def write_to_bronze(self, df):
        """Write raw events to Bronze layer (HDFS/S3)"""
        logger.info("Writing to Bronze layer...")
        
        bronze_df = apply_bronze_transformations(df)
        
        # Deduplicate based on event_id with watermark
        deduped_df = bronze_df \
            .withWatermark("event_time", "30 minutes") \
            .dropDuplicates(["event_id"])
        
        query = deduped_df.writeStream \
            .format("parquet") \
            .outputMode("append") \
            .option("path", self.config['bronze_path']) \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/bronze") \
            .partitionBy("event_date", "event_hour") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("bronze_writer") \
            .start()
        
        return query, deduped_df
    
    def write_to_silver(self, bronze_df):
        """Write cleaned data to Silver layer (Hive/Iceberg)"""
        logger.info("Writing to Silver layer...")
        
        # Apply transformations and get valid/invalid splits
        valid_df, invalid_df = apply_silver_transformations(bronze_df)
        
        # Write valid events to Silver
        valid_query = valid_df.writeStream \
            .format("iceberg") \
            .outputMode("append") \
            .option("path", f"{self.config['silver_path']}/ecommerce_events") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/silver_valid") \
            .option("fanout-enabled", "true") \
            .partitionBy("event_date", "event_hour") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("silver_valid_writer") \
            .start()
        
        # Write invalid events to quarantine
        invalid_query = invalid_df.writeStream \
            .format("parquet") \
            .outputMode("append") \
            .option("path", f"{self.config['silver_path']}/quarantine") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/silver_invalid") \
            .partitionBy("event_date") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("silver_invalid_writer") \
            .start()
        
        return valid_query, invalid_query, valid_df
    
    def write_aggregations_to_druid(self, silver_df):
        """Write aggregated metrics to Apache Druid"""
        logger.info("Creating aggregations for Druid...")
        
        queries = []
        
        # 1. Main 5-minute aggregations
        agg_df = create_5min_aggregations(silver_df, watermark_delay="10 minutes")
        
        agg_query = agg_df.writeStream \
            .format("kafka") \
            .outputMode("append") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("topic", self.config['druid_kafka_topic']) \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/druid_main") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("druid_main_aggregations") \
            .start()
        
        queries.append(agg_query)
        
        # 2. Product metrics
        product_df = create_product_metrics(silver_df, watermark_delay="10 minutes")
        
        product_query = product_df.writeStream \
            .format("kafka") \
            .outputMode("append") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("topic", f"{self.config['druid_kafka_topic']}_products") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/druid_products") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("druid_product_metrics") \
            .start()
        
        queries.append(product_query)
        
        # 3. Payment metrics
        payment_df = create_payment_metrics(silver_df, watermark_delay="10 minutes")
        
        payment_query = payment_df.writeStream \
            .format("kafka") \
            .outputMode("append") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("topic", f"{self.config['druid_kafka_topic']}_payments") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/druid_payments") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("druid_payment_metrics") \
            .start()
        
        queries.append(payment_query)
        
        # 4. Category metrics
        category_df = create_category_metrics(silver_df, watermark_delay="10 minutes")
        
        category_query = category_df.writeStream \
            .format("kafka") \
            .outputMode("append") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("topic", f"{self.config['druid_kafka_topic']}_categories") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/druid_categories") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("druid_category_metrics") \
            .start()
        
        queries.append(category_query)
        
        # 5. Real-time KPI summary
        kpi_df = create_realtime_kpi_summary(silver_df, watermark_delay="10 minutes")
        
        kpi_query = kpi_df.writeStream \
            .format("kafka") \
            .outputMode("append") \
            .option("kafka.bootstrap.servers", self.config['kafka_bootstrap_servers']) \
            .option("topic", f"{self.config['druid_kafka_topic']}_kpis") \
            .option("checkpointLocation", f"{self.config['checkpoint_location']}/druid_kpis") \
            .trigger(processingTime=self.config.get('trigger_interval', '30 seconds')) \
            .queryName("druid_kpi_summary") \
            .start()
        
        queries.append(kpi_query)
        
        return queries
    
    def run(self):
        """Run the complete streaming pipeline"""
        logger.info("Starting E-Commerce Streaming Pipeline...")
        
        try:
            # 1. Read from Kafka
            kafka_df = self.read_from_kafka()
            
            # 2. Write to Bronze layer
            bronze_query, bronze_df = self.write_to_bronze(kafka_df)
            
            # 3. Write to Silver layer
            silver_valid_query, silver_invalid_query, silver_df = self.write_to_silver(bronze_df)
            
            # 4. Write aggregations to Druid via Kafka
            druid_queries = self.write_aggregations_to_druid(silver_df)
            
            # Collect all queries
            all_queries = [bronze_query, silver_valid_query, silver_invalid_query] + druid_queries
            
            logger.info(f"Pipeline started successfully with {len(all_queries)} streaming queries")
            logger.info("Monitoring queries... Press Ctrl+C to stop")
            
            # Wait for termination
            for query in all_queries:
                query.awaitTermination()
                
        except KeyboardInterrupt:
            logger.info("Pipeline interrupted by user")
        except Exception as e:
            logger.error(f"Pipeline failed with error: {str(e)}", exc_info=True)
            raise
        finally:
            logger.info("Stopping Spark session...")
            self.spark.stop()


def main():
    """Main entry point"""
    
    # Configuration
    config = {
        # Kafka configuration
        'kafka_bootstrap_servers': 'localhost:9092',
        'kafka_topic': 'ecommerce-events',
        'starting_offsets': 'latest',
        'max_offsets_per_trigger': 10000,
        
        # Druid Kafka topic (for ingestion)
        'druid_kafka_topic': 'druid-ecommerce-metrics',
        
        # Storage paths
        'bronze_path': 'hdfs://localhost:9000/data/bronze/ecommerce_events',
        'silver_path': 'hdfs://localhost:9000/data/silver/ecommerce',
        
        # Checkpoint location
        'checkpoint_location': 'hdfs://localhost:9000/checkpoints/ecommerce_pipeline',
        
        # Streaming configuration
        'trigger_interval': '30 seconds',
    }
    
    # For S3 instead of HDFS, use:
    # 'bronze_path': 's3a://my-bucket/data/bronze/ecommerce_events',
    # 'silver_path': 's3a://my-bucket/data/silver/ecommerce',
    # 'checkpoint_location': 's3a://my-bucket/checkpoints/ecommerce_pipeline',
    
    # Create and run pipeline
    pipeline = EcommerceStreamingPipeline(config)
    pipeline.run()


if __name__ == "__main__":
    main()

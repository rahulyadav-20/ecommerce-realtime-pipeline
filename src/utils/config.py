"""
Configuration Management for E-Commerce Pipeline
Centralizes all configuration settings
"""

import os
from dataclasses import dataclass
from typing import Dict, Optional
import yaml


@dataclass
class KafkaConfig:
    """Kafka configuration"""
    bootstrap_servers: str = "localhost:9092"
    topic: str = "ecommerce-events"
    druid_topic: str = "druid-ecommerce-metrics"
    consumer_group: str = "ecommerce-pipeline"
    starting_offsets: str = "latest"
    max_offsets_per_trigger: int = 10000
    
    @classmethod
    def from_env(cls):
        return cls(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            topic=os.getenv("KAFKA_TOPIC", "ecommerce-events"),
            druid_topic=os.getenv("DRUID_KAFKA_TOPIC", "druid-ecommerce-metrics"),
            consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", "ecommerce-pipeline"),
        )


@dataclass
class StorageConfig:
    """Storage paths configuration"""
    storage_type: str = "hdfs"  # or "s3"
    bronze_path: str = "hdfs://localhost:9000/data/bronze/ecommerce_events"
    silver_path: str = "hdfs://localhost:9000/data/silver/ecommerce"
    gold_path: str = "hdfs://localhost:9000/data/gold/ecommerce"
    checkpoint_location: str = "hdfs://localhost:9000/checkpoints/ecommerce_pipeline"
    
    @classmethod
    def from_env(cls):
        storage_type = os.getenv("STORAGE_TYPE", "hdfs")
        
        if storage_type == "s3":
            bucket = os.getenv("S3_BUCKET", "my-data-lake")
            return cls(
                storage_type="s3",
                bronze_path=f"s3a://{bucket}/data/bronze/ecommerce_events",
                silver_path=f"s3a://{bucket}/data/silver/ecommerce",
                gold_path=f"s3a://{bucket}/data/gold/ecommerce",
                checkpoint_location=f"s3a://{bucket}/checkpoints/ecommerce_pipeline"
            )
        else:
            namenode = os.getenv("HDFS_NAMENODE", "localhost:9000")
            return cls(
                storage_type="hdfs",
                bronze_path=f"hdfs://{namenode}/data/bronze/ecommerce_events",
                silver_path=f"hdfs://{namenode}/data/silver/ecommerce",
                gold_path=f"hdfs://{namenode}/data/gold/ecommerce",
                checkpoint_location=f"hdfs://{namenode}/checkpoints/ecommerce_pipeline"
            )


@dataclass
class StreamingConfig:
    """Streaming job configuration"""
    trigger_interval: str = "30 seconds"
    watermark_delay: str = "10 minutes"
    late_data_tolerance: str = "30 minutes"
    checkpoint_interval: str = "30 seconds"
    
    @classmethod
    def from_env(cls):
        return cls(
            trigger_interval=os.getenv("TRIGGER_INTERVAL", "30 seconds"),
            watermark_delay=os.getenv("WATERMARK_DELAY", "10 minutes"),
            late_data_tolerance=os.getenv("LATE_DATA_TOLERANCE", "30 minutes"),
        )


@dataclass
class SparkConfig:
    """Spark session configuration"""
    app_name: str = "EcommerceRealtimePipeline"
    master: str = "spark://localhost:7077"
    shuffle_partitions: int = 6
    adaptive_enabled: bool = True
    
    # Memory settings
    executor_memory: str = "2g"
    executor_cores: int = 2
    driver_memory: str = "1g"
    
    # Serialization
    serializer: str = "org.apache.spark.serializer.KryoSerializer"
    
    @classmethod
    def from_env(cls):
        return cls(
            app_name=os.getenv("SPARK_APP_NAME", "EcommerceRealtimePipeline"),
            master=os.getenv("SPARK_MASTER", "spark://localhost:7077"),
            shuffle_partitions=int(os.getenv("SPARK_SHUFFLE_PARTITIONS", "6")),
            executor_memory=os.getenv("SPARK_EXECUTOR_MEMORY", "2g"),
            driver_memory=os.getenv("SPARK_DRIVER_MEMORY", "1g"),
        )
    
    def to_spark_config(self) -> Dict[str, str]:
        """Convert to Spark configuration dictionary"""
        return {
            "spark.sql.shuffle.partitions": str(self.shuffle_partitions),
            "spark.sql.adaptive.enabled": str(self.adaptive_enabled).lower(),
            "spark.sql.adaptive.coalescePartitions.enabled": "true",
            "spark.serializer": self.serializer,
            "spark.executor.memory": self.executor_memory,
            "spark.executor.cores": str(self.executor_cores),
            "spark.driver.memory": self.driver_memory,
            "spark.streaming.kafka.consumer.cache.enabled": "false",
        }


@dataclass
class DruidConfig:
    """Druid configuration"""
    coordinator_url: str = "http://localhost:8081"
    broker_url: str = "http://localhost:8082"
    router_url: str = "http://localhost:8888"
    
    @classmethod
    def from_env(cls):
        return cls(
            coordinator_url=os.getenv("DRUID_COORDINATOR_URL", "http://localhost:8081"),
            broker_url=os.getenv("DRUID_BROKER_URL", "http://localhost:8082"),
            router_url=os.getenv("DRUID_ROUTER_URL", "http://localhost:8888"),
        )


@dataclass
class MonitoringConfig:
    """Monitoring and alerting configuration"""
    enable_metrics: bool = True
    metrics_port: int = 9090
    log_level: str = "INFO"
    
    # Alert thresholds
    max_kafka_lag: int = 10000
    max_processing_delay_minutes: int = 5
    min_throughput_events_per_sec: int = 100
    
    @classmethod
    def from_env(cls):
        return cls(
            enable_metrics=os.getenv("ENABLE_METRICS", "true").lower() == "true",
            metrics_port=int(os.getenv("METRICS_PORT", "9090")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_kafka_lag=int(os.getenv("MAX_KAFKA_LAG", "10000")),
        )


class PipelineConfig:
    """Main pipeline configuration"""
    
    def __init__(
        self,
        kafka: Optional[KafkaConfig] = None,
        storage: Optional[StorageConfig] = None,
        streaming: Optional[StreamingConfig] = None,
        spark: Optional[SparkConfig] = None,
        druid: Optional[DruidConfig] = None,
        monitoring: Optional[MonitoringConfig] = None,
    ):
        self.kafka = kafka or KafkaConfig.from_env()
        self.storage = storage or StorageConfig.from_env()
        self.streaming = streaming or StreamingConfig.from_env()
        self.spark = spark or SparkConfig.from_env()
        self.druid = druid or DruidConfig.from_env()
        self.monitoring = monitoring or MonitoringConfig.from_env()
    
    @classmethod
    def from_yaml(cls, config_path: str):
        """Load configuration from YAML file"""
        with open(config_path, 'r') as f:
            config_dict = yaml.safe_load(f)
        
        return cls(
            kafka=KafkaConfig(**config_dict.get('kafka', {})),
            storage=StorageConfig(**config_dict.get('storage', {})),
            streaming=StreamingConfig(**config_dict.get('streaming', {})),
            spark=SparkConfig(**config_dict.get('spark', {})),
            druid=DruidConfig(**config_dict.get('druid', {})),
            monitoring=MonitoringConfig(**config_dict.get('monitoring', {})),
        )
    
    @classmethod
    def from_env(cls):
        """Load configuration from environment variables"""
        return cls()
    
    def to_dict(self) -> Dict:
        """Convert configuration to dictionary"""
        return {
            'kafka': self.kafka.__dict__,
            'storage': self.storage.__dict__,
            'streaming': self.streaming.__dict__,
            'spark': self.spark.__dict__,
            'druid': self.druid.__dict__,
            'monitoring': self.monitoring.__dict__,
        }
    
    def validate(self) -> bool:
        """Validate configuration"""
        errors = []
        
        # Validate Kafka config
        if not self.kafka.bootstrap_servers:
            errors.append("Kafka bootstrap servers not configured")
        
        # Validate storage paths
        if not self.storage.bronze_path:
            errors.append("Bronze path not configured")
        
        # Validate Spark config
        if self.spark.shuffle_partitions < 1:
            errors.append("Shuffle partitions must be >= 1")
        
        if errors:
            raise ValueError(f"Configuration validation failed: {', '.join(errors)}")
        
        return True


# Environment-specific configurations
class DevelopmentConfig(PipelineConfig):
    """Development environment configuration"""
    def __init__(self):
        super().__init__(
            kafka=KafkaConfig(
                bootstrap_servers="localhost:9092",
                starting_offsets="earliest",
            ),
            spark=SparkConfig(
                master="local[*]",
                shuffle_partitions=2,
            ),
            monitoring=MonitoringConfig(
                log_level="DEBUG",
            )
        )


class ProductionConfig(PipelineConfig):
    """Production environment configuration"""
    def __init__(self):
        super().__init__(
            kafka=KafkaConfig(
                bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS"),
                max_offsets_per_trigger=50000,
            ),
            spark=SparkConfig(
                master=os.getenv("SPARK_MASTER"),
                shuffle_partitions=20,
                executor_memory="4g",
                executor_cores=4,
            ),
            monitoring=MonitoringConfig(
                log_level="INFO",
                max_kafka_lag=50000,
            )
        )


def get_config(env: str = None) -> PipelineConfig:
    """Get configuration for specified environment"""
    env = env or os.getenv("ENVIRONMENT", "development")
    
    if env == "development":
        return DevelopmentConfig()
    elif env == "production":
        return ProductionConfig()
    else:
        return PipelineConfig.from_env()

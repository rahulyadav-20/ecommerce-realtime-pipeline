from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "E-Commerce Analytics API"
    app_version: str = "1.0.0"
    debug: bool = False

    # CORS
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:5173"]

    # ── Druid (HTTP SQL API) ──────────────────────────────────────────────────
    druid_url: str = "http://localhost:8888"
    druid_timeout: int = 30
    druid_max_retries: int = 3
    druid_backoff_factor: float = 0.5

    # ── ClickHouse ────────────────────────────────────────────────────────────
    clickhouse_url: str = "http://localhost:8123"
    clickhouse_timeout: int = 30
    clickhouse_host: str = "localhost"
    clickhouse_native_port: int = 9000
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_database: str = "ecom"
    clickhouse_pool_size: int = 5
    clickhouse_query_timeout: int = 60

    # ── Flink ─────────────────────────────────────────────────────────────────
    flink_url: str = "http://localhost:8082"
    flink_timeout: int = 10
    flink_max_retries: int = 3
    flink_backoff_factor: float = 0.3

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "flink-ecom-etl"    # existing ETL group (health only)
    kafka_clean_topic: str = "ecommerce.events.clean.v1"

    # ── Schema Registry (Avro deserialization for WebSocket consumer) ─────────
    schema_registry_url: str = "http://localhost:8081"
    schema_registry_timeout: int = 10

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 20

    # Cache TTLs (seconds)
    cache_ttl_kpis: int = 30
    cache_ttl_timeseries: int = 60
    cache_ttl_funnel: int = 300
    cache_ttl_products: int = 300
    cache_ttl_live: int = 5
    cache_ttl_flink: int = 15

    # ── WebSocket ─────────────────────────────────────────────────────────────
    # Max simultaneous WebSocket clients (rate-limiting)
    ws_max_clients: int = 50

    # Seconds to wait for a slow client's send to complete before giving up
    ws_send_timeout: float = 2.0

    # Server → client heartbeat interval (seconds).
    # Must be < typical NAT/proxy idle timeout (≈60 s).
    ws_heartbeat_interval: int = 25

    # Optional static token; set in env to enable auth (empty = disabled).
    # Clients pass it as: ws://host/ws/events?token=<value>
    ws_secret_token: str = ""

    # Dedicated Kafka consumer group for WebSocket broadcasting.
    # Separate from Flink/ClickHouse groups so offset management is independent.
    ws_kafka_consumer_group: str = "dashboard-ws-consumer"

    # How many messages to buffer before broadcasting starts.
    # "latest" → only stream new events; "earliest" → replay from beginning.
    ws_kafka_offset_reset: str = "latest"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()

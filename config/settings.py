"""
config/settings.py — Central configuration for the ecom real-time pipeline.

Environment selection
─────────────────────
Set the APP_ENV environment variable before running any script:

  APP_ENV=local   (default) — running from host machine (localhost + exposed ports)
  APP_ENV=docker             — running inside Docker containers (internal hostnames)
  APP_ENV=prod               — production deployment

The corresponding file  config/{env}.env  is loaded first.
Individual environment variables always WIN over the file values, so Docker
Compose or Kubernetes env blocks override the file without touching it.

Usage in Python scripts
───────────────────────
  # Option A — import the singleton (recommended for most scripts)
  from config.settings import cfg
  print(cfg.kafka.bootstrap_servers)

  # Option B — call the factory (useful when you need a fresh read)
  from config.settings import get_settings
  cfg = get_settings()

  # Option C — in CLI scripts that add repo root to sys.path
  import sys, pathlib
  sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[N]))
  from config.settings import cfg

Adding a new config key
───────────────────────
1. Add the field to the appropriate dataclass below.
2. Add the variable to all three  config/*.env  files.
3. Wire the default value in  _build_settings().
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Path helpers ──────────────────────────────────────────────────────────────

CONFIG_DIR   = Path(__file__).resolve().parent   # the config/ directory
PROJECT_ROOT = CONFIG_DIR.parent                  # repo root


# ── .env file loader ─────────────────────────────────────────────────────────

def _load_env_file(path: Path) -> dict[str, str]:
    """
    Parse a KEY=VALUE .env file into a dict.

    Rules:
    • Lines starting with # are comments → skipped.
    • Blank lines are skipped.
    • Inline comments after ' #' are stripped.
    • Values are NOT shell-expanded (no ${VAR} substitution).
    • Quoted values have their outer quotes stripped.
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key   = key.strip()
        value = value.strip()

        # Strip inline comments (value must have a space before #)
        if " #" in value:
            value = value[: value.index(" #")].rstrip()

        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        env[key] = value
    return env


def _get(key: str, default: str, file_values: dict[str, str]) -> str:
    """
    Look up a config key with this priority (highest → lowest):
      1. Real environment variable (os.environ)
      2. .env file value
      3. Hard-coded default
    """
    return os.environ.get(key) or file_values.get(key) or default


def _get_int(key: str, default: int, file_values: dict[str, str]) -> int:
    try:
        return int(_get(key, str(default), file_values))
    except ValueError:
        return default


def _get_bool(key: str, default: bool, file_values: dict[str, str]) -> bool:
    v = _get(key, str(default).lower(), file_values).lower()
    return v in ("1", "true", "yes", "on")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG DATACLASSES  (frozen → values are read-only after construction)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers:     str    # broker list, comma-separated
    schema_registry_url:   str
    raw_topic:             str    # producer → raw events
    clean_topic:           str    # Flink → cleaned events (hub topic)
    dlq_topic:             str    # dead-letter queue
    replication_factor:    int
    consumer_group_prefix: str    # e.g. "ecom-"


@dataclass(frozen=True)
class FlinkConfig:
    rest_url:        str    # JobManager REST API
    checkpoint_dir:  str    # s3:// path for checkpoints
    savepoint_dir:   str    # s3:// path for savepoints
    etl_job_id:      str    # populated after first submission


@dataclass(frozen=True)
class MinioConfig:
    endpoint:                str    # HTTP endpoint accessible from this process
    access_key:              str
    secret_key:              str
    warehouse_bucket:        str    # Iceberg data + GDPR audit logs
    flink_ckpt_bucket:       str
    flink_savepoints_bucket: str
    dq_results_bucket:       str    # Great Expectations output


@dataclass(frozen=True)
class ClickHouseConfig:
    url:      str    # HTTP interface URL
    user:     str
    password: str
    database: str


@dataclass(frozen=True)
class IcebergConfig:
    rest_url:     str    # tabulario REST catalog URL
    warehouse:    str    # s3:// warehouse root
    events_table: str    # warehouse.silver.events_clean


@dataclass(frozen=True)
class DruidConfig:
    rest_url:   str    # router / query URL
    datasource: str


@dataclass(frozen=True)
class PipelineConfig:
    """Application-level tunables (not infrastructure endpoints)."""
    # Iceberg compaction
    target_file_size_mb:  int
    min_file_size_mb:     int
    snapshot_retain_days: int

    # Data quality
    dq_lookback_hours:    int
    dq_sample_rows:       int
    dq_results_prefix:    str

    # Flink savepoint rotation
    savepoint_retain_days:  int
    savepoint_min_keep:     int
    savepoint_timeout_sec:  int

    # GDPR
    gdpr_audit_prefix:    str

    # Latency sampling in benchmark
    latency_sample_rate:  int    # 1-in-N events tracked for latency

    # Kafka Connect
    iceberg_plugin_version: str


@dataclass(frozen=True)
class Settings:
    env:          str             # local | docker | prod
    kafka:        KafkaConfig
    flink:        FlinkConfig
    minio:        MinioConfig
    clickhouse:   ClickHouseConfig
    iceberg:      IcebergConfig
    druid:        DruidConfig
    pipeline:     PipelineConfig
    slack_webhook_url: str

    # ── Convenience helpers ──────────────────────────────────────────────────

    def is_local(self)  -> bool: return self.env == "local"
    def is_docker(self) -> bool: return self.env == "docker"
    def is_prod(self)   -> bool: return self.env == "prod"

    def boto3_kwargs(self) -> dict:
        """Keyword arguments for boto3.client("s3", **cfg.boto3_kwargs())."""
        return {
            "endpoint_url":          self.minio.endpoint,
            "aws_access_key_id":     self.minio.access_key,
            "aws_secret_access_key": self.minio.secret_key,
        }

    def pyiceberg_catalog_props(self) -> dict:
        """Properties dict for pyiceberg RestCatalog constructor."""
        return {
            "uri":                  self.iceberg.rest_url,
            "s3.endpoint":          self.minio.endpoint,
            "s3.access-key-id":     self.minio.access_key,
            "s3.secret-access-key": self.minio.secret_key,
            "s3.path-style-access": "true",
        }


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def _build_settings(env: str, fv: dict[str, str]) -> Settings:
    """Construct the Settings object from file values and environment overrides."""
    return Settings(
        env = env,

        kafka = KafkaConfig(
            bootstrap_servers    = _get("KAFKA_BOOTSTRAP_SERVERS",    "localhost:9092",                    fv),
            schema_registry_url  = _get("SCHEMA_REGISTRY_URL",        "http://localhost:8081",             fv),
            raw_topic            = _get("KAFKA_RAW_TOPIC",            "ecommerce.events.raw.v1",           fv),
            clean_topic          = _get("KAFKA_CLEAN_TOPIC",          "ecommerce.events.clean.v1",         fv),
            dlq_topic            = _get("KAFKA_DLQ_TOPIC",            "dlq.events",                        fv),
            replication_factor   = _get_int("KAFKA_REPLICATION_FACTOR", 2,                                 fv),
            consumer_group_prefix= _get("KAFKA_CONSUMER_GROUP_PREFIX","ecom-",                             fv),
        ),

        flink = FlinkConfig(
            rest_url       = _get("FLINK_REST_URL",        "http://localhost:8082",              fv),
            checkpoint_dir = _get("FLINK_CHECKPOINT_DIR", "s3://flink-ckpt/ecom/",              fv),
            savepoint_dir  = _get("FLINK_SAVEPOINT_DIR",  "s3://flink-savepoints/ecom/",        fv),
            etl_job_id     = _get("FLINK_ETL_JOB_ID",     "",                                   fv),
        ),

        minio = MinioConfig(
            endpoint                = _get("MINIO_ENDPOINT",                "http://localhost:9002",   fv),
            access_key              = _get("MINIO_ACCESS_KEY",               "minio",                  fv),
            secret_key              = _get("MINIO_SECRET_KEY",               "minio123",               fv),
            warehouse_bucket        = _get("MINIO_WAREHOUSE_BUCKET",         "warehouse",              fv),
            flink_ckpt_bucket       = _get("MINIO_FLINK_CKPT_BUCKET",       "flink-ckpt",             fv),
            flink_savepoints_bucket = _get("MINIO_FLINK_SAVEPOINTS_BUCKET", "flink-savepoints",       fv),
            dq_results_bucket       = _get("MINIO_DQ_RESULTS_BUCKET",        "warehouse",              fv),
        ),

        clickhouse = ClickHouseConfig(
            url      = _get("CLICKHOUSE_URL",      "http://localhost:8123", fv),
            user     = _get("CLICKHOUSE_USER",     "default",              fv),
            password = _get("CLICKHOUSE_PASSWORD", "",                     fv),
            database = _get("CLICKHOUSE_DATABASE", "ecom",                 fv),
        ),

        iceberg = IcebergConfig(
            rest_url     = _get("ICEBERG_REST_URL",     "http://localhost:8181",             fv),
            warehouse    = _get("ICEBERG_WAREHOUSE",    "s3://warehouse/",                   fv),
            events_table = _get("ICEBERG_EVENTS_TABLE", "warehouse.silver.events_clean",     fv),
        ),

        druid = DruidConfig(
            rest_url   = _get("DRUID_REST_URL",   "http://localhost:8888",  fv),
            datasource = _get("DRUID_DATASOURCE", "ecommerce_events",       fv),
        ),

        pipeline = PipelineConfig(
            # Iceberg compaction
            target_file_size_mb  = _get_int("TARGET_FILE_SIZE_MB",      128, fv),
            min_file_size_mb     = _get_int("MIN_FILE_SIZE_MB",          10,  fv),
            snapshot_retain_days = _get_int("SNAPSHOT_RETAIN_DAYS",      7,   fv),
            # DQ
            dq_lookback_hours    = _get_int("DQ_LOOKBACK_HOURS",          1,   fv),
            dq_sample_rows       = _get_int("DQ_SAMPLE_ROWS",         10000,   fv),
            dq_results_prefix    = _get("DQ_RESULTS_PREFIX",  "dq-results/great-expectations", fv),
            # Savepoint rotation
            savepoint_retain_days   = _get_int("FLINK_SAVEPOINT_RETAIN_DAYS",    7, fv),
            savepoint_min_keep      = _get_int("FLINK_SAVEPOINT_MIN_KEEP",        2, fv),
            savepoint_timeout_sec   = _get_int("FLINK_SAVEPOINT_TIMEOUT_SEC",   300, fv),
            # GDPR
            gdpr_audit_prefix    = _get("GDPR_AUDIT_PREFIX", "gdpr-audit", fv),
            # Benchmark
            latency_sample_rate  = _get_int("LATENCY_SAMPLE_RATE", 100, fv),
            # Kafka Connect
            iceberg_plugin_version = _get("ICEBERG_PLUGIN_VERSION", "0.6.19", fv),
        ),

        slack_webhook_url = _get("SLACK_WEBHOOK_URL", "", fv),
    )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Build and cache the Settings singleton.

    The cache is keyed to the process; re-import or call
    get_settings.cache_clear() to force a reload.
    """
    env      = os.environ.get("APP_ENV", "local").lower()
    env_file = CONFIG_DIR / f"{env}.env"
    fv       = _load_env_file(env_file)
    settings = _build_settings(env, fv)

    # Emit one line at startup so you always know which environment is active
    import logging
    logging.getLogger("config").info(
        "Config loaded  env=%s  file=%s  kafka=%s  flink=%s",
        env,
        env_file if env_file.exists() else "(not found, using defaults)",
        settings.kafka.bootstrap_servers,
        settings.flink.rest_url,
    )
    return settings


# ── Module-level singleton ────────────────────────────────────────────────────
# Import as:  from config.settings import cfg
cfg: Settings = get_settings()

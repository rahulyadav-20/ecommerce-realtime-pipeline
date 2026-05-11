#!/usr/bin/env python3
"""
iceberg_compaction — Daily Iceberg table maintenance DAG
Schedule : daily at 02:00 UTC
Owner    : data-platform

Tasks
─────
1. rewrite_data_files  Bin-pack small Parquet files → 128 MB target size.
2. expire_snapshots    Remove snapshots older than SNAPSHOT_RETAIN_DAYS (7).
3. collect_stats       Count rows, data files, and total on-disk size.

Standalone usage
────────────────
  # Inside the Docker network (from any container):
  python /opt/airflow/dags/iceberg_compaction.py

  # From the host, pointing at exposed ports:
  ICEBERG_REST_URL=http://localhost:8181 \\
  MINIO_ENDPOINT=http://localhost:9002   \\
  python airflow/dags/iceberg_compaction.py --task all

  # Dry-run (no writes):
  python airflow/dags/iceberg_compaction.py --dry-run

Dependencies
────────────
  pip install "pyiceberg[s3fsspec]>=0.7" pyarrow
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Config (project root → config/) ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import cfg  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("iceberg_compaction")

# ── Resolved config values (aliases for readability) ─────────────────────────
CATALOG_URL          = cfg.iceberg.rest_url
S3_ENDPOINT          = cfg.minio.endpoint
S3_ACCESS_KEY        = cfg.minio.access_key
S3_SECRET_KEY        = cfg.minio.secret_key
TABLE_ID             = cfg.iceberg.events_table
TARGET_FILE_SIZE_MB  = cfg.pipeline.target_file_size_mb
SNAPSHOT_RETAIN_DAYS = cfg.pipeline.snapshot_retain_days
MIN_FILE_SIZE_MB     = cfg.pipeline.min_file_size_mb
MIN_FILE_SIZE_MB     = int(os.getenv("MIN_FILE_SIZE_MB",     "10"))

# ── Catalog helper ────────────────────────────────────────────────────────────

def _catalog():
    """Return an authenticated PyIceberg REST catalog backed by MinIO."""
    try:
        from pyiceberg.catalog.rest import RestCatalog
    except ImportError as e:
        log.error("pyiceberg missing → pip install 'pyiceberg[s3fsspec]>=0.7'")
        raise RuntimeError("pyiceberg not installed") from e

    return RestCatalog(
        name="default",
        **{
            "uri":                   CATALOG_URL,
            "s3.endpoint":           S3_ENDPOINT,
            "s3.access-key-id":      S3_ACCESS_KEY,
            "s3.secret-access-key":  S3_SECRET_KEY,
            "s3.path-style-access":  "true",
        },
    )


def _load_table():
    """Load the target Iceberg table. Accepts dotted or tuple identifier."""
    cat   = _catalog()
    parts = TABLE_ID.split(".")
    # PyIceberg accepts either a string or a tuple for nested namespaces
    ident = tuple(parts) if len(parts) > 2 else TABLE_ID
    tbl   = cat.load_table(ident)
    log.info("Loaded table %s  (format-version=%s)", TABLE_ID, tbl.format_version)
    return tbl


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — rewrite_data_files
# ═══════════════════════════════════════════════════════════════════════════════

def rewrite_data_files(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Bin-pack small Parquet data files into files of ~TARGET_FILE_SIZE_MB.

    Strategy
    ────────
    Only rewrite files smaller than MIN_FILE_SIZE_MB (avoids rewriting large,
    already-optimal files).  The bin-pack algorithm groups small files until the
    target size is reached, then writes a new consolidated file.

    Returns a dict with compaction statistics pushed to XCom when run in Airflow.
    """
    log.info(
        "rewrite_data_files  table=%s  target=%d MB  min_size=%d MB  dry_run=%s",
        TABLE_ID, TARGET_FILE_SIZE_MB, MIN_FILE_SIZE_MB, dry_run,
    )

    tbl = _load_table()

    # Inspect current file distribution before compaction
    current_snapshot = tbl.current_snapshot()
    if current_snapshot is None:
        log.warning("Table has no snapshots — nothing to compact")
        return {"rewritten_files": 0, "skipped": True}

    # Gather file sizes from the current snapshot manifest
    try:
        files_before = list(tbl.scan().plan_files())
        small_files  = [f for f in files_before
                        if f.file.file_size_in_bytes < MIN_FILE_SIZE_MB * 1024 * 1024]

        log.info(
            "Before compaction: %d total files, %d small files (<  %d MB)",
            len(files_before), len(small_files), MIN_FILE_SIZE_MB,
        )

        if not small_files:
            log.info("No small files found — skipping compaction")
            return {"rewritten_files": 0, "skipped": True, "reason": "no_small_files"}

        if dry_run:
            log.info("[dry-run] Would rewrite %d small files", len(small_files))
            return {"rewritten_files": len(small_files), "dry_run": True}

        # Execute compaction (requires pyiceberg >= 0.7.0)
        result = tbl.rewrite_data_files(
            strategy="binpack",
            options={
                "target-file-size-bytes": str(TARGET_FILE_SIZE_MB * 1024 * 1024),
                "min-file-size-bytes":    str(MIN_FILE_SIZE_MB * 1024 * 1024),
                "max-file-group-size-bytes": str(TARGET_FILE_SIZE_MB * 5 * 1024 * 1024),
            },
        )

        files_after = list(tbl.scan().plan_files())
        stats = {
            "files_before":   len(files_before),
            "files_after":    len(files_after),
            "rewritten_files": result.rewritten_data_files_count,
            "added_files":     result.added_data_files_count,
            "table":           TABLE_ID,
            "ts":              datetime.now(timezone.utc).isoformat(),
        }
        log.info("Compaction complete: %s", json.dumps(stats, indent=2))
        return stats

    except AttributeError:
        # PyIceberg < 0.7 does not have rewrite_data_files
        log.error(
            "tbl.rewrite_data_files() not available.  "
            "Upgrade: pip install 'pyiceberg>=0.7'"
        )
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — expire_snapshots
# ═══════════════════════════════════════════════════════════════════════════════

def expire_snapshots(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Expire Iceberg snapshots older than SNAPSHOT_RETAIN_DAYS.

    Why expire?
    ───────────
    Every Iceberg write produces a new snapshot.  Without expiration, the
    metadata directory accumulates manifest lists and manifest files indefinitely.
    Expiring removes the manifest list references; the orphan data files are
    cleaned up separately (see delete_orphan_files in production setups).

    Returns snapshot statistics.
    """
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_RETAIN_DAYS)).timestamp()
        * 1000
    )
    cutoff_dt = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).isoformat()
    log.info(
        "expire_snapshots  table=%s  cutoff=%s  dry_run=%s",
        TABLE_ID, cutoff_dt, dry_run,
    )

    tbl       = _load_table()
    snapshots = list(tbl.history())

    to_expire = [s for s in snapshots if s.timestamp_ms < cutoff_ms]
    to_keep   = [s for s in snapshots if s.timestamp_ms >= cutoff_ms]

    log.info(
        "Snapshots: %d total, %d to expire (older than %s), %d to retain",
        len(snapshots), len(to_expire), cutoff_dt, len(to_keep),
    )

    if not to_expire:
        log.info("No snapshots to expire")
        return {"expired": 0, "retained": len(to_keep)}

    if dry_run:
        log.info("[dry-run] Would expire %d snapshots", len(to_expire))
        return {"expired": len(to_expire), "retained": len(to_keep), "dry_run": True}

    try:
        tbl.expire_snapshots(expire_older_than_ms=cutoff_ms).commit()
        stats = {
            "expired":      len(to_expire),
            "retained":     len(to_keep),
            "cutoff_ms":    cutoff_ms,
            "cutoff_dt":    cutoff_dt,
            "ts":           datetime.now(timezone.utc).isoformat(),
        }
        log.info("Snapshot expiry complete: %s", json.dumps(stats, indent=2))
        return stats

    except AttributeError:
        log.error("tbl.expire_snapshots() not available.  Upgrade pyiceberg >= 0.7")
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — collect_stats
# ═══════════════════════════════════════════════════════════════════════════════

def collect_stats(**kwargs: Any) -> dict:
    """
    Collect table statistics: row count, data-file count, total on-disk size.

    Uses PyIceberg's scan plan (no full table scan) — fast even for very large
    tables.  Statistics are logged and returned for XCom / downstream alerting.
    """
    log.info("collect_stats  table=%s", TABLE_ID)

    tbl              = _load_table()
    current_snapshot = tbl.current_snapshot()

    if current_snapshot is None:
        log.warning("Table %s has no current snapshot", TABLE_ID)
        return {"row_count": 0, "file_count": 0, "size_bytes": 0}

    try:
        planned_files = list(tbl.scan().plan_files())
    except Exception as exc:
        log.warning("plan_files() failed (%s) — falling back to metadata", exc)
        planned_files = []

    # Row count from snapshot summary (written by the engine on each commit)
    summary   = current_snapshot.summary or {}
    row_count = int(summary.get("total-records", 0))

    # File stats from manifest scan
    file_count  = len(planned_files)
    total_bytes = sum(getattr(f.file, "file_size_in_bytes", 0) for f in planned_files)

    # Snapshot age
    snapshot_age_hours = (
        datetime.now(timezone.utc).timestamp()
        - current_snapshot.timestamp_ms / 1000
    ) / 3600

    stats = {
        "table":             TABLE_ID,
        "row_count":         row_count,
        "file_count":        file_count,
        "size_bytes":        total_bytes,
        "size_mb":           round(total_bytes / (1024 * 1024), 2),
        "snapshot_id":       current_snapshot.snapshot_id,
        "snapshot_age_hours": round(snapshot_age_hours, 1),
        "ts":                datetime.now(timezone.utc).isoformat(),
    }

    log.info("Table stats: %s", json.dumps(stats, indent=2))

    # Warn if the table is unusually small (possible pipeline stall)
    if row_count == 0:
        log.warning("Table %s has 0 rows — pipeline may be stalled!", TABLE_ID)
    elif file_count > 1000:
        log.warning(
            "Table %s has %d data files — compaction may be needed",
            TABLE_ID, file_count,
        )

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# AIRFLOW DAG DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
    from airflow.utils.dates import days_ago

    _default_args = {
        "owner":            "data-platform",
        "retries":          2,
        "retry_delay":      timedelta(minutes=10),
        "email_on_failure": False,
        "depends_on_past":  False,
    }

    with DAG(
        dag_id="iceberg_compaction",
        description="Daily Iceberg table maintenance: compact → expire → stats",
        schedule_interval="0 2 * * *",          # 02:00 UTC every day
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,                       # never run two compactions concurrently
        default_args=_default_args,
        tags=["iceberg", "maintenance", "lakehouse"],
        doc_md=__doc__,
    ) as dag:

        t_compact = PythonOperator(
            task_id="rewrite_data_files",
            python_callable=rewrite_data_files,
            op_kwargs={"dry_run": False},
            doc_md="Bin-pack small Parquet files into 128 MB targets.",
        )

        t_expire = PythonOperator(
            task_id="expire_snapshots",
            python_callable=expire_snapshots,
            op_kwargs={"dry_run": False},
            doc_md="Remove snapshots older than 7 days.",
        )

        t_stats = PythonOperator(
            task_id="collect_stats",
            python_callable=collect_stats,
            doc_md="Log row count, file count, and total size.",
        )

        # Compact → expire old snapshots → record final stats
        t_compact >> t_expire >> t_stats

except ImportError:
    log.debug("Airflow not found — DAG object not created (standalone mode)")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# python airflow/dags/iceberg_compaction.py [--task compact|expire|stats|all] [--dry-run]
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Iceberg table maintenance — run without Airflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  ICEBERG_REST_URL    REST catalog base URL  (default: http://iceberg-rest:8181)
  MINIO_ENDPOINT      S3-compatible endpoint (default: http://minio:9000)
  MINIO_ACCESS_KEY    S3 access key          (default: minio)
  MINIO_SECRET_KEY    S3 secret key          (default: minio123)
  ICEBERG_TABLE       Table identifier       (default: warehouse.silver.events_clean)
""",
    )
    parser.add_argument(
        "--task",
        choices=["compact", "expire", "stats", "all"],
        default="all",
        help="Which task(s) to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing anything",
    )
    args = parser.parse_args()

    log.info(
        "Running standalone  task=%s  dry_run=%s  table=%s",
        args.task, args.dry_run, TABLE_ID,
    )

    results = {}

    if args.task in ("compact", "all"):
        results["compact"] = rewrite_data_files(dry_run=args.dry_run)

    if args.task in ("expire", "all"):
        results["expire"] = expire_snapshots(dry_run=args.dry_run)

    if args.task in ("stats", "all"):
        results["stats"] = collect_stats()

    print(json.dumps(results, indent=2))
    sys.exit(0)

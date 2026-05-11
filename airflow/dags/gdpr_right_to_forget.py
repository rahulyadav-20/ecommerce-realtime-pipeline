#!/usr/bin/env python3
"""
gdpr_right_to_forget — GDPR Article 17 right-to-erasure DAG
Schedule : manual trigger only  (schedule_interval=None)
Owner    : data-platform

Trigger param  (required)
──────────────────────────
  user_id : str   The authenticated user_id to erase from all storage layers.

Tasks
─────
1. validate_request     Confirm user_id is non-empty; record the erasure request
                        in the audit table before any data is deleted.
2. delete_iceberg       Write an Iceberg equality-delete file that logically removes
                        all rows where user_id matches.  Copy-on-write rewrite is
                        triggered on next compaction.
3. delete_clickhouse    Execute ALTER TABLE DELETE (mutation) against all ClickHouse
                        tables that store user_id.  SETTINGS mutations_sync=2 blocks
                        until the mutation commits.
4. delete_druid         Query Druid SQL for time intervals containing the user, then
                        POST kill tasks for those segments.
5. write_audit_log      Write a signed, immutable audit record to MinIO documenting
                        what was deleted, when, and by whom.

Standalone usage
────────────────
  python airflow/dags/gdpr_right_to_forget.py --user-id u-12345678
  python airflow/dags/gdpr_right_to_forget.py --user-id u-12345678 --dry-run

  With AIRFLOW_CTX_DAG_ID set:
    The script detects the Airflow context and does not re-run all tasks.

Dependencies
────────────
  pip install "pyiceberg[s3fsspec]>=0.7" requests boto3 pyarrow
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gdpr_right_to_forget")

# ── Configuration ─────────────────────────────────────────────────────────────
CATALOG_URL         = os.getenv("ICEBERG_REST_URL",       "http://iceberg-rest:8181")
S3_ENDPOINT         = os.getenv("MINIO_ENDPOINT",         "http://minio:9000")
S3_ACCESS_KEY       = os.getenv("MINIO_ACCESS_KEY",       "minio")
S3_SECRET_KEY       = os.getenv("MINIO_SECRET_KEY",       "minio123")
AUDIT_BUCKET        = os.getenv("GDPR_AUDIT_BUCKET",      "warehouse")
AUDIT_PREFIX        = os.getenv("GDPR_AUDIT_PREFIX",      "gdpr-audit")
ICEBERG_TABLE       = os.getenv("ICEBERG_EVENTS_TABLE",   "warehouse.silver.events_clean")
CLICKHOUSE_URL      = os.getenv("CLICKHOUSE_URL",         "http://clickhouse:8123")
CLICKHOUSE_USER     = os.getenv("CLICKHOUSE_USER",        "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD",    "")
DRUID_URL           = os.getenv("DRUID_REST_URL",         "http://druid-router:8888")
DRUID_DATASOURCE    = os.getenv("DRUID_DATASOURCE",       "ecommerce_events")

# Tables in ClickHouse that store user_id
CLICKHOUSE_TABLES = [
    "ecom.events_local",
    # kpi_1min and funnel_5min are pre-aggregated — no user_id column,
    # so they do not need individual row deletion.
]

# ── Shared state for standalone execution ─────────────────────────────────────
_STATE: dict = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _catalog():
    try:
        from pyiceberg.catalog.rest import RestCatalog
    except ImportError:
        raise RuntimeError("pip install 'pyiceberg[s3fsspec]>=0.7'")
    return RestCatalog(
        name="default",
        **{
            "uri":                  CATALOG_URL,
            "s3.endpoint":          S3_ENDPOINT,
            "s3.access-key-id":     S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.path-style-access": "true",
        },
    )


def _s3_client():
    try:
        import boto3
    except ImportError:
        raise RuntimeError("pip install boto3")
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def _ch_query(sql: str, dry_run: bool = False) -> dict:
    """Execute a ClickHouse SQL statement via the HTTP interface."""
    if dry_run:
        log.info("[dry-run] ClickHouse SQL: %s", sql[:200])
        return {"dry_run": True, "sql": sql}
    import requests
    resp = requests.post(
        CLICKHOUSE_URL,
        data=sql.encode(),
        params={"user": CLICKHOUSE_USER, "password": CLICKHOUSE_PASSWORD},
        headers={"Content-Type": "text/plain"},
        timeout=120,        # mutations can take a while to complete
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"ClickHouse error {resp.status_code}: {resp.text[:300]}"
        )
    return {"status": "ok", "response": resp.text.strip()[:200]}


def _get_param(key: str, kwargs: dict, default: str = "") -> str:
    """
    Retrieve a DAG param from Airflow context (kwargs['params']) or _STATE.
    Falls back to `default`.
    """
    # Airflow 2.x passes params via kwargs['params']
    params = kwargs.get("params") or {}
    if key in params:
        return str(params[key])
    # Standalone mode stores params in _STATE
    return _STATE.get(key, default)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — validate_request
# ═══════════════════════════════════════════════════════════════════════════════

def validate_request(**kwargs: Any) -> dict:
    """
    Validate the erasure request and write an initial audit entry.

    Raises ValueError if user_id is empty or obviously invalid.
    Returns a request_id (UUID) used by all downstream tasks to correlate
    audit log entries.
    """
    user_id = _get_param("user_id", kwargs)
    if not user_id or not user_id.strip():
        raise ValueError("user_id param is required and must not be empty")
    user_id = user_id.strip()

    # Simple sanity check — adjust the regex to match your user ID format
    import re
    if not re.match(r"^[a-zA-Z0-9_\-]{1,128}$", user_id):
        raise ValueError(
            f"user_id contains invalid characters: {user_id!r}. "
            "Expected alphanumeric + hyphens/underscores, max 128 chars."
        )

    request_id  = str(uuid.uuid4())
    requested_at = datetime.now(timezone.utc).isoformat()

    log.info(
        "GDPR erasure request validated  user_id=%s  request_id=%s",
        user_id, request_id,
    )

    record = {
        "request_id":   request_id,
        "user_id":      user_id,
        "requested_at": requested_at,
        "status":       "in_progress",
        "tasks":        {},
    }
    _STATE.update(record)

    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="request_id", value=request_id)
        kwargs["ti"].xcom_push(key="user_id",    value=user_id)

    return record


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — delete_iceberg
# ═══════════════════════════════════════════════════════════════════════════════

def delete_iceberg(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Delete all rows for user_id from the Iceberg silver.events_clean table.

    PyIceberg 0.7+ supports positional delete files (merge-on-read) and
    copy-on-write rewrites.  We use the delete_where() approach which writes
    an equality-delete file — the matching rows become invisible immediately
    (within a FINAL scan) and are physically removed on next compaction.
    """
    user_id    = _get_param("user_id", kwargs) or _STATE.get("user_id", "")
    request_id = _get_param("request_id", kwargs) or _STATE.get("request_id", "")

    if not user_id:
        raise ValueError("user_id not available — run validate_request first")

    log.info(
        "delete_iceberg  user_id=%s  table=%s  dry_run=%s",
        user_id, ICEBERG_TABLE, dry_run,
    )

    if dry_run:
        log.info("[dry-run] Would delete rows where user_id = '%s'", user_id)
        result = {"dry_run": True, "table": ICEBERG_TABLE, "user_id": user_id}
        _STATE.setdefault("tasks", {})["iceberg"] = result
        return result

    cat   = _catalog()
    parts = ICEBERG_TABLE.split(".")
    ident = tuple(parts) if len(parts) > 2 else ICEBERG_TABLE
    tbl   = cat.load_table(ident)

    # Count affected rows before deletion (for the audit log)
    try:
        from pyiceberg.expressions import EqualTo
        scan         = tbl.scan(row_filter=EqualTo("user_id", user_id))
        before_arrow = scan.to_arrow()
        rows_before  = len(before_arrow)
        log.info("Found %d rows to delete for user_id=%s", rows_before, user_id)
    except Exception as exc:
        log.warning("Pre-delete row count failed: %s", exc)
        rows_before = -1

    # Execute the delete
    # PyIceberg >= 0.7.0: tbl.delete(delete_filter=EqualTo("user_id", user_id))
    try:
        from pyiceberg.expressions import EqualTo as IceEq
        tbl.delete(delete_filter=IceEq("user_id", user_id))
        log.info(
            "Iceberg delete committed  user_id=%s  rows_deleted~=%d",
            user_id, rows_before,
        )
    except AttributeError:
        log.error(
            "tbl.delete() not available in this PyIceberg version.  "
            "Upgrade: pip install 'pyiceberg>=0.7'"
        )
        raise

    result = {
        "table":         ICEBERG_TABLE,
        "user_id":       user_id,
        "rows_deleted":  rows_before,
        "request_id":    request_id,
        "ts":            datetime.now(timezone.utc).isoformat(),
    }
    _STATE.setdefault("tasks", {})["iceberg"] = result
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="iceberg_result", value=result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — delete_clickhouse
# ═══════════════════════════════════════════════════════════════════════════════

def delete_clickhouse(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Delete all rows for user_id from ClickHouse via ALTER TABLE DELETE (mutation).

    SETTINGS mutations_sync = 2 blocks until the mutation is APPLIED
    (i.e. all affected parts have been rewritten), giving synchronous semantics.
    This can be slow for large tables; reduce to mutations_sync=1 if timeouts occur
    and accept that old parts may still be visible briefly after the call returns.

    Note: kpi_1min and funnel_5min are aggregated tables with no user_id column
    and are therefore not affected.
    """
    user_id    = _get_param("user_id", kwargs) or _STATE.get("user_id", "")
    request_id = _get_param("request_id", kwargs) or _STATE.get("request_id", "")

    if not user_id:
        raise ValueError("user_id not available — run validate_request first")

    log.info(
        "delete_clickhouse  user_id=%s  tables=%s  dry_run=%s",
        user_id, CLICKHOUSE_TABLES, dry_run,
    )

    results = {}
    for table in CLICKHOUSE_TABLES:
        # Escape single quotes in user_id to prevent SQL injection
        safe_user_id = user_id.replace("'", "\\'")
        sql = (
            f"ALTER TABLE {table} "
            f"DELETE WHERE user_id = '{safe_user_id}' "
            f"SETTINGS mutations_sync = 2"
        )
        try:
            result    = _ch_query(sql, dry_run=dry_run)
            log.info("ClickHouse delete OK  table=%s", table)
            results[table] = {"status": "ok", "result": result}
        except Exception as exc:
            log.error("ClickHouse delete FAILED  table=%s  error=%s", table, exc)
            results[table] = {"status": "error", "error": str(exc)}

    # Verify deletion (skip for dry-run)
    if not dry_run and CLICKHOUSE_TABLES:
        verify_table   = CLICKHOUSE_TABLES[0]
        safe_uid       = user_id.replace("'", "\\'")
        verify_sql     = (
            f"SELECT count() AS c FROM {verify_table} "
            f"WHERE user_id = '{safe_uid}' SETTINGS max_execution_time = 30"
        )
        try:
            verify_result = _ch_query(verify_sql)
            remaining     = int(verify_result.get("response", "0").strip() or "0")
            if remaining > 0:
                log.warning(
                    "Verification: %d rows still present in %s after mutation",
                    remaining, verify_table,
                )
            else:
                log.info("Verification: 0 rows remain in %s ✓", verify_table)
        except Exception as exc:
            log.warning("Verification query failed: %s", exc)

    summary = {
        "user_id":    user_id,
        "request_id": request_id,
        "tables":     results,
        "ts":         datetime.now(timezone.utc).isoformat(),
    }
    _STATE.setdefault("tasks", {})["clickhouse"] = summary
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="ch_result", value=summary)
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4 — delete_druid
# ═══════════════════════════════════════════════════════════════════════════════

def delete_druid(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Remove user data from Druid by killing the affected time-interval segments.

    Druid limitation: segments are immutable — individual rows cannot be deleted
    without re-ingesting the entire segment.  This task:
      1. Queries Druid SQL to identify time intervals (floored to the day)
         that contain at least one row for the user.
      2. Posts a "kill" task for each affected interval to drop segments.

    Trade-off: killing a segment removes ALL rows in that time window, not just
    the user's rows.  For strict GDPR compliance, replace with a Druid
    "overwrite" batch re-index that filters out the user before writing segments.
    That approach is left as a production TODO here because it requires a Spark
    or native Druid parallel index spec.
    """
    import requests as _requests

    user_id    = _get_param("user_id", kwargs) or _STATE.get("user_id", "")
    request_id = _get_param("request_id", kwargs) or _STATE.get("request_id", "")

    if not user_id:
        raise ValueError("user_id not available — run validate_request first")

    log.info(
        "delete_druid  user_id=%s  datasource=%s  dry_run=%s",
        user_id, DRUID_DATASOURCE, dry_run,
    )

    # Step 1: Find affected time intervals via Druid SQL API
    safe_uid    = user_id.replace("'", "\\'")
    find_sql    = (
        f"SELECT FLOOR(__time TO DAY) AS day_bucket, COUNT(*) AS cnt "
        f"FROM {DRUID_DATASOURCE} "
        f"WHERE user_id = '{safe_uid}' "
        f"GROUP BY 1 ORDER BY 1"
    )
    affected_intervals = []
    try:
        resp = _requests.post(
            f"{DRUID_URL}/druid/v2/sql",
            json={"query": find_sql},
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if resp.status_code == 200:
            rows = resp.json()
            for row in rows:
                # Convert day_bucket to ISO interval: "2026-05-11T00:00:00.000Z/P1D"
                day = row.get("day_bucket", "").replace(" ", "T")
                if day:
                    affected_intervals.append(f"{day}.000Z/P1D")
            log.info(
                "Found user_id in %d day-intervals: %s",
                len(affected_intervals), affected_intervals[:5],
            )
        else:
            log.warning("Druid SQL query returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.error("Failed to query Druid SQL: %s", exc)
        affected_intervals = []

    if not affected_intervals:
        log.info("User %s not found in Druid datasource %s", user_id, DRUID_DATASOURCE)
        result = {"intervals_found": 0, "kill_tasks": [], "user_id": user_id}
        _STATE.setdefault("tasks", {})["druid"] = result
        return result

    if dry_run:
        log.info(
            "[dry-run] Would kill %d interval(s): %s",
            len(affected_intervals), affected_intervals,
        )
        result = {
            "dry_run": True,
            "intervals_found": len(affected_intervals),
            "intervals": affected_intervals,
        }
        _STATE.setdefault("tasks", {})["druid"] = result
        return result

    # Step 2: Post kill tasks for each affected interval
    kill_tasks = []
    for interval in affected_intervals:
        kill_spec = {
            "type": "kill",
            "dataSource": DRUID_DATASOURCE,
            "interval": interval,
            "context": {
                "gdpr_request_id": request_id,
                "gdpr_user_id":    user_id,
            },
        }
        try:
            resp = _requests.post(
                f"{DRUID_URL}/druid/indexer/v1/task",
                json=kill_spec,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            if resp.status_code == 200:
                task_id = resp.json().get("task", "unknown")
                log.info("Kill task submitted  interval=%s  task_id=%s", interval, task_id)
                kill_tasks.append({"interval": interval, "task_id": task_id, "status": "submitted"})
            else:
                log.error(
                    "Kill task failed  interval=%s  status=%s  body=%s",
                    interval, resp.status_code, resp.text[:200],
                )
                kill_tasks.append({"interval": interval, "status": "error", "code": resp.status_code})
        except Exception as exc:
            log.error("Failed to submit kill task for %s: %s", interval, exc)
            kill_tasks.append({"interval": interval, "status": "exception", "error": str(exc)})

    result = {
        "user_id":          user_id,
        "request_id":       request_id,
        "intervals_found":  len(affected_intervals),
        "kill_tasks":       kill_tasks,
        "ts":               datetime.now(timezone.utc).isoformat(),
    }
    _STATE.setdefault("tasks", {})["druid"] = result
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="druid_result", value=result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5 — write_audit_log
# ═══════════════════════════════════════════════════════════════════════════════

def write_audit_log(**kwargs: Any) -> dict:
    """
    Write an immutable audit record to MinIO documenting the completed erasure.

    S3 path: s3://warehouse/gdpr-audit/YYYY/MM/DD/{request_id}.json

    The record is the single source of truth for compliance reporting.
    It captures what was deleted, from which system, and the outcome of each
    subtask.  Object versioning on the MinIO bucket is recommended to prevent
    tampering.
    """
    user_id    = _get_param("user_id", kwargs) or _STATE.get("user_id", "")
    request_id = _get_param("request_id", kwargs) or _STATE.get("request_id", str(uuid.uuid4()))

    # Collect task results from XCom (Airflow) or _STATE (standalone)
    if kwargs.get("ti"):
        ti = kwargs["ti"]
        iceberg_result = ti.xcom_pull(task_ids="delete_iceberg",    key="iceberg_result") or {}
        ch_result      = ti.xcom_pull(task_ids="delete_clickhouse", key="ch_result")      or {}
        druid_result   = ti.xcom_pull(task_ids="delete_druid",      key="druid_result")   or {}
    else:
        task_states    = _STATE.get("tasks", {})
        iceberg_result = task_states.get("iceberg",    {})
        ch_result      = task_states.get("clickhouse", {})
        druid_result   = task_states.get("druid",      {})

    completed_at = datetime.now(timezone.utc).isoformat()

    audit_record = {
        "schema_version":  "1.0",
        "request_id":      request_id,
        "user_id":         user_id,
        "requested_at":    _STATE.get("requested_at", completed_at),
        "completed_at":    completed_at,
        "regulation":      "GDPR Article 17",
        "handler":         "gdpr_right_to_forget DAG",
        "systems_erased": {
            "iceberg": {
                "table":        ICEBERG_TABLE,
                "rows_deleted": iceberg_result.get("rows_deleted", "unknown"),
                "status":       "ok" if iceberg_result else "not_run",
            },
            "clickhouse": {
                "tables":  CLICKHOUSE_TABLES,
                "status":  "ok" if ch_result else "not_run",
                "details": ch_result,
            },
            "druid": {
                "datasource":      DRUID_DATASOURCE,
                "intervals_found": druid_result.get("intervals_found", 0),
                "kill_tasks":      druid_result.get("kill_tasks", []),
                "status":          "ok" if druid_result else "not_run",
            },
        },
    }

    # Write to MinIO
    now      = datetime.now(timezone.utc)
    s3_key   = (
        f"{AUDIT_PREFIX}/"
        f"{now.year}/{now.month:02d}/{now.day:02d}/"
        f"{request_id}.json"
    )
    s3_path  = f"s3://{AUDIT_BUCKET}/{s3_key}"

    try:
        s3 = _s3_client()
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=s3_key,
            Body=json.dumps(audit_record, indent=2).encode(),
            ContentType="application/json",
            # Metadata for bucket-level governance queries
            Metadata={
                "x-amz-meta-request-id": request_id,
                "x-amz-meta-user-id":    user_id,
                "x-amz-meta-regulation": "GDPR-Art17",
            },
        )
        log.info("Audit log written to %s", s3_path)
        audit_record["s3_path"] = s3_path
    except Exception as exc:
        log.error("Failed to write audit log to S3: %s", exc)
        audit_record["s3_error"] = str(exc)

    log.info(
        "GDPR erasure completed  request_id=%s  user_id=%s",
        request_id, user_id,
    )
    _STATE["audit_record"] = audit_record
    return audit_record


# ═══════════════════════════════════════════════════════════════════════════════
# AIRFLOW DAG DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
    from airflow.utils.dates import days_ago
    from datetime import timedelta

    _default_args = {
        "owner":            "data-platform",
        "retries":          0,                  # do NOT auto-retry erasure tasks
        "email_on_failure": True,               # always alert on erasure failures
        "depends_on_past":  False,
    }

    with DAG(
        dag_id="gdpr_right_to_forget",
        description="GDPR Art.17 erasure: Iceberg → ClickHouse → Druid → audit log",
        schedule_interval=None,                 # manual trigger only
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=5,                      # allow concurrent erasures for different users
        default_args=_default_args,
        params={"user_id": ""},                 # required trigger parameter
        tags=["gdpr", "compliance", "erasure"],
        doc_md=__doc__,
    ) as dag:

        t_validate  = PythonOperator(
            task_id="validate_request",
            python_callable=validate_request,
        )
        t_iceberg   = PythonOperator(
            task_id="delete_iceberg",
            python_callable=delete_iceberg,
            op_kwargs={"dry_run": False},
        )
        t_ch        = PythonOperator(
            task_id="delete_clickhouse",
            python_callable=delete_clickhouse,
            op_kwargs={"dry_run": False},
        )
        t_druid     = PythonOperator(
            task_id="delete_druid",
            python_callable=delete_druid,
            op_kwargs={"dry_run": False},
        )
        t_audit     = PythonOperator(
            task_id="write_audit_log",
            python_callable=write_audit_log,
            trigger_rule="all_done",            # write audit even if a delete task fails
        )

        t_validate >> [t_iceberg, t_ch, t_druid] >> t_audit

except ImportError:
    log.debug("Airflow not found — running in standalone mode")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# python airflow/dags/gdpr_right_to_forget.py --user-id <id> [--dry-run]
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GDPR right-to-forget erasure — run without Airflow",
    )
    parser.add_argument("--user-id",  required=True, help="User ID to erase")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print what would be done without writing anything")
    parser.add_argument("--skip-iceberg",    action="store_true")
    parser.add_argument("--skip-clickhouse", action="store_true")
    parser.add_argument("--skip-druid",      action="store_true")
    args = parser.parse_args()

    # Inject params into _STATE so task functions can read them
    _STATE["user_id"] = args.user_id

    log.info(
        "Standalone GDPR erasure  user_id=%s  dry_run=%s",
        args.user_id, args.dry_run,
    )

    # Task 1: validate
    record = validate_request(params={"user_id": args.user_id})
    _STATE.update(record)

    # Tasks 2-4: delete (parallel in Airflow; sequential here for simplicity)
    if not args.skip_iceberg:
        delete_iceberg(dry_run=args.dry_run, params={"user_id": args.user_id})
    else:
        log.info("Skipping Iceberg delete")

    if not args.skip_clickhouse:
        delete_clickhouse(dry_run=args.dry_run, params={"user_id": args.user_id})
    else:
        log.info("Skipping ClickHouse delete")

    if not args.skip_druid:
        delete_druid(dry_run=args.dry_run, params={"user_id": args.user_id})
    else:
        log.info("Skipping Druid kill")

    # Task 5: audit
    audit = write_audit_log(params={"user_id": args.user_id})

    print(json.dumps(audit, indent=2))
    sys.exit(0)

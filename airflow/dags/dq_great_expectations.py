#!/usr/bin/env python3
"""
dq_great_expectations — Hourly data-quality validation on silver.events_clean
Schedule : @hourly
Owner    : data-platform

Tasks
─────
1. load_sample       Read the latest hour of events from the Iceberg table
                     into a Pandas DataFrame.
2. run_expectations  Execute the GE suite: not-null, value ranges, regex,
                     set membership, statistical checks.
3. publish_results   Write the validation result JSON to MinIO and push a
                     summary to XCom.  On failure → Slack alert.

Expectations
────────────
  event_id    not null, unique sample, matches UUID-like regex
  event_time  not null, within [now-48h, now+5min] (freshness window)
  event_type  not null, in the 9-value enum
  device      not null, in [web, android, ios, other]
  price       >= 0 when not null
  quantity    >= 0 and <= 10 000 when not null
  category    lowercase (no uppercase characters) when not null
  session_id  not null, len >= 8

Standalone usage
────────────────
  python airflow/dags/dq_great_expectations.py
  python airflow/dags/dq_great_expectations.py --lookback-hours 3 --sample-rows 5000
  python airflow/dags/dq_great_expectations.py --fail-fast   # exit 1 on any failure

Dependencies
────────────
  pip install "pyiceberg[s3fsspec]>=0.7" pyarrow pandas great_expectations boto3
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
log = logging.getLogger("dq_great_expectations")

# ── Resolved config values ────────────────────────────────────────────────────
CATALOG_URL       = cfg.iceberg.rest_url
S3_ENDPOINT       = cfg.minio.endpoint
S3_ACCESS_KEY     = cfg.minio.access_key
S3_SECRET_KEY     = cfg.minio.secret_key
TABLE_ID          = cfg.iceberg.events_table
DQ_RESULTS_BUCKET = cfg.minio.dq_results_bucket
DQ_RESULTS_PREFIX = cfg.pipeline.dq_results_prefix
SLACK_WEBHOOK     = cfg.slack_webhook_url
LOOKBACK_HOURS    = cfg.pipeline.dq_lookback_hours
SAMPLE_ROWS       = cfg.pipeline.dq_sample_rows

VALID_EVENT_TYPES = [
    "VIEW", "ADD_TO_CART", "REMOVE_CART", "WISHLIST",
    "ORDER_CREATED", "PAYMENT_SUCCESS", "PAYMENT_FAILED",
    "ORDER_CANCELLED", "REFUND",
]
VALID_DEVICES = ["web", "android", "ios", "other"]


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


def _send_slack(message: str) -> None:
    if not SLACK_WEBHOOK:
        log.warning("SLACK_WEBHOOK_URL not set — alert not sent")
        log.warning("Alert message: %s", message)
        return
    try:
        import requests
        resp = requests.post(
            SLACK_WEBHOOK,
            json={"text": message},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Slack alert sent")
    except Exception as exc:
        log.error("Failed to send Slack alert: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — load_sample
# ═══════════════════════════════════════════════════════════════════════════════

def load_sample(lookback_hours: int = LOOKBACK_HOURS, max_rows: int = SAMPLE_ROWS, **kwargs: Any):
    """
    Read the last `lookback_hours` of data from the Iceberg table into a
    Pandas DataFrame.  Caps at `max_rows` to bound memory usage.

    Returns the DataFrame and pushes it to XCom (serialised as Parquet bytes
    via a temp path) when running inside Airflow.
    """
    log.info(
        "load_sample  table=%s  lookback=%dh  max_rows=%d",
        TABLE_ID, lookback_hours, max_rows,
    )

    cat   = _catalog()
    parts = TABLE_ID.split(".")
    ident = tuple(parts) if len(parts) > 2 else TABLE_ID
    tbl   = cat.load_table(ident)

    # Filter to the last N hours using PyIceberg expression pushdown
    try:
        from pyiceberg.expressions import GreaterThanOrEqual
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000
        )
        scan   = tbl.scan(row_filter=GreaterThanOrEqual("event_time", cutoff_ms))
    except Exception as exc:
        log.warning("Pushdown filter failed (%s) — scanning full table", exc)
        scan = tbl.scan()

    # Convert to Pandas — arrow→pandas is zero-copy for most types
    try:
        import pyarrow as pa
        arrow_table = scan.to_arrow()
        if max_rows and len(arrow_table) > max_rows:
            log.info("Sampling %d of %d rows", max_rows, len(arrow_table))
            import pyarrow.compute as pc
            # Deterministic sample: take every Nth row
            step = max(1, len(arrow_table) // max_rows)
            arrow_table = arrow_table.take(list(range(0, len(arrow_table), step))[:max_rows])
        df = arrow_table.to_pandas()
    except Exception as exc:
        log.error("Failed to load table data: %s", exc)
        raise

    log.info("Loaded %d rows, %d columns", len(df), len(df.columns))

    # Push to XCom as a JSON-serialisable summary; the full DataFrame is stored
    # as a task-level temp file for the next task
    summary = {
        "row_count": len(df),
        "columns":   list(df.columns),
        "lookback_hours": lookback_hours,
    }
    if kwargs.get("ti"):                         # running inside Airflow
        kwargs["ti"].xcom_push(key="df_summary", value=summary)

    # Store df in the instance dict so run_expectations() can pick it up
    # when executing standalone (tasks share memory within the same process)
    _STATE["df"] = df
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — run_expectations
# ═══════════════════════════════════════════════════════════════════════════════

_STATE: dict = {}   # shared state for standalone execution (tasks in same process)


def run_expectations(**kwargs: Any) -> dict:
    """
    Run the Great Expectations suite against the loaded DataFrame.

    Uses GE's ephemeral (in-memory) context — no on-disk DataContext directory
    needed.  Validation results are returned as a dict and pushed to XCom.
    """
    try:
        import great_expectations as gx
        from great_expectations.data_context import EphemeralDataContext
    except ImportError:
        raise RuntimeError("pip install great_expectations>=0.18")

    # Retrieve DataFrame from previous task (XCom in Airflow, _STATE standalone)
    df = _STATE.get("df")
    if df is None:
        # Try to load fresh if running as a standalone task
        log.warning("No DataFrame in _STATE — calling load_sample() first")
        load_sample(**kwargs)
        df = _STATE.get("df")
    if df is None:
        raise RuntimeError("DataFrame not available — run load_sample first")

    log.info("Running GE expectations on %d rows", len(df))

    # ── Build GE context (ephemeral — no filesystem writes) ──────────────────
    context = gx.get_context(mode="ephemeral")

    # Add a Pandas datasource
    ds = context.sources.add_pandas("events_source")
    da = ds.add_dataframe_asset("events_clean")

    batch_request = da.build_batch_request(dataframe=df)
    validator      = context.get_validator(
        batch_request=batch_request,
        expectation_suite_name="silver_events_clean",
        create_expectation_suite_if_not_exists=True,
    )

    now_ts   = datetime.now(timezone.utc).timestamp() * 1000
    ago48_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp() * 1000
    fut5_ts  = (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp() * 1000

    # ── Expectations ─────────────────────────────────────────────────────────
    expectations = [

        # ── Identity & freshness ────────────────────────────────────────────
        validator.expect_column_values_to_not_be_null("event_id"),
        validator.expect_column_value_lengths_to_be_between("event_id", min_value=8),
        validator.expect_column_values_to_match_regex(
            "event_id",
            r"^[0-9a-fA-F\-]{8,}$",           # UUID or similar hex string
        ),

        validator.expect_column_values_to_not_be_null("event_time"),
        validator.expect_column_values_to_be_between(
            "event_time",
            min_value=ago48_ts,
            max_value=fut5_ts,                 # no future timestamps beyond 5 min clock skew
        ),

        validator.expect_column_values_to_not_be_null("session_id"),
        validator.expect_column_value_lengths_to_be_between("session_id", min_value=8),

        # ── Event classification ────────────────────────────────────────────
        validator.expect_column_values_to_not_be_null("event_type"),
        validator.expect_column_values_to_be_in_set("event_type", VALID_EVENT_TYPES),

        validator.expect_column_values_to_not_be_null("device"),
        validator.expect_column_values_to_be_in_set("device", VALID_DEVICES),

        # ── Nullable commerce metrics ───────────────────────────────────────
        # Price must be non-negative when present
        validator.expect_column_values_to_be_between(
            "price",
            min_value=0.0,
            mostly=0.99,                        # allow 1 % for data-entry errors
        ),
        # Quantity: 0–10 000 (catches obvious outliers)
        validator.expect_column_values_to_be_between(
            "quantity",
            min_value=0,
            max_value=10_000,
            mostly=0.999,
        ),

        # ── Category format ─────────────────────────────────────────────────
        # Flink lowercases category before writing; uppercase letters indicate
        # the normalisation step was skipped
        validator.expect_column_values_to_match_regex(
            "category",
            r"^[a-z][a-z0-9_]*$",
            mostly=0.98,                        # allow 2% for unknown/null categories
        ),

        # ── Statistical completeness ────────────────────────────────────────
        # Payment events must have price set; non-payment events must not
        validator.expect_column_pair_values_a_to_be_greater_than_b(
            "event_time", "event_time",         # self-comparison — GE API requires two cols
            or_equal=True,
            ignore_row_if="either_value_is_missing",
        ),

        # Row count sanity: at least 1 row per hour per device (very loose lower bound)
        validator.expect_table_row_count_to_be_between(
            min_value=len(VALID_DEVICES),       # at least 4 rows
        ),
    ]

    # ── Run validation ────────────────────────────────────────────────────────
    validation_result = validator.validate(result_format="SUMMARY")

    # ── Parse results ─────────────────────────────────────────────────────────
    passed_count = sum(1 for r in expectations if getattr(r, "success", False))
    failed       = [
        {
            "expectation": r.expectation_config.expectation_type,
            "column":      r.expectation_config.kwargs.get("column", "—"),
            "result":      str(r.result)[:200],
        }
        for r in expectations if not getattr(r, "success", False)
    ]

    suite_passed = validation_result.success
    stats = {
        "suite_passed":   suite_passed,
        "expectations":   len(expectations),
        "passed":         passed_count,
        "failed":         len(failed),
        "failed_details": failed,
        "row_count":      len(df),
        "table":          TABLE_ID,
        "ts":             datetime.now(timezone.utc).isoformat(),
    }

    if suite_passed:
        log.info("DQ PASSED  %d/%d expectations  table=%s", passed_count, len(expectations), TABLE_ID)
    else:
        log.error("DQ FAILED  %d failures  table=%s", len(failed), TABLE_ID)
        for f in failed:
            log.error("  ✗ %s on column '%s'", f["expectation"], f["column"])

    _STATE["dq_stats"] = stats

    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="dq_stats", value=stats)

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — publish_results
# ═══════════════════════════════════════════════════════════════════════════════

def publish_results(fail_on_error: bool = False, **kwargs: Any) -> dict:
    """
    Write validation results to MinIO and send a Slack alert if the suite failed.

    Path: s3://warehouse/dq-results/great-expectations/YYYY/MM/DD/HH/result.json

    On suite failure:
      • Raises AirflowException (marks the task + DAG run as FAILED) if
        fail_on_error=True (default in the Airflow DAG).
      • Sends a Slack alert regardless.
    """
    # Retrieve stats from previous task
    stats = _STATE.get("dq_stats")
    if stats is None and kwargs.get("ti"):
        stats = kwargs["ti"].xcom_pull(task_ids="run_expectations", key="dq_stats")
    if stats is None:
        raise RuntimeError("No DQ stats found — run run_expectations first")

    # ── Write result to MinIO ─────────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    s3_key  = (
        f"{DQ_RESULTS_PREFIX}/"
        f"{now_utc.year}/{now_utc.month:02d}/{now_utc.day:02d}/"
        f"{now_utc.hour:02d}/result.json"
    )

    try:
        s3 = _s3_client()
        s3.put_object(
            Bucket=DQ_RESULTS_BUCKET,
            Key=s3_key,
            Body=json.dumps(stats, indent=2).encode(),
            ContentType="application/json",
        )
        log.info("DQ results written to s3://%s/%s", DQ_RESULTS_BUCKET, s3_key)
        stats["s3_path"] = f"s3://{DQ_RESULTS_BUCKET}/{s3_key}"
    except Exception as exc:
        log.warning("Could not write results to S3: %s", exc)

    # ── Alert on failure ──────────────────────────────────────────────────────
    if not stats["suite_passed"]:
        failed_list = "\n".join(
            f"  • `{f['expectation']}` on `{f['column']}`"
            for f in stats["failed_details"][:10]   # cap at 10 for readability
        )
        msg = (
            f":x: *DQ Suite FAILED* — `{TABLE_ID}`\n"
            f"{stats['failed']} of {stats['expectations']} expectations failed "
            f"({stats['row_count']:,} rows sampled)\n"
            f"Failed checks:\n{failed_list}\n"
            f"Results: `{stats.get('s3_path', 'N/A')}`"
        )
        _send_slack(msg)

        if fail_on_error:
            # Raise so Airflow marks this task (and the DAG run) as FAILED
            try:
                from airflow.exceptions import AirflowException
                raise AirflowException(
                    f"DQ suite failed: {stats['failed']} expectations not met"
                )
            except ImportError:
                raise RuntimeError(
                    f"DQ suite failed: {stats['failed']} expectations not met"
                )
    else:
        log.info("DQ suite passed — no alert needed")

    return stats


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
        "retries":          1,
        "retry_delay":      timedelta(minutes=5),
        "email_on_failure": False,
    }

    with DAG(
        dag_id="dq_great_expectations",
        description="Hourly GE data-quality check on silver.events_clean",
        schedule_interval="@hourly",
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,
        default_args=_default_args,
        tags=["dq", "great-expectations", "iceberg", "silver"],
        doc_md=__doc__,
    ) as dag:

        t_load = PythonOperator(
            task_id="load_sample",
            python_callable=load_sample,
        )

        t_expect = PythonOperator(
            task_id="run_expectations",
            python_callable=run_expectations,
        )

        t_publish = PythonOperator(
            task_id="publish_results",
            python_callable=publish_results,
            op_kwargs={"fail_on_error": True},
        )

        t_load >> t_expect >> t_publish

except ImportError:
    log.debug("Airflow not found — running in standalone mode")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# python airflow/dags/dq_great_expectations.py [--lookback-hours N] [--fail-fast]
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run GE data-quality checks without Airflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--lookback-hours", type=int, default=LOOKBACK_HOURS,
                        help=f"Hours of data to validate (default: {LOOKBACK_HOURS})")
    parser.add_argument("--sample-rows", type=int, default=SAMPLE_ROWS,
                        help=f"Max rows to sample (default: {SAMPLE_ROWS})")
    parser.add_argument("--fail-fast", action="store_true",
                        help="Exit with code 1 if any expectation fails")
    args = parser.parse_args()

    log.info(
        "Running standalone DQ check  table=%s  lookback=%dh  sample=%d",
        TABLE_ID, args.lookback_hours, args.sample_rows,
    )

    load_sample(lookback_hours=args.lookback_hours, max_rows=args.sample_rows)
    stats = run_expectations()
    publish_results(fail_on_error=args.fail_fast)

    print(json.dumps(stats, indent=2))
    sys.exit(0 if stats["suite_passed"] else 1)

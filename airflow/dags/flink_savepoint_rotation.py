#!/usr/bin/env python3
"""
flink_savepoint_rotation — Daily Flink savepoint management DAG
Schedule : daily at 03:00 UTC
Owner    : data-platform

Tasks
─────
1. trigger_savepoints   POST /jobs/{id}/savepoints for every RUNNING Flink job.
                        Blocks until each savepoint reaches COMPLETED status.
2. list_savepoints      Enumerate all savepoint directories in s3://flink-savepoints/
                        and parse their metadata to find creation timestamps.
3. delete_old           Delete savepoint directories older than RETAIN_DAYS (7)
                        from MinIO, keeping at least MIN_KEEP (2) per job.

Why save + rotate?
──────────────────
Savepoints capture the full operator state (including Kafka offsets) at a point
in time.  They let you:
  • Resume the job from a known-good state after a code upgrade.
  • Roll back to yesterday's state if a bad deployment corrupts state.
  • Drain a job cleanly without losing in-flight events.

Rotating old savepoints prevents unbounded MinIO storage growth — a Flink job
running continuously accumulates one savepoint per day.

Standalone usage
────────────────
  python airflow/dags/flink_savepoint_rotation.py
  python airflow/dags/flink_savepoint_rotation.py --task list
  python airflow/dags/flink_savepoint_rotation.py --task delete --dry-run

  Override endpoints for host-side testing:
    FLINK_REST_URL=http://localhost:8082 \\
    MINIO_ENDPOINT=http://localhost:9002 \\
    python airflow/dags/flink_savepoint_rotation.py

Dependencies
────────────
  pip install requests boto3
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("flink_savepoint_rotation")

# ── Configuration ─────────────────────────────────────────────────────────────
FLINK_URL         = os.getenv("FLINK_REST_URL",         "http://flink-jobmanager:8081")
S3_ENDPOINT       = os.getenv("MINIO_ENDPOINT",         "http://minio:9000")
S3_ACCESS_KEY     = os.getenv("MINIO_ACCESS_KEY",       "minio")
S3_SECRET_KEY     = os.getenv("MINIO_SECRET_KEY",       "minio123")
SAVEPOINT_BUCKET  = os.getenv("FLINK_SAVEPOINT_BUCKET", "flink-savepoints")
SAVEPOINT_PREFIX  = os.getenv("FLINK_SAVEPOINT_PREFIX", "ecom")
RETAIN_DAYS       = int(os.getenv("FLINK_SAVEPOINT_RETAIN_DAYS", "7"))
MIN_KEEP          = int(os.getenv("FLINK_SAVEPOINT_MIN_KEEP",     "2"))
SAVEPOINT_TIMEOUT = int(os.getenv("FLINK_SAVEPOINT_TIMEOUT_SEC",  "300"))

# ── Shared state ──────────────────────────────────────────────────────────────
_STATE: dict = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flink_get(path: str) -> dict:
    """GET from the Flink REST API, return parsed JSON."""
    import requests
    url  = f"{FLINK_URL}{path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _flink_post(path: str, payload: dict) -> dict:
    """POST to the Flink REST API, return parsed JSON."""
    import requests
    url  = f"{FLINK_URL}{path}"
    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


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


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — trigger_savepoints
# ═══════════════════════════════════════════════════════════════════════════════

def trigger_savepoints(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Trigger a savepoint for every RUNNING Flink job.

    Flow:
      GET /jobs/overview                → find all RUNNING job IDs + names
      POST /jobs/{id}/savepoints        → trigger async savepoint
      GET  /jobs/{id}/savepoints/{tid}  → poll until COMPLETED or FAILED

    The savepoint target directory is:
      s3://{SAVEPOINT_BUCKET}/{SAVEPOINT_PREFIX}/{job_id}/{YYYY-MM-DD_HH-MM}/

    Returns a dict of {job_id: savepoint_path} for downstream tasks.
    """
    log.info("trigger_savepoints  flink_url=%s  dry_run=%s", FLINK_URL, dry_run)

    # List all RUNNING jobs
    try:
        overview = _flink_get("/jobs/overview")
    except Exception as exc:
        log.error("Cannot reach Flink REST API at %s: %s", FLINK_URL, exc)
        raise

    running_jobs = [
        j for j in overview.get("jobs", [])
        if j.get("state") == "RUNNING"
    ]

    if not running_jobs:
        log.warning("No RUNNING Flink jobs found — nothing to save")
        _STATE["savepoint_results"] = {}
        return {"saved_jobs": 0, "savepoints": {}}

    log.info("Found %d RUNNING job(s): %s", len(running_jobs),
             [j.get("name", j.get("jid")) for j in running_jobs])

    now_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    results   = {}

    for job in running_jobs:
        job_id   = job["jid"]
        job_name = job.get("name", job_id)
        target   = (
            f"s3://{SAVEPOINT_BUCKET}/{SAVEPOINT_PREFIX}/{job_id}/{now_str}/"
        )

        if dry_run:
            log.info("[dry-run] Would save job %s (%s) → %s", job_id, job_name, target)
            results[job_id] = {"job_name": job_name, "target": target, "dry_run": True}
            continue

        # POST to trigger savepoint
        try:
            trigger_resp = _flink_post(
                f"/jobs/{job_id}/savepoints",
                {
                    "target-directory":  target,
                    "cancel-job":        False,  # keep job running after savepoint
                },
            )
            trigger_id = trigger_resp.get("request-id") or trigger_resp.get("id")
            log.info(
                "Savepoint triggered  job=%s (%s)  trigger_id=%s",
                job_id, job_name, trigger_id,
            )
        except Exception as exc:
            log.error("Failed to trigger savepoint for job %s: %s", job_id, exc)
            results[job_id] = {"job_name": job_name, "status": "trigger_failed", "error": str(exc)}
            continue

        # Poll until savepoint completes or times out
        deadline   = time.time() + SAVEPOINT_TIMEOUT
        savepoint_path = None
        status         = "UNKNOWN"

        while time.time() < deadline:
            try:
                poll_resp = _flink_get(f"/jobs/{job_id}/savepoints/{trigger_id}")
                status    = poll_resp.get("status", {}).get("id", "UNKNOWN")
                log.debug("Savepoint poll  job=%s  status=%s", job_id, status)

                if status == "COMPLETED":
                    savepoint_path = (
                        poll_resp.get("operation", {})
                                 .get("location")
                        or target
                    )
                    log.info(
                        "Savepoint COMPLETED  job=%s (%s)  path=%s",
                        job_id, job_name, savepoint_path,
                    )
                    break
                elif status == "FAILED":
                    error_msg = poll_resp.get("operation", {}).get("failure-cause", "unknown")
                    log.error(
                        "Savepoint FAILED  job=%s (%s)  cause=%s",
                        job_id, job_name, error_msg,
                    )
                    break

            except Exception as exc:
                log.warning("Poll request failed: %s — retrying", exc)

            time.sleep(10)
        else:
            log.warning(
                "Savepoint timed out after %ds  job=%s (%s)",
                SAVEPOINT_TIMEOUT, job_id, job_name,
            )
            status = "TIMEOUT"

        results[job_id] = {
            "job_name":      job_name,
            "status":        status,
            "savepoint_path": savepoint_path,
            "triggered_at":  now_str,
        }

    summary = {
        "saved_jobs": sum(1 for v in results.values() if v.get("status") == "COMPLETED"),
        "total_jobs": len(running_jobs),
        "savepoints": results,
        "ts":         datetime.now(timezone.utc).isoformat(),
    }
    _STATE["savepoint_results"] = results
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="savepoint_results", value=results)
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — list_savepoints
# ═══════════════════════════════════════════════════════════════════════════════

def list_savepoints(**kwargs: Any) -> dict:
    """
    Enumerate all savepoint directories in MinIO under the configured prefix.

    Uses the MinIO (S3) ListObjectsV2 API to find all objects under
    s3://{SAVEPOINT_BUCKET}/{SAVEPOINT_PREFIX}/.

    Groups savepoints by job_id and extracts the creation timestamp from the
    directory name (format: {job_id}/{YYYY-MM-DD_HH-MM}/).

    Returns a dict of {job_id: [sorted list of {path, created_at}]}.
    """
    log.info(
        "list_savepoints  bucket=%s  prefix=%s/",
        SAVEPOINT_BUCKET, SAVEPOINT_PREFIX,
    )

    s3        = _s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    pages     = paginator.paginate(
        Bucket=SAVEPOINT_BUCKET,
        Prefix=f"{SAVEPOINT_PREFIX}/",
        Delimiter="/",          # list "directories" one level at a time
    )

    # Collect unique job directories: ecom/{job_id}/
    job_prefixes = set()
    for page in pages:
        for prefix in page.get("CommonPrefixes", []):
            job_prefixes.add(prefix["Prefix"])

    log.info("Found %d job-level savepoint directories", len(job_prefixes))

    # For each job, list its savepoint sub-directories
    savepoints_by_job = {}

    for job_prefix in sorted(job_prefixes):
        # job_prefix looks like: ecom/a1b2c3d4.../
        job_id      = job_prefix.rstrip("/").split("/")[-1]
        sp_pages    = paginator.paginate(
            Bucket=SAVEPOINT_BUCKET,
            Prefix=job_prefix,
            Delimiter="/",
        )
        sp_list = []
        for page in sp_pages:
            for sp_prefix in page.get("CommonPrefixes", []):
                sp_path  = sp_prefix["Prefix"]
                # Extract timestamp from path: .../YYYY-MM-DD_HH-MM/
                dir_name = sp_path.rstrip("/").split("/")[-1]
                try:
                    created_at = datetime.strptime(dir_name, "%Y-%m-%d_%H-%M")
                    created_at = created_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    # Non-standard directory name — use object modification time
                    # Fallback: list objects inside to get LastModified
                    try:
                        obj_list = s3.list_objects_v2(
                            Bucket=SAVEPOINT_BUCKET,
                            Prefix=sp_path,
                            MaxKeys=1,
                        ).get("Contents", [])
                        if obj_list:
                            created_at = obj_list[0]["LastModified"]
                        else:
                            log.debug("Empty savepoint directory: %s", sp_path)
                            continue
                    except Exception:
                        created_at = datetime.now(timezone.utc)

                sp_list.append({
                    "path":       f"s3://{SAVEPOINT_BUCKET}/{sp_path}",
                    "s3_prefix":  sp_path,
                    "created_at": created_at.isoformat(),
                    "job_id":     job_id,
                })

        # Sort ascending so newest is last
        sp_list.sort(key=lambda x: x["created_at"])
        savepoints_by_job[job_id] = sp_list
        log.info("Job %s: %d savepoint(s)", job_id, len(sp_list))

    _STATE["all_savepoints"] = savepoints_by_job
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="all_savepoints", value=savepoints_by_job)
    return savepoints_by_job


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — delete_old
# ═══════════════════════════════════════════════════════════════════════════════

def delete_old(dry_run: bool = False, **kwargs: Any) -> dict:
    """
    Delete savepoint directories older than RETAIN_DAYS, keeping at least
    MIN_KEEP per job regardless of age.

    Deletion uses S3 batch delete (up to 1 000 objects per request).

    Returns a summary of deleted / retained savepoints.
    """
    log.info(
        "delete_old  retain_days=%d  min_keep=%d  dry_run=%s",
        RETAIN_DAYS, MIN_KEEP, dry_run,
    )

    # Retrieve from previous task
    if kwargs.get("ti"):
        savepoints_by_job = kwargs["ti"].xcom_pull(
            task_ids="list_savepoints", key="all_savepoints"
        ) or {}
    else:
        savepoints_by_job = _STATE.get("all_savepoints")

    if not savepoints_by_job:
        log.warning("No savepoint data found — run list_savepoints first")
        # Try to collect it now
        savepoints_by_job = list_savepoints(**kwargs)

    cutoff         = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    s3             = _s3_client()
    deleted_total  = 0
    retained_total = 0
    delete_summary = {}

    for job_id, sp_list in savepoints_by_job.items():
        # Sort ascending (oldest first); keep the newest MIN_KEEP always
        sp_sorted = sorted(sp_list, key=lambda x: x["created_at"])

        to_delete = []
        to_keep   = []

        for i, sp in enumerate(sp_sorted):
            # Always keep the most recent MIN_KEEP
            is_among_newest = i >= len(sp_sorted) - MIN_KEEP
            created_at      = datetime.fromisoformat(sp["created_at"])
            is_expired      = created_at < cutoff

            if is_expired and not is_among_newest:
                to_delete.append(sp)
            else:
                to_keep.append(sp)

        log.info(
            "Job %s: %d to delete, %d to keep",
            job_id, len(to_delete), len(to_keep),
        )

        deleted_paths = []
        for sp in to_delete:
            sp_prefix = sp["s3_prefix"]
            if dry_run:
                log.info("[dry-run] Would delete s3://%s/%s", SAVEPOINT_BUCKET, sp_prefix)
                deleted_paths.append(sp["path"])
                continue

            # List all objects in the savepoint directory
            try:
                pages = s3.get_paginator("list_objects_v2").paginate(
                    Bucket=SAVEPOINT_BUCKET,
                    Prefix=sp_prefix,
                )
                objects = [
                    {"Key": obj["Key"]}
                    for page in pages
                    for obj in page.get("Contents", [])
                ]
                if not objects:
                    log.warning("Savepoint directory is empty: %s", sp_prefix)
                    continue

                # Batch delete up to 1000 objects at a time
                for i in range(0, len(objects), 1000):
                    batch = objects[i: i + 1000]
                    s3.delete_objects(
                        Bucket=SAVEPOINT_BUCKET,
                        Delete={"Objects": batch, "Quiet": True},
                    )

                log.info(
                    "Deleted savepoint  job=%s  path=%s  objects=%d",
                    job_id, sp["path"], len(objects),
                )
                deleted_paths.append(sp["path"])
                deleted_total += 1

            except Exception as exc:
                log.error("Failed to delete %s: %s", sp["path"], exc)

        retained_total += len(to_keep)
        delete_summary[job_id] = {
            "deleted":  deleted_paths,
            "retained": [sp["path"] for sp in to_keep],
        }

    summary = {
        "cutoff":          cutoff.isoformat(),
        "retain_days":     RETAIN_DAYS,
        "min_keep":        MIN_KEEP,
        "deleted_total":   deleted_total,
        "retained_total":  retained_total,
        "by_job":          delete_summary,
        "dry_run":         dry_run,
        "ts":              datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "Savepoint rotation complete  deleted=%d  retained=%d  dry_run=%s",
        deleted_total, retained_total, dry_run,
    )
    _STATE["rotation_summary"] = summary
    if kwargs.get("ti"):
        kwargs["ti"].xcom_push(key="rotation_summary", value=summary)
    return summary


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
        "depends_on_past":  False,
    }

    with DAG(
        dag_id="flink_savepoint_rotation",
        description="Daily: trigger Flink savepoints → list in S3 → delete >7 days",
        schedule_interval="0 3 * * *",          # 03:00 UTC every day
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,
        default_args=_default_args,
        tags=["flink", "savepoint", "maintenance"],
        doc_md=__doc__,
    ) as dag:

        t_trigger = PythonOperator(
            task_id="trigger_savepoints",
            python_callable=trigger_savepoints,
            op_kwargs={"dry_run": False},
            execution_timeout=timedelta(minutes=15),  # some jobs take time to save
        )

        t_list = PythonOperator(
            task_id="list_savepoints",
            python_callable=list_savepoints,
        )

        t_delete = PythonOperator(
            task_id="delete_old",
            python_callable=delete_old,
            op_kwargs={"dry_run": False},
        )

        # Trigger new savepoints FIRST, then list (so new ones are visible),
        # then delete old ones (so we never accidentally delete the only copy)
        t_trigger >> t_list >> t_delete

except ImportError:
    log.debug("Airflow not found — running in standalone mode")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# python airflow/dags/flink_savepoint_rotation.py [--task trigger|list|delete|all]
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Flink savepoint rotation — run without Airflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  FLINK_REST_URL     Flink JobManager REST URL  (default: http://flink-jobmanager:8081)
  MINIO_ENDPOINT     MinIO S3 endpoint          (default: http://minio:9000)
  MINIO_ACCESS_KEY   MinIO access key           (default: minio)
  MINIO_SECRET_KEY   MinIO secret key           (default: minio123)
  FLINK_SAVEPOINT_RETAIN_DAYS  Days to keep savepoints  (default: 7)
  FLINK_SAVEPOINT_MIN_KEEP     Minimum savepoints to keep per job (default: 2)
""",
    )
    parser.add_argument(
        "--task",
        choices=["trigger", "list", "delete", "all"],
        default="all",
        help="Which task(s) to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making changes",
    )
    args = parser.parse_args()

    log.info(
        "Standalone savepoint rotation  task=%s  dry_run=%s",
        args.task, args.dry_run,
    )

    results = {}

    if args.task in ("trigger", "all"):
        results["trigger"] = trigger_savepoints(dry_run=args.dry_run)

    if args.task in ("list", "all"):
        results["list"] = list_savepoints()

    if args.task in ("delete", "all"):
        results["delete"] = delete_old(dry_run=args.dry_run)

    print(json.dumps(results, indent=2, default=str))
    sys.exit(0)

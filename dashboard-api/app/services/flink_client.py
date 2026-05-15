"""
FlinkClient — async httpx client for the Flink REST API with per-request
exponential-backoff retry.

Flink REST port mapping:
  - External (host):  8082  (as configured in Settings.flink_url)
  - Internal (Docker): 8081 (standard Flink REST port)
The FLINK_URL env var selects the right value per environment.
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Metrics to pull per-job (comma-separated for the Flink metrics API)
_JOB_METRIC_IDS = ",".join([
    "numRecordsInPerSecond",
    "numRecordsOutPerSecond",
    "numRecordsIn",
    "numRecordsOut",
    "currentInputWatermark",
])

# HTTP status codes that are safe to retry
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# ── Client class ──────────────────────────────────────────────────────────────

class FlinkClient:
    """
    Async Flink REST API client.

    All network calls go through ``_request`` which retries up to
    ``max_retries`` times with exponential back-off on transient errors.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._client: Optional[httpx.AsyncClient] = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    # ── Retry core ────────────────────────────────────────────────────────────

    async def _request(self, path: str, timeout: Optional[float] = None) -> Any:
        """
        GET ``path`` with automatic retry on transient failures.

        Raises the last exception when all attempts are exhausted.
        Back-off sleeps: backoff_factor * 2^attempt  (0.3 s, 0.6 s, 1.2 s …)
        """
        last_exc: Exception = RuntimeError("No attempts made")

        for attempt in range(self._max_retries):
            try:
                resp = await self._http().get(
                    path, timeout=timeout or self._timeout
                )
                if resp.status_code in _RETRYABLE_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"Retryable {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()

            except (
                httpx.HTTPStatusError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    sleep_s = self._backoff_factor * (2 ** attempt)
                    logger.warning(
                        f"Flink request failed (attempt {attempt + 1}/"
                        f"{self._max_retries}), retry in {sleep_s:.1f}s: {exc}"
                    )
                    await asyncio.sleep(sleep_s)

        logger.error(f"Flink request exhausted retries for {path}: {last_exc}")
        raise last_exc

    # ── Public API ────────────────────────────────────────────────────────────

    async def list_jobs(self) -> List[Dict[str, Any]]:
        """Return all jobs known to Flink (any status)."""
        data = await self._request("/jobs")
        return data.get("jobs", [])

    async def get_job_metrics(self, job_id: str) -> Dict[str, Any]:
        """
        Fetch Flink-internal metrics for a specific job.

        Returns a flat dict: ``{metric_id: value_string, ...}``.
        """
        data = await self._request(
            f"/jobs/{job_id}/metrics?get={_JOB_METRIC_IDS}"
        )
        if isinstance(data, list):
            return {item["id"]: item.get("value", "0") for item in data}
        return {}

    async def get_checkpoint_stats(self, job_id: str) -> Dict[str, Any]:
        """
        Fetch checkpoint statistics for a specific job.

        Returns the raw Flink checkpoint summary dict including
        ``counts``, ``summary``, ``latest``, and ``history``.
        """
        return await self._request(f"/jobs/{job_id}/checkpoints")

    async def get_job_detail(self, job_id: str) -> Dict[str, Any]:
        """Full job detail including vertex plan, start time, and duration."""
        return await self._request(f"/jobs/{job_id}")

    async def get_overview(self) -> Dict[str, Any]:
        """Cluster overview: taskmanager count, slot totals, job counts."""
        return await self._request("/overview", timeout=5)

    # ── Composite: summary used by the dashboard ──────────────────────────────

    async def get_flink_metrics(self) -> Dict[str, Any]:
        """
        Composite call: find the first RUNNING job, then concurrently fetch
        its detail, metrics, and checkpoint stats.
        """
        jobs = await self.list_jobs()
        running = next((j for j in jobs if j.get("status") == "RUNNING"), None)

        if not running:
            return {
                "job_id": None,
                "status": "NO_RUNNING_JOB",
                "uptime_hours": 0.0,
                "records_processed": 0,
                "throughput_per_sec": 0.0,
                "last_checkpoint": None,
            }

        job_id = running["id"]

        detail, raw_metrics, checkpoints = await asyncio.gather(
            self.get_job_detail(job_id),
            self.get_job_metrics(job_id),
            self.get_checkpoint_stats(job_id),
            return_exceptions=True,
        )

        uptime_ms: int = (
            detail.get("duration", 0) if isinstance(detail, dict) else 0
        )

        metrics: Dict[str, str] = raw_metrics if isinstance(raw_metrics, dict) else {}

        last_cp = None
        if isinstance(checkpoints, dict):
            completed = checkpoints.get("latest", {}).get("completed")
            if completed:
                last_cp = {
                    "id": completed.get("id", 0),
                    "duration_ms": completed.get("duration", 0),
                    "status": "COMPLETED",
                    "timestamp": completed.get("trigger_timestamp"),
                }

        return {
            "job_id": job_id,
            "status": "RUNNING",
            "uptime_hours": round(uptime_ms / 3_600_000, 2),
            "records_processed": int(float(metrics.get("numRecordsIn", "0"))),
            "throughput_per_sec": round(
                float(metrics.get("numRecordsInPerSecond", "0")), 2
            ),
            "last_checkpoint": last_cp,
        }

    # ── Health ────────────────────────────────────────────────────────────────

    async def check_health(self) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            overview = await self.get_overview()
            elapsed = round((time.monotonic() - t0) * 1000, 2)
            tms = overview.get("taskmanagers", 0)
            running = overview.get("jobs-running", 0)
            return {
                "status": "up",
                "response_time_ms": elapsed,
                "detail": f"{tms} TaskManagers, {running} running job(s)",
            }
        except Exception as exc:
            return {"status": "down", "detail": str(exc)}

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ── Module-level singleton ─────────────────────────────────────────────────────

_instance: Optional[FlinkClient] = None


def _get() -> FlinkClient:
    global _instance
    if _instance is None:
        from app.config import get_settings
        s = get_settings()
        _instance = FlinkClient(
            base_url=s.flink_url,
            timeout=s.flink_timeout,
            max_retries=s.flink_max_retries,
            backoff_factor=s.flink_backoff_factor,
        )
    return _instance


# ── Module-level async API (backward-compatible with routers) ──────────────────

async def list_jobs() -> List[Dict[str, Any]]:
    return await _get().list_jobs()


async def get_job_metrics(job_id: str) -> Dict[str, Any]:
    return await _get().get_job_metrics(job_id)


async def get_checkpoint_stats(job_id: str) -> Dict[str, Any]:
    return await _get().get_checkpoint_stats(job_id)


async def get_flink_metrics() -> Dict[str, Any]:
    return await _get().get_flink_metrics()


async def check_health() -> Dict[str, Any]:
    return await _get().check_health()


async def close_client() -> None:
    global _instance
    if _instance:
        await _instance.close()
        _instance = None

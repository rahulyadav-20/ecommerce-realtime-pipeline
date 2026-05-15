"""
DruidClient — synchronous requests-based client with connection pooling and
exponential-backoff retry.  Module-level async wrappers (via asyncio.to_thread)
keep the existing router interface intact.
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ISO-8601 granularity map (UI token → Druid duration)
_GRAN_ISO: Dict[str, str] = {
    "1m": "PT1M",
    "5m": "PT5M",
    "15m": "PT15M",
    "1h": "PT1H",
    "1d": "P1D",
}

# Window token → hours
_WIN_HOURS: Dict[str, int] = {
    "1h": 1,
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "7d": 168,
    "30d": 720,
}


# ── Client class ──────────────────────────────────────────────────────────────

class DruidClient:
    """
    Thread-safe Druid client backed by a requests.Session with:
    - Connection pool  (pool_connections=5, pool_maxsize=10)
    - Automatic retry  (3 attempts, exponential back-off, HTTP 5xx + 429)
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = self._build_session(max_retries, backoff_factor)

    # ── Session factory ───────────────────────────────────────────────────────

    @staticmethod
    def _build_session(max_retries: int, backoff_factor: float) -> requests.Session:
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,          # waits 0.5 s, 1.0 s, 2.0 s …
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,                  # let .raise_for_status() decide
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=5,
            pool_maxsize=10,
        )
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        return session

    # ── Core SQL query ────────────────────────────────────────────────────────

    def query_sql(
        self,
        sql: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a Druid SQL query and return a list of row dicts.

        ``parameters`` follows the Druid SQL parameter binding spec:
            [{"type": "VARCHAR", "value": "PAYMENT_SUCCESS"}, ...]
        """
        payload: Dict[str, Any] = {
            "query": sql,
            "resultFormat": "array",
            "header": True,
        }
        if parameters:
            payload["parameters"] = parameters

        url = f"{self._base_url}/druid/v2/sql"
        t0 = time.monotonic()

        try:
            resp = self._session.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error(f"Druid SQL timed out after {self._timeout}s | sql={sql[:120]}")
            raise
        except requests.exceptions.RequestException as exc:
            logger.error(f"Druid SQL failed: {exc}")
            raise

        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.info(f"Druid SQL OK in {elapsed}ms")

        data: List[Any] = resp.json()
        if not data or len(data) < 2:
            return []

        headers: List[str] = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]

    # ── Native timeseries query ───────────────────────────────────────────────

    def query_timeseries(
        self,
        datasource: str,
        intervals: List[str],
        granularity: str,
        aggregations: List[Dict[str, Any]],
        filter_spec: Optional[Dict[str, Any]] = None,
        post_aggregations: Optional[List[Dict[str, Any]]] = None,
        descending: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Execute a native Druid timeseries query (JSON-over-HTTP format).

        Returns a flat list of ``{timestamp, **result_fields}``.
        """
        payload: Dict[str, Any] = {
            "queryType": "timeseries",
            "dataSource": datasource,
            "intervals": intervals,
            "granularity": granularity,
            "aggregations": aggregations,
            "descending": descending,
        }
        if filter_spec:
            payload["filter"] = filter_spec
        if post_aggregations:
            payload["postAggregations"] = post_aggregations

        url = f"{self._base_url}/druid/v2"
        t0 = time.monotonic()

        try:
            resp = self._session.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.error(f"Druid timeseries query failed: {exc}")
            raise

        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.info(f"Druid timeseries OK in {elapsed}ms")

        return [
            {"timestamp": row["timestamp"], **row.get("result", {})}
            for row in resp.json()
        ]

    # ── Health ────────────────────────────────────────────────────────────────

    def check_health_sync(self) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            resp = self._session.get(
                f"{self._base_url}/status/health", timeout=5
            )
            elapsed = round((time.monotonic() - t0) * 1000, 2)
            if resp.status_code == 200:
                return {"status": "up", "response_time_ms": elapsed}
            return {
                "status": "degraded",
                "response_time_ms": elapsed,
                "detail": f"HTTP {resp.status_code}",
            }
        except Exception as exc:
            return {"status": "down", "detail": str(exc)}

    def close(self) -> None:
        self._session.close()


# ── Module-level singleton ─────────────────────────────────────────────────────

_instance: Optional[DruidClient] = None


def _get() -> DruidClient:
    global _instance
    if _instance is None:
        from app.config import get_settings
        s = get_settings()
        _instance = DruidClient(
            base_url=s.druid_url,
            timeout=s.druid_timeout,
            max_retries=s.druid_max_retries,
            backoff_factor=s.druid_backoff_factor,
        )
    return _instance


# ── Async wrappers (keep existing router interface) ────────────────────────────

async def get_realtime_kpis(window: str = "1h") -> Dict[str, Any]:
    hours = _WIN_HOURS.get(window, 1)
    sql = f"""
    SELECT
      COALESCE(SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN price ELSE 0 END), 0)
        AS revenue,
      COUNT(DISTINCT CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN event_id END)
        AS orders,
      COUNT(DISTINCT user_id)
        AS active_users,
      COALESCE(
        SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN price ELSE 0 END) /
        NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN event_id END), 0),
        0
      ) AS avg_order_value,
      COALESCE(
        CAST(COUNT(DISTINCT CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN user_id END) AS DOUBLE) /
        NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'ADD_TO_CART' THEN user_id END), 0),
        0
      ) AS cart_conversion_rate
    FROM "ecommerce_events"
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '{hours}' HOUR
    """
    rows = await asyncio.to_thread(_get().query_sql, sql)
    if not rows:
        return {
            "revenue": 0.0,
            "orders": 0,
            "active_users": 0,
            "avg_order_value": 0.0,
            "cart_conversion_rate": 0.0,
        }
    return rows[0]


async def get_revenue_timeseries(
    window: str = "24h", granularity: str = "5m"
) -> List[Dict[str, Any]]:
    hours = _WIN_HOURS.get(window, 24)
    gran = _GRAN_ISO.get(granularity, "PT5M")
    sql = f"""
    SELECT
      TIME_FLOOR(__time, '{gran}')                                                       AS bucket,
      COALESCE(SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN price ELSE 0 END), 0) AS revenue
    FROM "ecommerce_events"
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '{hours}' HOUR
    GROUP BY 1
    ORDER BY 1
    """
    return await asyncio.to_thread(_get().query_sql, sql)


async def check_health() -> Dict[str, Any]:
    return await asyncio.to_thread(_get().check_health_sync)


async def close_client() -> None:
    global _instance
    if _instance:
        await asyncio.to_thread(_instance.close)
        _instance = None

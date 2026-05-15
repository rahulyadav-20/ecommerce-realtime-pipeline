"""
ClickHouseClient — native-protocol client using clickhouse-driver with a
thread-safe connection pool of configurable size.

Why native protocol over HTTP?
- Binary wire format: ~3× faster for bulk column reads
- Server-side parameters: full SQL injection prevention
- Streaming via execute_iter: constant memory for large result sets
"""
import asyncio
import queue
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple

from clickhouse_driver import Client
from clickhouse_driver.errors import Error as ClickHouseError

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Column whitelist for dynamic GROUP BY — prevents SQL injection
_ALLOWED_GROUP_BY = frozenset({
    "category", "event_type", "user_segment", "device_type", "platform"
})


# ── Connection pool ───────────────────────────────────────────────────────────

class ClickHousePool:
    """
    Bounded pool of clickhouse-driver Client objects.

    Each Client holds a single persistent TCP connection.  The pool ensures
    at most ``pool_size`` connections are open simultaneously.  If all
    connections are busy, ``acquire()`` blocks for up to 30 s then raises
    ``queue.Empty``.

    On ClickHouseError the faulted connection is replaced so the pool
    never leaks broken sockets.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        pool_size: int = 5,
        query_timeout: int = 60,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._query_timeout = query_timeout
        self._pool: queue.Queue[Client] = queue.Queue(maxsize=pool_size)

        for _ in range(pool_size):
            self._pool.put(self._new_client())

    def _new_client(self) -> Client:
        return Client(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=self._database,
            connect_timeout=10,
            send_receive_timeout=self._query_timeout,
            sync_request_timeout=5,
            settings={
                "max_execution_time": self._query_timeout,
                "use_client_time_zone": True,
            },
            compression=True,
        )

    @contextmanager
    def acquire(self) -> Generator[Client, None, None]:
        """Context-manager: check out a connection, return it when done."""
        try:
            conn = self._pool.get(timeout=30)
        except queue.Empty:
            raise RuntimeError("ClickHouse pool exhausted — all connections busy")

        healthy = True
        try:
            yield conn
        except ClickHouseError as exc:
            healthy = False
            logger.warning(f"ClickHouse error on connection — recycling: {exc}")
            try:
                conn.disconnect()
            except Exception:
                pass
            raise
        finally:
            self._pool.put(conn if healthy else self._new_client())

    def close_all(self) -> None:
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.disconnect()
            except (queue.Empty, Exception):
                pass


# ── Client class ──────────────────────────────────────────────────────────────

class ClickHouseClient:
    """
    High-level ClickHouse client backed by a ClickHousePool.

    All query results are returned as ``List[Dict[str, Any]]`` where keys
    are column names.  Parameterized queries use ``%(name)s`` placeholders
    to prevent SQL injection at the driver level.
    """

    def __init__(self, pool: ClickHousePool) -> None:
        self._pool = pool

    # ── Core execute ──────────────────────────────────────────────────────────

    def execute(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run a SELECT query and return all rows as dicts.

        Use ``%(name)s`` placeholders and pass ``params`` to avoid
        string-formatting user input into queries.

        Example::

            client.execute(
                "SELECT * FROM events WHERE user_id = %(uid)s",
                {"uid": "user-abc"},
            )
        """
        t0 = time.monotonic()
        with self._pool.acquire() as conn:
            try:
                rows, col_meta = conn.execute(
                    query,
                    params or {},
                    with_column_types=True,
                )
            except ClickHouseError as exc:
                logger.error(f"ClickHouse execute failed: {exc} | query={query[:200]}")
                raise

        elapsed = round((time.monotonic() - t0) * 1000, 1)
        col_names = [col[0] for col in col_meta]
        logger.info(f"ClickHouse query OK in {elapsed}ms → {len(rows)} rows")
        return [dict(zip(col_names, row)) for row in rows]

    def execute_iter(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Streaming variant for large result sets.  Yields one dict per row
        without buffering the entire result in memory.

        Note: the connection remains checked out for the lifetime of the
        iterator — exhaust or close the generator promptly.
        """
        with self._pool.acquire() as conn:
            try:
                gen, col_meta = conn.execute_iter(
                    query,
                    params or {},
                    with_column_types=True,
                )
                col_names = [col[0] for col in col_meta]
                for row in gen:
                    yield dict(zip(col_names, row))
            except ClickHouseError as exc:
                logger.error(f"ClickHouse execute_iter failed: {exc}")
                raise

    # ── Domain queries ────────────────────────────────────────────────────────

    def get_funnel(
        self, period: str = "7d", group_by: str = "category"
    ) -> List[Dict[str, Any]]:
        safe_col = group_by if group_by in _ALLOWED_GROUP_BY else "category"
        amount = int(period.rstrip("dh"))
        unit = "HOUR" if period.endswith("h") else "DAY"

        sql = f"""
        SELECT
          {safe_col}                                                        AS category,
          countIf(event_type = 'PRODUCT_VIEW')                             AS views,
          countIf(event_type = 'ADD_TO_CART')                              AS carts,
          countIf(event_type = 'CHECKOUT_START')                           AS orders,
          countIf(event_type = 'PAYMENT_SUCCESS')                          AS payments,
          round(
            countIf(event_type = 'PAYMENT_SUCCESS') /
            nullIf(countIf(event_type = 'PRODUCT_VIEW'), 0),
            4
          )                                                                 AS conversion_rate
        FROM events
        WHERE event_time >= now() - INTERVAL {amount} {unit}
        GROUP BY {safe_col}
        ORDER BY views DESC
        LIMIT 20
        """
        return self.execute(sql)

    def get_top_products(
        self, limit: int = 10, period: str = "30d"
    ) -> List[Dict[str, Any]]:
        days = int(period.rstrip("d"))
        sql = """
        SELECT
          product_id,
          round(sum(price), 2)  AS revenue,
          count()               AS units_sold,
          round(avg(price), 2)  AS avg_price
        FROM events
        WHERE event_type      = 'PAYMENT_SUCCESS'
          AND event_time      >= now() - INTERVAL %(days)s DAY
          AND product_id      != ''
        GROUP BY product_id
        ORDER BY revenue DESC
        LIMIT %(limit)s
        """
        return self.execute(sql, {"days": days, "limit": limit})

    def get_live_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        sql = """
        SELECT
          event_id,
          event_type,
          user_id,
          product_id,
          price,
          toString(event_time) AS timestamp
        FROM events
        ORDER BY event_time DESC
        LIMIT %(limit)s
        """
        return self.execute(sql, {"limit": limit})

    # ── Health ────────────────────────────────────────────────────────────────

    def check_health_sync(self) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            rows = self.execute("SELECT 1 AS ping")
            elapsed = round((time.monotonic() - t0) * 1000, 2)
            if rows and rows[0].get("ping") == 1:
                return {"status": "up", "response_time_ms": elapsed}
            return {"status": "degraded", "response_time_ms": elapsed}
        except Exception as exc:
            return {"status": "down", "detail": str(exc)}

    def close(self) -> None:
        self._pool.close_all()


# ── Module-level singleton ─────────────────────────────────────────────────────

_instance: Optional[ClickHouseClient] = None


def _get() -> ClickHouseClient:
    global _instance
    if _instance is None:
        from app.config import get_settings
        s = get_settings()
        pool = ClickHousePool(
            host=s.clickhouse_host,
            port=s.clickhouse_native_port,
            user=s.clickhouse_user,
            password=s.clickhouse_password,
            database=s.clickhouse_database,
            pool_size=s.clickhouse_pool_size,
            query_timeout=s.clickhouse_query_timeout,
        )
        _instance = ClickHouseClient(pool)
    return _instance


# ── Async wrappers ────────────────────────────────────────────────────────────

async def get_funnel(
    period: str = "7d", group_by: str = "category"
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_get().get_funnel, period, group_by)


async def get_top_products(
    limit: int = 10, period: str = "30d"
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_get().get_top_products, limit, period)


async def get_live_events(limit: int = 100) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_get().get_live_events, limit)


async def check_health() -> Dict[str, Any]:
    return await asyncio.to_thread(_get().check_health_sync)


async def close_client() -> None:
    global _instance
    if _instance:
        await asyncio.to_thread(_instance.close)
        _instance = None

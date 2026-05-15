"""
Unit tests for all four dashboard-api clients.

Strategy
--------
DruidClient      — ``responses`` intercepts requests.Session HTTP calls.
ClickHouseClient — ``patch("app.services.clickhouse_client.Client")`` stubs
                   the symbol exactly as imported in that module.
FlinkClient      — ``with respx.mock as rm`` + ``rm.get(...)`` keeps routes on
                   the LOCAL router created by the context manager (NOT the
                   global ``respx`` module-level router).
CacheService     — ``fakeredis.FakeAsyncRedis`` provides an in-memory
                   redis.asyncio-compatible backend.

Run:
    cd dashboard-api
    pytest tests/test_clients.py -v
"""
import json
import queue
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
import pytest_asyncio
import respx
import responses as resp_lib
from httpx import Response

# ── helpers ───────────────────────────────────────────────────────────────────

def _druid_rows(headers: List[str], rows: List[List[Any]]) -> List:
    """Druid array resultFormat: [header_row, *data_rows]."""
    return [headers] + rows


_CH_CLIENT_PATH = "app.services.clickhouse_client.Client"


# ═══════════════════════════════════════════════════════════════════════════════
# DruidClient
# ═══════════════════════════════════════════════════════════════════════════════

class TestDruidClient:
    BASE = "http://druid-test:8888"

    def _client(self):
        from app.services.druid_client import DruidClient
        return DruidClient(
            base_url=self.BASE, timeout=5, max_retries=2, backoff_factor=0.0
        )

    @resp_lib.activate
    def test_query_sql_returns_list_of_dicts(self):
        resp_lib.add(
            resp_lib.POST, f"{self.BASE}/druid/v2/sql",
            json=_druid_rows(["revenue", "orders"], [[125_630.50, 342]]),
            status=200,
        )
        result = self._client().query_sql("SELECT revenue, orders FROM events")
        assert result == [{"revenue": 125_630.50, "orders": 342}]

    @resp_lib.activate
    def test_query_sql_returns_empty_when_no_data_rows(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json=[["col_a"]], status=200)
        assert self._client().query_sql("SELECT col_a FROM empty_ds") == []

    @resp_lib.activate
    def test_query_sql_returns_empty_on_truly_empty_response(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json=[], status=200)
        assert self._client().query_sql("SELECT 1") == []

    @resp_lib.activate
    def test_query_sql_sends_parameters_in_payload(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json=_druid_rows(["cnt"], [[5]]), status=200)
        params = [{"type": "VARCHAR", "value": "PAYMENT_SUCCESS"}]
        self._client().query_sql("SELECT COUNT(*) AS cnt WHERE type = ?", params)
        body = json.loads(resp_lib.calls[0].request.body)
        assert body["parameters"] == params
        assert body["resultFormat"] == "array"
        assert body["header"] is True

    @resp_lib.activate
    def test_query_sql_retries_on_502_then_succeeds(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json={}, status=502)
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json=_druid_rows(["x"], [[1]]), status=200)
        assert self._client().query_sql("SELECT x") == [{"x": 1}]
        assert len(resp_lib.calls) == 2

    @resp_lib.activate
    def test_query_sql_raises_after_all_retries_exhausted(self):
        for _ in range(3):
            resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                         json={}, status=503)
        import requests as req_lib
        with pytest.raises(req_lib.exceptions.HTTPError):
            self._client().query_sql("SELECT 1")

    @resp_lib.activate
    def test_query_sql_multiple_rows(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2/sql",
                     json=_druid_rows(["ts", "revenue"], [
                         ["2026-05-15T00:00:00Z", 1000.0],
                         ["2026-05-15T00:05:00Z", 2000.0],
                     ]), status=200)
        result = self._client().query_sql("SELECT ts, revenue FROM ds")
        assert len(result) == 2
        assert result[1]["revenue"] == 2000.0

    @resp_lib.activate
    def test_query_timeseries_builds_correct_payload(self):
        druid_result = [
            {"timestamp": "2026-05-01T00:00:00.000Z", "result": {"revenue": 4523.20}},
            {"timestamp": "2026-05-01T00:05:00.000Z", "result": {"revenue": 4831.10}},
        ]
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2",
                     json=druid_result, status=200)
        result = self._client().query_timeseries(
            datasource="ecommerce_events",
            intervals=["2026-05-01T00:00:00/2026-05-02T00:00:00"],
            granularity="PT5M",
            aggregations=[{"type": "doubleSum", "name": "revenue", "fieldName": "price"}],
        )
        body = json.loads(resp_lib.calls[0].request.body)
        assert body["queryType"] == "timeseries"
        assert body["granularity"] == "PT5M"
        assert len(result) == 2
        assert result[0] == {"timestamp": "2026-05-01T00:00:00.000Z", "revenue": 4523.20}

    @resp_lib.activate
    def test_query_timeseries_includes_filter(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2", json=[], status=200)
        f = {"type": "selector", "dimension": "event_type", "value": "PAYMENT_SUCCESS"}
        self._client().query_timeseries("ds", ["P1D/now"], "PT1H", [], filter_spec=f)
        body = json.loads(resp_lib.calls[0].request.body)
        assert body["filter"] == f

    @resp_lib.activate
    def test_query_timeseries_includes_post_aggregations(self):
        resp_lib.add(resp_lib.POST, f"{self.BASE}/druid/v2", json=[], status=200)
        pa = [{"type": "arithmetic", "name": "aov", "fn": "/",
               "fields": [{"type": "fieldAccess", "name": "revenue"},
                           {"type": "fieldAccess", "name": "orders"}]}]
        self._client().query_timeseries("ds", ["P1D/now"], "PT1H", [], post_aggregations=pa)
        assert json.loads(resp_lib.calls[0].request.body)["postAggregations"] == pa

    @resp_lib.activate
    def test_check_health_returns_up_on_200(self):
        resp_lib.add(resp_lib.GET, f"{self.BASE}/status/health",
                     json={"status": "OK"}, status=200)
        r = self._client().check_health_sync()
        assert r["status"] == "up"
        assert "response_time_ms" in r

    @resp_lib.activate
    def test_check_health_returns_degraded_on_non_200(self):
        resp_lib.add(resp_lib.GET, f"{self.BASE}/status/health", json={}, status=503)
        r = self._client().check_health_sync()
        assert r["status"] == "degraded"

    def test_check_health_returns_down_on_connection_error(self):
        r = self._client().check_health_sync()   # no responses registered
        assert r["status"] == "down"
        assert "detail" in r


# ═══════════════════════════════════════════════════════════════════════════════
# ClickHousePool
# ═══════════════════════════════════════════════════════════════════════════════

class TestClickHousePool:

    def test_pool_initialises_with_correct_size(self):
        from app.services.clickhouse_client import ClickHousePool
        with patch(_CH_CLIENT_PATH):
            pool = ClickHousePool("h", 9000, "u", "", "db", pool_size=3, query_timeout=5)
        assert pool._pool.qsize() == 3

    def test_acquire_checks_out_and_returns_connection(self):
        from app.services.clickhouse_client import ClickHousePool
        mock_conn = MagicMock()
        with patch(_CH_CLIENT_PATH, return_value=mock_conn):
            pool = ClickHousePool("h", 9000, "u", "", "db", pool_size=1, query_timeout=5)

        with pool.acquire() as conn:
            assert conn is mock_conn
            assert pool._pool.qsize() == 0     # checked out
        assert pool._pool.qsize() == 1          # returned

    def test_acquire_replaces_connection_on_clickhouse_error(self):
        from clickhouse_driver.errors import Error as CHError
        from app.services.clickhouse_client import ClickHousePool

        original_conn = MagicMock()
        replacement_conn = MagicMock()
        call_counts = {"n": 0}

        def _side_effect(*a, **kw):
            call_counts["n"] += 1
            return original_conn if call_counts["n"] == 1 else replacement_conn

        # Keep the patch ACTIVE through the acquire() call so _new_client()
        # can produce the replacement without touching the real driver.
        with patch(_CH_CLIENT_PATH, side_effect=_side_effect):
            pool = ClickHousePool("h", 9000, "u", "", "db", pool_size=1, query_timeout=5)

            with pytest.raises(CHError):
                with pool.acquire():
                    raise CHError("simulated connection error")

            returned = pool._pool.get_nowait()
            assert returned is replacement_conn     # faulted conn was replaced

    def test_acquire_raises_on_pool_exhaustion(self):
        from app.services.clickhouse_client import ClickHousePool
        with patch(_CH_CLIENT_PATH):
            pool = ClickHousePool("h", 9000, "u", "", "db", pool_size=1, query_timeout=5)

        pool._pool = queue.Queue(maxsize=1)      # drain pool → empty

        with pytest.raises(RuntimeError, match="exhausted"):
            with pool.acquire():
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# ClickHouseClient
# ═══════════════════════════════════════════════════════════════════════════════

class TestClickHouseClient:

    def _client_with_mock_conn(self, execute_return=None):
        from app.services.clickhouse_client import ClickHouseClient, ClickHousePool
        mock_conn = MagicMock()
        mock_conn.execute.return_value = execute_return or ([], [])
        mock_pool = MagicMock(spec=ClickHousePool)
        mock_pool.acquire.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__exit__ = MagicMock(return_value=False)
        return ClickHouseClient(pool=mock_pool), mock_conn

    def test_execute_returns_list_of_dicts(self):
        rows = [("evt_001", "PAYMENT_SUCCESS", 99.99)]
        cols = [("event_id", "String"), ("event_type", "String"), ("price", "Float64")]
        client, _ = self._client_with_mock_conn((rows, cols))
        assert client.execute("SELECT * FROM events") == [
            {"event_id": "evt_001", "event_type": "PAYMENT_SUCCESS", "price": 99.99}
        ]

    def test_execute_passes_params_to_driver(self):
        client, mock_conn = self._client_with_mock_conn(([], []))
        client.execute("SELECT * FROM e WHERE user_id = %(uid)s", {"uid": "u1"})
        mock_conn.execute.assert_called_once_with(
            "SELECT * FROM e WHERE user_id = %(uid)s", {"uid": "u1"}, with_column_types=True
        )

    def test_execute_returns_empty_list_on_zero_rows(self):
        client, _ = self._client_with_mock_conn(([], [("x", "UInt64")]))
        assert client.execute("SELECT x FROM empty") == []

    def test_execute_raises_on_clickhouse_error(self):
        from clickhouse_driver.errors import Error as CHError
        from app.services.clickhouse_client import ClickHouseClient, ClickHousePool
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = CHError("column not found")
        mock_pool = MagicMock(spec=ClickHousePool)
        mock_pool.acquire.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__exit__ = MagicMock(return_value=False)
        with pytest.raises(CHError):
            ClickHouseClient(pool=mock_pool).execute("SELECT bad FROM events")

    def test_get_top_products_uses_parameterized_query(self):
        cols = [("product_id", "String"), ("revenue", "Float64"),
                ("units_sold", "UInt64"), ("avg_price", "Float64")]
        client, mock_conn = self._client_with_mock_conn(([("prod_xyz", 89_230.0, 127, 702.60)], cols))
        result = client.get_top_products(limit=5, period="30d")
        assert result[0]["product_id"] == "prod_xyz"
        # Verify params dict was passed (not f-string interpolation)
        positional = mock_conn.execute.call_args.args
        assert positional[1] == {"days": 30, "limit": 5}

    def test_get_top_products_sql_uses_placeholders(self):
        client, mock_conn = self._client_with_mock_conn(([], []))
        client.get_top_products(limit=10, period="7d")
        sql = mock_conn.execute.call_args.args[0]
        assert "%(days)s" in sql
        assert "%(limit)s" in sql

    def test_get_funnel_falls_back_to_category_on_injection_attempt(self):
        cols = [("category", "String"), ("views", "UInt64"), ("carts", "UInt64"),
                ("orders", "UInt64"), ("payments", "UInt64"), ("conversion_rate", "Float64")]
        client, mock_conn = self._client_with_mock_conn(([], cols))
        client.get_funnel(period="7d", group_by="'; DROP TABLE events; --")
        sql = mock_conn.execute.call_args.args[0]
        assert "DROP TABLE" not in sql
        assert "category" in sql

    def test_get_funnel_uses_allowed_group_by(self):
        cols = [("category", "String"), ("views", "UInt64"), ("carts", "UInt64"),
                ("orders", "UInt64"), ("payments", "UInt64"), ("conversion_rate", "Float64")]
        client, mock_conn = self._client_with_mock_conn(([], cols))
        client.get_funnel(period="7d", group_by="event_type")
        assert "event_type" in mock_conn.execute.call_args.args[0]

    def test_check_health_sync_returns_up(self):
        client, _ = self._client_with_mock_conn(([( 1,)], [("ping", "UInt8")]))
        r = client.check_health_sync()
        assert r["status"] == "up"
        assert "response_time_ms" in r

    def test_check_health_sync_returns_down_on_error(self):
        from clickhouse_driver.errors import Error as CHError
        from app.services.clickhouse_client import ClickHouseClient, ClickHousePool
        mock_pool = MagicMock(spec=ClickHousePool)
        mock_pool.acquire.side_effect = CHError("connection refused")
        r = ClickHouseClient(pool=mock_pool).check_health_sync()
        assert r["status"] == "down"
        assert "detail" in r


# ═══════════════════════════════════════════════════════════════════════════════
# FlinkClient
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def flink_client():
    from app.services.flink_client import FlinkClient
    return FlinkClient(
        base_url="http://flink-test:8081",
        timeout=5,
        max_retries=2,
        backoff_factor=0.0,  # no sleep during retry in tests
    )


def _mock_http_get(flink_client, status: int, json_body: Any):
    """
    Inject a mock httpx.AsyncClient into flink_client._client so that
    ``client.get(...)`` returns a pre-configured response without any
    real or mocked HTTP transport.
    """
    import httpx

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status
    mock_resp.json.return_value = json_body
    # raise_for_status raises on 4xx/5xx; simulate real behaviour
    if status >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=mock_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.is_closed = False
    flink_client._client = mock_client
    return mock_client, mock_resp


class TestFlinkClient:
    """
    FlinkClient tests inject a mock httpx.AsyncClient directly into
    ``flink_client._client``.  This avoids transport-level mock libraries
    and tests the client logic (URL paths, response parsing, retry) cleanly.
    """

    # ── list_jobs ─────────────────────────────────────────────────────────────

    async def test_list_jobs_returns_job_list(self, flink_client):
        _mock_http_get(flink_client, 200, {
            "jobs": [
                {"id": "abc123", "status": "RUNNING"},
                {"id": "def456", "status": "FINISHED"},
            ]
        })
        result = await flink_client.list_jobs()
        assert len(result) == 2
        assert result[0] == {"id": "abc123", "status": "RUNNING"}

    async def test_list_jobs_returns_empty_when_no_jobs(self, flink_client):
        _mock_http_get(flink_client, 200, {"jobs": []})
        assert await flink_client.list_jobs() == []

    async def test_list_jobs_calls_correct_path(self, flink_client):
        mock_client, _ = _mock_http_get(flink_client, 200, {"jobs": []})
        await flink_client.list_jobs()
        mock_client.get.assert_called_once()
        assert "/jobs" in mock_client.get.call_args.args[0]

    # ── get_job_metrics ───────────────────────────────────────────────────────

    async def test_get_job_metrics_returns_flat_dict(self, flink_client):
        _mock_http_get(flink_client, 200, [
            {"id": "numRecordsInPerSecond", "value": "8234.5"},
            {"id": "numRecordsIn", "value": "245827340"},
        ])
        result = await flink_client.get_job_metrics("abc123")
        assert result["numRecordsInPerSecond"] == "8234.5"
        assert result["numRecordsIn"] == "245827340"

    async def test_get_job_metrics_returns_empty_dict_on_non_list(self, flink_client):
        _mock_http_get(flink_client, 200, {})
        assert await flink_client.get_job_metrics("xyz") == {}

    async def test_get_job_metrics_calls_correct_path(self, flink_client):
        mock_client, _ = _mock_http_get(flink_client, 200, [])
        await flink_client.get_job_metrics("job-999")
        path = mock_client.get.call_args.args[0]
        assert "job-999" in path
        assert "metrics" in path

    # ── get_checkpoint_stats ──────────────────────────────────────────────────

    async def test_get_checkpoint_stats_returns_raw_dict(self, flink_client):
        payload = {
            "counts": {"completed": 72, "failed": 0},
            "latest": {
                "completed": {"id": 72, "duration": 3421, "trigger_timestamp": 1746614412000}
            },
        }
        _mock_http_get(flink_client, 200, payload)
        result = await flink_client.get_checkpoint_stats("abc123")
        assert result["counts"]["completed"] == 72
        assert result["latest"]["completed"]["id"] == 72

    async def test_get_checkpoint_stats_calls_correct_path(self, flink_client):
        mock_client, _ = _mock_http_get(flink_client, 200, {})
        await flink_client.get_checkpoint_stats("job-777")
        path = mock_client.get.call_args.args[0]
        assert "job-777" in path
        assert "checkpoints" in path

    # ── retry logic ───────────────────────────────────────────────────────────

    async def test_request_retries_on_502_then_succeeds(self, flink_client):
        import httpx

        resp_502 = MagicMock(spec=httpx.Response)
        resp_502.status_code = 502
        resp_502.raise_for_status.side_effect = httpx.HTTPStatusError(
            "502", request=MagicMock(), response=resp_502
        )

        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.raise_for_status.return_value = None
        resp_200.json.return_value = {"taskmanagers": 2, "jobs-running": 1}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp_502, resp_200])
        mock_client.is_closed = False
        flink_client._client = mock_client

        data = await flink_client.get_overview()
        assert data["taskmanagers"] == 2
        assert mock_client.get.call_count == 2

    async def test_request_raises_after_all_retries_exhausted(self, flink_client):
        import httpx

        resp_503 = MagicMock(spec=httpx.Response)
        resp_503.status_code = 503
        resp_503.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=resp_503
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp_503)
        mock_client.is_closed = False
        flink_client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await flink_client.get_overview()
        assert mock_client.get.call_count == flink_client._max_retries

    # ── get_flink_metrics (composite) ─────────────────────────────────────────

    async def test_get_flink_metrics_no_running_job(self, flink_client):
        _mock_http_get(flink_client, 200, {"jobs": []})
        result = await flink_client.get_flink_metrics()
        assert result["status"] == "NO_RUNNING_JOB"
        assert result["job_id"] is None
        assert result["uptime_hours"] == 0.0

    async def test_get_flink_metrics_aggregates_sub_calls(self, flink_client):
        """
        Composite method: patch _request directly so the three asyncio.gather
        sub-calls each return pre-configured data without transport involvement.
        """
        job_id = "job-001"

        async def _fake_request(path: str, timeout=None):
            if path == "/jobs":
                return {"jobs": [{"id": job_id, "status": "RUNNING"}]}
            if path == f"/jobs/{job_id}":
                return {"duration": 259_200_000}    # 72 h in ms
            if "metrics" in path:
                return [
                    {"id": "numRecordsIn", "value": "1000000"},
                    {"id": "numRecordsInPerSecond", "value": "5000.0"},
                ]
            if "checkpoints" in path:
                return {
                    "latest": {
                        "completed": {"id": 99, "duration": 2800, "trigger_timestamp": 0}
                    }
                }
            return {}

        with patch.object(flink_client, "_request", side_effect=_fake_request):
            result = await flink_client.get_flink_metrics()

        assert result["job_id"] == job_id
        assert result["status"] == "RUNNING"
        assert result["uptime_hours"] == 72.0
        assert result["records_processed"] == 1_000_000
        assert result["throughput_per_sec"] == 5000.0
        assert result["last_checkpoint"]["id"] == 99
        assert result["last_checkpoint"]["duration_ms"] == 2800

    # ── health ────────────────────────────────────────────────────────────────

    async def test_check_health_returns_up(self, flink_client):
        _mock_http_get(flink_client, 200, {"taskmanagers": 2, "jobs-running": 1})
        result = await flink_client.check_health()
        assert result["status"] == "up"
        assert "response_time_ms" in result
        assert "2 TaskManagers" in result["detail"]

    async def test_check_health_returns_down_on_connect_error(self, flink_client):
        import httpx
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.is_closed = False
        flink_client._client = mock_client

        result = await flink_client.check_health()
        assert result["status"] == "down"
        assert "detail" in result


# ═══════════════════════════════════════════════════════════════════════════════
# CacheService
# ═══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def cache_svc():
    """CacheService wired to an in-memory FakeAsyncRedis — no Redis needed."""
    from app.utils.cache import CacheService

    server = fakeredis.FakeServer()
    fake_r = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    svc = CacheService.__new__(CacheService)
    svc._pool = MagicMock()      # pool not exercised here
    svc._redis = fake_r

    yield svc
    await fake_r.aclose()


class TestCacheService:

    async def test_get_returns_none_on_cache_miss(self, cache_svc):
        assert await cache_svc.get("missing:key") is None

    async def test_set_then_get_round_trip(self, cache_svc):
        payload = {"revenue": 125_630.50, "orders": 342}
        await cache_svc.set("kpis:abc", payload, ttl=30)
        assert await cache_svc.get("kpis:abc") == payload

    async def test_set_respects_ttl(self, cache_svc):
        await cache_svc.set("short:key", {"v": 1}, ttl=5)
        remaining = await cache_svc.ttl("short:key")
        assert 0 < remaining <= 5

    async def test_get_deserialises_nested_structures(self, cache_svc):
        data = {"data": [{"ts": "2026-05-15T10:00:00", "revenue": 4523.2}], "window": "24h"}
        await cache_svc.set("ts:xyz", data, ttl=60)
        assert (await cache_svc.get("ts:xyz"))["data"][0]["revenue"] == 4523.2

    async def test_invalidate_deletes_matching_keys(self, cache_svc):
        await cache_svc.set("kpis:a1b2", {"v": 1}, ttl=60)
        await cache_svc.set("kpis:c3d4", {"v": 2}, ttl=60)
        await cache_svc.set("funnel:e5f6", {"v": 3}, ttl=60)

        deleted = await cache_svc.invalidate("kpis:*")

        assert deleted == 2
        assert await cache_svc.get("kpis:a1b2") is None
        assert await cache_svc.get("kpis:c3d4") is None
        assert await cache_svc.get("funnel:e5f6") is not None   # untouched

    async def test_invalidate_returns_zero_when_no_match(self, cache_svc):
        assert await cache_svc.invalidate("nonexistent:*") == 0

    async def test_invalidate_does_not_raise_on_empty_store(self, cache_svc):
        assert await cache_svc.invalidate("kpis:*") == 0

    async def test_ping_returns_true(self, cache_svc):
        assert await cache_svc.ping() is True

    async def test_overwrite_key_updates_value(self, cache_svc):
        await cache_svc.set("k", {"v": 1}, ttl=60)
        await cache_svc.set("k", {"v": 99}, ttl=60)
        assert (await cache_svc.get("k"))["v"] == 99

    async def test_get_returns_none_on_redis_error(self, cache_svc):
        cache_svc._redis.get = AsyncMock(side_effect=Exception("redis timeout"))
        assert await cache_svc.get("some:key") is None    # fail-open

    async def test_set_silently_swallows_redis_error(self, cache_svc):
        cache_svc._redis.setex = AsyncMock(side_effect=Exception("redis timeout"))
        await cache_svc.set("key", {"x": 1}, ttl=30)     # must not raise

    def test_make_key_is_deterministic(self):
        from app.utils.cache import CacheService
        assert (CacheService.make_key("kpis", window="1h") ==
                CacheService.make_key("kpis", window="1h"))

    def test_make_key_differs_for_different_args(self):
        from app.utils.cache import CacheService
        assert (CacheService.make_key("kpis", window="1h") !=
                CacheService.make_key("kpis", window="24h"))

    def test_make_key_prefix_is_preserved(self):
        from app.utils.cache import CacheService
        assert CacheService.make_key("funnel", period="7d").startswith("funnel:")

    def test_make_key_kwargs_order_independent(self):
        from app.utils.cache import CacheService
        assert (CacheService.make_key("ts", window="24h", granularity="5m") ==
                CacheService.make_key("ts", granularity="5m", window="24h"))

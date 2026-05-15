from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models.enums import Granularity, TimeWindow
from app.models.schemas import RealtimeKPIs, TimeseriesResponse
from app.services import druid_client
from app.utils.cache import cache_get, cache_key, cache_set
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Druid — Real-Time OLAP"])


@router.get("/kpis/realtime", response_model=RealtimeKPIs)
async def get_realtime_kpis(window: TimeWindow = TimeWindow.ONE_HOUR):
    """Real-time KPIs aggregated over the given time window (default last 1 hour)."""
    from app.config import get_settings
    key = cache_key("kpis", window=window.value)
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        row = await druid_client.get_realtime_kpis(window.value)
    except Exception as exc:
        logger.error(f"Druid KPI query failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Druid query error: {exc}")

    result = {
        "revenue": float(row.get("revenue") or 0),
        "orders": int(row.get("orders") or 0),
        "active_users": int(row.get("active_users") or 0),
        "avg_order_value": float(row.get("avg_order_value") or 0),
        "cart_conversion_rate": float(row.get("cart_conversion_rate") or 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await cache_set(key, result, get_settings().cache_ttl_kpis)
    return result


@router.get("/metrics/revenue", response_model=TimeseriesResponse)
async def get_revenue_timeseries(
    window: TimeWindow = TimeWindow.TWENTY_FOUR_HOURS,
    granularity: Granularity = Granularity.FIVE_MINUTES,
):
    """Revenue timeseries bucketed by granularity over the given window."""
    from app.config import get_settings
    key = cache_key("ts_revenue", window=window.value, gran=granularity.value)
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        rows = await druid_client.get_revenue_timeseries(window.value, granularity.value)
    except Exception as exc:
        logger.error(f"Druid timeseries query failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    result = {
        "data": [
            {
                "timestamp": str(r.get("bucket") or r.get("__time", "")),
                "revenue": float(r.get("revenue") or 0),
            }
            for r in rows
        ],
        "window": window.value,
        "granularity": granularity.value,
    }
    await cache_set(key, result, get_settings().cache_ttl_timeseries)
    return result

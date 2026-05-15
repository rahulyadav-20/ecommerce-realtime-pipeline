import re

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import FunnelResponse, LiveEventsResponse, TopProductsResponse
from app.services import clickhouse_client
from app.utils.cache import cache_get, cache_key, cache_set
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["ClickHouse — Historical OLAP"])

_PERIOD_RE = re.compile(r"^\d+[dh]$")


@router.get("/funnel", response_model=FunnelResponse)
async def get_funnel(
    period: str = Query("7d", description="Time period e.g. 7d, 24h"),
    group_by: str = Query("category", description="Grouping dimension"),
):
    """Conversion funnel from product view → payment, grouped by the chosen dimension."""
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period must match \\d+[dh], e.g. 7d or 24h")

    from app.config import get_settings
    key = cache_key("funnel", period=period, group_by=group_by)
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        rows = await clickhouse_client.get_funnel(period, group_by)
    except Exception as exc:
        logger.error(f"ClickHouse funnel query failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    result = {"data": rows, "period": period, "group_by": group_by}
    await cache_set(key, result, get_settings().cache_ttl_funnel)
    return result


@router.get("/products/top", response_model=TopProductsResponse)
async def get_top_products(
    limit: int = Query(10, ge=1, le=50, description="Number of products"),
    period: str = Query("30d", description="Lookback period e.g. 30d"),
):
    """Top N products by revenue over the given period."""
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period must match \\d+[dh]")

    from app.config import get_settings
    key = cache_key("top_products", limit=limit, period=period)
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        rows = await clickhouse_client.get_top_products(limit, period)
    except Exception as exc:
        logger.error(f"ClickHouse top-products query failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    result = {"data": rows, "period": period, "limit": limit}
    await cache_set(key, result, get_settings().cache_ttl_products)
    return result


@router.get("/events/live", response_model=LiveEventsResponse)
async def get_live_events(
    limit: int = Query(100, ge=1, le=500, description="Max events to return"),
):
    """Most recent N events ordered by event_time DESC."""
    from app.config import get_settings
    key = cache_key("live_events", limit=limit)
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        rows = await clickhouse_client.get_live_events(limit)
    except Exception as exc:
        logger.error(f"ClickHouse live-events query failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    result = {"data": rows, "count": len(rows)}
    await cache_set(key, result, get_settings().cache_ttl_live)
    return result

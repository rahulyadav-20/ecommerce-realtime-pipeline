from fastapi import APIRouter, HTTPException

from app.models.schemas import FlinkMetricsResponse
from app.services import flink_client
from app.utils.cache import cache_get, cache_set
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/flink", tags=["Flink — Stream Processing"])


@router.get("/metrics", response_model=FlinkMetricsResponse)
async def get_flink_metrics():
    """Running Flink job ID, state, throughput, and latest checkpoint info."""
    from app.config import get_settings
    key = "flink:metrics"
    cached = await cache_get(key)
    if cached:
        return cached

    try:
        data = await flink_client.get_flink_metrics()
    except Exception as exc:
        logger.error(f"Flink metrics failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    await cache_set(key, data, get_settings().cache_ttl_flink)
    return data

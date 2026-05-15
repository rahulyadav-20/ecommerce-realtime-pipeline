import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter

from app.models.schemas import HealthResponse
from app.services import clickhouse_client, druid_client, flink_client
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Health"])


async def _kafka_health() -> dict:
    """Approximate Kafka health via Flink job overview (avoids kafka-python dependency)."""
    try:
        import httpx
        from app.config import get_settings
        s = get_settings()
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(f"{s.flink_url}/jobs")
            jobs = resp.json().get("jobs", [])
            running = [j for j in jobs if j.get("status") == "RUNNING"]
            return {
                "status": "up",
                "detail": f"Flink consumer group active, {len(running)} job(s) running",
            }
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)}


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Parallel health check across Druid, ClickHouse, Flink, and Kafka."""
    results = await asyncio.gather(
        druid_client.check_health(),
        clickhouse_client.check_health(),
        flink_client.check_health(),
        _kafka_health(),
        return_exceptions=True,
    )

    names = ["druid", "clickhouse", "flink", "kafka"]
    components = {}
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            components[name] = {"status": "down", "detail": str(result)}
        else:
            components[name] = result

    statuses = {c["status"] for c in components.values()}
    if statuses == {"up"}:
        overall = "healthy"
    elif "down" in statuses:
        overall = "unhealthy"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "components": components,
        "timestamp": datetime.now(timezone.utc),
    }

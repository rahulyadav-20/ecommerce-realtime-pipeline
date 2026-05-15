from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ── KPIs ─────────────────────────────────────────────────────────────────────

class RealtimeKPIs(BaseModel):
    revenue: float
    orders: int
    active_users: int
    avg_order_value: float
    cart_conversion_rate: float
    timestamp: datetime


# ── Timeseries ────────────────────────────────────────────────────────────────

class TimeseriesPoint(BaseModel):
    timestamp: str
    revenue: float


class TimeseriesResponse(BaseModel):
    data: List[TimeseriesPoint]
    window: str
    granularity: str


# ── Funnel ────────────────────────────────────────────────────────────────────

class FunnelStep(BaseModel):
    category: str
    views: int
    carts: int
    orders: int
    payments: int
    conversion_rate: float


class FunnelResponse(BaseModel):
    data: List[FunnelStep]
    period: str
    group_by: str


# ── Products ──────────────────────────────────────────────────────────────────

class TopProduct(BaseModel):
    product_id: str
    revenue: float
    units_sold: int
    avg_price: float


class TopProductsResponse(BaseModel):
    data: List[TopProduct]
    period: str
    limit: int


# ── Live Events ───────────────────────────────────────────────────────────────

class LiveEvent(BaseModel):
    event_id: str
    event_type: str
    user_id: str
    product_id: Optional[str] = None
    price: Optional[float] = None
    timestamp: str


class LiveEventsResponse(BaseModel):
    data: List[LiveEvent]
    count: int


# ── Health ────────────────────────────────────────────────────────────────────

class ComponentHealth(BaseModel):
    status: str  # "up" | "degraded" | "down"
    response_time_ms: Optional[float] = None
    detail: Optional[str] = None
    lag: Optional[int] = None


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    components: Dict[str, ComponentHealth]
    timestamp: datetime


# ── Flink ─────────────────────────────────────────────────────────────────────

class CheckpointInfo(BaseModel):
    id: int
    duration_ms: int
    status: str
    timestamp: Optional[Any] = None


class FlinkMetricsResponse(BaseModel):
    job_id: Optional[str]
    status: str
    uptime_hours: float
    records_processed: int
    throughput_per_sec: float
    last_checkpoint: Optional[CheckpointInfo] = None

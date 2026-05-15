from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, make_asgi_app

from app.config import get_settings
from app.routers import clickhouse, druid, flink, health
from app.routers import websocket as ws_router
from app.services import clickhouse_client, druid_client, flink_client
from app.utils.cache import close_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Request latency in seconds", ["endpoint"]
)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    # Start WebSocket Kafka fan-out background task
    await ws_router.start_fanout()

    yield

    logger.info("Shutting down — stopping Kafka fan-out and closing connections")
    await ws_router.stop_fanout()
    await druid_client.close_client()
    await clickhouse_client.close_client()
    await flink_client.close_client()
    await close_redis()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Unified query backend for the E-Commerce Realtime Analytics Dashboard. "
        "Aggregates data from Apache Druid (real-time OLAP), ClickHouse (historical OLAP), "
        "Flink REST API (stream processing metrics), and streams live Kafka events "
        "over WebSocket (/ws/events)."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — allow React dev server and production origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Prometheus scrape endpoint
app.mount("/metrics", make_asgi_app())

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(druid.router)
app.include_router(clickhouse.router)
app.include_router(flink.router)
app.include_router(health.router)
app.include_router(ws_router.router)   # WebSocket endpoint + /api/v1/ws/status


@app.get("/", tags=["Root"])
async def root():
    return {
        "name":     settings.app_name,
        "version":  settings.app_version,
        "docs":     "/docs",
        "metrics":  "/metrics",
        "ws":       "/ws/events",
        "ws_status": "/api/v1/ws/status",
    }

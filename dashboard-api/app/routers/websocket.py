"""
WebSocket real-time event stream  —  /ws/events

Architecture
════════════

                  Kafka topic
              ecommerce.events.clean.v1
                       │  (Avro / Confluent wire format)
                       ▼
              ┌──────────────────┐
              │  KafkaFanout     │  background asyncio Task
              │  (aiokafka)      │  started in app lifespan
              └────────┬─────────┘
                       │ decoded JSON dict
                       ▼
              ┌──────────────────┐
              │ ConnectionManager│  tracks ≤ MAX_CLIENTS WebSocket sockets
              │  .broadcast()    │  parallel sends with per-client timeout
              └────────┬─────────┘
                       │ JSON text frames
                 ┌─────┴──────┐
              Client A    Client B  …  Client N

Message frames
══════════════
Server → Client:
  • Event data   {"event_id":…, "event_type":…, …}
  • Server ping  {"type":"ping","ts":<unix_ms>}
  • Connected    {"type":"connected","clients":<n>}
  • Error        {"type":"error","code":<n>,"reason":"…"}

Client → Server:
  • "ping"  → server replies "pong"  (keep-alive)
  • "pong"  → acknowledged (heartbeat reply — no-op)

Authentication (optional)
═════════════════════════
Set WS_SECRET_TOKEN in Settings/env.  Clients must include it as a
query parameter: ws://host/ws/events?token=<secret>
An empty WS_SECRET_TOKEN disables the check entirely.

Backpressure
════════════
If ws.send_text() blocks for more than WS_SEND_TIMEOUT seconds the client
is considered slow/dead: its socket is forcefully closed and removed from
the connection set.  The broadcast loop never stalls.

Avro deserialization
════════════════════
Confluent wire format = 0x00 (1 byte) + schema_id (4 bytes BE int) + Avro payload.
Schemas are fetched on demand from Schema Registry and cached indefinitely in
process memory (schemas are immutable once registered with FULL_TRANSITIVE compat).
If deserialization fails the raw bytes are skipped with a warning — no crash.
"""
import asyncio
import io
import json
import struct
import time
from typing import Any, Dict, Optional, Set

import fastavro
import httpx
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["WebSocket"])

# ── Constants ─────────────────────────────────────────────────────────────────

_MAGIC_BYTE = 0x00          # Confluent Avro wire format magic byte
_HEADER_LEN = 5             # 1 byte magic + 4 bytes schema ID

# ═══════════════════════════════════════════════════════════════════════════════
# Schema Registry client  (lazy-fetches and caches parsed Avro schemas)
# ═══════════════════════════════════════════════════════════════════════════════

class SchemaRegistryClient:
    """Fetches parsed fastavro schemas from Confluent Schema Registry by ID."""

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout  = timeout
        self._cache: Dict[int, Any] = {}   # schema_id → parsed schema

    async def get_schema(self, schema_id: int) -> Any:
        if schema_id in self._cache:
            return self._cache[schema_id]

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            url  = f"{self._base_url}/schemas/ids/{schema_id}"
            resp = await client.get(url)
            resp.raise_for_status()
            raw_schema = json.loads(resp.json()["schema"])

        parsed = fastavro.parse_schema(raw_schema)
        self._cache[schema_id] = parsed
        logger.info(f"SchemaRegistry: cached schema id={schema_id}")
        return parsed

    def decode(self, raw: bytes) -> Optional[Dict[str, Any]]:
        """
        Synchronous decode of a cached schema.  Returns None if the schema
        hasn't been fetched yet (caller should fetch async first).
        """
        if len(raw) < _HEADER_LEN or raw[0] != _MAGIC_BYTE:
            return None
        schema_id = struct.unpack(">I", raw[1:5])[0]
        schema = self._cache.get(schema_id)
        if schema is None:
            return None
        stream = io.BytesIO(raw[_HEADER_LEN:])
        return fastavro.schemaless_reader(stream, schema)

    async def decode_async(self, raw: bytes) -> Optional[Dict[str, Any]]:
        """Fetch schema if needed, then decode. Returns None on failure."""
        try:
            if len(raw) < _HEADER_LEN or raw[0] != _MAGIC_BYTE:
                # Not Confluent Avro — try plain JSON fallback
                return json.loads(raw.decode("utf-8", errors="replace"))

            schema_id = struct.unpack(">I", raw[1:5])[0]
            if schema_id not in self._cache:
                await self.get_schema(schema_id)

            return self.decode(raw)
        except Exception as exc:
            logger.warning(f"Avro decode failed (skipping message): {exc}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# Connection Manager  (fan-out broadcast, backpressure, max-clients cap)
# ═══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    """
    Thread-safe (asyncio-safe) registry of active WebSocket connections.

    Invariants
    ──────────
    • At most `max_clients` sockets are in `_clients` at any time.
    • broadcast() never blocks: slow/dead sockets are skipped via timeout.
    • All mutations of `_clients` happen inside the event loop (no threads).
    """

    def __init__(self, max_clients: int = 50, send_timeout: float = 2.0) -> None:
        self._clients:    Set[WebSocket] = set()
        self._max:        int            = max_clients
        self._timeout:    float          = send_timeout

    @property
    def count(self) -> int:
        return len(self._clients)

    async def accept(self, ws: WebSocket) -> bool:
        """
        Perform WebSocket handshake and register the client.
        Returns False (and closes with 1008) if the cap is already reached.
        """
        if len(self._clients) >= self._max:
            await ws.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"Server at capacity ({self._max} clients)",
            )
            logger.warning(f"WS: rejected — {self._max} client cap reached")
            return False

        await ws.accept()
        self._clients.add(ws)
        logger.info(f"WS: client connected  total={len(self._clients)}")
        return True

    def remove(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info(f"WS: client disconnected  total={len(self._clients)}")

    async def broadcast(self, payload: str) -> None:
        """
        Send `payload` to every connected client concurrently.

        Slow clients (send takes longer than `_timeout` seconds) are
        silently disconnected and removed — they do not block fast clients.
        """
        if not self._clients:
            return

        dead: Set[WebSocket] = set()

        async def _send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=self._timeout)
            except (asyncio.TimeoutError, Exception):
                dead.add(ws)
                try:
                    await ws.close(code=status.WS_1001_GOING_AWAY)
                except Exception:
                    pass

        await asyncio.gather(*(_send(ws) for ws in list(self._clients)))

        for ws in dead:
            self.remove(ws)

    async def send_one(self, ws: WebSocket, payload: str) -> None:
        """Send a frame to a single client (used for handshake / pong)."""
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=self._timeout)
        except Exception:
            self.remove(ws)


# ── Module-level singletons (initialised in lifespan) ────────────────────────

_manager:  Optional[ConnectionManager]    = None
_registry: Optional[SchemaRegistryClient] = None
_fanout_task: Optional[asyncio.Task]      = None


def get_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        s = get_settings()
        _manager = ConnectionManager(
            max_clients=s.ws_max_clients,
            send_timeout=s.ws_send_timeout,
        )
    return _manager


def get_registry() -> SchemaRegistryClient:
    global _registry
    if _registry is None:
        s = get_settings()
        _registry = SchemaRegistryClient(
            base_url=s.schema_registry_url,
            timeout=s.schema_registry_timeout,
        )
    return _registry


# ═══════════════════════════════════════════════════════════════════════════════
# Kafka Fan-out Task  (runs as long-lived background asyncio.Task)
# ═══════════════════════════════════════════════════════════════════════════════

async def kafka_fanout_task(stop_event: asyncio.Event) -> None:
    """
    Consumes `ecommerce.events.clean.v1`, decodes each Avro message, and
    broadcasts the JSON payload to all connected WebSocket clients.

    Restart behaviour
    ─────────────────
    On transient Kafka errors the consumer sleeps with exponential back-off
    (max 60 s) then reconnects.  On stop_event the loop exits cleanly.
    """
    settings  = get_settings()
    manager   = get_manager()
    registry  = get_registry()
    backoff   = 1.0

    logger.info(
        f"KafkaFanout: starting consumer "
        f"topic={settings.kafka_clean_topic} "
        f"group={settings.ws_kafka_consumer_group}"
    )

    while not stop_event.is_set():
        consumer: Optional[AIOKafkaConsumer] = None
        try:
            consumer = AIOKafkaConsumer(
                settings.kafka_clean_topic,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.ws_kafka_consumer_group,
                auto_offset_reset=settings.ws_kafka_offset_reset,
                enable_auto_commit=True,
                auto_commit_interval_ms=5_000,
                # Receive raw bytes — we handle Avro ourselves
                value_deserializer=None,
                # Fetch up to 1 MB per poll; keeps memory bounded
                max_partition_fetch_bytes=1_048_576,
                fetch_max_wait_ms=500,
            )
            await consumer.start()
            backoff = 1.0   # reset after successful start
            logger.info("KafkaFanout: consumer connected to Kafka")

            async for msg in consumer:
                if stop_event.is_set():
                    break

                # Skip broadcasting if no clients are connected (saves CPU)
                if manager.count == 0:
                    continue

                event_dict = await registry.decode_async(msg.value)
                if event_dict is None:
                    continue

                # Convert datetime/Decimal/bytes to JSON-serialisable form
                payload = _safe_json(event_dict)
                await manager.broadcast(payload)

        except KafkaError as exc:
            logger.error(f"KafkaFanout: Kafka error — {exc}")
        except asyncio.CancelledError:
            logger.info("KafkaFanout: task cancelled")
            break
        except Exception as exc:
            logger.exception(f"KafkaFanout: unexpected error — {exc}")
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:
                    pass

        if not stop_event.is_set():
            sleep = min(backoff, 60.0)
            logger.info(f"KafkaFanout: reconnecting in {sleep:.0f} s…")
            await asyncio.sleep(sleep)
            backoff = min(backoff * 2, 60.0)

    logger.info("KafkaFanout: stopped")


def _safe_json(obj: Any) -> str:
    """Serialise a dict that may contain bytes/dates to a JSON string."""

    def _default(v: Any) -> Any:
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        return str(v)

    return json.dumps(obj, default=_default)


# ── Lifespan helpers (called from main.py) ────────────────────────────────────

_stop_event: Optional[asyncio.Event] = None


async def start_fanout() -> None:
    """Launch the Kafka fan-out background task.  Called from app lifespan."""
    global _fanout_task, _stop_event
    _stop_event   = asyncio.Event()
    _fanout_task  = asyncio.create_task(
        kafka_fanout_task(_stop_event),
        name="kafka-ws-fanout",
    )
    logger.info("KafkaFanout: background task created")


async def stop_fanout() -> None:
    """Signal the fan-out task to stop and await its completion."""
    global _fanout_task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _fanout_task and not _fanout_task.done():
        _fanout_task.cancel()
        try:
            await asyncio.wait_for(_fanout_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    logger.info("KafkaFanout: background task stopped")


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket endpoint
# ═══════════════════════════════════════════════════════════════════════════════

def _check_token(token: str) -> bool:
    """Return True if auth is disabled or token matches secret."""
    secret = get_settings().ws_secret_token
    if not secret:
        return True
    return token == secret


@router.websocket("/ws/events")
async def ws_events(
    ws:    WebSocket,
    token: str = Query(default="", description="Optional auth token"),
) -> None:
    """
    WebSocket endpoint — streams live Kafka events to connected clients.

    Query parameters
    ────────────────
    token  Optional static auth token (must match WS_SECRET_TOKEN when set).

    Close codes sent by server
    ──────────────────────────
    1008  Policy violation: bad token OR server at capacity.
    1001  Going away: client was too slow to receive a broadcast.
    1000  Normal closure: server shutting down.
    """
    # ── Auth ─────────────────────────────────────────────────────────────────
    if not _check_token(token):
        await ws.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or missing auth token",
        )
        logger.warning("WS: rejected — bad token")
        return

    # ── Register ──────────────────────────────────────────────────────────────
    manager = get_manager()
    accepted = await manager.accept(ws)
    if not accepted:
        return   # already closed inside accept()

    settings = get_settings()

    try:
        # Send handshake confirmation
        await manager.send_one(ws, json.dumps({
            "type":    "connected",
            "clients": manager.count,
            "topic":   settings.kafka_clean_topic,
            "auth":    bool(settings.ws_secret_token),
        }))

        # ── Main receive loop (heartbeat + ping/pong) ─────────────────────────
        while True:
            try:
                # Wait for a client frame; timeout triggers a server ping
                text = await asyncio.wait_for(
                    ws.receive_text(),
                    timeout=float(settings.ws_heartbeat_interval),
                )

                if text == "ping":
                    await manager.send_one(ws, "pong")
                # "pong" (reply to our server ping) is silently acknowledged
                # Any other text is ignored

            except asyncio.TimeoutError:
                # Heartbeat interval elapsed — probe the client
                await manager.send_one(ws, json.dumps({
                    "type": "ping",
                    "ts":   int(time.monotonic() * 1000),
                }))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(f"WS: session ended with {type(exc).__name__}: {exc}")
    finally:
        manager.remove(ws)


# ── Diagnostic REST endpoints ─────────────────────────────────────────────────

@router.get("/api/v1/ws/status", tags=["WebSocket"])
async def ws_status() -> Dict[str, Any]:
    """Return current WebSocket connection stats."""
    settings = get_settings()
    return {
        "connected_clients": get_manager().count,
        "max_clients":       settings.ws_max_clients,
        "topic":             settings.kafka_clean_topic,
        "consumer_group":    settings.ws_kafka_consumer_group,
        "auth_enabled":      bool(settings.ws_secret_token),
        "fanout_running":    _fanout_task is not None and not (_fanout_task.done() if _fanout_task else True),
    }

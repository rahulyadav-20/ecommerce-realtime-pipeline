"""
CacheService — async Redis client with an explicit ConnectionPool,
JSON serialisation, and SCAN-based pattern invalidation.

The module also exposes backward-compatible module-level coroutines
(``cache_get``, ``cache_set``, ``cache_key``, ``close_redis``) so existing
routers need no changes.
"""
import hashlib
import json
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Service class ──────────────────────────────────────────────────────────────

class CacheService:
    """
    Async Redis cache backed by an explicit ConnectionPool.

    All values are JSON-serialised (with ``default=str`` for dates/decimals).
    Cache misses and Redis errors are silent — callers always get a result,
    just without caching on errors.
    """

    def __init__(self, redis_url: str, max_connections: int = 20) -> None:
        self._pool = ConnectionPool.from_url(
            redis_url,
            max_connections=max_connections,
            decode_responses=True,
        )
        self._redis = aioredis.Redis(connection_pool=self._pool)

    # ── Core operations ───────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        """
        Fetch a cached value.  Returns ``None`` on miss *or* on any Redis
        error (fail-open — the caller falls through to the real source).
        """
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning(f"cache.get error key={key}: {exc}")
            return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """
        Store ``value`` under ``key`` with a TTL (seconds).
        Silently swallows Redis errors.
        """
        try:
            payload = json.dumps(value, default=str)
            await self._redis.setex(key, ttl, payload)
        except Exception as exc:
            logger.warning(f"cache.set error key={key}: {exc}")

    async def invalidate(self, pattern: str) -> int:
        """
        Delete all keys matching ``pattern`` (Redis glob syntax, e.g.
        ``"kpis:*"`` or ``"funnel:*"``).

        Uses non-blocking SCAN so it never blocks the Redis event loop.
        Returns the number of keys deleted.
        """
        deleted = 0
        try:
            async for key in self._redis.scan_iter(match=pattern, count=100):
                await self._redis.delete(key)
                deleted += 1
            if deleted:
                logger.info(f"cache.invalidate pattern={pattern} deleted={deleted}")
        except Exception as exc:
            logger.warning(f"cache.invalidate error pattern={pattern}: {exc}")
        return deleted

    async def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds, or -2 if key does not exist."""
        try:
            return await self._redis.ttl(key)
        except Exception:
            return -2

    async def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()
        await self._pool.aclose()

    # ── Key builder ───────────────────────────────────────────────────────────

    @staticmethod
    def make_key(prefix: str, **kwargs: Any) -> str:
        """
        Deterministic cache key from a prefix and keyword arguments.

        Example::

            CacheService.make_key("kpis", window="1h")
            # → "kpis:a1b2c3d4e5"
        """
        payload = "&".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        digest = hashlib.md5(payload.encode()).hexdigest()[:10]
        return f"{prefix}:{digest}"


# ── Module-level singleton ─────────────────────────────────────────────────────

_instance: Optional[CacheService] = None


def _get() -> CacheService:
    global _instance
    if _instance is None:
        from app.config import get_settings
        s = get_settings()
        _instance = CacheService(
            redis_url=s.redis_url,
            max_connections=s.redis_max_connections,
        )
    return _instance


# ── Backward-compatible module-level coroutines ────────────────────────────────

async def cache_get(key: str) -> Optional[Any]:
    return await _get().get(key)


async def cache_set(key: str, value: Any, ttl: int) -> None:
    await _get().set(key, value, ttl)


async def cache_invalidate(pattern: str) -> int:
    return await _get().invalidate(pattern)


def cache_key(prefix: str, **kwargs: Any) -> str:
    return CacheService.make_key(prefix, **kwargs)


async def close_redis() -> None:
    global _instance
    if _instance:
        await _instance.close()
        _instance = None

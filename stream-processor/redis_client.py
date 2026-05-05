"""Own aioredis instance for the stream-processor — not shared with other services."""
from __future__ import annotations

import redis.asyncio as aioredis

from config import settings

_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=False)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_redis() -> aioredis.Redis:
    if _client is None:
        raise RuntimeError("Redis not initialised — call init_redis() first")
    return _client

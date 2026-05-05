"""
asyncpg connection pool — owned exclusively by the auth-service.

The ingest-api owns a separate pool. We do NOT share pools across services
(see Phase 2 spec): keeping them separate means each service has independent
connection budgets and can be scaled / restarted independently.
"""
from __future__ import annotations

import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Postgres pool not initialised — did lifespan run?")
    return _pool

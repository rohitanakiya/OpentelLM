"""
Test fixtures for cross-tenant isolation tests.

These tests run against the live services started by `docker compose up`.
That gives us the most honest verification: real HTTP, real Postgres,
real Redis, the actual auth-service and ingest-api binaries the user
will deploy.

Why not import the FastAPI apps directly via ASGI?
  Both services have a top-level `main.py` and `config.py`. Importing
  both into the same Python process collides on those module names and
  produces fragile, hard-to-debug failures. Real HTTP is simpler and
  faithful to production.

Required before running:
    docker compose up -d
    # wait for healthchecks to pass
    pytest tests/test_isolation.py

Optional environment overrides:
    AUTH_URL          (default http://localhost:8001)
    INGEST_URL        (default http://localhost:8000)
    TEST_POSTGRES_DSN (default postgresql://opentelm:opentelm@localhost:5432/opentelm)
"""
from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parent.parent

AUTH_URL   = os.environ.get("AUTH_URL",   "http://localhost:8001")
INGEST_URL = os.environ.get("INGEST_URL", "http://localhost:8000")
TEST_DSN   = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql://opentelm:opentelm@localhost:5432/opentelm",
)


# ---------------------------------------------------------------------------
# Reachability gate
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session", autouse=True)
async def _services_up():
    """Skip the entire test module if the services aren't running."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            r1 = await client.get(f"{AUTH_URL}/health")
            r2 = await client.get(f"{INGEST_URL}/health")
            if r1.status_code != 200 or r2.status_code != 200:
                pytest.skip("auth-service or ingest-api /health not 200")
        except (httpx.HTTPError, OSError) as exc:
            pytest.skip(
                f"Services unreachable ({exc}). Run `docker compose up -d` first."
            )


# ---------------------------------------------------------------------------
# Postgres — used to wipe state between tests
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def pg_pool():
    try:
        pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=4)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable at {TEST_DSN}: {exc}")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def clean_db(pg_pool):
    """Wipe Phase 2 tables AND the auth-service's Redis cache entries."""
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE api_keys, projects, tenants RESTART IDENTITY CASCADE"
        )
    # Flush Redis so previous tests' cached api-key mappings don't bleed in.
    # We use the Redis HTTP debug bridge by hitting redis-cli via a quick
    # connection. Simpler: reach into Redis directly.
    import redis.asyncio as aioredis
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = aioredis.from_url(redis_url)
        await r.flushdb()
        await r.close()
    except Exception:
        pass
    yield pg_pool


# ---------------------------------------------------------------------------
# HTTP clients
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def auth_client(clean_db):
    async with httpx.AsyncClient(base_url=AUTH_URL, timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
async def ingest_client(clean_db):
    async with httpx.AsyncClient(base_url=INGEST_URL, timeout=10.0) as client:
        yield client

"""
End-to-end tests for the Phase 3 stream-processor and anomaly-consumer.

These tests run against `docker compose up`. They exercise the real
ingest pipeline: HTTP → Kafka → stream-processor → ClickHouse + Redis.

Run subsets:
    pytest tests/test_stream_processor.py -m "not slow"   # fast
    pytest tests/test_stream_processor.py                 # all (includes 75s waits)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from clickhouse_driver import Client as ClickHouseClient

pytestmark = pytest.mark.asyncio


CH_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CH_PORT = int(os.environ.get("CLICKHOUSE_PORT", "9000"))
CH_DB   = os.environ.get("CLICKHOUSE_DB", "opentelm")
REDIS_URL_ENV = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def ch_client():
    client = ClickHouseClient(host=CH_HOST, port=CH_PORT, database=CH_DB)
    yield client
    client.disconnect()


@pytest_asyncio.fixture
async def redis_client():
    r = aioredis.from_url(REDIS_URL_ENV, decode_responses=False)
    yield r
    await r.close()


@pytest_asyncio.fixture
async def tenant_setup(auth_client):
    """Create a fresh tenant + project + write key. Returns (tenant_id, project_id, raw_key, model)."""
    email = f"stream-{uuid.uuid4().hex[:8]}@example.com"
    r = await auth_client.post("/auth/register", json={
        "name": "Stream Tester", "email": email, "password": "supersecret123",
    })
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]

    r = await auth_client.post("/auth/login", json={
        "email": email, "password": "supersecret123",
    })
    access = r.json()["access_token"]

    r = await auth_client.post(
        "/projects", json={"name": "stream-test"},
        headers={"Authorization": f"Bearer {access}"},
    )
    project_id = r.json()["id"]

    r = await auth_client.post(
        f"/projects/{project_id}/keys", json={"permissions": "write"},
        headers={"Authorization": f"Bearer {access}"},
    )
    raw_key = r.json()["api_key"]

    return tenant_id, project_id, raw_key, "gpt-4o"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hex64(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _event(model: str = "gpt-4o", *, prompt_tokens=10, completion_tokens=5, status="success"):
    return {
        "model": model,
        "provider": "openai",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": 200,
        "status": status,
        "prompt_hash": _hex64(uuid.uuid4().hex),
        "response_hash": _hex64(uuid.uuid4().hex),
    }


async def _seed_hourly_baseline(redis_client, tenant_id: str, project_id: str, usd_per_hour: float):
    """Pretend the project spent `usd_per_hour` in each of the previous 24
    hours (the anomaly detector's baseline window, see consumers/anomaly.py)."""
    this_hour = int(time.time()) // 3600
    pipe = redis_client.pipeline()
    for h in range(this_hour - 24, this_hour):
        key = f"hourcost:{tenant_id}:{project_id}:{h}"
        pipe.set(key, str(usd_per_hour).encode(), ex=26 * 3600)
    await pipe.execute()


async def _ingest(ingest_client: httpx.AsyncClient, raw_key: str, events: list[dict]):
    r = await ingest_client.post(
        "/v1/ingest",
        json={"events": events},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 202, r.text


# ---------------------------------------------------------------------------
# 1. Metrics flushed to ClickHouse after window closes  [SLOW]
# ---------------------------------------------------------------------------
@pytest.mark.slow
async def test_metrics_written_after_window_close(
    ingest_client, tenant_setup, ch_client,
):
    tenant_id, project_id, raw_key, model = tenant_setup
    await _ingest(ingest_client, raw_key, [_event(model) for _ in range(10)])

    # Window is 60s; give the aggregator 15s of buffer to flush.
    await asyncio.sleep(75)

    rows = ch_client.execute(
        """
        SELECT sum(request_count)
        FROM opentelm.llm_metrics_1m
        WHERE tenant_id = %(tenant)s AND project_id = %(project)s
        """,
        {"tenant": tenant_id, "project": project_id},
    )
    total = rows[0][0] if rows else 0
    assert total >= 10, f"Expected ≥10 requests, got {total}"


# ---------------------------------------------------------------------------
# 2. Live metrics published to Redis  [SLOW]
# ---------------------------------------------------------------------------
@pytest.mark.slow
async def test_redis_live_metrics_updated(
    ingest_client, tenant_setup, redis_client,
):
    tenant_id, project_id, raw_key, model = tenant_setup
    await _ingest(ingest_client, raw_key, [_event(model) for _ in range(5)])

    await asyncio.sleep(75)

    raw = await redis_client.get(f"metrics:{tenant_id}:{project_id}:{model}")
    assert raw is not None, "metrics:* key was never set in Redis"
    data = json.loads(raw)
    assert "p99_latency_ms" in data
    assert data["request_count"] >= 5


# ---------------------------------------------------------------------------
# 3. Cost-spike anomaly fires
# ---------------------------------------------------------------------------
async def test_cost_spike_anomaly_fires(
    ingest_client, tenant_setup, redis_client,
):
    tenant_id, project_id, raw_key, _ = tenant_setup

    # A quiet history ($0.10/h) so the 20 big calls below (~$45) are a spike.
    await _seed_hourly_baseline(redis_client, tenant_id, project_id, 0.10)
    # Clear any stale alerts from previous runs
    await redis_client.delete(f"alerts:{tenant_id}")

    def big_event():
        return _event("claude-3-opus", prompt_tokens=50_000, completion_tokens=20_000)
    await _ingest(ingest_client, raw_key, [big_event() for _ in range(20)])

    # The detector + persister chain is fast (no window wait); poll for up to 30s
    deadline = time.monotonic() + 30
    items: list[bytes] = []
    while time.monotonic() < deadline:
        items = await redis_client.lrange(f"alerts:{tenant_id}", 0, -1)
        cost_spikes = [i for i in items if json.loads(i).get("type") == "cost_spike"]
        if cost_spikes:
            break
        await asyncio.sleep(1)

    cost_spikes = [json.loads(i) for i in items if json.loads(i).get("type") == "cost_spike"]
    assert cost_spikes, f"No cost_spike anomaly appeared in alerts:{tenant_id} within 30s"
    assert cost_spikes[0]["tenant_id"] == tenant_id
    assert cost_spikes[0]["project_id"] == project_id


# ---------------------------------------------------------------------------
# 3b. Normal traffic does NOT fire (the previous detector failed this)
# ---------------------------------------------------------------------------
async def test_normal_traffic_does_not_fire(
    ingest_client, tenant_setup, redis_client,
):
    tenant_id, project_id, raw_key, _ = tenant_setup
    # This project normally spends ~$100/h, so ~$45 this hour is unremarkable.
    await _seed_hourly_baseline(redis_client, tenant_id, project_id, 100.0)
    await redis_client.delete(f"alerts:{tenant_id}")

    await _ingest(ingest_client, raw_key, [
        _event("claude-3-opus", prompt_tokens=50_000, completion_tokens=20_000)
        for _ in range(20)
    ])

    await asyncio.sleep(10)  # well past the detector's normal latency
    items = await redis_client.lrange(f"alerts:{tenant_id}", 0, -1)
    assert not [i for i in items if json.loads(i).get("type") == "cost_spike"]


# ---------------------------------------------------------------------------
# 4. Anomaly deduplication (one alert per tenant/project per hour)
# ---------------------------------------------------------------------------
async def test_anomaly_deduplication(
    ingest_client, tenant_setup, redis_client,
):
    tenant_id, project_id, raw_key, _ = tenant_setup

    await _seed_hourly_baseline(redis_client, tenant_id, project_id, 0.10)
    await redis_client.delete(f"alerts:{tenant_id}")

    def big_event():
        return _event("claude-3-opus", prompt_tokens=50_000, completion_tokens=20_000)

    # First burst — should trigger one alert
    await _ingest(ingest_client, raw_key, [big_event() for _ in range(20)])
    # Second burst — must be suppressed by the dedup key
    await _ingest(ingest_client, raw_key, [big_event() for _ in range(20)])

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        items = await redis_client.lrange(f"alerts:{tenant_id}", 0, -1)
        cost_spikes_for_proj = [
            i for i in items
            if (j := json.loads(i)).get("type") == "cost_spike"
            and j.get("project_id") == project_id
        ]
        if cost_spikes_for_proj:
            break
        await asyncio.sleep(1)

    items = await redis_client.lrange(f"alerts:{tenant_id}", 0, -1)
    cost_spikes_for_proj = [
        i for i in items
        if (j := json.loads(i)).get("type") == "cost_spike"
        and j.get("project_id") == project_id
    ]
    assert len(cost_spikes_for_proj) == 1, (
        f"Expected exactly 1 dedup'd cost_spike for project, got {len(cost_spikes_for_proj)}"
    )


# ---------------------------------------------------------------------------
# 5. Anomalies persisted to ClickHouse
# ---------------------------------------------------------------------------
async def test_anomaly_written_to_clickhouse(
    ingest_client, tenant_setup, redis_client, ch_client,
):
    tenant_id, project_id, raw_key, _ = tenant_setup

    await _seed_hourly_baseline(redis_client, tenant_id, project_id, 0.10)

    await _ingest(ingest_client, raw_key, [
        _event("claude-3-opus", prompt_tokens=50_000, completion_tokens=20_000)
        for _ in range(20)
    ])

    # Wait for the anomaly-consumer to flush to ClickHouse.
    deadline = time.monotonic() + 30
    rows: list = []
    while time.monotonic() < deadline:
        rows = ch_client.execute(
            """
            SELECT count()
            FROM opentelm.llm_anomalies
            WHERE tenant_id = %(tenant)s AND anomaly_type = 'cost_spike'
            """,
            {"tenant": tenant_id},
        )
        if rows and rows[0][0] >= 1:
            break
        await asyncio.sleep(1)

    assert rows and rows[0][0] >= 1, "no cost_spike row found in llm_anomalies"

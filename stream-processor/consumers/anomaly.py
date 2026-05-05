"""
anomaly-detector: cost-spike detection via exponential moving average.

For each (tenant_id, project_id) we maintain:
  - ema_baseline_usd     — long-running per-event cost EMA, persisted in Redis
  - current_hour_cost    — accumulator for the current wall-clock hour, in Redis

When current_hour_cost > multiplier * ema_baseline (and the baseline is
non-trivial), publish a cost_spike anomaly to llm.anomalies.

PERSISTENCE
-----------
EMA state lives in Redis at  ema:{tenant}:{project}, value = float as bytes.
We READ this value lazily (per-key) on first use after startup, and WRITE
it back after every event. A service restart therefore does NOT reset the
baseline — see _load_ema and the redis pipeline at the bottom of _process.

Dedup: alerts are deduped per (tenant, project) per hour via Redis key
   anomaly_fired:{tenant}:{project}   TTL = 3600s
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from clickhouse import ClickHouseWriter  # noqa: F401  (kept for future symmetry)
from config import settings
from kafka_client import make_consumer, publish_anomaly
from pricing import calculate_cost
from redis_client import get_redis

logger = logging.getLogger(__name__)

GROUP_ID = "anomaly-detector"

EMA_KEY      = "ema:{tenant}:{project}"
HOUR_KEY     = "hourcost:{tenant}:{project}:{hour}"
DEDUP_KEY    = "anomaly_fired:{tenant}:{project}"


async def run_anomaly_detector(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    redis = get_redis()

    # Per-process EMA cache so we don't re-fetch from Redis on every event.
    # Cache is repopulated lazily from Redis (the source of truth).
    ema_cache: dict[tuple[str, str], float] = {}

    logger.info("anomaly-detector: started")

    try:
        while not stop.is_set():
            try:
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                batches = {}

            for tp, msgs in batches.items():
                for msg in msgs:
                    await _process(msg.value, redis, ema_cache)

            if batches:
                await consumer.commit()

    finally:
        await consumer.stop()
        logger.info("anomaly-detector: stopped")


async def _load_ema(redis, tenant: str, project: str, cache: dict) -> float:
    """Lazy hydrate from Redis. Source of truth is Redis; cache is just a hot copy."""
    if (tenant, project) in cache:
        return cache[(tenant, project)]
    raw = await redis.get(EMA_KEY.format(tenant=tenant, project=project))
    val = float(raw) if raw is not None else 0.0
    cache[(tenant, project)] = val
    return val


async def _process(event: dict, redis, ema_cache: dict) -> None:
    tenant = event.get("tenant_id")
    project = event.get("project_id")
    if not (tenant and project):
        return

    pt = int(event.get("prompt_tokens") or 0)
    ct = int(event.get("completion_tokens") or 0)
    model = event.get("model") or "unknown"
    cost = calculate_cost(model, pt, ct)

    # 1. Update the per-event EMA. alpha is intentionally small so single
    #    expensive calls don't move the baseline much.
    old_ema = await _load_ema(redis, tenant, project, ema_cache)
    new_ema = settings.ema_alpha * cost + (1 - settings.ema_alpha) * old_ema
    ema_cache[(tenant, project)] = new_ema

    # 2. Bucket the cost into the current wall-clock hour.
    hour_bucket = int(time.time() // 3600)
    hour_key = HOUR_KEY.format(tenant=tenant, project=project, hour=hour_bucket)
    ema_key = EMA_KEY.format(tenant=tenant, project=project)

    # Pipeline: write back EMA, increment hour bucket, fetch updated value.
    pipe = redis.pipeline()
    pipe.set(ema_key, str(new_ema).encode())
    # incrbyfloat is atomic; set TTL on first write so the bucket auto-expires
    pipe.incrbyfloat(hour_key, cost)
    pipe.expire(hour_key, 7200)  # keep two hours' worth so boundary races resolve
    results = await pipe.execute()
    current_hour_cost = float(results[1])

    # 3. Decide if we should fire. Skip until baseline > 0 (no history yet).
    # Without this guard, the very first event would trivially exceed 3 * 0.
    threshold = settings.cost_spike_multiplier * new_ema
    if new_ema <= 0 or current_hour_cost <= threshold:
        return

    # 4. Dedup. SETNX returns True iff we won the race.
    dedup_key = DEDUP_KEY.format(tenant=tenant, project=project)
    won = await redis.set(dedup_key, b"1", ex=settings.anomaly_dedup_ttl_s, nx=True)
    if not won:
        return

    multiplier = current_hour_cost / new_ema if new_ema > 0 else 0.0
    severity = "critical" if multiplier > 10 else "warning"

    await publish_anomaly({
        "type":        "cost_spike",
        "tenant_id":   tenant,
        "project_id":  project,
        "severity":    severity,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "details": {
            "current_hour_cost_usd": round(current_hour_cost, 6),
            "ema_baseline_usd":      round(new_ema, 6),
            "multiplier":            round(multiplier, 3),
        },
    })
    logger.warning(
        "cost_spike fired tenant=%s project=%s hour=$%.4f baseline=$%.6f x%.1f",
        tenant, project, current_hour_cost, new_ema, multiplier,
    )

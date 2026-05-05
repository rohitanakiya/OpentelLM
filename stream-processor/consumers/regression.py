"""
prompt-regression-detector: compares per-version error rate and avg latency.

Per (tenant_id, project_id, version) we maintain a Redis hash:
    regression:{tenant}:{project}:{version}
       request_count       INCR
       error_count         INCR
       total_latency_ms    INCRBYFLOAT

Every settings.regression_compare_every_n requests for a given version,
we compare it against the previously-seen version for the same project
(stored at  regression_prev:{tenant}:{project}). We require both versions
to have at least settings.regression_min_samples requests; otherwise we
skip the comparison.

Fires a `prompt_regression` anomaly if EITHER:
   error_rate(current) - error_rate(prev) > 0.10  (10pp absolute)
   avg_latency(current) / avg_latency(prev) > 1.40 (40% increase)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from config import settings
from kafka_client import make_consumer, publish_anomaly
from redis_client import get_redis

logger = logging.getLogger(__name__)

GROUP_ID = "prompt-regression-detector"

STATS_KEY    = "regression:{tenant}:{project}:{version}"
PREV_VER_KEY = "regression_prev:{tenant}:{project}"
DEDUP_KEY    = "regression_fired:{tenant}:{project}:{version}"


async def run_regression_detector(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    redis = get_redis()
    logger.info("prompt-regression-detector: started")

    try:
        while not stop.is_set():
            try:
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                batches = {}

            for tp, msgs in batches.items():
                for msg in msgs:
                    await _process(msg.value, redis)

            if batches:
                await consumer.commit()
    finally:
        await consumer.stop()
        logger.info("prompt-regression-detector: stopped")


async def _process(event: dict, redis) -> None:
    tenant = event.get("tenant_id")
    project = event.get("project_id")
    # The SDK can emit either field name; accept both.
    version = event.get("prompt_version") or event.get("deployment_version")
    if not (tenant and project and version):
        return

    is_error = event.get("status") in ("error", "timeout")
    latency = float(event.get("latency_ms") or 0)

    stats_key = STATS_KEY.format(tenant=tenant, project=project, version=version)

    pipe = redis.pipeline()
    pipe.hincrby(stats_key, "request_count", 1)
    pipe.hincrby(stats_key, "error_count", 1 if is_error else 0)
    pipe.hincrbyfloat(stats_key, "total_latency_ms", latency)
    pipe.expire(stats_key, 60 * 60 * 24 * 14)  # keep 14 days of regression history
    results = await pipe.execute()

    request_count = int(results[0])

    # Only consider comparing every N requests, AND only after the minimum
    # sample threshold for THIS version has been crossed.
    if request_count < settings.regression_min_samples:
        # Still a candidate to become "previous" — record the most-recent
        # version we've seen for this project so future versions know what
        # to compare against.
        await redis.set(
            PREV_VER_KEY.format(tenant=tenant, project=project),
            version.encode(),
        )
        return

    if request_count % settings.regression_compare_every_n != 0:
        return

    prev_raw = await redis.get(PREV_VER_KEY.format(tenant=tenant, project=project))
    prev_version = prev_raw.decode() if prev_raw else None
    if not prev_version or prev_version == version:
        # Mark this version as the latest if there's no prior to compare against
        await redis.set(
            PREV_VER_KEY.format(tenant=tenant, project=project),
            version.encode(),
        )
        return

    prev_stats = await _load_stats(redis, tenant, project, prev_version)
    if prev_stats["request_count"] < settings.regression_min_samples:
        return

    current_stats = await _load_stats(redis, tenant, project, version)
    cur_err_rate  = current_stats["error_count"] / current_stats["request_count"]
    prev_err_rate = prev_stats["error_count"] / prev_stats["request_count"]
    cur_avg_lat   = current_stats["total_latency_ms"] / current_stats["request_count"]
    prev_avg_lat  = (prev_stats["total_latency_ms"] / prev_stats["request_count"]) or 1e-9

    err_delta = cur_err_rate - prev_err_rate
    lat_delta_pct = (cur_avg_lat - prev_avg_lat) / prev_avg_lat

    if err_delta <= settings.regression_error_rate_delta and \
       lat_delta_pct <= settings.regression_latency_delta_pct:
        return

    dedup_key = DEDUP_KEY.format(tenant=tenant, project=project, version=version)
    won = await redis.set(dedup_key, b"1", ex=settings.anomaly_dedup_ttl_s, nx=True)
    if not won:
        return

    severity = "critical" if (err_delta > 0.25 or lat_delta_pct > 1.0) else "warning"
    await publish_anomaly({
        "type":        "prompt_regression",
        "tenant_id":   tenant,
        "project_id":  project,
        "severity":    severity,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "details": {
            "current_version":   version,
            "previous_version":  prev_version,
            "error_rate_delta":  round(err_delta, 4),
            "latency_delta_pct": round(lat_delta_pct, 4),
        },
    })
    logger.warning(
        "prompt_regression fired tenant=%s project=%s %s→%s err_delta=%.3f lat_delta=%.3f",
        tenant, project, prev_version, version, err_delta, lat_delta_pct,
    )


async def _load_stats(redis, tenant: str, project: str, version: str) -> dict:
    raw = await redis.hgetall(STATS_KEY.format(tenant=tenant, project=project, version=version))
    return {
        "request_count":    int(raw.get(b"request_count", b"0") or 0),
        "error_count":      int(raw.get(b"error_count", b"0") or 0),
        "total_latency_ms": float(raw.get(b"total_latency_ms", b"0") or 0),
    }

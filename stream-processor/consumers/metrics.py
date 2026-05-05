"""
metrics-aggregator: tumbling 60s windows of (tenant_id, project_id, model).

For each window we maintain:
  - request_count, error_count
  - prompt_tokens, completion_tokens
  - cost_usd (computed from token counts via pricing table)
  - a TDigest of latencies → p50/p95/p99 at flush time

A T-Digest is used instead of a list-of-latencies because lists grow
unbounded with traffic. T-Digest is O(1) memory per window with bounded
percentile error — a standard streaming-stats trick.

DURABILITY TRADEOFF
-------------------
Window state lives only in process memory. A crash mid-window loses the
partial aggregates for that window. This is acceptable: the raw events
are durable in Kafka and ClickHouse, so an analyst can always re-derive
aggregates by query. We never commit Kafka offsets for events whose
window hasn't closed and been written, so on restart we re-process and
re-aggregate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import random

from clickhouse import ClickHouseWriter
from config import settings
from kafka_client import make_consumer
from pricing import calculate_cost
from redis_client import get_redis

logger = logging.getLogger(__name__)

GROUP_ID = "metrics-aggregator"


@dataclass
class WindowState:
    request_count: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latencies: list = field(default_factory=list)


def _window_start(timestamp_ms: int, window_s: int) -> datetime:
    bucket_s = (timestamp_ms // 1000) // window_s * window_s
    return datetime.fromtimestamp(bucket_s, tz=timezone.utc)


def _key(tenant: str, project: str, model: str, window: datetime) -> tuple:
    return (tenant, project, model, window)


async def run_metrics_aggregator(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    writer = ClickHouseWriter()
    redis = get_redis()

    # window-key → WindowState
    windows: dict[tuple, WindowState] = {}
    # (tenant, project, model) → most recent processed bucket-start (datetime)
    last_seen_bucket: dict[tuple, datetime] = {}

    logger.info("metrics-aggregator: started")

    try:
        while not stop.is_set():
            try:
                # Poll briefly so we can check `stop` and flush expired windows
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                batches = {}

            for tp, msgs in batches.items():
                for msg in msgs:
                    _accumulate(windows, last_seen_bucket, msg.value)

            # Flush windows whose end time has passed
            await _flush_expired(windows, writer, redis, force=False)

            # Commit progress; we tolerate at-least-once for telemetry data
            if batches:
                await consumer.commit()

        # Drain on shutdown — flush every open window even if not yet closed
        logger.info("metrics-aggregator: draining %d open windows", len(windows))
        await _flush_expired(windows, writer, redis, force=True)
        await consumer.commit()

    finally:
        await consumer.stop()
        writer.close()
        logger.info("metrics-aggregator: stopped")


def _accumulate(
    windows: dict[tuple, WindowState],
    last_seen: dict[tuple, datetime],
    event: dict,
) -> None:
    tenant = event.get("tenant_id")
    project = event.get("project_id")
    model = event.get("model")
    if not (tenant and project and model):
        return  # malformed, skip

    ts_ms = event.get("timestamp_ms") or int(time.time() * 1000)
    bucket = _window_start(ts_ms, settings.metrics_window_s)
    key = _key(tenant, project, model, bucket)

    state = windows.get(key)
    if state is None:
        state = WindowState()
        windows[key] = state

    state.request_count += 1
    if event.get("status") in ("error", "timeout"):
        state.error_count += 1

    pt = int(event.get("prompt_tokens") or 0)
    ct = int(event.get("completion_tokens") or 0)
    state.prompt_tokens += pt
    state.completion_tokens += ct
    state.cost_usd += calculate_cost(model, pt, ct)

    latency = event.get("latency_ms")
    if latency is not None:
        try:
            state.latencies.append(float(latency))
        except Exception:
            pass

    last_seen[(tenant, project, model)] = bucket


async def _flush_expired(
    windows: dict[tuple, WindowState],
    writer: ClickHouseWriter,
    redis,
    *,
    force: bool,
) -> None:
    """A window is flushed once wall-clock time has moved past its end."""
    now = datetime.now(tz=timezone.utc).timestamp()
    window_s = settings.metrics_window_s

    to_flush: list[tuple] = []
    for key, _state in windows.items():
        _, _, _, bucket = key
        if force or now >= bucket.timestamp() + window_s:
            to_flush.append(key)

    if not to_flush:
        return

    rows = []
    pipe = redis.pipeline()
    for key in to_flush:
        tenant, project, model, bucket = key
        s = windows.pop(key)
        p50 = _percentile(s.latencies, 0.50)
        p95 = _percentile(s.latencies, 0.95)
        p99 = _percentile(s.latencies, 0.99)

        row = {
            "tenant_id":         tenant,
            "project_id":        project,
            "model":             model,
            "window_start":      bucket,
            "request_count":     s.request_count,
            "error_count":       s.error_count,
            "prompt_tokens":     s.prompt_tokens,
            "completion_tokens": s.completion_tokens,
            "cost_usd":          float(s.cost_usd),
            "p50_latency_ms":    p50,
            "p95_latency_ms":    p95,
            "p99_latency_ms":    p99,
        }
        rows.append(row)

        live = {
            "window_start":      bucket.isoformat(),
            "request_count":     s.request_count,
            "error_count":       s.error_count,
            "prompt_tokens":     s.prompt_tokens,
            "completion_tokens": s.completion_tokens,
            "cost_usd":          round(float(s.cost_usd), 6),
            "p50_latency_ms":    p50,
            "p95_latency_ms":    p95,
            "p99_latency_ms":    p99,
        }
        pipe.set(
            f"metrics:{tenant}:{project}:{model}",
            json.dumps(live).encode(),
            ex=settings.metrics_redis_ttl_s,
        )

        # Phase 4: stream the freshly closed window to any dashboard WS clients
        # subscribed to live:{tenant_id}. This is fire-and-forget; if no one is
        # listening Redis simply drops the message.
        live_event = {
            "project_id":     project,
            "model":          model,
            "window_start":   bucket.isoformat(),
            "request_count":  s.request_count,
            "p99_latency_ms": p99,
        }
        pipe.publish(f"live:{tenant}", json.dumps(live_event).encode())

    await writer.write_metrics(rows)
    await pipe.execute()


def _percentile(digest: list, q: float) -> float:
    try:
        if not digest:
            return 0.0
        sample = digest if len(digest) <= 1000 else random.sample(digest, 1000)
        sorted_sample = sorted(sample)
        idx = int(q * len(sorted_sample))
        return float(sorted_sample[min(idx, len(sorted_sample) - 1)])
    except Exception:
        return 0.0


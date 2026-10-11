"""
metrics-aggregator: tumbling 60 s windows per (tenant_id, project_id, model).

Each window keeps counts, tokens, cost and a t-digest of latencies
(tdigest.py), so memory per window is bounded however much traffic it gets.

WHEN A WINDOW IS WRITTEN (watermark + allowed lateness)
------------------------------------------------------
Event times come from the SDK, and events reach Kafka a few seconds late
(the SDK batches every 2 s). A window [t, t+60s) is written once the wall
clock passes t + 60 s + `metrics_allowed_lateness_s` (default 10 s), so
normal stragglers still land in it.

An event that arrives after its window was written isn't dropped and
doesn't overwrite anything: it opens a small "late" window for the same
minute that is written on the next loop. So a minute can have more than
one row in llm_metrics_1m. Every reader aggregates rows: counts and cost
are summed, and percentiles merge the stored t-digest centroids:

    quantileTDigestWeightedArray(0.99)(latency_means, latency_weights)

The previous version wrote a second row for the same minute in that case,
which the Overview page then averaged as if it were another minute. It
also showed "avg of per-minute p99s", which is not a p99 at all.

OFFSETS (at-least-once)
-----------------------
Kafka offsets are committed only up to the oldest event still sitting in
an open window, so a crash or restart re-reads exactly the events whose
windows weren't written yet. (Previously offsets were committed on every
poll, so a crash silently lost up to 70 s of metrics.) The cost of
at-least-once: an event re-read after a crash whose own window had
already been written gets counted twice; that only affects a few late
events around the crash.

Redis: `metrics:{tenant}:{project}:{model}` holds the latest complete
window (late corrections don't overwrite it), and every written window is
published on `live:{tenant}` for the dashboard's live chart, which adds
late corrections to the minute they belong to.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from clickhouse import ClickHouseWriter
from config import settings
from kafka_client import make_consumer
from pricing import calculate_cost
from redis_client import get_redis
from tdigest import TDigest

logger = logging.getLogger(__name__)

GROUP_ID = "metrics-aggregator"

WindowKey = tuple[str, str, str, int]   # tenant, project, model, window start (epoch s)


@dataclass
class Window:
    late: bool = False
    request_count: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    digest: TDigest = field(default_factory=TDigest)
    # Lowest Kafka offset per partition among this window's events: the
    # committed offset can't move past it until the window is written.
    first_offsets: dict[Any, int] = field(default_factory=dict)


class MetricsAggregator:
    """Pure windowing logic (no I/O), so it can be unit-tested."""

    def __init__(self, window_s: int, lateness_s: float) -> None:
        self.window_s = window_s
        self.lateness_s = lateness_s
        self.windows: dict[WindowKey, Window] = {}
        self.consumed: dict[Any, int] = {}   # partition -> next offset to read

    def deadline(self, window_start: int) -> float:
        return window_start + self.window_s + self.lateness_s

    def add(self, tp: Any, offset: int, event: Any, now: float | None = None) -> None:
        self.consumed[tp] = offset + 1
        if not isinstance(event, dict):
            return  # undecodable message (see kafka_client._safe_json)
        tenant, project, model = event.get("tenant_id"), event.get("project_id"), event.get("model")
        if not (tenant and project and model):
            return
        now = time.time() if now is None else now
        try:
            ts_s = int(event.get("timestamp_ms") or now * 1000) // 1000
        except (TypeError, ValueError):
            ts_s = int(now)
        start = ts_s - ts_s % self.window_s
        key = (str(tenant), str(project), str(model), start)

        w = self.windows.get(key)
        if w is None:
            # Its regular window was already written (or would have been).
            w = self.windows[key] = Window(late=now >= self.deadline(start))
        w.first_offsets.setdefault(tp, offset)

        w.request_count += 1
        if event.get("status") in ("error", "timeout"):
            w.error_count += 1
        try:
            pt = int(event.get("prompt_tokens") or 0)
            ct = int(event.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pt = ct = 0
        w.prompt_tokens += pt
        w.completion_tokens += ct
        w.cost_usd += calculate_cost(str(model), pt, ct)
        try:
            w.digest.add(float(event.get("latency_ms")))
        except (TypeError, ValueError):
            pass

    def pop_due(self, now: float | None = None, *, force: bool = False) -> list[tuple[WindowKey, Window]]:
        """Remove and return windows that are ready to write."""
        now = time.time() if now is None else now
        due = [k for k, w in self.windows.items()
               if force or w.late or now >= self.deadline(k[3])]
        return [(k, self.windows.pop(k)) for k in sorted(due, key=lambda k: k[3])]

    def commit_positions(self) -> dict[Any, int]:
        """Offsets that are safe to commit: everything before them has been
        written (or skipped as malformed)."""
        positions = dict(self.consumed)
        for w in self.windows.values():
            for tp, off in w.first_offsets.items():
                if off < positions.get(tp, off + 1):
                    positions[tp] = off
        return positions


def window_row(key: WindowKey, w: Window) -> dict:
    tenant, project, model, start = key
    centroids = w.digest.centroids()
    return {
        "tenant_id":         tenant,
        "project_id":        project,
        "model":             model,
        "window_start":      datetime.fromtimestamp(start, tz=timezone.utc),
        "request_count":     w.request_count,
        "error_count":       w.error_count,
        "prompt_tokens":     w.prompt_tokens,
        "completion_tokens": w.completion_tokens,
        "cost_usd":          float(w.cost_usd),
        "p50_latency_ms":    w.digest.quantile(0.50),
        "p95_latency_ms":    w.digest.quantile(0.95),
        "p99_latency_ms":    w.digest.quantile(0.99),
        "latency_means":     [float(m) for m, _ in centroids],
        "latency_weights":   [int(round(c)) for _, c in centroids],
    }


async def write_windows(windows: list[tuple[WindowKey, Window]], writer: Any, redis: Any) -> None:
    if not windows:
        return
    rows = [window_row(k, w) for k, w in windows]
    await writer.write_metrics(rows)   # raises on failure: nothing is committed

    pipe = redis.pipeline()
    for (key, w), row in zip(windows, rows):
        tenant, project, model, _ = key
        summary = {
            "window_start":      row["window_start"].isoformat(),
            "request_count":     row["request_count"],
            "error_count":       row["error_count"],
            "prompt_tokens":     row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "cost_usd":          round(row["cost_usd"], 6),
            "p50_latency_ms":    row["p50_latency_ms"],
            "p95_latency_ms":    row["p95_latency_ms"],
            "p99_latency_ms":    row["p99_latency_ms"],
        }
        if not w.late:
            pipe.set(f"metrics:{tenant}:{project}:{model}", json.dumps(summary).encode(),
                     ex=settings.metrics_redis_ttl_s)
        # Fire-and-forget to dashboard WebSocket clients; late corrections
        # are added to the same minute by the live chart.
        pipe.publish(f"live:{tenant}", json.dumps({
            "project_id":     project,
            "model":          model,
            "window_start":   summary["window_start"],
            "request_count":  row["request_count"],
            "p99_latency_ms": row["p99_latency_ms"],
            "late":           w.late,
        }).encode())
    await pipe.execute()


async def step(agg: MetricsAggregator, consumer: Any, writer: Any, redis: Any,
               committed: dict[Any, int], *, now: float | None = None, force: bool = False) -> None:
    """Write due windows, then commit whatever offsets became safe."""
    await write_windows(agg.pop_due(now, force=force), writer, redis)
    positions = agg.commit_positions()
    changed = {tp: off for tp, off in positions.items() if committed.get(tp) != off}
    if changed:
        await consumer.commit(changed)
        committed.update(changed)


async def run_metrics_aggregator(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    writer = ClickHouseWriter()
    await writer.ensure_metrics_schema()
    redis = get_redis()
    agg = MetricsAggregator(settings.metrics_window_s, settings.metrics_allowed_lateness_s)
    committed: dict[Any, int] = {}
    logger.info("metrics-aggregator: started (window %ss, lateness %ss)",
                settings.metrics_window_s, settings.metrics_allowed_lateness_s)
    try:
        while not stop.is_set():
            try:
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                batches = {}
            for tp, msgs in batches.items():
                for msg in msgs:
                    agg.add(tp, msg.offset, msg.value)
            await step(agg, consumer, writer, redis, committed)

        logger.info("metrics-aggregator: draining %d open windows", len(agg.windows))
        await step(agg, consumer, writer, redis, committed, force=True)
    finally:
        await consumer.stop()
        writer.close()
        logger.info("metrics-aggregator: stopped")

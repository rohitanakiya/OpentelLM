"""
anomaly-detector: cost-spike detection against an hourly baseline.

THE RULE
--------
For each (tenant_id, project_id), fire a `cost_spike` when ALL of:

  1. the project had traffic in at least `cost_spike_min_baseline_hours`
     of the 24 complete hours before the current one (no baseline, no alert:
     a brand-new project must not alert on its first busy hour);
  2. spend so far in the current hour >= `cost_spike_min_usd`
     (nobody wants to be paged over 30 cents);
  3. spend so far in the current hour > `cost_spike_multiplier` x baseline,
     where baseline = average spend of the ACTIVE hours among those 24.

Why active hours, not a plain 24-hour average: a project that only gets
traffic during office hours spends nothing for ~16 hours a day. Dividing
its spend by 24 would put the baseline at about a third of a normal busy
hour, so every ordinary working hour would look like a 3x spike. Averaging
over the hours that actually had traffic compares a busy hour with other
busy hours.

The check runs on every event, against the running total of the current
hour, so a runaway loop is flagged as soon as it crosses the threshold
rather than when the hour ends.

(The previous version compared this hour's TOTAL spend against an EMA of
the cost of a SINGLE event. Any project making more than ~3 calls an hour
crossed that line, so it fired every hour for every active project.)

STATE (all in Redis, so restarts and extra replicas need no warm-up)
-----
  hourcost:{tenant}:{project}:{hour}    INCRBYFLOAT per event, TTL 26h
                                        hour = Unix time // 3600, taken from
                                        the event's own timestamp
  anomaly_fired:{tenant}:{project}:{hour}   SET NX dedup, one alert per hour

The baseline for a given (tenant, project, hour) only depends on earlier
hours, so each process computes it once per hour with a single 24-key MGET
and caches it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from config import settings
from kafka_client import make_consumer, publish_anomaly
from pricing import calculate_cost
from redis_client import get_redis

logger = logging.getLogger(__name__)

GROUP_ID = "anomaly-detector"

HOUR_KEY = "hourcost:{tenant}:{project}:{hour}"
DEDUP_KEY = "anomaly_fired:{tenant}:{project}:{hour}"

BASELINE_HOURS = 24
HOUR_KEY_TTL_S = (BASELINE_HOURS + 2) * 3600   # current hour + 24 history + slack
_MAX_FUTURE_SKEW_MS = 5 * 60 * 1000             # tolerate small client clock skew
_MAX_CACHE_ENTRIES = 100_000


# ---------------------------------------------------------------------------
# Pure logic (unit-tested without Redis)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Baseline:
    hourly_usd: float   # average spend of the active hours
    active_hours: int   # hours with spend > 0 among the previous 24


def baseline_from_history(hourly_costs: list[float]) -> Baseline:
    active = [c for c in hourly_costs if c > 0]
    if not active:
        return Baseline(0.0, 0)
    return Baseline(sum(active) / len(active), len(active))


@dataclass(frozen=True)
class SpikeRule:
    multiplier: float = 3.0
    min_baseline_hours: int = 3
    min_cost_usd: float = 1.0

    def is_spike(self, current_hour_usd: float, baseline: Baseline) -> bool:
        if baseline.active_hours < self.min_baseline_hours or baseline.hourly_usd <= 0:
            return False
        if current_hour_usd < self.min_cost_usd:
            return False
        return current_hour_usd > self.multiplier * baseline.hourly_usd


def event_hour(timestamp_ms: Any, now_ms: int) -> int:
    """Hour bucket for an event: its own timestamp when plausible, otherwise
    the current hour (missing, garbage, too old, or from the future)."""
    try:
        ts = int(timestamp_ms)
    except (TypeError, ValueError):
        ts = now_ms
    if ts > now_ms + _MAX_FUTURE_SKEW_MS or ts < now_ms - BASELINE_HOURS * 3600 * 1000:
        ts = now_ms
    return ts // 3_600_000


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
class CostSpikeDetector:
    def __init__(
        self,
        redis: Any,
        rule: SpikeRule,
        *,
        publish: Callable[[dict], Awaitable[None]],
        dedup_ttl_s: int = 3600,
        clock: Callable[[], float] = time.time,
        cost_fn: Callable[[str, int, int], float] = calculate_cost,
    ) -> None:
        self._redis = redis
        self._rule = rule
        self._publish = publish
        self._dedup_ttl_s = dedup_ttl_s
        self._clock = clock
        self._cost_fn = cost_fn
        self._baselines: dict[tuple[str, str, int], Baseline] = {}

    async def process(self, event: dict) -> dict | None:
        """Account one event; publish and return the anomaly if it fires."""
        tenant = event.get("tenant_id")
        project = event.get("project_id")
        if not (tenant and project):
            return None

        cost = self._cost_fn(
            event.get("model") or "unknown",
            int(event.get("prompt_tokens") or 0),
            int(event.get("completion_tokens") or 0),
        )
        if cost <= 0:
            return None

        hour = event_hour(event.get("timestamp_ms"), int(self._clock() * 1000))
        hour_key = HOUR_KEY.format(tenant=tenant, project=project, hour=hour)
        pipe = self._redis.pipeline()
        pipe.incrbyfloat(hour_key, cost)
        pipe.expire(hour_key, HOUR_KEY_TTL_S)
        current = float((await pipe.execute())[0])

        # Cheap pre-check before touching the baseline: below the absolute
        # floor nothing can fire, which is the common case for most events.
        if current < self._rule.min_cost_usd:
            return None

        baseline = await self._baseline(tenant, project, hour)
        if not self._rule.is_spike(current, baseline):
            return None

        dedup_key = DEDUP_KEY.format(tenant=tenant, project=project, hour=hour)
        if not await self._redis.set(dedup_key, b"1", ex=self._dedup_ttl_s, nx=True):
            return None

        multiplier = current / baseline.hourly_usd
        anomaly = {
            "type": "cost_spike",
            "tenant_id": tenant,
            "project_id": project,
            "severity": "critical" if multiplier > 10 else "warning",
            "detected_at": datetime.now(tz=timezone.utc).isoformat(),
            "details": {
                "current_hour_cost_usd": round(current, 6),
                "baseline_hourly_usd": round(baseline.hourly_usd, 6),
                "baseline_hours": baseline.active_hours,
                "multiplier": round(multiplier, 3),
                "hour_start": datetime.fromtimestamp(hour * 3600, tz=timezone.utc).isoformat(),
            },
        }
        await self._publish(anomaly)
        logger.warning(
            "cost_spike fired tenant=%s project=%s hour=$%.4f baseline=$%.4f/h over %dh x%.1f",
            tenant, project, current, baseline.hourly_usd, baseline.active_hours, multiplier,
        )
        return anomaly

    async def _baseline(self, tenant: str, project: str, hour: int) -> Baseline:
        cache_key = (tenant, project, hour)
        cached = self._baselines.get(cache_key)
        if cached is not None:
            return cached
        keys = [
            HOUR_KEY.format(tenant=tenant, project=project, hour=h)
            for h in range(hour - BASELINE_HOURS, hour)
        ]
        raw = await self._redis.mget(keys)
        baseline = baseline_from_history([float(v) if v is not None else 0.0 for v in raw])
        if len(self._baselines) >= _MAX_CACHE_ENTRIES:
            self._baselines.clear()  # entries for past hours are never needed again
        self._baselines[cache_key] = baseline
        return baseline


def detector_from_settings(redis: Any) -> CostSpikeDetector:
    return CostSpikeDetector(
        redis,
        SpikeRule(
            multiplier=settings.cost_spike_multiplier,
            min_baseline_hours=settings.cost_spike_min_baseline_hours,
            min_cost_usd=settings.cost_spike_min_usd,
        ),
        publish=publish_anomaly,
        dedup_ttl_s=settings.anomaly_dedup_ttl_s,
    )


async def run_anomaly_detector(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    detector = detector_from_settings(get_redis())
    logger.info("anomaly-detector: started")

    try:
        while not stop.is_set():
            try:
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                batches = {}

            for _tp, msgs in batches.items():
                for msg in msgs:
                    await detector.process(msg.value)

            if batches:
                await consumer.commit()

    finally:
        await consumer.stop()
        logger.info("anomaly-detector: stopped")

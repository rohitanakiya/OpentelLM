"""
prompt-regression-detector: is a newly deployed prompt/model version worse
than the one before it?

Developers tag deployments with the SDK (`opentelm.set_version("prompt-v2")`),
which puts `deployment_version` on every event.

WHAT COUNTS AS A REGRESSION
---------------------------
The new version is compared with its PREDECESSOR (see "version order") on:

  error rate   one-sided two-proportion z-test, or Fisher's exact test when
               the counts are too small for the normal approximation
  latency      one-sided Mann-Whitney U test on successful requests' latency
               (rank-based, so a few 60-second timeouts don't swamp it)

A finding needs BOTH:
  * statistical significance at this look's share of the alpha budget
    (see "repeated testing" below), and
  * a meaningful size: error rate up >= 2 percentage points, or median
    latency up >= 20% (configurable).
Significance alone isn't enough: with enough traffic, a 0.1-point change
in error rate is "significant" but not worth waking anyone up for. Size
alone isn't enough either: 2 errors in 40 calls vs 0 in 40 is a 5-point
jump that is often just noise.

A version that got BETTER never alerts (the tests are one-sided).

VERSION ORDER
-------------
`regression_versions:{tenant}:{project}` is a sorted set of version ->
first-seen timestamp (ZADD LT, so the earliest timestamp wins even if
events arrive out of order). A version's predecessor is the newest version
first seen before it that has at least `regression_min_samples` requests.
This is correct during a rolling deploy, when v1 and v2 events are
interleaved, and after a rollback (going back to v1 compares v1 with v0,
never with the bad v2).

(The previous implementation stored a single "previous version" pointer,
which new versions overwrote with their own name during their first 50
requests. A clean v1 -> v2 cutover then compared v2 with itself and could
never fire; interleaved traffic compared the versions the wrong way round.)

STATE (Redis, 14-day TTL)
-------------------------
  regression:{t}:{p}:{v}          hash  request_count, error_count
  regression_lat:{t}:{p}:{v}      list  latency_ms of the most recent
                                        successful requests (capped)
  regression_versions:{t}:{p}     zset  version -> first-seen ms
  regression_fired:{t}:{p}:{v}:{metric}   dedup: one alert per version+metric

REPEATED TESTING
----------------
Testing again and again as data arrives ("peeking") inflates false alarms:
with a fixed p < 0.01 checked every 50 requests, about 4% of simulated
no-change deploys alerted at least once. So the detector looks at the new
version only when its request count reaches min_samples x 2^k (50, 100,
200, 400, ...) and gives look k a significance level of alpha / 2^(k+1).
Those levels sum to at most alpha (a union bound), so the chance that a
no-change deploy EVER alerts stays under alpha, however long it runs. Early
looks need overwhelming evidence; later looks have lots of data, so even
their small alpha still has plenty of power for real regressions.
tests/test_regression.py measures the false-alarm rate on simulated
no-change deploys.
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
from redis_client import get_redis
from stats import error_rate_increase_test, mann_whitney_greater, median

logger = logging.getLogger(__name__)

GROUP_ID = "prompt-regression-detector"

STATS_KEY = "regression:{tenant}:{project}:{version}"
LAT_KEY = "regression_lat:{tenant}:{project}:{version}"
VERSIONS_KEY = "regression_versions:{tenant}:{project}"
DEDUP_KEY = "regression_fired:{tenant}:{project}:{version}:{metric}"

_MAX_LOOKBACK = 5  # how many earlier versions to consider as a baseline


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VersionStats:
    version: str
    requests: int
    errors: int
    latencies: list[float]

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


@dataclass(frozen=True)
class RegressionRule:
    min_samples: int = 50
    alpha: float = 0.01          # total false-alarm budget per version, across all looks
    min_error_rate_delta: float = 0.02
    min_latency_increase: float = 0.20

    def look(self, request_count: int) -> int | None:
        """Look number k if `request_count` == min_samples * 2^k, else None."""
        if request_count < self.min_samples or request_count % self.min_samples:
            return None
        ratio = request_count // self.min_samples
        if ratio & (ratio - 1):
            return None  # not a power of two
        return ratio.bit_length() - 1

    def alpha_for_look(self, k: int) -> float:
        return self.alpha / 2 ** (k + 1)


@dataclass(frozen=True)
class Finding:
    metric: str            # "error_rate" | "latency"
    baseline_value: float  # error rate, or median latency in ms
    current_value: float
    change: float          # absolute delta (error rate) or relative change (latency)
    p_value: float
    test: str
    severity: str


def compare_versions(baseline: VersionStats, current: VersionStats,
                     rule: RegressionRule, alpha: float | None = None) -> list[Finding]:
    """`alpha` is this look's significance level (defaults to the whole budget,
    i.e. a single one-off comparison)."""
    alpha = rule.alpha if alpha is None else alpha
    findings: list[Finding] = []
    if baseline.requests < rule.min_samples or current.requests < rule.min_samples:
        return findings

    # Error rate. Check the effect size first: it's free, and the test is not.
    delta = current.error_rate - baseline.error_rate
    if delta >= rule.min_error_rate_delta:
        test = error_rate_increase_test(baseline.errors, baseline.requests,
                                        current.errors, current.requests)
        if test.p_value < alpha:
            findings.append(Finding(
                "error_rate", baseline.error_rate, current.error_rate, delta,
                test.p_value, test.method, "critical" if delta >= 0.10 else "warning",
            ))

    # Latency, over successful requests only (failures are often instant).
    if len(baseline.latencies) >= rule.min_samples and len(current.latencies) >= rule.min_samples:
        base_median, cur_median = median(baseline.latencies), median(current.latencies)
        if base_median > 0:
            change = cur_median / base_median - 1.0
            if change >= rule.min_latency_increase:
                p = mann_whitney_greater(baseline.latencies, current.latencies)
                if p < alpha:
                    findings.append(Finding(
                        "latency", base_median, cur_median, change, p, "mann-whitney-u",
                        "critical" if change >= 1.0 else "warning",
                    ))
    return findings


def _observed_latency_change(baseline: VersionStats, current: VersionStats) -> float:
    b = median(baseline.latencies)
    return (median(current.latencies) / b - 1.0) if b > 0 and current.latencies else 0.0


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
class RegressionDetector:
    def __init__(
        self,
        redis: Any,
        rule: RegressionRule,
        *,
        publish: Callable[[dict], Awaitable[None]],
        latency_samples: int = 1000,
        state_ttl_s: int = 14 * 24 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._rule = rule
        self._publish = publish
        self._latency_samples = latency_samples
        self._ttl = state_ttl_s
        self._clock = clock

    async def process(self, event: dict) -> list[dict]:
        """Record one event; return (and publish) any regressions it reveals."""
        if not isinstance(event, dict):
            return []  # undecodable message
        tenant = event.get("tenant_id")
        project = event.get("project_id")
        version = event.get("deployment_version") or event.get("prompt_version")
        if not (tenant and project and version):
            return []
        version = str(version)
        is_error = event.get("status") in ("error", "timeout")
        try:
            first_seen = int(event.get("timestamp_ms") or self._clock() * 1000)
        except (TypeError, ValueError):
            first_seen = int(self._clock() * 1000)

        k = {"tenant": tenant, "project": project, "version": version}
        stats_key, lat_key = STATS_KEY.format(**k), LAT_KEY.format(**k)
        versions_key = VERSIONS_KEY.format(tenant=tenant, project=project)

        pipe = self._redis.pipeline()
        pipe.hincrby(stats_key, "request_count", 1)
        pipe.hincrby(stats_key, "error_count", 1 if is_error else 0)
        pipe.expire(stats_key, self._ttl)
        pipe.zadd(versions_key, {version: first_seen}, lt=True)
        pipe.expire(versions_key, self._ttl)
        latency = event.get("latency_ms")
        if not is_error and latency is not None:
            pipe.lpush(lat_key, float(latency))
            pipe.ltrim(lat_key, 0, self._latency_samples - 1)
            pipe.expire(lat_key, self._ttl)
        request_count = int((await pipe.execute())[0])

        look = self._rule.look(request_count)
        if look is None:
            return []

        baseline_version = await self._predecessor(tenant, project, version)
        if baseline_version is None:
            return []
        baseline = await self._load(tenant, project, baseline_version)
        current = await self._load(tenant, project, version)

        published = []
        alpha = self._rule.alpha_for_look(look)
        for finding in compare_versions(baseline, current, self._rule, alpha):
            dedup = DEDUP_KEY.format(**k, metric=finding.metric)
            if not await self._redis.set(dedup, b"1", ex=self._ttl, nx=True):
                continue
            anomaly = _anomaly(tenant, project, baseline, current, finding)
            await self._publish(anomaly)
            published.append(anomaly)
            logger.warning(
                "prompt_regression fired tenant=%s project=%s %s -> %s %s %.4g -> %.4g (p=%.2g, %s)",
                tenant, project, baseline.version, version, finding.metric,
                finding.baseline_value, finding.current_value, finding.p_value, finding.test,
            )
        return published

    async def _predecessor(self, tenant: str, project: str, version: str) -> str | None:
        versions_key = VERSIONS_KEY.format(tenant=tenant, project=project)
        score = await self._redis.zscore(versions_key, version)
        if score is None:
            return None
        candidates = await self._redis.zrevrangebyscore(
            versions_key, max=f"({score}", min="-inf", start=0, num=_MAX_LOOKBACK,
        )
        for raw in candidates:
            candidate = raw.decode() if isinstance(raw, bytes) else str(raw)
            count = await self._redis.hget(
                STATS_KEY.format(tenant=tenant, project=project, version=candidate), "request_count",
            )
            if count is not None and int(count) >= self._rule.min_samples:
                return candidate
        return None

    async def _load(self, tenant: str, project: str, version: str) -> VersionStats:
        k = {"tenant": tenant, "project": project, "version": version}
        pipe = self._redis.pipeline()
        pipe.hmget(STATS_KEY.format(**k), "request_count", "error_count")
        pipe.lrange(LAT_KEY.format(**k), 0, -1)
        (requests, errors), latencies = await pipe.execute()
        return VersionStats(
            version=version,
            requests=int(requests or 0),
            errors=int(errors or 0),
            latencies=[float(x) for x in latencies],
        )


def _anomaly(tenant: str, project: str, baseline: VersionStats, current: VersionStats,
             f: Finding) -> dict:
    return {
        "type": "prompt_regression",
        "tenant_id": tenant,
        "project_id": project,
        "severity": f.severity,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "details": {
            "metric": f.metric,
            "current_version": current.version,
            "previous_version": baseline.version,
            "baseline_value": round(f.baseline_value, 6),
            "current_value": round(f.current_value, 6),
            "p_value": f.p_value,
            "test": f.test,
            "n_baseline": baseline.requests,
            "n_current": current.requests,
            # Observed changes on both metrics, for context in the dashboard.
            "error_rate_delta": round(current.error_rate - baseline.error_rate, 4),
            "latency_delta_pct": round(_observed_latency_change(baseline, current), 4),
        },
    }


def detector_from_settings(redis: Any) -> RegressionDetector:
    return RegressionDetector(
        redis,
        RegressionRule(
            min_samples=settings.regression_min_samples,
            alpha=settings.regression_alpha,
            min_error_rate_delta=settings.regression_error_rate_delta,
            min_latency_increase=settings.regression_latency_delta_pct,
        ),
        publish=publish_anomaly,
        latency_samples=settings.regression_latency_samples,
        state_ttl_s=settings.regression_state_ttl_s,
    )


async def run_regression_detector(stop: asyncio.Event) -> None:
    consumer = make_consumer(GROUP_ID)
    await consumer.start()
    detector = detector_from_settings(get_redis())
    logger.info("prompt-regression-detector: started")

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
        logger.info("prompt-regression-detector: stopped")

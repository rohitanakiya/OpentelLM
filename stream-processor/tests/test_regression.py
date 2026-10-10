"""
Prompt-regression detector tests.

The first test is the scenario the previous implementation could never
detect: a clean cutover from v1 to a broken v2.
"""
from __future__ import annotations

import random

import fakeredis
import pytest
from consumers.regression import (
    RegressionDetector,
    RegressionRule,
    VersionStats,
    compare_versions,
)

T0_MS = 1_760_000_000_000
RULE = RegressionRule(min_samples=50, alpha=0.01, min_error_rate_delta=0.02, min_latency_increase=0.20)


class Harness:
    def __init__(self, seed: int = 1, redis=None) -> None:
        self.redis = redis or fakeredis.aioredis.FakeRedis()
        self.rng = random.Random(seed)
        self.ts = T0_MS
        self.published: list[dict] = []
        self.detector = self._new()

    def _new(self) -> RegressionDetector:
        async def publish(a: dict) -> None:
            self.published.append(a)
        return RegressionDetector(self.redis, RULE, publish=publish, clock=lambda: self.ts / 1000)

    def restart(self) -> None:
        self.detector = self._new()

    def event(self, version: str | None, *, error_rate: float, median_ms: float,
              tenant: str = "t1", project: str = "p1") -> dict:
        self.ts += 1000
        is_error = self.rng.random() < error_rate
        return {
            "tenant_id": tenant, "project_id": project, "deployment_version": version,
            "status": "error" if is_error else "success",
            "latency_ms": round(self.rng.lognormvariate(0, 0.4) * median_ms),
            "timestamp_ms": self.ts,
        }

    async def traffic(self, version: str | None, n: int, *, error_rate: float = 0.01,
                      median_ms: float = 800, **kw) -> None:
        for _ in range(n):
            await self.detector.process(self.event(version, error_rate=error_rate,
                                                   median_ms=median_ms, **kw))

    def by_metric(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for a in self.published:
            out.setdefault(a["details"]["metric"], []).append(a)
        return out


# ---------------------------------------------------------------------------
# The old bug
# ---------------------------------------------------------------------------
async def test_clean_cutover_to_broken_version_fires():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    await h.traffic("v2", 300, error_rate=0.15)
    alerts = h.by_metric()["error_rate"]
    assert len(alerts) == 1
    d = alerts[0]["details"]
    assert (d["previous_version"], d["current_version"]) == ("v1", "v2")
    assert d["p_value"] < 0.01 and d["current_value"] > d["baseline_value"]
    assert alerts[0]["severity"] == "critical"            # +10 points or more
    assert alerts[0]["type"] == "prompt_regression"


# ---------------------------------------------------------------------------
# Must stay quiet
# ---------------------------------------------------------------------------
async def test_cutover_with_no_real_change_is_quiet():
    h = Harness(seed=3)
    await h.traffic("v1", 500, error_rate=0.02)
    await h.traffic("v2", 500, error_rate=0.02)
    assert h.published == []


async def test_improvement_is_quiet():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.10, median_ms=1500)
    await h.traffic("v2", 300, error_rate=0.01, median_ms=600)
    assert h.published == []


async def test_small_latency_change_is_quiet_even_if_significant():
    """+10% median latency over thousands of calls is statistically real but
    below the 20% floor: not worth an alert."""
    h = Harness()
    await h.traffic("v1", 1500, median_ms=800)
    await h.traffic("v2", 1500, median_ms=880)
    assert h.published == []


async def test_early_noise_is_quiet():
    """2 errors in v2's first 50 calls vs 0 in v1: a 4-point jump, but nowhere
    near significant (Fisher p ~ 0.25)."""
    h = Harness()
    for _ in range(100):
        await h.detector.process({**h.event("v1", error_rate=0, median_ms=800)})
    for i in range(50):
        e = h.event("v2", error_rate=0, median_ms=800)
        if i in (10, 30):
            e["status"] = "error"
        await h.detector.process(e)
    assert h.published == []


async def test_untagged_traffic_is_ignored():
    h = Harness()
    await h.traffic(None, 300, error_rate=0.5)
    assert h.published == []
    assert await h.redis.keys("regression*") == []


# ---------------------------------------------------------------------------
# Must fire
# ---------------------------------------------------------------------------
async def test_latency_regression_fires_alone():
    h = Harness()
    await h.traffic("v1", 300, median_ms=800)
    await h.traffic("v2", 300, median_ms=1300)      # +60%, same error rate
    by = h.by_metric()
    assert list(by) == ["latency"]
    d = by["latency"][0]["details"]
    assert d["test"] == "mann-whitney-u"
    assert 0.4 < d["latency_delta_pct"] < 0.8
    assert by["latency"][0]["severity"] == "warning"


async def test_both_metrics_can_fire_for_one_version():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01, median_ms=800)
    await h.traffic("v2", 300, error_rate=0.20, median_ms=2000)
    assert set(h.by_metric()) == {"error_rate", "latency"}


async def test_alerts_once_per_version_and_metric():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    await h.traffic("v2", 1000, error_rate=0.15)    # many more comparisons happen
    assert len(h.published) == 1


async def test_restart_keeps_state_and_dedup():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    await h.traffic("v2", 200, error_rate=0.15)
    assert len(h.published) == 1
    h.restart()
    await h.traffic("v2", 200, error_rate=0.15)
    assert len(h.published) == 1


# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------
async def test_rolling_deploy_compares_in_the_right_direction():
    """v1 and v2 events interleaved, as during a rolling deploy."""
    h = Harness()
    await h.traffic("v1", 200, error_rate=0.01)
    for _ in range(400):
        await h.detector.process(h.event("v1", error_rate=0.01, median_ms=800))
        await h.detector.process(h.event("v2", error_rate=0.15, median_ms=800))
    assert len(h.published) == 1
    d = h.published[0]["details"]
    assert (d["previous_version"], d["current_version"]) == ("v1", "v2")


async def test_rollback_does_not_blame_the_old_version():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    await h.traffic("v2", 300, error_rate=0.15)     # fires: v2 is worse than v1
    await h.traffic("v1", 600, error_rate=0.01)     # rolled back
    assert len(h.published) == 1
    assert h.published[0]["details"]["current_version"] == "v2"


async def test_baseline_skips_versions_without_enough_data():
    """v2 was a hotfix that only ran for 10 calls; v3 is compared with v1."""
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    await h.traffic("v2", 10, error_rate=0.01)
    await h.traffic("v3", 300, error_rate=0.15)
    d = h.published[0]["details"]
    assert (d["previous_version"], d["current_version"]) == ("v1", "v3")


async def test_first_seen_order_survives_out_of_order_arrival():
    """A late v1 event (older timestamp) arriving after v2 is first seen must
    not make v1 look newer than v2."""
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01)
    late_v1 = h.event("v1", error_rate=0.01, median_ms=800)
    late_v1["timestamp_ms"] = T0_MS - 3_600_000
    await h.traffic("v2", 10, error_rate=0.15)
    await h.detector.process(late_v1)
    await h.traffic("v2", 290, error_rate=0.15)
    d = h.published[0]["details"]
    assert (d["previous_version"], d["current_version"]) == ("v1", "v2")


async def test_projects_are_isolated():
    h = Harness()
    await h.traffic("v1", 300, error_rate=0.01, project="a")
    await h.traffic("v2", 300, error_rate=0.30, project="b")   # different project, no baseline
    assert h.published == []


# ---------------------------------------------------------------------------
# False-alarm rate under repeated testing ("peeking")
# ---------------------------------------------------------------------------
def _simulate_deploy(rng: random.Random, v2_error_rate_factor: float = 1.0,
                     v2_latency_factor: float = 1.0, max_requests: int = 3200,
                     base_error_rate: float | None = None) -> bool:
    """One simulated deploy, tested on exactly the detector's look schedule.
    Returns True if any look alerts."""
    err = base_error_rate if base_error_rate is not None else rng.choice([0.005, 0.02, 0.05])

    def sample(n, e_rate, lat_factor):
        errors = sum(rng.random() < e_rate for _ in range(n))
        return errors, [rng.lognormvariate(0, 0.5) * 800 * lat_factor for _ in range(n - errors)]

    e1, l1 = sample(2000, err, 1.0)
    base = VersionStats("v1", 2000, e1, l1)
    cur_err, cur_lat, n = 0, [], 0
    while n < max_requests:
        step = RULE.min_samples if n == 0 else n  # grow to the next power-of-two look
        e, lat = sample(step, err * v2_error_rate_factor, v2_latency_factor)
        n, cur_err, cur_lat = n + step, cur_err + e, cur_lat + lat
        k = RULE.look(n)
        assert k is not None
        if compare_versions(base, VersionStats("v2", n, cur_err, cur_lat[-1000:]), RULE,
                            RULE.alpha_for_look(k)):
            return True
    return False


def test_false_alarm_rate_on_no_change_deploys_is_low():
    """400 deploys where v2 is statistically identical to v1, re-tested at
    50, 100, ..., 3200 requests. A fixed p < 0.01 at every 50 requests used
    to alert on ~4% of them."""
    rng = random.Random(12345)
    trials = 400
    alarms = sum(_simulate_deploy(rng) for _ in range(trials))
    # Theory says <= alpha (1%); observed 4/400. Small headroom for the
    # normal approximations, but far below the old ~4%.
    assert alarms / trials <= 0.015, f"{alarms}/{trials} no-change deploys alerted"


def test_still_catches_real_regressions():
    """Power check: the alpha spending must not make real regressions slip by.
    (A 0.5% -> 1.5% error rate is deliberately NOT tested: it's below the
    2-point effect floor, so it never alerts by design.)"""
    rng = random.Random(99)
    caught_err = sum(_simulate_deploy(rng, v2_error_rate_factor=3.0, base_error_rate=0.02)
                     for _ in range(100))   # 2% -> 6%
    caught_lat = sum(_simulate_deploy(rng, v2_latency_factor=1.5) for _ in range(100))  # +50%
    assert caught_err >= 95, caught_err
    assert caught_lat >= 95, caught_lat


@pytest.mark.parametrize("count,look", [(49, None), (50, 0), (75, None), (100, 1), (150, None),
                                        (200, 2), (400, 3), (3200, 6)])
def test_look_schedule(count, look):
    assert RULE.look(count) == look


def test_alpha_budget_sums_to_at_most_alpha():
    assert sum(RULE.alpha_for_look(k) for k in range(60)) <= RULE.alpha


@pytest.mark.parametrize("requests", [0, 49])
def test_no_comparison_below_min_samples(requests):
    base = VersionStats("v1", 500, 5, [800.0] * 495)
    cur = VersionStats("v2", requests, requests, [])
    assert compare_versions(base, cur, RULE) == []

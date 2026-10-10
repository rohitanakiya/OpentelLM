"""
Cost-spike detector tests.

The most important ones are the "no alert" cases. The previous detector
fired every hour for every project with more than ~3 calls/hour, and the
old integration tests only ever checked that an alert DID fire, so nothing
caught it.
"""
from __future__ import annotations

import fakeredis
import pytest
from consumers.anomaly import (
    BASELINE_HOURS,
    Baseline,
    CostSpikeDetector,
    SpikeRule,
    baseline_from_history,
    event_hour,
)

HOUR_MS = 3_600_000
T0_HOUR = 490_000          # an arbitrary hour number (~2025-11)
T0_MS = T0_HOUR * HOUR_MS


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000


def usd_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Test pricing: 1 prompt token = $0.001, so events can state dollars."""
    return prompt_tokens / 1000


def ev(usd: float, ts_ms: int, tenant: str = "t1", project: str = "p1") -> dict:
    return {
        "tenant_id": tenant, "project_id": project, "model": "gpt-4o",
        "prompt_tokens": round(usd * 1000), "completion_tokens": 0,
        "timestamp_ms": ts_ms,
    }


class Harness:
    def __init__(self, rule: SpikeRule | None = None, redis=None) -> None:
        self.redis = redis or fakeredis.aioredis.FakeRedis()
        self.clock = Clock(T0_MS)
        self.published: list[dict] = []
        self.rule = rule or SpikeRule(multiplier=3.0, min_baseline_hours=3, min_cost_usd=1.0)
        self.detector = self._new_detector()

    def _new_detector(self) -> CostSpikeDetector:
        async def publish(anomaly: dict) -> None:
            self.published.append(anomaly)

        return CostSpikeDetector(self.redis, self.rule, publish=publish,
                                 clock=self.clock, cost_fn=usd_cost)

    def restart(self) -> None:
        """Simulate a process restart: new detector, same Redis."""
        self.detector = self._new_detector()

    async def hour_of_traffic(self, hour: int, usd: float, calls: int = 60, **kw) -> None:
        """Spread `usd` over `calls` events inside hour `hour`, advancing the clock."""
        for i in range(calls):
            ts = hour * HOUR_MS + i * (HOUR_MS // calls)
            self.clock.ms = ts
            await self.detector.process(ev(usd / calls, ts, **kw))


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------
def test_baseline_averages_only_active_hours():
    assert baseline_from_history([0, 0, 10, 0, 20, 0]) == Baseline(15.0, 2)
    assert baseline_from_history([0.0] * 24) == Baseline(0.0, 0)
    assert baseline_from_history([]) == Baseline(0.0, 0)


@pytest.mark.parametrize("current,baseline,expected", [
    (31.0, Baseline(10.0, 24), True),    # > 3x
    (30.0, Baseline(10.0, 24), False),   # exactly 3x is not a spike
    (29.0, Baseline(10.0, 24), False),
    (100.0, Baseline(10.0, 2), False),   # not enough history
    (0.9, Baseline(0.1, 24), False),     # 9x, but under the $1 floor
    (1.0, Baseline(0.1, 24), True),      # at the floor and 10x
    (50.0, Baseline(0.0, 0), False),     # no baseline at all
])
def test_spike_rule(current, baseline, expected):
    assert SpikeRule(3.0, 3, 1.0).is_spike(current, baseline) is expected


def test_event_hour_uses_event_timestamp_when_plausible():
    now = T0_MS + 30 * 60 * 1000
    assert event_hour(T0_MS - 2 * HOUR_MS, now) == T0_HOUR - 2      # late event: its own hour
    assert event_hour(now + 60_000, now) == T0_HOUR                  # 1 min clock skew is fine
    for bad in (None, "garbage", now + 10 * 60_000, now - 25 * HOUR_MS):
        assert event_hour(bad, now) == T0_HOUR                       # fall back to now


# ---------------------------------------------------------------------------
# The regression: normal traffic must never alert
# ---------------------------------------------------------------------------
async def test_steady_traffic_never_alerts():
    """30 hours at a constant $5/h, 60 calls an hour. The old EMA detector
    compared $5/hour against ~$0.08/call and fired every single hour."""
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 30):
        await h.hour_of_traffic(hour, usd=5.0)
    assert h.published == []


async def test_noisy_but_normal_traffic_never_alerts():
    """Hour-to-hour variation up to 2x the average is normal, not a spike."""
    h = Harness()
    pattern = [3.0, 6.0, 4.5, 8.0, 5.0, 2.5, 7.0, 4.0]
    for i, hour in enumerate(range(T0_HOUR, T0_HOUR + 48)):
        await h.hour_of_traffic(hour, usd=pattern[i % len(pattern)])
    assert h.published == []


async def test_office_hours_pattern_never_alerts():
    """8 busy hours a day at $10/h, idle the other 16, for three days. A plain
    24-hour average would put the baseline at $3.33/h, so every busy hour
    would be exactly 3x and the first slightly busier one would fire."""
    h = Harness()
    for day in range(3):
        for hour_of_day in range(24):
            if 9 <= hour_of_day < 17:
                await h.hour_of_traffic(T0_HOUR + day * 24 + hour_of_day, usd=10.5)
    assert h.published == []


async def test_new_project_does_not_alert_without_history():
    h = Harness()
    await h.hour_of_traffic(T0_HOUR, usd=500.0)       # huge first hour, no baseline
    await h.hour_of_traffic(T0_HOUR + 1, usd=1.0)
    await h.hour_of_traffic(T0_HOUR + 2, usd=1.0)
    assert h.published == []                          # only 2 hours of history so far


async def test_small_projects_are_not_paged_over_cents():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=0.01)
    await h.hour_of_traffic(T0_HOUR + 24, usd=0.90)   # 90x, but only 90 cents
    assert h.published == []


# ---------------------------------------------------------------------------
# Real spikes
# ---------------------------------------------------------------------------
async def test_real_spike_fires_once_as_soon_as_it_crosses():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=5.0)

    spike_hour = T0_HOUR + 24
    await h.hour_of_traffic(spike_hour, usd=40.0, calls=40)   # $1/call, threshold $15

    assert len(h.published) == 1, "dedup: one alert per project per hour"
    a = h.published[0]
    assert a["type"] == "cost_spike" and a["tenant_id"] == "t1" and a["project_id"] == "p1"
    d = a["details"]
    assert d["baseline_hourly_usd"] == pytest.approx(5.0, rel=0.01)  # token rounding
    assert d["baseline_hours"] == BASELINE_HOURS
    threshold = 3 * d["baseline_hourly_usd"]
    # Fired on the $1 call that crossed the threshold, not at the end of the hour.
    assert threshold < d["current_hour_cost_usd"] <= threshold + 1.0
    assert d["multiplier"] == pytest.approx(d["current_hour_cost_usd"] / d["baseline_hourly_usd"], rel=1e-3)
    assert a["severity"] == "warning"
    assert d["hour_start"].startswith("20")


async def test_huge_spike_is_critical():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=1.0)
    h.clock.ms = (T0_HOUR + 24) * HOUR_MS
    await h.detector.process(ev(50.0, h.clock.ms))           # one $50 call: 50x
    assert [a["severity"] for a in h.published] == ["critical"]


async def test_next_hour_can_alert_again():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=5.0)
    await h.hour_of_traffic(T0_HOUR + 24, usd=20.0)
    await h.hour_of_traffic(T0_HOUR + 25, usd=60.0)
    assert len(h.published) == 2


async def test_restart_keeps_baseline_and_dedup():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=5.0)
    await h.hour_of_traffic(T0_HOUR + 24, usd=30.0, calls=30)   # first half of the spike
    assert len(h.published) == 1

    h.restart()                                                   # baseline cache is gone
    await h.hour_of_traffic(T0_HOUR + 24, usd=30.0, calls=30)   # same hour, more spend
    assert len(h.published) == 1, "dedup lives in Redis, so a restart must not re-alert"

    h.restart()
    await h.hour_of_traffic(T0_HOUR + 25, usd=5.0)
    assert len(h.published) == 1, "baseline lives in Redis, so normal traffic after a restart is quiet"


async def test_projects_and_tenants_are_isolated():
    h = Harness()
    for hour in range(T0_HOUR, T0_HOUR + 24):
        await h.hour_of_traffic(hour, usd=5.0, tenant="t1", project="p1")
        await h.hour_of_traffic(hour, usd=500.0, tenant="t2", project="p1")
    # Tenant 2's big-but-normal hour must not count as tenant 1's spike, or vice versa.
    await h.hour_of_traffic(T0_HOUR + 24, usd=500.0, tenant="t2", project="p1")
    await h.hour_of_traffic(T0_HOUR + 24, usd=5.0, tenant="t1", project="p1")
    assert h.published == []
    await h.hour_of_traffic(T0_HOUR + 24, usd=20.0, tenant="t1", project="p2")  # new project
    assert h.published == []


async def test_ignores_malformed_and_free_events():
    h = Harness()
    assert await h.detector.process({"model": "gpt-4o", "prompt_tokens": 10}) is None
    assert await h.detector.process(ev(0.0, T0_MS)) is None
    assert await h.redis.keys("hourcost:*") == []


async def test_hour_buckets_expire():
    h = Harness()
    await h.detector.process(ev(1.0, T0_MS))
    ttl = await h.redis.ttl(f"hourcost:t1:p1:{T0_HOUR}")
    assert BASELINE_HOURS * 3600 < ttl <= (BASELINE_HOURS + 2) * 3600

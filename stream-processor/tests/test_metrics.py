"""
metrics-aggregator tests: window timing, late events, offset commits, and
(when chdb is installed) the dashboard's ClickHouse queries over the rows.
"""
from __future__ import annotations

import json
import random

import fakeredis
import pytest
from consumers.metrics import MetricsAggregator, step, window_row
from tdigest import TDigest

W = 60           # window length
LATE = 10        # allowed lateness
T0 = 1_800_000_000 - 1_800_000_000 % W   # a window boundary (epoch seconds)


def ev(ts_s: float, latency: float = 100, status: str = "success", model: str = "gpt-4o-mini",
       tenant: str = "t1", project: str = "p1") -> dict:
    return {"tenant_id": tenant, "project_id": project, "model": model, "status": status,
            "timestamp_ms": int(ts_s * 1000), "latency_ms": latency,
            "prompt_tokens": 100, "completion_tokens": 50}


class FakeConsumer:
    def __init__(self) -> None:
        self.commits: list[dict] = []

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))


class FakeWriter:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict] = []
        self.fail = fail

    async def write_metrics(self, rows: list[dict]) -> None:
        if self.fail:
            raise RuntimeError("ClickHouse down")
        self.rows.extend(rows)


# ---------------------------------------------------------------------------
# Window timing
# ---------------------------------------------------------------------------
def test_events_in_one_minute_share_a_window():
    agg = MetricsAggregator(W, LATE)
    for i in range(5):
        agg.add("p0", i, ev(T0 + i * 10, status="error" if i == 0 else "success"), now=T0 + 50)
    assert len(agg.windows) == 1
    (key, w), = agg.windows.items()
    assert key == ("t1", "p1", "gpt-4o-mini", T0)
    assert (w.request_count, w.error_count, w.prompt_tokens, w.completion_tokens) == (5, 1, 500, 250)
    assert w.cost_usd == pytest.approx(5 * (100 * 0.15 + 50 * 0.60) / 1e6)


def test_window_waits_for_allowed_lateness_before_writing():
    agg = MetricsAggregator(W, LATE)
    agg.add("p0", 0, ev(T0 + 5), now=T0 + 6)
    assert agg.pop_due(now=T0 + W + LATE - 0.1) == []        # straggler time
    agg.add("p0", 1, ev(T0 + 59), now=T0 + W + 3)              # arrives 4 s late: still counts
    due = agg.pop_due(now=T0 + W + LATE)
    assert len(due) == 1 and due[0][1].request_count == 2 and not due[0][1].late


def test_event_after_its_window_was_written_becomes_a_late_correction():
    agg = MetricsAggregator(W, LATE)
    agg.add("p0", 0, ev(T0 + 5), now=T0 + 6)
    assert len(agg.pop_due(now=T0 + W + LATE)) == 1
    agg.add("p0", 1, ev(T0 + 30), now=T0 + W + 40)            # 70 s late
    due = agg.pop_due(now=T0 + W + 40)                          # written right away
    assert len(due) == 1
    key, w = due[0]
    assert key[3] == T0 and w.late and w.request_count == 1


def test_different_models_and_projects_get_separate_windows():
    agg = MetricsAggregator(W, LATE)
    agg.add("p0", 0, ev(T0, model="a"), now=T0)
    agg.add("p0", 1, ev(T0, model="b"), now=T0)
    agg.add("p0", 2, ev(T0, project="p2"), now=T0)
    agg.add("p0", 3, ev(T0 + W), now=T0)                        # next minute
    assert len(agg.windows) == 4


def test_malformed_messages_are_skipped_but_not_reread():
    agg = MetricsAggregator(W, LATE)
    agg.add("p0", 0, None, now=T0)                              # undecodable JSON
    agg.add("p0", 1, {"model": "x"}, now=T0)                    # no tenant
    agg.add("p0", 2, ev(T0) | {"prompt_tokens": "lots"}, now=T0)
    assert agg.commit_positions() == {"p0": 2}                  # 0, 1 skipped; 2 is open
    agg.pop_due(force=True)
    assert agg.commit_positions() == {"p0": 3}


# ---------------------------------------------------------------------------
# Offsets: only commit what has been written
# ---------------------------------------------------------------------------
def test_offsets_stop_at_the_oldest_unwritten_event():
    agg = MetricsAggregator(W, LATE)
    agg.add("p0", 10, ev(T0 + 1), now=T0 + 2)
    agg.add("p0", 11, ev(T0 + W + 1), now=T0 + W + 2)          # next window
    agg.add("p1", 5, ev(T0 + W + 3), now=T0 + W + 4)
    assert agg.commit_positions() == {"p0": 10, "p1": 5}
    agg.pop_due(now=T0 + W + LATE)                               # first window written
    assert agg.commit_positions() == {"p0": 11, "p1": 5}
    agg.pop_due(now=T0 + 2 * W + LATE)
    assert agg.commit_positions() == {"p0": 12, "p1": 6}


async def test_step_writes_then_commits():
    agg, consumer, writer = MetricsAggregator(W, LATE), FakeConsumer(), FakeWriter()
    redis = fakeredis.FakeAsyncRedis()
    committed: dict = {}
    for i in range(3):
        agg.add("p0", i, ev(T0 + i), now=T0 + 3)
    await step(agg, consumer, writer, redis, committed, now=T0 + 30)
    assert writer.rows == [] and consumer.commits == [{"p0": 0}]   # nothing written yet
    await step(agg, consumer, writer, redis, committed, now=T0 + W + LATE)
    assert len(writer.rows) == 1 and writer.rows[0]["request_count"] == 3
    assert consumer.commits[-1] == {"p0": 3}
    await step(agg, consumer, writer, redis, committed, now=T0 + W + LATE + 1)
    assert len(consumer.commits) == 2                               # unchanged: no extra commit


async def test_failed_write_commits_nothing():
    agg, consumer = MetricsAggregator(W, LATE), FakeConsumer()
    redis = fakeredis.FakeAsyncRedis()
    agg.add("p0", 7, ev(T0), now=T0)
    committed = {"p0": 7}
    with pytest.raises(RuntimeError):
        await step(agg, consumer, FakeWriter(fail=True), redis, committed, now=T0 + W + LATE)
    assert consumer.commits == [] and committed == {"p0": 7}


async def test_redis_keeps_the_complete_window_not_a_late_correction():
    agg, consumer, writer = MetricsAggregator(W, LATE), FakeConsumer(), FakeWriter()
    redis = fakeredis.FakeAsyncRedis()
    for i in range(4):
        agg.add("p0", i, ev(T0 + i), now=T0 + 5)
    await step(agg, consumer, writer, redis, {}, now=T0 + W + LATE)
    agg.add("p0", 4, ev(T0 + 30), now=T0 + 200)                 # late
    await step(agg, consumer, writer, redis, {}, now=T0 + 200)
    live = json.loads(await redis.get("metrics:t1:p1:gpt-4o-mini"))
    assert live["request_count"] == 4
    assert [r["request_count"] for r in writer.rows] == [4, 1]


# ---------------------------------------------------------------------------
# Rows carry the digest
# ---------------------------------------------------------------------------
def test_row_stores_mergeable_digest():
    agg = MetricsAggregator(W, LATE)
    r = random.Random(1)
    lat = [r.lognormvariate(6, 0.6) for _ in range(3_000)]
    for i, x in enumerate(lat):
        agg.add("p0", i, ev(T0 + i % 60, latency=x), now=T0)
    (key, w), = agg.pop_due(force=True)
    row = window_row(key, w)
    assert sum(row["latency_weights"]) == 3_000
    assert len(row["latency_means"]) == len(row["latency_weights"]) <= 200
    assert row["p50_latency_ms"] <= row["p95_latency_ms"] <= row["p99_latency_ms"]


def test_old_approach_was_noisy():
    # Regression guard for why this changed: the old code sorted a random
    # sample of 1000 latencies. Here, on 50k values, the digest's p99 is far
    # closer to the truth than that sample's typically is.
    r = random.Random(9)
    lat = [r.lognormvariate(6, 0.6) for _ in range(50_000)]
    true_p99 = sorted(lat)[int(0.99 * 49_999)]
    d = TDigest()
    for x in lat:
        d.add(x)
    sample_err = []
    for seed in range(20):
        s = sorted(random.Random(seed).sample(lat, 1000))
        sample_err.append(abs(s[min(int(0.99 * 1000), 999)] - true_p99) / true_p99)
    assert abs(d.quantile(0.99) - true_p99) / true_p99 < sorted(sample_err)[10] / 2


# ---------------------------------------------------------------------------
# The dashboard's ClickHouse queries (embedded ClickHouse; skipped without chdb)
# ---------------------------------------------------------------------------
def test_clickhouse_merges_rows_into_a_true_p99():
    pytest.importorskip("chdb")
    from chdb import session

    r = random.Random(2)
    agg = MetricsAggregator(W, LATE)
    lat = []
    # 30 minutes of traffic, plus late corrections for some minutes.
    for minute in range(30):
        for i in range(r.randint(50, 400)):
            x = r.lognormvariate(6, 0.6) * (3 if minute == 17 else 1)   # one slow minute
            lat.append(x)
            agg.add("p0", len(lat), ev(T0 + minute * W + i % 60, latency=x), now=T0 + minute * W)
    rows = [window_row(k, w) for k, w in agg.pop_due(force=True)]
    for minute in (3, 17):
        late = MetricsAggregator(W, LATE)
        for _ in range(20):
            x = r.lognormvariate(6, 0.6)
            lat.append(x)
            late.add("p0", 0, ev(T0 + minute * W + 1, latency=x), now=T0 + 10_000)
        rows += [window_row(k, w) for k, w in late.pop_due(now=T0 + 10_000)]

    s = session.Session()
    s.query("CREATE DATABASE IF NOT EXISTS opentelm")
    s.query("""CREATE TABLE opentelm.m (window_start DateTime64(3), request_count UInt32,
               p99_latency_ms Float32, latency_means Array(Float32), latency_weights Array(UInt32))
               ENGINE = MergeTree ORDER BY window_start""")
    values = ",".join(
        f"({row['window_start'].timestamp()}, {row['request_count']}, {row['p99_latency_ms']}, "
        f"{row['latency_means']}, {row['latency_weights']})" for row in rows)
    s.query(f"INSERT INTO opentelm.m VALUES {values}")
    out = s.query("""
        SELECT sum(request_count),
               quantileTDigestWeightedArray(0.99)(latency_means, latency_weights),
               avg(p99_latency_ms)
        FROM opentelm.m""", "JSONCompact")
    total, merged_p99, avg_of_p99s = json.loads(out.bytes())["data"][0]

    assert int(total) == len(lat)
    s_lat = sorted(lat)
    true_p99 = s_lat[int(0.99 * (len(s_lat) - 1))]
    assert rank_error_frac(s_lat, merged_p99) <= 0.01
    # The old Overview number was nowhere near it.
    assert abs(avg_of_p99s - true_p99) > abs(merged_p99 - true_p99)


def rank_error_frac(sorted_values: list[float], estimate: float) -> float:
    import bisect
    pos = bisect.bisect_left(sorted_values, estimate) / len(sorted_values)
    return abs(pos - 0.99)

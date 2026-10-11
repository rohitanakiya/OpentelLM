"""
Consumer loop tests with a fake Kafka consumer and a fake ClickHouse writer.

The two bugs these pin down:
  * a quiet topic left events unwritten (timeout only checked on arrival);
  * a dead letter committed the whole consumer position, including healthy
    events that hadn't been written yet.
"""
from __future__ import annotations

import asyncio
import json
from collections import namedtuple

import pytest
from main import Batch, InvalidEvent, consume, enrich

TP = namedtuple("TP", "topic partition")
Msg = namedtuple("Msg", "offset value")
P0 = TP("llm.requests", 0)


def event(i: int, **over) -> bytes:
    e = {
        "tenant_id": "t1", "project_id": "p1", "trace_id": f"tr-{i}", "span_id": f"sp-{i}",
        "model": "gpt-4o-2024-08-06", "provider": "openai", "prompt_tokens": 1000,
        "completion_tokens": 100, "latency_ms": 250, "status": "success",
        "prompt_hash": "a" * 64, "response_hash": "b" * 64, "timestamp_ms": 1_760_000_000_000,
    }
    e.update(over)
    return json.dumps(e).encode()


class FakeKafka:
    """Hands out queued message batches, then nothing (like an idle topic)."""

    def __init__(self, batches: list[dict]) -> None:
        self.batches = list(batches)
        self.commits: list[dict] = []
        self.polls = 0

    async def getmany(self, timeout_ms: int = 0, max_records: int | None = None):
        self.polls += 1
        if self.batches:
            return self.batches.pop(0)
        await asyncio.sleep(timeout_ms / 1000)
        return {}

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))


class FakeWriter:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict] = []
        self.dlq: list[tuple] = []
        self.fail = fail

    def write_batch(self, rows):
        if self.fail:
            raise ConnectionError("clickhouse down")
        self.rows.extend(rows)

    def write_dlq(self, payload, reason, offset):
        if self.fail:
            raise ConnectionError("clickhouse down")
        self.dlq.append((payload, reason, offset))


async def run_until(kafka, writer, stop_after_s: float, batch_size=100, timeout_s=0.2):
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(stop_after_s, stop.set)
    await consume(kafka, writer, stop, batch_size=batch_size, timeout_s=timeout_s, poll_ms=20)


# ---------------------------------------------------------------------------
async def test_single_event_on_quiet_topic_is_written_by_timeout():
    """Regression: one event and then silence used to sit in memory forever."""
    kafka = FakeKafka([{P0: [Msg(0, event(0))]}])
    writer = FakeWriter()
    stop = asyncio.Event()
    task = asyncio.create_task(consume(kafka, writer, stop, batch_size=100, timeout_s=0.2, poll_ms=20))
    await asyncio.sleep(0.6)                 # no shutdown: only the timer can flush it
    assert [r["trace_id"] for r in writer.rows] == ["tr-0"]
    assert kafka.commits == [{P0: 1}]
    stop.set()
    await task


async def test_full_batch_flushes_immediately():
    kafka = FakeKafka([{P0: [Msg(i, event(i)) for i in range(5)]}])
    writer = FakeWriter()
    await run_until(kafka, writer, 0.05, batch_size=5, timeout_s=60)
    assert len(writer.rows) == 5
    assert kafka.commits[0] == {P0: 5}


async def test_dead_letter_does_not_commit_unwritten_events():
    """Regression: a bad message used to trigger consumer.commit() for the
    whole position while good events were still unwritten in the batch."""
    kafka = FakeKafka([{P0: [Msg(0, event(0)), Msg(1, b"{not json"), Msg(2, event(2))]}])
    writer = FakeWriter(fail=True)
    with pytest.raises(ConnectionError):
        await run_until(kafka, writer, 1.0)
    assert kafka.commits == []               # nothing committed => all 3 are replayed


async def test_dead_letters_are_written_and_committed_with_the_batch():
    kafka = FakeKafka([{P0: [Msg(0, event(0)), Msg(1, b"{not json"), Msg(2, event(2, model=""))]}])
    writer = FakeWriter()
    await run_until(kafka, writer, 0.5)
    assert [r["trace_id"] for r in writer.rows] == ["tr-0"]
    assert [d[2] for d in writer.dlq] == [1, 2]
    assert "invalid JSON" in writer.dlq[0][1] and "model" in writer.dlq[1][1]
    assert kafka.commits == [{P0: 3}]


async def test_failed_write_commits_nothing():
    kafka = FakeKafka([{P0: [Msg(0, event(0))]}])
    with pytest.raises(ConnectionError):
        await run_until(kafka, FakeWriter(fail=True), 1.0)
    assert kafka.commits == []


async def test_offsets_tracked_per_partition():
    p1 = TP("llm.requests", 1)
    kafka = FakeKafka([{P0: [Msg(10, event(1))], p1: [Msg(4, event(2)), Msg(5, event(3))]}])
    writer = FakeWriter()
    await run_until(kafka, writer, 0.5)
    assert kafka.commits == [{P0: 11, p1: 6}]


async def test_shutdown_drains_the_batch():
    kafka = FakeKafka([{P0: [Msg(0, event(0))]}])
    writer = FakeWriter()
    await run_until(kafka, writer, 0.05, timeout_s=60)   # stop before the timer fires
    assert len(writer.rows) == 1 and kafka.commits == [{P0: 1}]


# ---------------------------------------------------------------------------
def test_enrich_adds_cost_from_dated_model_id():
    e = enrich(event(0))
    # gpt-4o: $2.50 / $10 per 1M -> 1000 in + 100 out
    assert e["cost_usd"] == pytest.approx(0.0025 + 0.001)


def test_enrich_fills_missing_timestamp():
    e = enrich(event(0, timestamp_ms=None), now_ms=123)
    assert e["timestamp_ms"] == 123


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"\xff\xfe", event(0, prompt_tokens="many")])
def test_enrich_rejects_garbage(raw):
    with pytest.raises(InvalidEvent):
        enrich(raw)


def test_batch_due():
    b = Batch()
    assert not b.due(10, 1.0)
    b.add(P0, 0, event(0))
    assert not b.due(10, 1.0, now=b.started + 0.5)
    assert b.due(10, 1.0, now=b.started + 1.0)
    assert b.due(1, 100.0)

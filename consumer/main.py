"""
Raw-event consumer: llm.requests (Kafka) -> opentelm.llm_events (ClickHouse).

DELIVERY GUARANTEES
-------------------
At-least-once. Offsets are committed only AFTER the events (and any
dead-lettered messages) up to that offset have been written to ClickHouse.
If a write fails, the process exits without committing; on restart Kafka
replays from the last commit, so nothing is lost (a crash between write and
commit can produce duplicates, which is the usual at-least-once trade-off).

Two bugs in the previous version broke that:
  * The dead-letter path called consumer.commit() for the whole consumer
    position, which also committed the healthy events still waiting in the
    in-memory batch. A crash before the next flush lost them for good.
  * The batch timeout was only checked when a new message arrived
    (`async for msg in consumer`), so in a quiet system (a local demo, a
    low-traffic tenant) a handful of events could sit unwritten forever.
    The loop now polls with a timeout, so the 5-second flush happens even
    when no new messages arrive.

Messages that aren't valid JSON or lack required fields go to
opentelm.dlq_events instead of crashing the consumer (previously a single
malformed message made the JSON deserializer raise on every restart).
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from config import settings
from pricing import calculate_cost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("consumer")

REQUIRED_FIELDS = ("tenant_id", "project_id", "trace_id", "model", "provider",
                   "prompt_tokens", "completion_tokens", "latency_ms", "status",
                   "prompt_hash", "response_hash")


class InvalidEvent(ValueError):
    pass


def enrich(raw: bytes | str, now_ms: int | None = None) -> dict[str, Any]:
    """Decode and validate one Kafka message, and add cost_usd."""
    try:
        event = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise InvalidEvent(f"invalid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise InvalidEvent("event is not a JSON object")
    missing = [f for f in REQUIRED_FIELDS if event.get(f) in (None, "")]
    if missing:
        raise InvalidEvent(f"missing fields: {', '.join(missing)}")
    try:
        prompt_tokens = int(event["prompt_tokens"])
        completion_tokens = int(event["completion_tokens"])
    except (TypeError, ValueError) as exc:
        raise InvalidEvent(f"bad token counts: {exc}") from exc
    if not event.get("timestamp_ms"):
        # ingest-api always sets this; never write 1970-01-01 if it didn't.
        event["timestamp_ms"] = now_ms if now_ms is not None else int(time.time() * 1000)
    event["cost_usd"] = calculate_cost(str(event["model"]), prompt_tokens, completion_tokens)
    return event


@dataclass
class Batch:
    """Events and dead letters waiting to be written, plus the offsets that
    become safe to commit once they are."""
    events: list[dict] = field(default_factory=list)
    dead_letters: list[tuple[str, str, int]] = field(default_factory=list)
    offsets: dict[Any, int] = field(default_factory=dict)   # TopicPartition -> next offset
    started: float = field(default_factory=time.monotonic)

    def add(self, tp: Any, offset: int, raw: bytes) -> None:
        if not self.events and not self.dead_letters:
            self.started = time.monotonic()
        try:
            self.events.append(enrich(raw))
        except InvalidEvent as exc:
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            self.dead_letters.append((text, str(exc), offset))
            logger.warning("dead-lettered offset=%d: %s", offset, exc)
        self.offsets[tp] = offset + 1

    def __len__(self) -> int:
        return len(self.events) + len(self.dead_letters)

    def due(self, batch_size: int, timeout_s: float, now: float | None = None) -> bool:
        if not len(self):
            return False
        now = time.monotonic() if now is None else now
        return len(self) >= batch_size or now - self.started >= timeout_s


async def flush(batch: Batch, writer: Any, consumer: Any) -> None:
    """Write, THEN commit exactly the offsets covered by what was written."""
    if not len(batch):
        return
    loop = asyncio.get_running_loop()
    # clickhouse-driver is synchronous; keep it off the event loop so Kafka
    # heartbeats keep flowing during a slow insert.
    if batch.events:
        await loop.run_in_executor(None, writer.write_batch, batch.events)
    for payload, reason, offset in batch.dead_letters:
        await loop.run_in_executor(None, writer.write_dlq, payload, reason, offset)
    await consumer.commit(dict(batch.offsets))
    logger.info("wrote %d events (%d dead-lettered), committed offsets",
                len(batch.events), len(batch.dead_letters))


async def consume(consumer: Any, writer: Any, stop: asyncio.Event,
                  batch_size: int, timeout_s: float, poll_ms: int = 500) -> None:
    batch = Batch()
    while not stop.is_set():
        records = await consumer.getmany(timeout_ms=poll_ms, max_records=batch_size)
        for tp, messages in records.items():
            for msg in messages:
                batch.add(tp, msg.offset, msg.value)
        if batch.due(batch_size, timeout_s):
            await flush(batch, writer, consumer)
            batch = Batch()
    await flush(batch, writer, consumer)  # drain on shutdown


async def run_consumer() -> None:
    from aiokafka import AIOKafkaConsumer

    from clickhouse_writer import ClickHouseWriter

    writer = ClickHouseWriter()
    consumer = AIOKafkaConsumer(
        settings.kafka_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,   # we commit after each successful write
        # No value_deserializer: decoding happens in enrich(), so a malformed
        # message is dead-lettered instead of crashing the poll loop.
    )
    await consumer.start()
    logger.info("consumer started on topic %s", settings.kafka_topic)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await consume(consumer, writer, stop, settings.batch_size, settings.batch_timeout_s)
    finally:
        await consumer.stop()
        writer.close()


if __name__ == "__main__":
    asyncio.run(run_consumer())

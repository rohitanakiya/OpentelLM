"""
aiokafka consumer / producer factory for the stream-processor.

Each consumer group passed to `make_consumer` must be unique per logical
worker. We rely on Kafka's group-coordination so that adding a second
replica of stream-processor splits partitions between them.
"""
from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from config import settings

logger = logging.getLogger(__name__)


def _safe_json(raw: bytes):
    """Decode a message, or return None if it isn't valid JSON. Raising here
    would make getmany() fail on the same message forever, taking down every
    consumer in this process. The raw-events consumer dead-letters such
    messages; here they're just skipped."""
    try:
        return json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError, AttributeError):
        logger.warning("skipping a message that isn't valid JSON (%d bytes)", len(raw or b""))
        return None


def make_consumer(group_id: str, topic: str | None = None) -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        topic or settings.kafka_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="latest",          # only process events going forward
        enable_auto_commit=False,            # commit explicitly after side-effects
        value_deserializer=_safe_json,
        # Pull large batches to keep throughput up — each consumer is a single
        # asyncio task and is easily the bottleneck if Kafka is fast.
        max_poll_records=500,
        session_timeout_ms=30_000,
        heartbeat_interval_ms=10_000,
    )


_producer: AIOKafkaProducer | None = None


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
            acks=1,
        )
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish_anomaly(event: dict) -> None:
    producer = await get_producer()
    # Partition by tenant_id so per-tenant anomaly streams stay ordered
    await producer.send(
        settings.anomalies_topic,
        value=event,
        key=event["tenant_id"].encode(),
    )

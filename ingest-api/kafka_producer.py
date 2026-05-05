from __future__ import annotations
import json
import logging
from aiokafka import AIOKafkaProducer

from config import settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
            compression_type="gzip",
            # Durability: wait for leader ack only (acks=1).
            # We use a DLQ on the consumer side for failures; losing an event
            # at the broker is acceptable for telemetry data.
            acks=1,
            request_timeout_ms=5_000,
        )
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None


async def publish_events(events: list[dict]) -> None:
    producer = await get_producer()
    for event in events:
        # Partition by tenant_id so all events for a tenant land on the same partition,
        # preserving per-tenant ordering without a global lock.
        await producer.send(
            settings.kafka_topic,
            value=event,
            key=event["tenant_id"].encode(),
        )

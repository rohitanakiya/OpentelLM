from __future__ import annotations
import asyncio
import json
import logging
import time

from aiokafka import AIOKafkaConsumer

from clickhouse_writer import ClickHouseWriter
from config import settings
from pricing import compute_cost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run_consumer() -> None:
    writer = ClickHouseWriter()
    consumer = AIOKafkaConsumer(
        settings.kafka_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,  # manual commit after successful write
        value_deserializer=lambda b: json.loads(b.decode()),
    )
    await consumer.start()
    logger.info("Consumer started, listening on topic '%s'", settings.kafka_topic)

    batch: list[dict] = []
    last_flush = time.monotonic()

    try:
        async for msg in consumer:
            raw = msg.value
            offset = msg.offset

            try:
                enriched = _enrich(raw)
                batch.append(enriched)
            except Exception as exc:
                logger.warning("Enrichment failed offset=%d: %s", offset, exc)
                try:
                    writer.write_dlq(json.dumps(raw), str(exc), offset)
                except Exception:
                    logger.exception("DLQ write failed")
                await consumer.commit()
                continue

            age = time.monotonic() - last_flush
            if len(batch) >= settings.batch_size or age >= settings.batch_timeout_s:
                _flush(writer, batch)
                batch.clear()
                last_flush = time.monotonic()
                await consumer.commit()

    finally:
        # Flush remaining events on shutdown
        if batch:
            _flush(writer, batch)
            await consumer.commit()
        await consumer.stop()


def _enrich(raw: dict) -> dict:
    raw["cost_usd"] = compute_cost(
        raw["model"], raw["prompt_tokens"], raw["completion_tokens"]
    )
    return raw


def _flush(writer: ClickHouseWriter, batch: list[dict]) -> None:
    try:
        writer.write_batch(batch)
    except Exception:
        logger.exception("ClickHouse write failed for batch of %d", len(batch))
        raise


if __name__ == "__main__":
    asyncio.run(run_consumer())

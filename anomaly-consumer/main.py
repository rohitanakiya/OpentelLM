"""
anomaly-consumer: persists anomaly events.

Consumes from llm.anomalies and:
  1. Writes a row to ClickHouse llm_anomalies.
  2. LPUSHes the full event JSON onto Redis list  alerts:{tenant_id},
     then LTRIMs the list to 100 items so it stays bounded.

The dashboard polls (or, in Phase 4, websocket-streams) the Redis list
to render the live alert feed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime

from aiokafka import AIOKafkaConsumer
from clickhouse_driver import Client
import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("anomaly-consumer")


KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ANOMALIES_TOPIC = os.environ.get("ANOMALIES_TOPIC", "llm.anomalies")
GROUP_ID        = os.environ.get("ANOMALY_CONSUMER_GROUP", "anomaly-persister")
CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_DB   = os.environ.get("CLICKHOUSE_DB", "opentelm")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_SECURE = os.environ.get("CLICKHOUSE_SECURE", "").lower() in ("1", "true", "yes")
REDIS_URL       = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
ALERT_LIST_CAP  = 100


_INSERT_SQL = """
INSERT INTO opentelm.llm_anomalies (
    tenant_id, project_id, anomaly_type, severity, details, detected_at
) VALUES
"""


def _to_row(event: dict) -> dict:
    return {
        "tenant_id":    event["tenant_id"],
        "project_id":   event["project_id"],
        "anomaly_type": event["type"],
        "severity":     event.get("severity", "warning"),
        "details":      json.dumps(event.get("details", {})),
        "detected_at":  datetime.fromisoformat(event["detected_at"].replace("Z", "+00:00")),
    }


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop(*_):
        if not stop.is_set():
            logger.info("shutdown signal received, draining...")
            stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, _stop)

    consumer = AIOKafkaConsumer(
        ANOMALIES_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode()),
    )
    await consumer.start()

    ch = Client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT, database=CLICKHOUSE_DB,
                user=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD, secure=CLICKHOUSE_SECURE)
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)

    logger.info("anomaly-consumer: started")
    try:
        while not stop.is_set():
            try:
                batches = await asyncio.wait_for(consumer.getmany(timeout_ms=1_000), timeout=2.0)
            except asyncio.TimeoutError:
                continue

            for tp, msgs in batches.items():
                rows = []
                pipe = redis.pipeline()
                for msg in msgs:
                    event = msg.value
                    try:
                        rows.append(_to_row(event))
                    except Exception:
                        logger.exception("malformed anomaly event, dropping: %s", event)
                        continue

                    list_key = f"alerts:{event['tenant_id']}"
                    pipe.lpush(list_key, json.dumps(event).encode())
                    pipe.ltrim(list_key, 0, ALERT_LIST_CAP - 1)

                if rows:
                    # ClickHouse insert is sync; offload to executor so it doesn't
                    # stall the asyncio loop while a fat batch flushes.
                    await loop.run_in_executor(None, ch.execute, _INSERT_SQL, rows)
                    await pipe.execute()
                    logger.info("persisted %d anomalies", len(rows))

            await consumer.commit()

    finally:
        await consumer.stop()
        await redis.close()
        ch.disconnect()
        logger.info("anomaly-consumer: clean exit")


if __name__ == "__main__":
    asyncio.run(run())

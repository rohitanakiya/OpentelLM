"""
Healthcheck used by the Dockerfile.

We consider the stream-processor healthy if the metrics-aggregator
consumer group is registered with Kafka. Even if it has no committed
offsets yet (fresh deploy with no traffic), the group description being
discoverable proves the consumer thread is running.
"""
from __future__ import annotations

import os
import sys

from aiokafka.admin import AIOKafkaAdminClient
import asyncio


async def _check() -> int:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        groups = await admin.list_consumer_groups()
        names = {g[0] for g in groups}
        if "metrics-aggregator" in names:
            return 0
        return 1
    finally:
        await admin.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(_check()))
    except Exception as exc:
        print(f"healthcheck error: {exc}", file=sys.stderr)
        sys.exit(1)

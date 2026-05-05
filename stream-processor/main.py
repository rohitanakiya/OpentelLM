"""
stream-processor entry point.

Boots three asyncio.Tasks — one per consumer group — sharing a single
process. They are independent: a stall in any one doesn't block the
others, and Kafka's consumer-group coordination handles partition
rebalancing per group.

SIGINT/SIGTERM trip a single asyncio.Event that every consumer polls.
On signal, each consumer drains its current batch, flushes any in-memory
state (metrics-aggregator flushes open windows), commits offsets, and
returns. We then shut the producer down and exit 0.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from consumers.anomaly import run_anomaly_detector
from consumers.metrics import run_metrics_aggregator
from consumers.regression import run_regression_detector
from kafka_client import stop_producer
from redis_client import close_redis, init_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("stream-processor")


async def main() -> None:
    await init_redis()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        if not stop.is_set():
            logger.info("shutdown signal received, draining consumers...")
            stop.set()

    # SIGTERM is what Docker / Kubernetes send. SIGINT is Ctrl-C in dev.
    # asyncio signal handlers, NOT atexit — atexit can't run async code.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows: signal handlers are limited; fall back to default behaviour.
            signal.signal(sig, lambda *_: _request_stop())

    tasks = [
        asyncio.create_task(run_metrics_aggregator(stop), name="metrics-aggregator"),
        asyncio.create_task(run_anomaly_detector(stop),   name="anomaly-detector"),
        asyncio.create_task(run_regression_detector(stop), name="prompt-regression-detector"),
    ]

    # If any single consumer crashes, log it and trip stop so the others
    # can drain cleanly. We prefer fail-loud: a crash means a code bug
    # that needs a redeploy, not silent partial degradation.
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in done:
        if task.exception():
            logger.exception(
                "consumer %s crashed: %s", task.get_name(), task.exception(),
            )
            stop.set()

    # Wait for the rest to drain.
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    await stop_producer()
    await close_redis()
    logger.info("stream-processor: clean exit")


if __name__ == "__main__":
    asyncio.run(main())

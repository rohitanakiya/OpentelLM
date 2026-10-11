"""
ClickHouse writer for the stream-processor.

Per the spec, we use the synchronous clickhouse-driver wrapped in
run_in_executor — the async client has poor error handling and the sync
driver is battle-tested.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from clickhouse_driver import Client

from config import settings

logger = logging.getLogger(__name__)


_METRICS_INSERT_SQL = """
INSERT INTO opentelm.llm_metrics_1m (
    tenant_id, project_id, model, window_start,
    request_count, error_count,
    prompt_tokens, completion_tokens, cost_usd,
    p50_latency_ms, p95_latency_ms, p99_latency_ms
) VALUES
"""


_ANOMALY_INSERT_SQL = """
INSERT INTO opentelm.llm_anomalies (
    tenant_id, project_id, anomaly_type, severity, details, detected_at
) VALUES
"""


class ClickHouseWriter:
    def __init__(self) -> None:
        self._client = Client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            database=settings.clickhouse_db,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
            secure=settings.clickhouse_secure,
        )
        self._loop = asyncio.get_event_loop()

    async def write_metrics(self, rows: list[dict]) -> None:
        if not rows:
            return
        await self._loop.run_in_executor(None, self._sync_write_metrics, rows)

    def _sync_write_metrics(self, rows: list[dict]) -> None:
        try:
            self._client.execute(_METRICS_INSERT_SQL, rows)
            logger.info("Wrote %d metric windows to ClickHouse", len(rows))
        except Exception:
            logger.exception("ClickHouse metrics write failed for %d rows", len(rows))
            raise

    async def write_anomaly(self, row: dict) -> None:
        await self._loop.run_in_executor(None, self._sync_write_anomaly, row)

    def _sync_write_anomaly(self, row: dict) -> None:
        try:
            self._client.execute(_ANOMALY_INSERT_SQL, [row])
            logger.info(
                "Wrote anomaly type=%s tenant=%s",
                row["anomaly_type"], row["tenant_id"],
            )
        except Exception:
            logger.exception("ClickHouse anomaly write failed")
            raise

    def close(self) -> None:
        self._client.disconnect()


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)

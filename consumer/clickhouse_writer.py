from __future__ import annotations
import logging
from datetime import datetime, timezone

from clickhouse_driver import Client

from config import settings

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO opentelm.llm_events (
    tenant_id, project_id, trace_id, span_id,
    model, provider,
    prompt_tokens, completion_tokens, total_tokens,
    latency_ms, cost_usd,
    status, error_code, error_message,
    prompt_hash, response_hash,
    sdk_version, deployment_version, tags, timestamp
) VALUES
"""

_DLQ_SQL = """
INSERT INTO opentelm.dlq_events (raw_payload, failure_reason, kafka_offset)
VALUES
"""


class ClickHouseWriter:
    def __init__(self):
        self._client = Client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            database=settings.clickhouse_db,
        )

    def write_batch(self, rows: list[dict]) -> None:
        if not rows:
            return
        data = []
        for r in rows:
            ts_ms = r.get("timestamp_ms") or 0
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            data.append({
                "tenant_id":          r["tenant_id"],
                "project_id":         r["project_id"],
                "trace_id":           r["trace_id"],
                "span_id":            r.get("span_id", ""),
                "model":              r["model"],
                "provider":           r["provider"],
                "prompt_tokens":      r["prompt_tokens"],
                "completion_tokens":  r["completion_tokens"],
                "total_tokens":       r["prompt_tokens"] + r["completion_tokens"],
                "latency_ms":         r["latency_ms"],
                "cost_usd":           r["cost_usd"],
                "status":             r["status"],
                "error_code":         r.get("error_code"),
                "error_message":      r.get("error_message"),
                "prompt_hash":        r["prompt_hash"],
                "response_hash":      r["response_hash"],
                "sdk_version":        r.get("sdk_version", "unknown"),
                "deployment_version": r.get("deployment_version"),
                "tags":               r.get("tags", {}),
                "timestamp":          ts,
            })
        self._client.execute(_INSERT_SQL, data)
        logger.info("Wrote %d events to ClickHouse", len(data))

    def write_dlq(self, payload: str, reason: str, offset: int) -> None:
        self._client.execute(_DLQ_SQL, [{"raw_payload": payload, "failure_reason": reason, "kafka_offset": offset}])

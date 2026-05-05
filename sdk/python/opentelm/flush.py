from __future__ import annotations
import asyncio
import hashlib
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .client import OpenTelLM

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL_S = 2.0
_MAX_QUEUE = 500


class AsyncFlusher:
    """
    Background flush loop that batches events and sends them to the ingest API.

    Runs in a daemon thread with its own event loop so it never blocks the
    developer's application. Events are dropped (with a warning) if the queue
    fills up — telemetry must never impact the app's availability.
    """

    def __init__(self, client: "OpenTelLM"):
        self._client = client
        self._queue: deque[dict] = deque(maxlen=_MAX_QUEUE)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="opentelm-flush")
        self._thread.start()

    def enqueue(self, event: dict) -> None:
        if len(self._queue) >= _MAX_QUEUE:
            logger.warning("OpenTelLM queue full — dropping event")
            return
        self._queue.append(event)

    def _run(self) -> None:
        self._loop.run_until_complete(self._flush_loop())

    async def _flush_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as http:
            while True:
                await asyncio.sleep(_FLUSH_INTERVAL_S)
                if not self._queue:
                    continue
                batch = []
                while self._queue and len(batch) < 100:
                    batch.append(self._queue.popleft())
                await self._send(http, batch)

    async def _send(self, http: httpx.AsyncClient, batch: list[dict]) -> None:
        try:
            resp = await http.post(
                f"{self._client.endpoint}/v1/ingest",
                json={"events": batch},
                headers={"Authorization": f"Bearer {self._client.api_key}"},
            )
            if resp.status_code not in (200, 202):
                logger.warning("Ingest API returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Failed to flush events: %s", exc)

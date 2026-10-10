"""
Background event flusher.

Design rules:
  * The caller's thread only ever does a lock + deque.append. No I/O,
    no blocking, no exceptions.
  * A single daemon thread batches events and POSTs them to the ingest API.
  * The queue is bounded. When it is full the OLDEST event is dropped:
    telemetry must never grow memory without bound or slow the app down.
  * On interpreter exit (atexit) the remaining queue is flushed with a short
    timeout, so short-lived scripts and CLI tools do not lose their events.

Only the standard library is used for HTTP, so the SDK adds no runtime
dependencies and cannot conflict with the httpx/httpx2 version pinned by
the openai / anthropic packages.
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Callable

from ._version import SDK_VERSION

logger = logging.getLogger("opentelm")

DEFAULT_FLUSH_INTERVAL_S = 2.0
DEFAULT_MAX_QUEUE = 1000
DEFAULT_BATCH_SIZE = 100  # ingest-api accepts up to 500 per request
DEFAULT_TIMEOUT_S = 5.0
_RETRY_BACKOFF_S = (0.5, 1.0)
_WARN_EVERY_S = 60.0

Sender = Callable[[bytes, float], int]  # (JSON body, timeout seconds) -> HTTP status


class _RateLimitedLogger:
    """Log a given warning at most once per interval, so an unreachable
    ingest endpoint cannot flood the application's logs."""

    def __init__(self, interval_s: float = _WARN_EVERY_S) -> None:
        self._interval = interval_s
        self._last: dict[str, float] = {}

    def warning(self, key: str, msg: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last.get(key, -1e9) >= self._interval:
            self._last[key] = now
            logger.warning(msg, *args)


class Flusher:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        max_queue: int = DEFAULT_MAX_QUEUE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sender: Sender | None = None,
        register_atexit: bool = True,
    ) -> None:
        self._url = _ingest_url(endpoint)
        self._api_key = api_key
        self._interval = flush_interval_s
        self._batch_size = max(1, min(batch_size, 500))
        self._timeout = timeout_s
        self._send_fn: Sender = sender or self._http_send

        self._queue: deque[dict] = deque(maxlen=max_queue)
        self._lock = threading.Lock()        # guards the queue
        self._send_lock = threading.Lock()   # one drain at a time
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._warn = _RateLimitedLogger()

        self.dropped = 0   # events dropped because the queue was full
        self.failed = 0    # events dropped after failed delivery
        self.sent = 0      # events accepted by the ingest API

        self._thread = threading.Thread(
            target=self._run, name="opentelm-flush", daemon=True,
        )
        self._thread.start()
        if register_atexit:
            atexit.register(self.shutdown)

    # ------------------------------------------------------------------
    # Caller-side API
    # ------------------------------------------------------------------
    def enqueue(self, event: dict) -> None:
        if self._stopped.is_set():
            return
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1  # deque(maxlen) evicts the oldest on append
                self._warn.warning("queue_full", "opentelm: event queue full, dropping oldest events")
            self._queue.append(event)
            full_batch = len(self._queue) >= self._batch_size
        if full_batch:
            self._wake.set()

    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def flush(self, timeout_s: float | None = None) -> None:
        """Synchronously send everything queued so far. Safe to call from
        any thread, e.g. at the end of a serverless handler."""
        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self._timeout * 2)
        self._drain(deadline)

    def shutdown(self, timeout_s: float = 2.0) -> None:
        """Stop the background thread and flush what is left. Idempotent."""
        if self._stopped.is_set():
            return
        deadline = time.monotonic() + timeout_s
        self._stopped.set()
        self._wake.set()
        self._thread.join(timeout=timeout_s)
        self._drain(deadline)
        try:
            atexit.unregister(self.shutdown)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stopped.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stopped.is_set():
                break
            try:
                self._drain(None)
            except Exception:  # never let the thread die
                logger.debug("opentelm: flush loop error", exc_info=True)

    def _take_batch(self) -> list[dict]:
        with self._lock:
            n = min(len(self._queue), self._batch_size)
            return [self._queue.popleft() for _ in range(n)]

    def _drain(self, deadline: float | None) -> None:
        if deadline is None:
            acquired = self._send_lock.acquire()
        else:
            acquired = self._send_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if not acquired:
            return
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    return
                batch = self._take_batch()
                if not batch:
                    return
                if not self._deliver(batch, deadline):
                    return
        finally:
            self._send_lock.release()

    def _requeue(self, batch: list[dict]) -> None:
        with self._lock:
            room = self._queue.maxlen - len(self._queue)
            keep = batch[-room:] if room > 0 else []
            self.dropped += len(batch) - len(keep)
            self._queue.extendleft(reversed(keep))

    def _deliver(self, batch: list[dict], deadline: float | None) -> bool:
        """Send one batch with retries. Returns False when the caller should
        stop draining (shutdown requested or deadline reached)."""
        body = json.dumps({"events": batch}, default=str).encode("utf-8")
        attempts = len(_RETRY_BACKOFF_S) + 1
        for attempt in range(attempts):
            timeout = self._timeout
            if deadline is not None:
                timeout = min(timeout, max(0.1, deadline - time.monotonic()))
            try:
                status: int | None = self._send_fn(body, timeout)
            except Exception as exc:
                status = None
                self._warn.warning(
                    "send_error", "opentelm: could not reach ingest API at %s: %s", self._url, exc,
                )
            if status is not None and 200 <= status < 300:
                self.sent += len(batch)
                return True
            if status is not None and 400 <= status < 500 and status != 429:
                # Bad API key or invalid payload: retrying cannot help.
                self._warn.warning(
                    f"http_{status}", "opentelm: ingest API rejected %d events with HTTP %d",
                    len(batch), status,
                )
                self.failed += len(batch)
                return True
            if attempt == attempts - 1:
                break
            wait = _RETRY_BACKOFF_S[attempt]
            if deadline is not None:
                if time.monotonic() + wait >= deadline:
                    break
                time.sleep(wait)
            elif self._stopped.wait(wait):
                # Shutdown started mid-retry: hand the batch to the final drain.
                self._requeue(batch)
                return False
        self.failed += len(batch)
        return deadline is None or time.monotonic() < deadline

    def _http_send(self, body: bytes, timeout: float) -> int:
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"opentelm-python/{SDK_VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code


def _ingest_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/ingest"):
        return endpoint
    return f"{endpoint}/v1/ingest"

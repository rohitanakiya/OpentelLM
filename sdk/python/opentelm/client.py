from __future__ import annotations

import hashlib
import logging
import random
import time
import uuid
from typing import Any

from ._version import SDK_VERSION
from .flush import Flusher, Sender
from .pii import scrub_pii

logger = logging.getLogger("opentelm")

# Must match the Literal accepted by ingest-api/models.py.
PROVIDERS = frozenset({"openai", "anthropic", "google", "cohere", "other"})
STATUSES = frozenset({"success", "error", "timeout"})
_MAX_ERROR_MESSAGE = 1000


def _new_id() -> str:
    # Trace IDs only need to be unique, not unpredictable; getrandbits avoids
    # the os.urandom syscall that uuid4() makes on every call.
    return str(uuid.UUID(int=random.getrandbits(128), version=4))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class OpenTelLM:
    def __init__(
        self,
        api_key: str,
        endpoint: str = "http://localhost:8000",
        default_tags: dict[str, str] | None = None,
        *,
        scrub_pii: bool = True,
        flush_interval_s: float = 2.0,
        max_queue: int = 1000,
        sender: Sender | None = None,
        register_atexit: bool = True,
    ):
        if not api_key:
            raise ValueError("opentelm: api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.sdk_version = SDK_VERSION
        self.deployment_version: str | None = None
        self.default_tags: dict[str, str] = {str(k): str(v) for k, v in (default_tags or {}).items()}
        self.scrub_pii = scrub_pii
        self.flusher = Flusher(
            self.endpoint,
            api_key,
            flush_interval_s=flush_interval_s,
            max_queue=max_queue,
            sender=sender,
            register_atexit=register_atexit,
        )
        self._instrumented = False

    # ------------------------------------------------------------------
    # Instrumentation
    # ------------------------------------------------------------------
    def instrument(self) -> None:
        if self._instrumented:
            return
        from .monkey_patch import instrument_all
        instrument_all(self)
        self._instrumented = True

    def uninstrument(self) -> None:
        from .monkey_patch import uninstrument_all
        uninstrument_all()
        self._instrumented = False

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _hash(self, text: str) -> str:
        return _sha256(scrub_pii(text) if self.scrub_pii else text)

    def build_event(
        self,
        *,
        provider: str,
        model: str,
        prompt_text: str,
        response_text: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str = "success",
        error_code: str | None = None,
        error_message: str | None = None,
        timestamp_ms: int | None = None,
        extra_tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if error_message is not None:
            error_message = str(error_message)
            if self.scrub_pii:
                error_message = scrub_pii(error_message)
            error_message = error_message[:_MAX_ERROR_MESSAGE]
        tags = dict(self.default_tags)
        if extra_tags:
            tags.update({str(k): str(v) for k, v in extra_tags.items()})
        return {
            "trace_id": _new_id(),
            "span_id": _new_id(),
            "model": str(model or "unknown"),
            "provider": provider if provider in PROVIDERS else "other",
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "latency_ms": max(0, int(latency_ms or 0)),
            "status": status if status in STATUSES else "error",
            "error_code": None if error_code is None else str(error_code)[:100],
            "error_message": error_message,
            "prompt_hash": self._hash(prompt_text or ""),
            "response_hash": self._hash(response_text or ""),
            "sdk_version": self.sdk_version,
            "deployment_version": self.deployment_version,
            "tags": tags,
            "timestamp_ms": int(timestamp_ms if timestamp_ms is not None else time.time() * 1000),
        }

    def record(self, **fields: Any) -> None:
        """Build and queue an event. Never raises."""
        try:
            self.flusher.enqueue(self.build_event(**fields))
        except Exception:
            logger.debug("opentelm: failed to record event", exc_info=True)

    def track(
        self,
        *,
        model: str,
        provider: str,
        prompt: str,
        response: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str = "success",
        error_code: str | None = None,
        error_message: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Manually record a call to a provider the SDK does not auto-patch."""
        self.record(
            provider=provider,
            model=model,
            prompt_text=prompt,
            response_text=response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            extra_tags=tags,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def flush(self, timeout_s: float | None = None) -> None:
        self.flusher.flush(timeout_s)

    def shutdown(self, timeout_s: float = 2.0) -> None:
        self.flusher.shutdown(timeout_s)

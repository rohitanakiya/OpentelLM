"""
OpenTelLM Python SDK.

    import opentelm
    opentelm.init(api_key="opentelm_live_...")
    # every openai / anthropic call from here on is traced

Configuration can also come from the environment:
    OPENTELM_API_KEY, OPENTELM_ENDPOINT
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ._version import SDK_VERSION
from .client import OpenTelLM
from .pii import scrub_pii

__version__ = SDK_VERSION
__all__ = [
    "OpenTelLM", "init", "shutdown", "flush", "track",
    "tag_version", "set_version", "get_version", "get_client", "scrub_pii",
]

logger = logging.getLogger("opentelm")
_client: OpenTelLM | None = None


def init(
    api_key: str | None = None,
    endpoint: str | None = None,
    **kwargs: Any,
) -> OpenTelLM:
    """Initialize the SDK and auto-instrument openai / anthropic.

    Call once at startup. It can run before or after the openai / anthropic
    clients are created: the patch is applied to their classes, so existing
    client instances are covered too. Calling init() again returns the
    existing client unchanged.

    Keyword options: default_tags, scrub_pii (default True),
    flush_interval_s, max_queue.
    """
    global _client
    if _client is not None:
        logger.warning("opentelm.init() called more than once; keeping the first configuration")
        return _client
    api_key = api_key or os.environ.get("OPENTELM_API_KEY")
    if not api_key:
        raise ValueError("opentelm.init(): pass api_key= or set OPENTELM_API_KEY")
    endpoint = endpoint or os.environ.get("OPENTELM_ENDPOINT") or "http://localhost:8000"
    client = OpenTelLM(api_key=api_key, endpoint=endpoint, **kwargs)
    client.instrument()
    _client = client
    return client


def shutdown(timeout_s: float = 2.0) -> None:
    """Remove the patches and flush queued events. Mostly useful in tests;
    normal processes are flushed automatically at exit."""
    global _client
    if _client is None:
        return
    client, _client = _client, None
    client.uninstrument()
    client.shutdown(timeout_s)


def flush(timeout_s: float | None = None) -> None:
    """Send queued events now, e.g. before a serverless handler returns."""
    if _client is not None:
        _client.flush(timeout_s)


def track(**fields: Any) -> None:
    """Manually record a call to a provider the SDK does not auto-patch.
    See OpenTelLM.track for the fields."""
    if _client is None:
        raise RuntimeError("opentelm: call init() before track()")
    _client.track(**fields)


def tag_version(version: str | None) -> None:
    """Tag subsequent events with a deployment / prompt version, used by
    prompt-regression detection. Pass None to clear."""
    if _client is not None:
        _client.deployment_version = version


def set_version(version: str | None) -> None:
    """Alias of tag_version(), matching the Node.js SDK's setVersion()."""
    tag_version(version)


def get_version() -> str | None:
    return _client.deployment_version if _client else None


def get_client() -> OpenTelLM | None:
    return _client

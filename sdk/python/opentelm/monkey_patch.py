"""
Monkey-patching strategy:

OpenAI SDK  — wrap openai.resources.chat.completions.Completions.create
              and its async counterpart.
Anthropic   — wrap anthropic.resources.messages.Messages.create
              and its async counterpart.

We wrap at the class method level rather than replacing the module-level
`openai.chat.completions.create` shorthand, because the shorthand is just
a reference and gets re-bound when the client is instantiated.

The wrapper:
  1. Records wall-clock start time.
  2. Calls through to the original method.
  3. On return, computes latency, extracts token counts, hashes prompt/response.
  4. Enqueues the event without blocking — the caller sees no added latency
     beyond a dict allocation and a deque.append().
"""
from __future__ import annotations
import hashlib
import time
import uuid
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import OpenTelLM


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
def _patch_openai(client: "OpenTelLM") -> None:
    try:
        from openai.resources.chat.completions import Completions
    except ImportError:
        return  # openai not installed — skip silently

    original_create = Completions.create
    original_acreate = getattr(Completions, "acreate", None)  # older SDK versions

    @wraps(original_create)
    def patched_create(self_completions, *args, **kwargs):
        start = time.monotonic()
        try:
            response = original_create(self_completions, *args, **kwargs)
            _record_openai(client, kwargs, response, start, "success")
            return response
        except Exception as exc:
            _record_openai(client, kwargs, None, start, "error", str(exc))
            raise

    Completions.create = patched_create

    if original_acreate:
        @wraps(original_acreate)
        async def patched_acreate(self_completions, *args, **kwargs):
            start = time.monotonic()
            try:
                response = await original_acreate(self_completions, *args, **kwargs)
                _record_openai(client, kwargs, response, start, "success")
                return response
            except Exception as exc:
                _record_openai(client, kwargs, None, start, "error", str(exc))
                raise
        Completions.acreate = patched_acreate


def _record_openai(client, kwargs, response, start, status, error_msg=None):
    latency_ms = int((time.monotonic() - start) * 1000)
    model = kwargs.get("model", "unknown")
    messages = kwargs.get("messages", [])
    prompt_text = " ".join(m.get("content", "") for m in messages if isinstance(m, dict))

    prompt_tokens = completion_tokens = 0
    response_text = ""
    if response is not None:
        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        choices = getattr(response, "choices", [])
        if choices:
            msg = getattr(choices[0], "message", None)
            response_text = getattr(msg, "content", "") or ""

    client.flusher.enqueue({
        "trace_id": str(uuid.uuid4()),
        "span_id": str(uuid.uuid4()),
        "model": model,
        "provider": "openai",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
        "status": status,
        "error_message": error_msg,
        "prompt_hash": _sha256(prompt_text),
        "response_hash": _sha256(response_text),
        "sdk_version": client.sdk_version,
        "deployment_version": client.deployment_version,
        "tags": client.default_tags,
        "timestamp_ms": int(time.time() * 1000),
    })


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
def _patch_anthropic(client: "OpenTelLM") -> None:
    try:
        from anthropic.resources.messages import Messages
    except ImportError:
        return

    original_create = Messages.create

    @wraps(original_create)
    def patched_create(self_messages, *args, **kwargs):
        start = time.monotonic()
        try:
            response = original_create(self_messages, *args, **kwargs)
            _record_anthropic(client, kwargs, response, start, "success")
            return response
        except Exception as exc:
            _record_anthropic(client, kwargs, None, start, "error", str(exc))
            raise

    Messages.create = patched_create

    # Async
    original_async = getattr(Messages, "acreate", None) or getattr(Messages, "create_async", None)
    if original_async:
        @wraps(original_async)
        async def patched_async(self_messages, *args, **kwargs):
            start = time.monotonic()
            try:
                response = await original_async(self_messages, *args, **kwargs)
                _record_anthropic(client, kwargs, response, start, "success")
                return response
            except Exception as exc:
                _record_anthropic(client, kwargs, None, start, "error", str(exc))
                raise
        setattr(Messages, original_async.__name__, patched_async)


def _record_anthropic(client, kwargs, response, start, status, error_msg=None):
    latency_ms = int((time.monotonic() - start) * 1000)
    model = kwargs.get("model", "unknown")
    messages = kwargs.get("messages", [])
    prompt_text = " ".join(
        m.get("content", "") if isinstance(m, dict) else str(m)
        for m in messages
    )

    prompt_tokens = completion_tokens = 0
    response_text = ""
    if response is not None:
        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "input_tokens", 0) or 0
            completion_tokens = getattr(usage, "output_tokens", 0) or 0
        content = getattr(response, "content", [])
        if content:
            response_text = getattr(content[0], "text", "") or ""

    client.flusher.enqueue({
        "trace_id": str(uuid.uuid4()),
        "span_id": str(uuid.uuid4()),
        "model": model,
        "provider": "anthropic",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
        "status": status,
        "error_message": error_msg,
        "prompt_hash": _sha256(prompt_text),
        "response_hash": _sha256(response_text),
        "sdk_version": client.sdk_version,
        "deployment_version": client.deployment_version,
        "tags": client.default_tags,
        "timestamp_ms": int(time.time() * 1000),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def instrument_all(client: "OpenTelLM") -> None:
    _patch_openai(client)
    _patch_anthropic(client)

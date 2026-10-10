"""
Test harness: real openai / anthropic clients talking to an in-memory HTTP
transport, so the SDK is exercised against the providers' actual classes,
response models and SSE parsers, with no network access.
"""
from __future__ import annotations

import importlib
import json
from typing import Any, Callable

import pytest

import opentelm

def _http_module():
    """Newer openai / anthropic releases use httpx2, older ones httpx. Use
    whichever the installed openai is built on (both may be installed)."""
    try:
        import openai
        base = next(c for c in openai.DefaultHttpxClient.__mro__ if c.__name__ == "Client")
        return importlib.import_module(base.__module__.split(".")[0])
    except Exception:
        try:
            return importlib.import_module("httpx2")
        except ImportError:
            return importlib.import_module("httpx")


httpx2 = _http_module()


# ---------------------------------------------------------------------------
# Canned provider responses
# ---------------------------------------------------------------------------
def openai_completion(content: str | None = "Paris", tool_calls: list | None = None,
                      prompt_tokens: int = 12, completion_tokens: int = 3) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0,
        "model": "gpt-4o-2024-08-06",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop", "logprobs": None}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


def openai_chunks(pieces: list[str], usage: dict | None = None) -> list[dict]:
    base = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4o"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}
        for p in pieces
    ]
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    if usage is not None:
        chunks.append({**base, "choices": [], "usage": usage})
    return chunks


def anthropic_message(text: str = "Paris", input_tokens: int = 10, output_tokens: int = 2,
                      cache_read: int = 0) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 0},
    }


def anthropic_events(pieces: list[str], input_tokens: int = 10,
                     output_tokens: int = 4) -> list[tuple[str, dict]]:
    msg = anthropic_message("", input_tokens, 1)
    msg["content"] = []
    events: list[tuple[str, dict]] = [("message_start", {"type": "message_start", "message": msg})]
    events.append(("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}))
    for p in pieces:
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": p}}))
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    events.append(("message_delta", {"type": "message_delta",
                                     "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                     "usage": {"output_tokens": output_tokens}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return events


def responses_result(text: str = "Paris") -> dict:
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-4.1",
        "status": "completed", "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    }


def sse(chunks: list[dict]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def sse_named(events: list[tuple[str, dict]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
class FakeProvider:
    """Queue of responses served in order; records the requests it saw."""

    def __init__(self) -> None:
        self.responses: list[Callable[[Any], Any]] = []
        self.requests: list[Any] = []

    def json(self, payload: dict, status: int = 200) -> "FakeProvider":
        self.responses.append(lambda req: httpx2.Response(status, json=payload))
        return self

    def stream(self, body: bytes) -> "FakeProvider":
        self.responses.append(lambda req: httpx2.Response(
            200, content=body, headers={"content-type": "text/event-stream"}))
        return self

    def error(self, exc: Exception) -> "FakeProvider":
        def raise_(req):
            raise exc
        self.responses.append(raise_)
        return self

    def handler(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeProvider: no response queued")
        return self.responses.pop(0)(request)

    def transport(self):
        return httpx2.MockTransport(self.handler)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def openai_client(provider):
    openai = pytest.importorskip("openai")
    return openai.OpenAI(api_key="sk-test", max_retries=0,
                         http_client=httpx2.Client(transport=provider.transport()))


@pytest.fixture
def async_openai_client(provider):
    openai = pytest.importorskip("openai")
    return openai.AsyncOpenAI(api_key="sk-test", max_retries=0,
                              http_client=httpx2.AsyncClient(transport=provider.transport()))


@pytest.fixture
def anthropic_client(provider):
    anthropic = pytest.importorskip("anthropic")
    return anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                               http_client=httpx2.Client(transport=provider.transport()))


@pytest.fixture
def async_anthropic_client(provider):
    anthropic = pytest.importorskip("anthropic")
    return anthropic.AsyncAnthropic(api_key="sk-ant-test", max_retries=0,
                                    http_client=httpx2.AsyncClient(transport=provider.transport()))


# ---------------------------------------------------------------------------
# SDK under test
# ---------------------------------------------------------------------------
class Sink:
    """Stands in for the ingest API: captures every batch the flusher sends."""

    def __init__(self) -> None:
        self.batches: list[list[dict]] = []
        self.status = 202

    def __call__(self, body: bytes, timeout: float) -> int:
        self.batches.append(json.loads(body)["events"])
        return self.status

    def events(self) -> list[dict]:
        opentelm.flush(timeout_s=2)
        return [e for batch in self.batches for e in batch]


@pytest.fixture
def sink():
    sink = Sink()
    opentelm.init(api_key="opentelm_live_test", sender=sink,
                  flush_interval_s=60, register_atexit=False)
    yield sink
    opentelm.shutdown()


def sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()

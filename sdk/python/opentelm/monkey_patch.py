"""
Auto-instrumentation for the openai and anthropic Python SDKs.

What gets patched (sync and async variants of each):

  openai     chat.completions.create / .parse     (.stream() goes through create)
             responses.create / .parse            (.stream() goes through create)
  anthropic  messages.create
             messages.stream()                    (the helper bypasses create; we
                                                   hook its manager's __enter__)

We patch the resource CLASSES, so every client instance -- including ones
created before init() -- is covered, and the developer changes nothing.

Safety contract (the whole point of this module):

  1. The wrapper calls the original method exactly once, with the original
     arguments, and returns exactly what it returned -- same object, same type.
  2. Exceptions from the original propagate unchanged.
  3. Nothing the SDK does while recording (text extraction, hashing, PII
     scrubbing, queueing) can raise into the developer's code: every
     recording step runs inside _safe().
  4. The hot path adds only CPU work (a few microseconds for hashing plus a
     deque append). Network I/O happens on the flusher's background thread.

Streaming: the SDKs' Stream/AsyncStream objects pull chunks from a private
generator stored in `_iterator`. We swap that generator for one that
observes each chunk and records the event when the stream finishes, fails,
or is closed early. The Stream object itself is untouched, so isinstance
checks, `.response`, `.close()` and context-manager use all keep working.
If a future SDK version drops `_iterator`, streaming calls are simply not
traced (logged once at DEBUG) -- the call itself is unaffected.
"""
from __future__ import annotations

import functools
import logging
import math
import time
from typing import TYPE_CHECKING, Any, Callable

from ._text import (
    anthropic_response_text,
    get,
    messages_to_text,
    openai_response_text,
    responses_api_response_text,
)

if TYPE_CHECKING:
    from .client import OpenTelLM

logger = logging.getLogger("opentelm")

_client: "OpenTelLM | None" = None
_originals: list[tuple[type, str, Any]] = []
_logged_once: set[str] = set()


def _debug_once(key: str, msg: str, *args: Any) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.debug(msg, *args)


def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a recording step; swallow and log anything it raises."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.debug("opentelm: recording step %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return None


def _approx_tokens(text: str) -> int:
    # ~4 characters per token for English text; only used when the provider
    # did not report usage (e.g. OpenAI streams without include_usage).
    return math.ceil(len(text) / 4) if text else 0


# ---------------------------------------------------------------------------
# Provider adapters
#
# An adapter knows how to read one API's request kwargs, response object and
# stream chunks. The wrappers below are provider-agnostic.
# ---------------------------------------------------------------------------
class _StreamState:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.failed = False

    def feed(self, chunk: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def response_text(self) -> str:
        return "".join(self.text)


class _OpenAIChatStream(_StreamState):
    def feed(self, chunk: Any) -> None:
        usage = get(chunk, "usage")
        if usage is not None:  # final chunk when stream_options.include_usage=True
            self.prompt_tokens = get(usage, "prompt_tokens")
            self.completion_tokens = get(usage, "completion_tokens")
        for choice in get(chunk, "choices") or []:
            delta = get(choice, "delta")
            content = get(delta, "content")
            if isinstance(content, str):
                self.text.append(content)
            for call in get(delta, "tool_calls") or []:
                fn = get(call, "function")
                name = get(fn, "name")
                if name:
                    self.text.append(f"[tool_call:{name}] ")
                args = get(fn, "arguments")
                if isinstance(args, str):
                    self.text.append(args)


class _OpenAIResponsesStream(_StreamState):
    def feed(self, event: Any) -> None:
        etype = get(event, "type") or ""
        if etype in ("response.output_text.delta", "response.function_call_arguments.delta"):
            delta = get(event, "delta")
            if isinstance(delta, str):
                self.text.append(delta)
        elif etype in ("response.completed", "response.incomplete", "response.failed"):
            usage = get(get(event, "response"), "usage")
            if usage is not None:
                self.prompt_tokens = get(usage, "input_tokens")
                self.completion_tokens = get(usage, "output_tokens")
            if etype == "response.failed":
                self.failed = True


def _anthropic_input_tokens(usage: Any) -> int:
    # Cached prompt tokens are reported separately; count them so apps that
    # rely on prompt caching do not appear to send near-empty prompts.
    return sum(
        int(get(usage, k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )


class _AnthropicStream(_StreamState):
    def feed(self, event: Any) -> None:
        etype = get(event, "type")
        if etype == "message_start":
            usage = get(get(event, "message"), "usage")
            if usage is not None:
                self.prompt_tokens = _anthropic_input_tokens(usage)
                self.completion_tokens = get(usage, "output_tokens")
        elif etype == "content_block_start":
            block = get(event, "content_block")
            if get(block, "type") == "tool_use":
                self.text.append(f"[tool_use:{get(block, 'name', '')}] ")
        elif etype == "content_block_delta":
            delta = get(event, "delta")
            dtype = get(delta, "type")
            if dtype == "text_delta":
                self.text.append(get(delta, "text") or "")
            elif dtype == "input_json_delta":
                self.text.append(get(delta, "partial_json") or "")
        elif etype == "message_delta":
            usage = get(event, "usage")
            if get(usage, "output_tokens") is not None:
                self.completion_tokens = get(usage, "output_tokens")
            if get(usage, "input_tokens") is not None:  # newer API versions
                self.prompt_tokens = max(self.prompt_tokens or 0, _anthropic_input_tokens(usage))


class _Adapter:
    provider: str
    state_cls: type[_StreamState]

    def __init__(self) -> None:
        self._stream_types: tuple[type, ...] | None = None
        self._timeout_types: tuple[type, ...] | None = None

    # -- request -----------------------------------------------------------
    def prompt_text(self, kwargs: dict) -> str:
        raise NotImplementedError

    # -- response ----------------------------------------------------------
    def is_response(self, result: Any) -> bool:
        """False for with_raw_response / with_streaming_response wrappers,
        whose parsed body we must not force."""
        raise NotImplementedError

    def read_response(self, result: Any) -> tuple[str, int, int]:
        raise NotImplementedError

    # -- streams / errors --------------------------------------------------
    def _load_types(self) -> None:
        raise NotImplementedError

    def is_stream(self, result: Any) -> bool:
        if self._stream_types is None:
            self._load_types()
        return bool(self._stream_types) and isinstance(result, self._stream_types)

    def is_timeout(self, exc: BaseException) -> bool:
        if self._timeout_types is None:
            self._load_types()
        return isinstance(exc, (TimeoutError, *(self._timeout_types or ())))


class _OpenAIBase(_Adapter):
    provider = "openai"

    def _load_types(self) -> None:
        try:
            import openai
            self._stream_types = (openai.Stream, openai.AsyncStream)
            self._timeout_types = (openai.APITimeoutError,)
        except Exception:
            self._stream_types, self._timeout_types = (), ()


class _OpenAIChat(_OpenAIBase):
    state_cls = _OpenAIChatStream

    def prompt_text(self, kwargs: dict) -> str:
        return messages_to_text(kwargs.get("messages"))

    def is_response(self, result: Any) -> bool:
        return hasattr(result, "choices")

    def read_response(self, result: Any) -> tuple[str, int, int]:
        usage = get(result, "usage")
        return (
            openai_response_text(result),
            int(get(usage, "prompt_tokens") or 0),
            int(get(usage, "completion_tokens") or 0),
        )


class _OpenAIResponses(_OpenAIBase):
    state_cls = _OpenAIResponsesStream

    def prompt_text(self, kwargs: dict) -> str:
        return messages_to_text(kwargs.get("input"), system=kwargs.get("instructions"))

    def is_response(self, result: Any) -> bool:
        return hasattr(result, "output") and hasattr(result, "usage")

    def read_response(self, result: Any) -> tuple[str, int, int]:
        usage = get(result, "usage")
        return (
            responses_api_response_text(result),
            int(get(usage, "input_tokens") or 0),
            int(get(usage, "output_tokens") or 0),
        )


class _Anthropic(_Adapter):
    provider = "anthropic"
    state_cls = _AnthropicStream

    def _load_types(self) -> None:
        try:
            import anthropic
            self._stream_types = (anthropic.Stream, anthropic.AsyncStream)
            self._timeout_types = (anthropic.APITimeoutError,)
        except Exception:
            self._stream_types, self._timeout_types = (), ()

    def prompt_text(self, kwargs: dict) -> str:
        return messages_to_text(kwargs.get("messages"), system=kwargs.get("system"))

    def is_response(self, result: Any) -> bool:
        return hasattr(result, "content") and hasattr(result, "usage")

    def read_response(self, result: Any) -> tuple[str, int, int]:
        usage = get(result, "usage")
        return (
            anthropic_response_text(result),
            _anthropic_input_tokens(usage),
            int(get(usage, "output_tokens") or 0),
        )


OPENAI_CHAT = _OpenAIChat()
OPENAI_RESPONSES = _OpenAIResponses()
ANTHROPIC = _Anthropic()


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
class _Call:
    """Everything known about one in-flight LLM call."""

    __slots__ = ("client", "adapter", "kwargs", "start", "start_wall_ms")

    def __init__(self, client: "OpenTelLM", adapter: _Adapter, kwargs: dict) -> None:
        self.client = client
        self.adapter = adapter
        self.kwargs = kwargs
        self.start = time.perf_counter()
        self.start_wall_ms = int(time.time() * 1000)

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)

    def record(self, *, response_text: str, prompt_tokens: int, completion_tokens: int,
               status: str = "success", exc: BaseException | None = None,
               extra_tags: dict[str, str] | None = None) -> None:
        error_code = error_message = None
        if exc is not None:
            status = "timeout" if self.adapter.is_timeout(exc) else "error"
            code = getattr(exc, "status_code", None)
            error_code = str(code) if code is not None else type(exc).__name__
            error_message = str(exc)
        self.client.record(
            provider=self.adapter.provider,
            model=self.kwargs.get("model") or "unknown",
            prompt_text=_safe(self.adapter.prompt_text, self.kwargs) or "",
            response_text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=self.elapsed_ms(),
            status=status,
            error_code=error_code,
            error_message=error_message,
            timestamp_ms=self.start_wall_ms,
            extra_tags=extra_tags,
        )


def _record_error(call: _Call, exc: BaseException) -> None:
    call.record(response_text="", prompt_tokens=0, completion_tokens=0, exc=exc)


def _handle_result(call: _Call, result: Any) -> None:
    adapter = call.adapter
    if adapter.is_stream(result):
        _instrument_stream(call, result)
    elif adapter.is_response(result):
        text, pt, ct = adapter.read_response(result)
        call.record(response_text=text, prompt_tokens=pt, completion_tokens=ct)
    else:
        _debug_once(f"skip:{type(result).__name__}",
                    "opentelm: not tracing call returning %s (raw/streaming response wrapper)",
                    type(result).__name__)


def _finish_stream(call: _Call, state: _StreamState, *, exc: BaseException | None,
                   completed: bool, ttft_ms: int | None) -> None:
    text = state.response_text()
    tags: dict[str, str] = {}
    pt, ct = state.prompt_tokens, state.completion_tokens
    if pt is None or ct is None:
        tags["otlm.tokens_estimated"] = "true"
        if pt is None:
            pt = _approx_tokens(_safe(call.adapter.prompt_text, call.kwargs) or "")
        if ct is None:
            ct = _approx_tokens(text)
    if ttft_ms is not None:
        tags["otlm.ttft_ms"] = str(ttft_ms)
    if not completed and exc is None:
        tags["otlm.stream_incomplete"] = "true"
    call.record(
        response_text=text, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0),
        status="error" if state.failed else "success", exc=exc, extra_tags=tags,
    )


def _instrument_stream(call: _Call, stream: Any, close_owner: Any = None) -> None:
    inner = getattr(stream, "_iterator", None)
    if inner is None:
        _debug_once("no_iterator", "opentelm: %s has no _iterator; streaming call not traced",
                    type(stream).__name__)
        return
    state = call.adapter.state_cls()
    progress = {"done": False, "completed": False, "ttft_ms": None}

    def finish(exc: BaseException | None) -> None:
        if not progress["done"]:
            progress["done"] = True
            _safe(_finish_stream, call, state, exc=exc,
                  completed=progress["completed"], ttft_ms=progress["ttft_ms"])

    def observe(item: Any) -> None:
        if progress["ttft_ms"] is None:
            progress["ttft_ms"] = call.elapsed_ms()
        state.feed(item)

    is_async = hasattr(inner, "__anext__")
    if is_async:
        async def observed_async():
            try:
                async for item in inner:
                    _safe(observe, item)
                    yield item
                progress["completed"] = True
            except Exception as exc:
                finish(exc)
                raise
            finally:
                finish(None)

        stream._iterator = observed_async()
    else:
        def observed():
            try:
                for item in inner:
                    _safe(observe, item)
                    yield item
                progress["completed"] = True
            except Exception as exc:
                finish(exc)
                raise
            finally:
                finish(None)

        stream._iterator = observed()

    # A consumer that breaks out early usually closes the stream (directly or
    # via `with`), but that does not close the generator we installed, so
    # also record from close(). Recording is idempotent.
    owner = close_owner if close_owner is not None else stream
    original_close = getattr(owner, "close", None)
    if callable(original_close):
        if is_async:
            async def close(*args: Any, **kwargs: Any) -> Any:
                finish(None)
                return await original_close(*args, **kwargs)
        else:
            def close(*args: Any, **kwargs: Any) -> Any:
                finish(None)
                return original_close(*args, **kwargs)
        try:
            owner.close = close
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------
def _sync_wrapper(original: Callable, adapter: _Adapter) -> Callable:
    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        client = _client
        if client is None:
            return original(self, *args, **kwargs)
        call = _Call(client, adapter, kwargs)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            _safe(_record_error, call, exc)
            raise
        _safe(_handle_result, call, result)
        return result

    return wrapper


def _async_wrapper(original: Callable, adapter: _Adapter) -> Callable:
    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        client = _client
        if client is None:
            return await original(self, *args, **kwargs)
        call = _Call(client, adapter, kwargs)
        try:
            result = await original(self, *args, **kwargs)
        except Exception as exc:
            _safe(_record_error, call, exc)
            raise
        _safe(_handle_result, call, result)
        return result

    return wrapper


def _stream_helper_wrapper(original: Callable) -> Callable:
    """Anthropic's messages.stream() does not go through create(): it returns
    a MessageStreamManager that sends the request in __enter__/__aenter__.
    Remember the request kwargs on the manager; the patched __enter__ below
    does the timing and observes the stream."""
    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        manager = original(self, *args, **kwargs)
        if _client is not None:
            try:
                manager._opentelm_kwargs = kwargs
            except Exception:
                pass
        return manager

    return wrapper


def _observe_message_stream(call: _Call, message_stream: Any) -> None:
    # MessageStream reads SSE events from its `_raw_stream` (a plain Stream)
    # in every anthropic release since the helper was introduced.
    raw = getattr(message_stream, "_raw_stream", None)
    if raw is None:
        _debug_once("no_raw_stream", "opentelm: %s has no _raw_stream; messages.stream() not traced",
                    type(message_stream).__name__)
        return
    _instrument_stream(call, raw, close_owner=message_stream)


def _manager_enter_wrapper(original: Callable) -> Callable:
    @functools.wraps(original)
    def __enter__(self):
        client, kwargs = _client, getattr(self, "_opentelm_kwargs", None)
        if client is None or kwargs is None:
            return original(self)
        call = _Call(client, ANTHROPIC, kwargs)
        try:
            message_stream = original(self)
        except Exception as exc:
            _safe(_record_error, call, exc)
            raise
        _safe(_observe_message_stream, call, message_stream)
        return message_stream

    return __enter__


def _manager_aenter_wrapper(original: Callable) -> Callable:
    @functools.wraps(original)
    async def __aenter__(self):
        client, kwargs = _client, getattr(self, "_opentelm_kwargs", None)
        if client is None or kwargs is None:
            return await original(self)
        call = _Call(client, ANTHROPIC, kwargs)
        try:
            message_stream = await original(self)
        except Exception as exc:
            _safe(_record_error, call, exc)
            raise
        _safe(_observe_message_stream, call, message_stream)
        return message_stream

    return __aenter__


def _patch(owner: type, name: str, make_wrapper: Callable[[Callable], Callable]) -> None:
    original = owner.__dict__.get(name)
    if original is None or not callable(original):
        return
    if getattr(original, "__opentelm_wrapped__", False):
        return  # already patched (e.g. init() called twice)
    wrapper = make_wrapper(original)
    wrapper.__opentelm_wrapped__ = True
    setattr(owner, name, wrapper)
    _originals.append((owner, name, original))


def _patch_openai() -> None:
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except Exception:
        return  # openai not installed
    for method in ("create", "parse"):
        _patch(Completions, method, lambda f: _sync_wrapper(f, OPENAI_CHAT))
        _patch(AsyncCompletions, method, lambda f: _async_wrapper(f, OPENAI_CHAT))
    try:
        from openai.resources.responses import AsyncResponses, Responses
    except Exception:
        return  # openai < 1.66 has no Responses API
    for method in ("create", "parse"):
        _patch(Responses, method, lambda f: _sync_wrapper(f, OPENAI_RESPONSES))
        _patch(AsyncResponses, method, lambda f: _async_wrapper(f, OPENAI_RESPONSES))


def _patch_anthropic() -> None:
    try:
        from anthropic.resources.messages import AsyncMessages, Messages
    except Exception:
        return  # anthropic not installed
    _patch(Messages, "create", lambda f: _sync_wrapper(f, ANTHROPIC))
    _patch(AsyncMessages, "create", lambda f: _async_wrapper(f, ANTHROPIC))
    _patch(Messages, "stream", _stream_helper_wrapper)
    _patch(AsyncMessages, "stream", _stream_helper_wrapper)
    try:
        from anthropic.lib.streaming import AsyncMessageStreamManager, MessageStreamManager
    except Exception:
        return  # very old anthropic without the stream helper
    _patch(MessageStreamManager, "__enter__", _manager_enter_wrapper)
    _patch(AsyncMessageStreamManager, "__aenter__", _manager_aenter_wrapper)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def instrument_all(client: "OpenTelLM") -> None:
    global _client
    _client = client
    _patch_openai()
    _patch_anthropic()


def uninstrument_all() -> None:
    global _client
    _client = None
    while _originals:
        owner, name, original = _originals.pop()
        setattr(owner, name, original)

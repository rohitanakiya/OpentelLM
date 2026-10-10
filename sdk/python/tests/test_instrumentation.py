from __future__ import annotations

import re

import pytest
from conftest import (
    anthropic_events,
    anthropic_message,
    httpx2,
    openai_chunks,
    openai_completion,
    responses_result,
    sha,
    sse,
    sse_named,
)

import opentelm
from opentelm import monkey_patch

openai = pytest.importorskip("openai")
anthropic = pytest.importorskip("anthropic")

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def only_event(sink) -> dict:
    events = sink.events()
    assert len(events) == 1, events
    return events[0]


def assert_valid_event(ev: dict) -> None:
    """Every event must satisfy ingest-api's LLMEvent schema."""
    assert ev["provider"] in {"openai", "anthropic", "google", "cohere", "other"}
    assert ev["status"] in {"success", "error", "timeout"}
    assert HEX64.match(ev["prompt_hash"]) and HEX64.match(ev["response_hash"])
    for key in ("prompt_tokens", "completion_tokens", "latency_ms"):
        assert isinstance(ev[key], int) and ev[key] >= 0
    assert isinstance(ev["tags"], dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in ev["tags"].items())
    assert isinstance(ev["timestamp_ms"], int)


# ---------------------------------------------------------------------------
# OpenAI chat completions
# ---------------------------------------------------------------------------
def test_openai_sync_call_is_recorded(sink, provider, openai_client):
    provider.json(openai_completion("Paris"))
    res = openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital of France?"}],
    )
    assert res.choices[0].message.content == "Paris"
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["provider"] == "openai" and ev["model"] == "gpt-4o"
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (12, 3)
    assert ev["status"] == "success"
    assert ev["prompt_hash"] == sha("user: Capital of France?")
    assert ev["response_hash"] == sha("Paris")


def test_tool_call_conversation_does_not_crash(sink, provider, openai_client):
    """Regression: assistant messages with content=None used to raise
    TypeError inside the SDK and crash the caller's successful request."""
    tool_calls = [{"id": "call_1", "type": "function",
                   "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]
    provider.json(openai_completion(content=None, tool_calls=tool_calls))
    res = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": "Weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": tool_calls},
            {"role": "tool", "tool_call_id": "call_1", "content": "18C"},
        ],
    )
    assert res.choices[0].message.tool_calls[0].function.name == "get_weather"
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["status"] == "success"
    assert ev["response_hash"] == sha('[tool_call:get_weather] {"city":"Paris"}')


def test_multimodal_list_content_does_not_crash(sink, provider, openai_client):
    provider.json(openai_completion("A cat"))
    openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}],
    )
    ev = only_event(sink)
    assert ev["prompt_hash"] == sha("user: What is this?\n[image_url]")


def test_recording_failure_never_reaches_caller(sink, provider, openai_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug inside the SDK")

    monkeypatch.setattr(monkey_patch._OpenAIChat, "read_response", boom)
    monkeypatch.setattr(monkey_patch._OpenAIChat, "prompt_text", boom)
    provider.json(openai_completion("Paris"))
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert res.choices[0].message.content == "Paris"


def test_provider_error_propagates_unchanged(sink, provider, openai_client):
    provider.json({"error": {"message": "boom", "type": "server_error"}}, status=500)
    with pytest.raises(openai.InternalServerError):
        openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["status"] == "error" and ev["error_code"] == "500"
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (0, 0)


def test_timeout_is_recorded_as_timeout(sink, provider, openai_client):
    provider.error(httpx2.ReadTimeout("slow"))
    with pytest.raises(openai.APITimeoutError):
        openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert only_event(sink)["status"] == "timeout"


async def test_async_openai_is_recorded(sink, provider, async_openai_client):
    provider.json(openai_completion("Paris"))
    res = await async_openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital of France?"}],
    )
    assert res.choices[0].message.content == "Paris"
    ev = only_event(sink)
    assert ev["provider"] == "openai" and ev["prompt_tokens"] == 12


def test_openai_stream_records_after_exhaustion(sink, provider, openai_client):
    provider.stream(sse(openai_chunks(["Pa", "ris"])))
    stream = openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital?"}], stream=True,
    )
    assert isinstance(stream, openai.Stream)  # same object type as without the SDK
    assert sink.events() == []                # nothing recorded until consumed
    text = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert text == "Paris"
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["response_hash"] == sha("Paris")
    assert ev["tags"]["otlm.tokens_estimated"] == "true"  # no include_usage
    assert "otlm.ttft_ms" in ev["tags"]


def test_openai_stream_uses_reported_usage(sink, provider, openai_client):
    usage = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
    provider.stream(sse(openai_chunks(["Pa", "ris"], usage=usage)))
    stream = openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital?"}], stream=True,
        stream_options={"include_usage": True},
    )
    for _ in stream:
        pass
    ev = only_event(sink)
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (7, 2)
    assert "otlm.tokens_estimated" not in ev["tags"]


def test_openai_stream_closed_early_is_marked(sink, provider, openai_client):
    provider.stream(sse(openai_chunks(["a", "b", "c", "d"])))
    with openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "x"}], stream=True,
    ) as stream:
        for _chunk in stream:
            break
    ev = only_event(sink)
    assert ev["tags"].get("otlm.stream_incomplete") == "true"


async def test_async_openai_stream(sink, provider, async_openai_client):
    provider.stream(sse(openai_chunks(["Pa", "ris"])))
    stream = await async_openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital?"}], stream=True,
    )
    assert isinstance(stream, openai.AsyncStream)
    pieces = [c.choices[0].delta.content async for c in stream if c.choices and c.choices[0].delta.content]
    assert "".join(pieces) == "Paris"
    assert only_event(sink)["response_hash"] == sha("Paris")


def test_openai_stream_helper_records_once(sink, provider, openai_client):
    provider.stream(sse(openai_chunks(["Pa", "ris"])))
    completions = openai_client.chat.completions
    if not hasattr(completions, "stream"):  # openai < 1.52 keeps it under beta
        completions = openai_client.beta.chat.completions
    with completions.stream(
        model="gpt-4o", messages=[{"role": "user", "content": "Capital?"}],
    ) as stream:
        final = stream.get_final_completion()
    assert final.choices[0].message.content == "Paris"
    assert only_event(sink)["response_hash"] == sha("Paris")


def test_raw_response_calls_still_work(sink, provider, openai_client):
    provider.json(openai_completion("Paris"))
    raw = openai_client.chat.completions.with_raw_response.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
    )
    assert raw.parse().choices[0].message.content == "Paris"


# ---------------------------------------------------------------------------
# OpenAI Responses API
# ---------------------------------------------------------------------------
needs_responses_api = pytest.mark.skipif(
    not hasattr(openai.OpenAI(api_key="x"), "responses"), reason="openai without Responses API",
)


@needs_responses_api
def test_responses_api_is_recorded(sink, provider, openai_client):
    provider.json(responses_result("Paris"))
    res = openai_client.responses.create(model="gpt-4.1", input="Capital of France?",
                                         instructions="Be brief.")
    assert res.output_text == "Paris"
    ev = only_event(sink)
    assert_valid_event(ev)
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (9, 2)
    assert ev["prompt_hash"] == sha("system: Be brief.\nCapital of France?")
    assert ev["response_hash"] == sha("Paris")


@needs_responses_api
def test_responses_api_stream(sink, provider, openai_client):
    done = responses_result("Paris")
    events = [
        {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0,
         "content_index": 0, "delta": "Pa", "sequence_number": 1, "logprobs": []},
        {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0,
         "content_index": 0, "delta": "ris", "sequence_number": 2, "logprobs": []},
        {"type": "response.completed", "response": done, "sequence_number": 3},
    ]
    provider.stream(sse_named([(e["type"], e) for e in events]))
    for _ in openai_client.responses.create(model="gpt-4.1", input="Capital?", stream=True):
        pass
    ev = only_event(sink)
    assert ev["response_hash"] == sha("Paris")
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (9, 2)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
def test_anthropic_sync_call_is_recorded(sink, provider, anthropic_client):
    provider.json(anthropic_message("Paris", input_tokens=10, output_tokens=2, cache_read=5))
    res = anthropic_client.messages.create(
        model="claude-sonnet-4-5", max_tokens=100, system="Be brief.",
        messages=[{"role": "user", "content": "Capital of France?"}],
    )
    assert res.content[0].text == "Paris"
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["provider"] == "anthropic"
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (15, 2)  # includes cached tokens
    assert ev["prompt_hash"] == sha("system: Be brief.\nuser: Capital of France?")


def test_anthropic_tool_result_blocks_do_not_crash(sink, provider, anthropic_client):
    provider.json(anthropic_message("It is 18C"))
    anthropic_client.messages.create(
        model="claude-sonnet-4-5", max_tokens=100,
        messages=[
            {"role": "user", "content": "Weather in Paris?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "get_weather", "input": {"city": "Paris"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "18C"}]}]},
        ],
    )
    ev = only_event(sink)
    assert ev["status"] == "success"
    assert ev["prompt_hash"] == sha(
        'user: Weather in Paris?\nassistant: [tool_use:get_weather] {"city": "Paris"}\nuser: 18C'
    )


async def test_async_anthropic_is_recorded(sink, provider, async_anthropic_client):
    provider.json(anthropic_message("Paris"))
    res = await async_anthropic_client.messages.create(
        model="claude-sonnet-4-5", max_tokens=100,
        messages=[{"role": "user", "content": "Capital of France?"}],
    )
    assert res.content[0].text == "Paris"
    assert only_event(sink)["provider"] == "anthropic"


def test_anthropic_stream_create(sink, provider, anthropic_client):
    provider.stream(sse_named(anthropic_events(["Pa", "ris"], input_tokens=11, output_tokens=4)))
    stream = anthropic_client.messages.create(
        model="claude-sonnet-4-5", max_tokens=100, stream=True,
        messages=[{"role": "user", "content": "Capital?"}],
    )
    assert isinstance(stream, anthropic.Stream)
    for _ in stream:
        pass
    ev = only_event(sink)
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (11, 4)
    assert ev["response_hash"] == sha("Paris")
    assert "otlm.tokens_estimated" not in ev["tags"]


def test_anthropic_messages_stream_helper(sink, provider, anthropic_client):
    provider.stream(sse_named(anthropic_events(["Pa", "ris"])))
    with anthropic_client.messages.stream(
        model="claude-sonnet-4-5", max_tokens=100,
        messages=[{"role": "user", "content": "Capital?"}],
    ) as stream:
        text = "".join(stream.text_stream)
    assert text == "Paris"
    ev = only_event(sink)
    assert ev["response_hash"] == sha("Paris") and ev["completion_tokens"] == 4


async def test_async_anthropic_messages_stream_helper(sink, provider, async_anthropic_client):
    provider.stream(sse_named(anthropic_events(["Pa", "ris"])))
    async with async_anthropic_client.messages.stream(
        model="claude-sonnet-4-5", max_tokens=100,
        messages=[{"role": "user", "content": "Capital?"}],
    ) as stream:
        final = await stream.get_final_message()
    assert final.content[0].text == "Paris"
    assert only_event(sink)["response_hash"] == sha("Paris")


# ---------------------------------------------------------------------------
# Lifecycle, tagging, privacy
# ---------------------------------------------------------------------------
def test_double_init_does_not_double_record(sink, provider, openai_client):
    opentelm.init(api_key="another-key")          # warns, keeps first config
    opentelm.get_client().instrument()            # no-op
    monkey_patch.instrument_all(opentelm.get_client())  # direct re-patch is guarded too
    provider.json(openai_completion("Paris"))
    openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert len(sink.events()) == 1


def test_shutdown_restores_original_methods():
    from openai.resources.chat.completions import Completions
    before = Completions.__dict__["create"]
    opentelm.init(api_key="k", sender=lambda b, t: 202, register_atexit=False)
    assert Completions.__dict__["create"] is not before
    opentelm.shutdown()
    assert Completions.__dict__["create"] is before


def test_patch_covers_clients_created_before_init(provider):
    client = openai.OpenAI(api_key="sk-test", max_retries=0,
                           http_client=httpx2.Client(transport=provider.transport()))
    from conftest import Sink
    sink = Sink()
    opentelm.init(api_key="k", sender=sink, flush_interval_s=60, register_atexit=False)
    try:
        provider.json(openai_completion("Paris"))
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert len(sink.events()) == 1
    finally:
        opentelm.shutdown()


def test_deployment_version_is_attached(sink, provider, openai_client):
    opentelm.set_version("prompt-v2")
    provider.json(openai_completion("Paris"))
    openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert only_event(sink)["deployment_version"] == "prompt-v2"


def test_pii_is_scrubbed_before_hashing(sink, provider, openai_client):
    provider.json(openai_completion("ok"))
    openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "Email ronu@example.com please"}],
    )
    assert only_event(sink)["prompt_hash"] == sha("user: Email [EMAIL] please")


def test_manual_track(sink):
    opentelm.track(model="llama-3-8b", provider="other", prompt="hi", response="hello",
                   prompt_tokens=1, completion_tokens=1, latency_ms=40, tags={"host": "gpu-1"})
    ev = only_event(sink)
    assert_valid_event(ev)
    assert ev["model"] == "llama-3-8b" and ev["tags"]["host"] == "gpu-1"


def test_uninstrumented_calls_pass_through(provider, openai_client):
    """With the SDK never initialised, calls behave exactly as normal."""
    provider.json(openai_completion("Paris"))
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert res.choices[0].message.content == "Paris"


def test_anthropic_stream_helper_early_exit(sink, provider, anthropic_client):
    provider.stream(sse_named(anthropic_events(["a", "b", "c"])))
    with anthropic_client.messages.stream(
        model="claude-sonnet-4-5", max_tokens=100, messages=[{"role": "user", "content": "x"}],
    ) as stream:
        for _text in stream.text_stream:
            break
    ev = only_event(sink)
    assert ev["tags"].get("otlm.stream_incomplete") == "true"


def test_anthropic_stream_helper_request_error(sink, provider, anthropic_client):
    provider.json({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, status=529)
    with pytest.raises(anthropic.APIStatusError):
        with anthropic_client.messages.stream(
            model="claude-sonnet-4-5", max_tokens=100, messages=[{"role": "user", "content": "x"}],
        ):
            pass
    ev = only_event(sink)
    assert ev["status"] == "error" and ev["error_code"] == "529"

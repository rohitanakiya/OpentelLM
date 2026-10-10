"""Contract test: every event the SDK emits must pass ingest-api's own
schema (ingest-api/models.py), so the two can't drift apart silently."""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from conftest import anthropic_message, openai_chunks, openai_completion, sse

import opentelm

MODELS = pathlib.Path(__file__).resolve().parents[3] / "ingest-api" / "models.py"
pytestmark = pytest.mark.skipif(not MODELS.exists(), reason="ingest-api not in this checkout")


@pytest.fixture(scope="module")
def ingest_models():
    spec = importlib.util.spec_from_file_location("ingest_models", MODELS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # lets pydantic resolve postponed annotations
    spec.loader.exec_module(mod)
    return mod


def test_sdk_events_pass_ingest_schema(ingest_models, sink, provider, openai_client, anthropic_client):
    import openai

    provider.json(openai_completion("Paris"))
    openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    provider.stream(sse(openai_chunks(["a", "b"])))
    for _ in openai_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True,
    ):
        pass
    provider.json({"error": {"message": "x" * 5000}}, status=500)
    with pytest.raises(openai.InternalServerError):
        openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    provider.json(anthropic_message("Paris"))
    anthropic_client.messages.create(model="claude-sonnet-4-5", max_tokens=5,
                                     messages=[{"role": "user", "content": "hi"}])
    opentelm.track(model="m", provider="not-a-provider", prompt="p", response="r",
                   prompt_tokens=1, completion_tokens=1, latency_ms=1)

    events = sink.events()
    assert len(events) == 5
    ingest_models.IngestRequest.model_validate({"events": events})

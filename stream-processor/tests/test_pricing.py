from __future__ import annotations

import importlib
import pathlib

import pricing
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_consumer_and_stream_processor_share_one_price_table():
    """Each service is built from its own directory, so pricing.py is copied
    into both. If the copies drift, Traces and Overview disagree on cost."""
    a = (ROOT / "consumer" / "pricing.py").read_bytes()
    b = (ROOT / "stream-processor" / "pricing.py").read_bytes()
    assert a == b, "copy stream-processor/pricing.py over consumer/pricing.py (or vice versa)"


@pytest.mark.parametrize("model,expected", [
    ("gpt-4o", "gpt-4o"),
    ("gpt-4o-2024-08-06", "gpt-4o"),              # dated snapshot
    ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),    # longest prefix wins
    ("gpt-4", "gpt-4"),
    ("gpt-4-0613", "gpt-4"),
    ("GPT-4.1-mini", "gpt-4.1-mini"),             # case-insensitive
    ("openai/gpt-5-nano", "gpt-5-nano"),          # provider prefix
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("claude-sonnet-4-20250514", "claude-sonnet-4"),
    ("claude-3-5-haiku-20241022", "claude-3-5-haiku"),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
    ("claude-opus-5-5", "claude-opus-5-5"),
])
def test_prefix_matching(model, expected):
    assert pricing.price_for(model) == pricing.PRICES_PER_MTOK[expected]


@pytest.mark.parametrize("model", ["gpt-4ox", "gpt-40", "llama-3-70b", "", "o3x"])
def test_no_false_prefix_matches(model):
    assert pricing.price_for(model) is None


def test_cost_math():
    # gpt-4o: $2.50 in / $10 out per 1M tokens
    assert pricing.calculate_cost("gpt-4o-2024-08-06", 1_000_000, 100_000) == pytest.approx(3.50)


def test_long_context_tier():
    assert pricing.calculate_cost("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    assert pricing.calculate_cost("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)


def test_unknown_model_uses_fallback():
    fin, fout = pricing.FALLBACK_PER_MTOK
    assert pricing.calculate_cost("my-local-llama", 1_000_000, 1_000_000) == pytest.approx(fin + fout)


def test_overrides_from_environment(monkeypatch):
    monkeypatch.setenv("PRICING_OVERRIDES", '{"my-finetune": [0.5, 1.5], "gpt-4o": [1, 1]}')
    mod = importlib.reload(pricing)
    try:
        assert mod.price_for("my-finetune-v2") == (0.5, 1.5)
        assert mod.price_for("gpt-4o-2024-08-06") == (1.0, 1.0)
    finally:
        monkeypatch.delenv("PRICING_OVERRIDES")
        importlib.reload(pricing)


def test_invalid_overrides_are_ignored(monkeypatch):
    monkeypatch.setenv("PRICING_OVERRIDES", "not json")
    mod = importlib.reload(pricing)
    try:
        assert mod.price_for("gpt-4o") == (2.50, 10.0)
    finally:
        monkeypatch.delenv("PRICING_OVERRIDES")
        importlib.reload(pricing)

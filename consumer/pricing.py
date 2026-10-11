"""
Model prices, used to turn token counts into cost_usd.

THIS FILE EXISTS TWICE, byte for byte: consumer/pricing.py (cost of each
trace) and stream-processor/pricing.py (per-minute totals and cost-spike
alerts). Each service is built from its own directory, so they can't import
one shared module; stream-processor/tests/test_pricing.py fails if the two
copies differ. Before, they had different model lists, so the Traces page
and the Overview page disagreed about the same calls.

Prices are USD per 1M tokens (input, output), standard tier, no discounts.
Sources, checked October 2026:
  Anthropic  https://platform.claude.com/docs/en/about-claude/pricing
  OpenAI     https://developers.openai.com/api/docs/pricing (current models)
             and published list prices for older models still in use
Providers rename and re-price models often. Add or correct entries without
a code change by setting PRICING_OVERRIDES to a JSON object, e.g.
    PRICING_OVERRIDES='{"my-finetune": [0.5, 1.5], "gpt-6-luna": [0.1, 0.5]}'

Model IDs from the APIs are often dated snapshots ("gpt-4o-2024-08-06",
"claude-sonnet-4-5-20250929") or carry a provider prefix
("openai/gpt-4o", "us.anthropic.claude-haiku-4-5-..."). Lookup normalises
the ID and uses the LONGEST table key that is a prefix of it at a "-"
boundary, so "gpt-4o-mini-2024-07-18" prices as gpt-4o-mini, not gpt-4o.
Previously only exact matches worked, so most real traffic fell through to
the fallback price.

Known simplifications:
  * Prompt-cache reads are billed at the full input price (the events don't
    say which prompt tokens were cached), so cached-heavy apps are
    overestimated.
  * Unknown models use FALLBACK_PER_MTOK, a mid-range guess, so cost-spike
    detection still works for them. It is logged once per model.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# model id prefix -> (input $/1M, output $/1M)
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # --- Anthropic (platform.claude.com pricing table) ---
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),      # prompts > 100K tokens: see LONG_CONTEXT
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-haiku": (0.25, 1.25),
    # --- OpenAI ---
    "gpt-6-astra": (10.0, 50.0),
    "gpt-6.1-sol": (2.0, 10.0),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-5.3-codex": (1.75, 14.0),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "o1": (15.0, 60.0),
    "o3": (2.0, 8.0),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.50, 1.50),
}

# Models priced by prompt length: prefix -> (threshold tokens, input, output)
LONG_CONTEXT: dict[str, tuple[int, float, float]] = {
    "claude-haiku-5-5": (100_000, 0.50, 2.50),
}

FALLBACK_PER_MTOK: tuple[float, float] = (1.0, 2.0)

_PREFIX = re.compile(r"^(?:[a-z]{2}\.)?(?:anthropic|openai)\.")   # bedrock-style "us.anthropic."
_warned: set[str] = set()


def _load_overrides() -> dict[str, tuple[float, float]]:
    raw = os.environ.get("PRICING_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k).lower(): (float(v[0]), float(v[1])) for k, v in data.items()}
    except (ValueError, TypeError, IndexError, AttributeError) as exc:
        logger.error("ignoring invalid PRICING_OVERRIDES: %s", exc)
        return {}


_TABLE: dict[str, tuple[float, float]] = {**PRICES_PER_MTOK, **_load_overrides()}


def normalize_model(model: str) -> str:
    m = (model or "").strip().lower()
    m = m.rsplit("/", 1)[-1]          # "openai/gpt-4o", "models/x"
    return _PREFIX.sub("", m)


def _lookup(key: str, table: dict) -> str | None:
    if key in table:
        return key
    best = None
    for prefix in table:
        if key.startswith(prefix) and key[len(prefix)] in "-@:" and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def price_for(model: str, prompt_tokens: int = 0) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens, or None for an unknown model."""
    key = normalize_model(model)
    match = _lookup(key, _TABLE)
    if match is None:
        return None
    long_ctx = _lookup(key, LONG_CONTEXT)
    if long_ctx is not None and long_ctx == match:
        threshold, long_in, long_out = LONG_CONTEXT[long_ctx]
        if prompt_tokens > threshold:
            return (long_in, long_out)
    return _TABLE[match]


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = price_for(model, prompt_tokens)
    if prices is None:
        key = normalize_model(model)
        if key not in _warned:
            _warned.add(key)
            logger.warning("no price for model %r; using fallback %s per 1M tokens "
                           "(set PRICING_OVERRIDES to add it)", model, FALLBACK_PER_MTOK)
        prices = FALLBACK_PER_MTOK
    return (prompt_tokens * prices[0] + completion_tokens * prices[1]) / 1_000_000


# Name used by older callers.
compute_cost = calculate_cost

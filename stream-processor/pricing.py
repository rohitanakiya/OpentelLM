"""
Model pricing table — USD per 1000 tokens.

Used by the metrics aggregator to compute cost_usd from raw token counts.
This is intentionally hardcoded rather than configurable: pricing changes
infrequently, and a typo in a config file would silently misreport every
tenant's spend.

NOTE: this table is deliberately kept independent of the ingest-side
consumer/pricing.py. Each consumer group computes cost from its own table
so a deploy of one service can roll out new prices without coordinating
a simultaneous deploy of the others.
"""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":              {"prompt": 0.005,    "completion": 0.015},
    "gpt-4o-mini":         {"prompt": 0.000150, "completion": 0.000600},
    "gpt-4-turbo":         {"prompt": 0.01,     "completion": 0.03},
    "gpt-3.5-turbo":       {"prompt": 0.0005,   "completion": 0.0015},
    "claude-3-5-sonnet":   {"prompt": 0.003,    "completion": 0.015},
    "claude-3-5-haiku":    {"prompt": 0.0008,   "completion": 0.004},
    "claude-3-opus":       {"prompt": 0.015,    "completion": 0.075},
    "gemini-1.5-pro":      {"prompt": 0.00125,  "completion": 0.005},
    "gemini-1.5-flash":    {"prompt": 0.000075, "completion": 0.000300},
    "unknown":             {"prompt": 0.001,    "completion": 0.002},
}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = PRICING.get(model, PRICING["unknown"])
    return (prompt_tokens / 1000 * rates["prompt"]) + \
           (completion_tokens / 1000 * rates["completion"])

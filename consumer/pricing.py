"""Token cost table. Prices in USD per 1 000 tokens (input, output)."""

_COSTS: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o":             (0.005,  0.015),
    "gpt-4o-mini":        (0.00015, 0.0006),
    "gpt-4-turbo":        (0.01,   0.03),
    "gpt-3.5-turbo":      (0.0005, 0.0015),
    # Anthropic
    "claude-opus-4-7":    (0.015,  0.075),
    "claude-sonnet-4-6":  (0.003,  0.015),
    "claude-haiku-4-5":   (0.00025, 0.00125),
    # Google
    "gemini-1.5-pro":     (0.00125, 0.005),
    "gemini-1.5-flash":   (0.000075, 0.0003),
}

_FALLBACK = (0.001, 0.002)


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_price, output_price = _COSTS.get(model.lower(), _FALLBACK)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1000

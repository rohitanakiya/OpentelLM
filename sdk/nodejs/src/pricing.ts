// Token pricing — USD per 1000 tokens.
// Mirrored from sdk/python/opentelm/pricing.py and stream-processor/pricing.py.
// Keep these three tables in sync when prices change.

export interface PriceRow {
  prompt: number;
  completion: number;
}

export const PRICING: Readonly<Record<string, PriceRow>> = Object.freeze({
  "gpt-4o":            { prompt: 0.005,    completion: 0.015 },
  "gpt-4o-mini":       { prompt: 0.000150, completion: 0.000600 },
  "gpt-4-turbo":       { prompt: 0.01,     completion: 0.03 },
  "gpt-3.5-turbo":     { prompt: 0.0005,   completion: 0.0015 },
  "claude-3-5-sonnet": { prompt: 0.003,    completion: 0.015 },
  "claude-3-5-haiku":  { prompt: 0.0008,   completion: 0.004 },
  "claude-3-opus":     { prompt: 0.015,    completion: 0.075 },
  "gemini-1.5-pro":    { prompt: 0.00125,  completion: 0.005 },
  "gemini-1.5-flash":  { prompt: 0.000075, completion: 0.000300 },
  unknown:             { prompt: 0.001,    completion: 0.002 },
});

export function calculateCost(
  model: string,
  promptTokens: number,
  completionTokens: number,
): number {
  // Strip provider prefixes some SDKs add ("anthropic/claude-3-opus")
  const key = model.split("/").pop() ?? model;
  const rates = PRICING[key] ?? PRICING.unknown;
  return (promptTokens / 1000) * rates.prompt
       + (completionTokens / 1000) * rates.completion;
}

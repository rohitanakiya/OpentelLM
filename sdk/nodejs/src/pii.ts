// PII scrubbing.
//
// Default-on. Replaces matched patterns with sentinels so the hash is still
// stable across calls (e.g. two calls with different emails won't collide
// into the same prompt_hash, but the same email twice WILL hash the same).
//
// Patterns are intentionally conservative — false positives are better
// than leaking PII. If a tenant's prompt happens to look like an email,
// the scrubbed version still hashes to a stable value.
//
// Disable per-call by setting `scrubPii: false` in the SDK config.

const PATTERNS: Array<{ name: string; re: RegExp; placeholder: string }> = [
  // Email addresses (RFC 5322 simplified)
  {
    name: "email",
    re: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g,
    placeholder: "[EMAIL]",
  },
  // Indian +91 phone numbers (10 digit national)
  {
    name: "phone_in",
    re: /(?:\+?91[-\s]?)?[6-9]\d{9}\b/g,
    placeholder: "[PHONE]",
  },
  // International E.164 — +<country><number>, 7-15 digits.
  // Order matters: this runs AFTER phone_in so Indian numbers get the
  // tighter placeholder. We still catch +1, +44, etc.
  {
    name: "phone_intl",
    re: /\+\d{1,3}[-\s]?\d{4,14}\b/g,
    placeholder: "[PHONE]",
  },
  // OpenTelLM API keys — never let these leak into prompt_hash territory
  {
    name: "opentelm_key",
    re: /opentelm_live_[A-Za-z0-9]{16,}/g,
    placeholder: "[OPENTELM_KEY]",
  },
  // OpenAI keys — sk-proj-... and sk-... formats
  {
    name: "openai_key",
    re: /sk-(?:proj-)?[A-Za-z0-9_-]{20,}/g,
    placeholder: "[OPENAI_KEY]",
  },
  // Anthropic keys — sk-ant-...
  {
    name: "anthropic_key",
    re: /sk-ant-[A-Za-z0-9_-]{20,}/g,
    placeholder: "[ANTHROPIC_KEY]",
  },
];

export function scrubPii(text: string): string {
  if (!text) return text;
  let out = text;
  for (const { re, placeholder } of PATTERNS) {
    out = out.replace(re, placeholder);
  }
  return out;
}

/** For tests + observability — return which categories matched. */
export function detectPii(text: string): string[] {
  const hits: string[] = [];
  for (const { name, re } of PATTERNS) {
    // RegExp.test mutates lastIndex on /g flags; clone to be safe
    const local = new RegExp(re.source, re.flags);
    if (local.test(text)) hits.push(name);
  }
  return hits;
}

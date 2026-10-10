// PII scrubbing, applied to prompt/response text BEFORE hashing.
//
// Port of sdk/python/opentelm/pii.py: same patterns, same order, same
// placeholders, so both SDKs hash the same text the same way
// (sdk/testdata/hash_parity.json checks this in both test suites).
//
// Matches are replaced with fixed placeholders, which keeps the hash
// stable: two prompts that differ only in the email address hash the same,
// which is what prompt grouping wants. Disable with `scrubPii: false`.
//
// Order matters, and the previous version of this file got it wrong:
//   * the international phone pattern runs BEFORE the Indian one, otherwise
//     "+44 7911123456" became "+44 [PHONE]" (the 10 digits matched the
//     Indian pattern and the country code leaked);
//   * the Anthropic key pattern runs BEFORE the OpenAI one, otherwise
//     "sk-ant-..." was labelled [OPENAI_KEY].

const EMAIL = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
const PHONE_INTL = /\+\d{1,3}[-\s]?\d{4,14}\b/g;     // +44 7911123456, +919876543210
const PHONE_IN = /[6-9]\d{9}\b/g;                     // 9876543210
const PHONE_IN_PREFIX = /91[-\s]?\[PHONE\]/g;          // "91 9876543210" leftovers
const OPENTELM_KEY = /opentelm_live_[A-Za-z0-9]{16,}/g;
const ANTHROPIC_KEY = /sk-ant-[A-Za-z0-9_-]{20,}/g;
const OPENAI_KEY = /sk-(?:proj-)?[A-Za-z0-9_-]{20,}/g;

export function scrubPii(text: string): string {
  if (!text) return text;
  let out = text;
  if (out.includes("@")) out = out.replace(EMAIL, "[EMAIL]");
  if (out.includes("+")) out = out.replace(PHONE_INTL, "[PHONE]");
  out = out.replace(PHONE_IN, "[PHONE]");
  if (out.includes("91") && out.includes("[PHONE]")) {
    out = out.replace(PHONE_IN_PREFIX, "[PHONE]").split("+[PHONE]").join("[PHONE]");
  }
  if (out.includes("opentelm_live_")) out = out.replace(OPENTELM_KEY, "[OPENTELM_KEY]");
  if (out.includes("sk-")) {
    out = out.replace(ANTHROPIC_KEY, "[ANTHROPIC_KEY]");
    out = out.replace(OPENAI_KEY, "[OPENAI_KEY]");
  }
  return out;
}

const DETECTORS: Array<[string, RegExp]> = [
  ["email", EMAIL],
  ["phone_intl", PHONE_INTL],
  ["phone_in", PHONE_IN],
  ["opentelm_key", OPENTELM_KEY],
  ["anthropic_key", ANTHROPIC_KEY],
  ["openai_key", OPENAI_KEY],
];

/** Names of the pattern categories found in `text`. */
export function detectPii(text: string): string[] {
  return DETECTORS.filter(([, re]) => new RegExp(re.source).test(text || "")).map(([name]) => name);
}

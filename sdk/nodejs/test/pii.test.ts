import { describe, it, expect } from "vitest";
import { scrubPii, detectPii } from "../src/pii";

describe("pii.scrubPii", () => {
  it("scrubs email addresses", () => {
    const out = scrubPii("contact me at jane.doe+filter@example.co.uk for help");
    expect(out).toBe("contact me at [EMAIL] for help");
  });

  it("scrubs Indian phone numbers with +91 prefix", () => {
    const out = scrubPii("call +91 9876543210 today");
    expect(out).toBe("call [PHONE] today");
  });

  it("scrubs Indian phone numbers without prefix", () => {
    const out = scrubPii("9876543210");
    expect(out).toBe("[PHONE]");
  });

  it("scrubs international phone numbers", () => {
    const out = scrubPii("US line: +1-415-555-0000");
    expect(out).toContain("[PHONE]");
    expect(out).not.toContain("415-555-0000");
  });

  it("scrubs OpenTelLM API keys", () => {
    const out = scrubPii("Bearer opentelm_live_abcdef0123456789abcdef0123456789");
    expect(out).toBe("Bearer [OPENTELM_KEY]");
  });

  it("scrubs OpenAI keys", () => {
    const out = scrubPii("export OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123");
    expect(out).toContain("[OPENAI_KEY]");
    expect(out).not.toMatch(/sk-proj-/);
  });

  it("leaves benign text alone", () => {
    const text = "What is the capital of France?";
    expect(scrubPii(text)).toBe(text);
  });

  it("handles empty input", () => {
    expect(scrubPii("")).toBe("");
  });

  it("hash-stable for the same PII (so prompt_hash dedup still works)", () => {
    const a = scrubPii("hi alice@x.com");
    const b = scrubPii("hi alice@x.com");
    expect(a).toBe(b);
  });

  it("scrubs multiple distinct PII types in one string", () => {
    const out = scrubPii(
      "alice@x.com used opentelm_live_abcdefghijklmnopqrstuvwxyz12 from +91 9876543210",
    );
    expect(out).toContain("[EMAIL]");
    expect(out).toContain("[OPENTELM_KEY]");
    expect(out).toContain("[PHONE]");
  });
});

describe("pii.detectPii", () => {
  it("returns matched category names", () => {
    expect(detectPii("alice@x.com")).toEqual(["email"]);
  });
  it("returns empty for clean text", () => {
    expect(detectPii("hello world")).toEqual([]);
  });
});

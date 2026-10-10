import { describe, expect, it } from "vitest";
import { detectPii, scrubPii } from "../src/pii";

// Most cases live in test/parity.test.ts (shared with the Python SDK).
describe("scrubPii", () => {
  it("keeps the country code out of international numbers", () => {
    // Regression: the Indian pattern ran first and left "+44 [PHONE]".
    expect(scrubPii("call +44 7911123456")).toBe("call [PHONE]");
  });

  it("labels Anthropic keys as Anthropic keys", () => {
    // Regression: the OpenAI pattern ran first and matched sk-ant-... too.
    expect(scrubPii("sk-ant-api03-abcdefghijklmnopqrstuv")).toBe("[ANTHROPIC_KEY]");
  });

  it("is stable, so identical prompts still group together", () => {
    expect(scrubPii("hi a@b.co")).toBe(scrubPii("hi x@y.io"));
  });

  it("leaves ordinary numbers alone", () => {
    expect(scrubPii("order 12345 shipped 2026-10-11")).toBe("order 12345 shipped 2026-10-11");
  });

  it("detectPii reports categories", () => {
    expect(detectPii("a@b.co 9876543210")).toEqual(["email", "phone_in"]);
    expect(detectPii("nothing here")).toEqual([]);
  });
});

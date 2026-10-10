// Cross-SDK parity: the same payload must produce the same text (and so the
// same prompt_hash) in Node as in the Python SDK. The fixture is generated
// from the Python implementation: sdk/testdata/generate.py.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { anthropicResponseText, messagesToText, openaiResponseText, responsesApiResponseText } from "../src/text";
import { scrubPii } from "../src/pii";

const fixture = JSON.parse(readFileSync(
  fileURLToPath(new URL("../../testdata/hash_parity.json", import.meta.url)), "utf8"));

const responseFns: Record<string, (r: unknown) => string> = {
  openai: openaiResponseText, anthropic: anthropicResponseText, responses: responsesApiResponseText,
};

describe("hash parity with the Python SDK", () => {
  it.each(fixture.messages.map((c: any) => [c.name, c]))("messages: %s", (_name, c: any) => {
    expect(messagesToText(c.messages, c.system)).toBe(c.expected);
  });
  it.each(fixture.responses.map((c: any) => [c.name, c]))("response: %s", (_name, c: any) => {
    expect(responseFns[c.kind](c.response)).toBe(c.expected);
  });
  it.each(fixture.pii.map((c: any) => [c.input, c]))("pii: %s", (_input, c: any) => {
    expect(scrubPii(c.input)).toBe(c.expected);
  });
});

// patch.test.ts — verifies the monkey-patch records an event with the
// right shape after a (mocked) OpenAI call.
//
// We don't depend on the real `openai` package. Instead we stub
// node's `require` so `tryRequire("openai")` returns a fake module
// whose Chat.Completions.prototype.create returns a canned response.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Module from "node:module";
import type { LLMEvent } from "../src/flush";

// ---------------------------------------------------------------
// Build the fake openai module
// ---------------------------------------------------------------
class FakeCompletions {
  async create(_params: any) {
    return {
      choices: [{ message: { role: "assistant", content: "Paris." } }],
      usage:   { prompt_tokens: 11, completion_tokens: 3 },
    };
  }
}

class FakeOpenAI {
  static Chat = { Completions: FakeCompletions };
  chat = { completions: new FakeCompletions() };
}

// Wire it up before importing patch.ts so tryRequire("openai") sees it.
const originalResolve = (Module as any)._resolveFilename;
const originalLoad    = (Module as any)._load;

beforeEach(() => {
  (Module as any)._load = function (request: string, ...rest: any[]) {
    if (request === "openai") {
      return { default: FakeOpenAI, OpenAI: FakeOpenAI };
    }
    if (request === "@anthropic-ai/sdk") {
      // Force tryRequire to return null for anthropic in these tests
      throw new Error("Cannot find module '@anthropic-ai/sdk'");
    }
    return originalLoad.call(this, request, ...rest);
  };
});

afterEach(() => {
  (Module as any)._resolveFilename = originalResolve;
  (Module as any)._load = originalLoad;
  vi.resetModules();
});

describe("patch — OpenAI chat completions", () => {
  it("captures a non-streaming completion as an LLMEvent", async () => {
    // Re-import after the require-shim is in place
    const { patchAll } = await import("../src/patch");
    const { EventFlusher } = await import("../src/flush");

    const flusher = new EventFlusher({
      endpoint: "http://localhost:1/v1/ingest",
      apiKey:   "opentelm_live_test",
      flushIntervalMs: 60_000,
    });

    // Capture sends without going to the network
    const captured: LLMEvent[] = [];
    // @ts-expect-error stubbing private
    flusher.send = async (events: LLMEvent[]) => { captured.push(...events); };

    patchAll({
      flusher,
      scrubPii: true,
      getVersion: () => "v1",
      sdkVersion: "test",
    });

    const client = new FakeOpenAI();
    const result = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "Capital of France?" }],
    } as any);

    expect((result as any).choices[0].message.content).toBe("Paris.");
    await flusher.flush();

    expect(captured.length).toBe(1);
    const ev = captured[0];
    expect(ev.provider).toBe("openai");
    expect(ev.model).toBe("gpt-4o");
    expect(ev.prompt_tokens).toBe(11);
    expect(ev.completion_tokens).toBe(3);
    expect(ev.status).toBe("success");
    expect(ev.deployment_version).toBe("v1");
    expect(ev.prompt_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(ev.response_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(ev.latency_ms).toBeGreaterThanOrEqual(0);

    await flusher.shutdown();
  });

  it("does not double-patch when patchAll is called twice", async () => {
    const { patchAll } = await import("../src/patch");
    const { EventFlusher } = await import("../src/flush");

    const flusher = new EventFlusher({
      endpoint: "http://localhost:1/v1/ingest",
      apiKey:   "opentelm_live_test",
      flushIntervalMs: 60_000,
    });
    const captured: LLMEvent[] = [];
    // @ts-expect-error stub
    flusher.send = async (events: LLMEvent[]) => { captured.push(...events); };

    patchAll({ flusher, scrubPii: false, getVersion: () => null, sdkVersion: "t" });
    patchAll({ flusher, scrubPii: false, getVersion: () => null, sdkVersion: "t" });

    const client = new FakeOpenAI();
    await client.chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    } as any);

    await flusher.flush();
    expect(captured.length).toBe(1); // not 2
    await flusher.shutdown();
  });
});

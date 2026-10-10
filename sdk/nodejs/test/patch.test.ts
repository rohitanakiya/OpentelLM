// Instrumentation tests against the real openai / @anthropic-ai/sdk packages
// (fake fetch, no network). Mirrors sdk/python/tests/test_instrumentation.py.

import { afterEach, describe, expect, it } from "vitest";
import { createRequire } from "node:module";
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import opentelm from "../src/index";
import type { LLMEvent } from "../src/flush";
import {
  FakeProvider, anthropicEvents, anthropicMessage, initWithSink, openaiChunks,
  openaiCompletion, responsesResult, sha, sse, sseNamed,
} from "./helpers";

const HEX64 = /^[0-9a-f]{64}$/;

function assertValidEvent(ev: LLMEvent) {
  expect(["openai", "anthropic", "google", "cohere", "other"]).toContain(ev.provider);
  expect(["success", "error", "timeout"]).toContain(ev.status);
  expect(ev.prompt_hash).toMatch(HEX64);
  expect(ev.response_hash).toMatch(HEX64);
  for (const k of ["prompt_tokens", "completion_tokens", "latency_ms"] as const) {
    expect(Number.isInteger(ev[k]) && ev[k] >= 0).toBe(true);
  }
  for (const v of Object.values(ev.tags ?? {})) expect(typeof v).toBe("string");
}

async function only(sink: { events(): Promise<LLMEvent[]> }): Promise<LLMEvent> {
  const events = await sink.events();
  expect(events).toHaveLength(1);
  assertValidEvent(events[0]);
  return events[0];
}

const openaiClient = (p: FakeProvider, extra: Record<string, unknown> = {}) =>
  new OpenAI({ apiKey: "sk-test", maxRetries: 0, fetch: p.fetch as any, ...extra });
const anthropicClient = (p: FakeProvider) =>
  new Anthropic({ apiKey: "sk-ant-test", maxRetries: 0, fetch: p.fetch as any });

afterEach(async () => {
  await opentelm.shutdown();
});

// ---------------------------------------------------------------------------
// OpenAI chat completions
// ---------------------------------------------------------------------------
describe("openai chat.completions", () => {
  it("records a call", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    const res = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "Capital of France?" }],
    });
    expect(res.choices[0].message.content).toBe("Paris");
    const ev = await only(sink);
    expect(ev.provider).toBe("openai");
    expect(ev.model).toBe("gpt-4o");
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([12, 3]);
    expect(ev.prompt_hash).toBe(await sha("user: Capital of France?"));
    expect(ev.response_hash).toBe(await sha("Paris"));
  });

  it("keeps the APIPromise API (.withResponse / .asResponse) intact", async () => {
    // Regression: create() used to become an async function returning a
    // plain Promise, so these methods disappeared.
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("Paris")).json(openaiCompletion("Rome"));
    const c = openaiClient(p);
    const { data, response } = await c.chat.completions
      .create({ model: "gpt-4o", messages: [{ role: "user", content: "a" }] })
      .withResponse();
    expect(data.choices[0].message.content).toBe("Paris");
    expect(response.status).toBe(200);
    const raw = await c.chat.completions
      .create({ model: "gpt-4o", messages: [{ role: "user", content: "b" }] })
      .asResponse();
    expect((await raw.json()).choices[0].message.content).toBe("Rome"); // body unread by us
    expect(await sink.events()).toHaveLength(1); // withResponse traced; raw asResponse is not
  });

  it("does not crash on tool-call conversations (content: null)", async () => {
    const sink = await initWithSink();
    const toolCalls = [{ id: "call_1", type: "function", function: { name: "get_weather", arguments: '{"city":"Paris"}' } }];
    const p = new FakeProvider().json(openaiCompletion(null, toolCalls));
    const res = await openaiClient(p).chat.completions.create({
      model: "gpt-4o",
      messages: [
        { role: "user", content: "Weather in Paris?" },
        { role: "assistant", content: null, tool_calls: toolCalls as any },
        { role: "tool", tool_call_id: "call_1", content: "18C" },
      ],
    });
    expect(res.choices[0].message.tool_calls?.[0]).toBeTruthy();
    const ev = await only(sink);
    expect(ev.response_hash).toBe(await sha('[tool_call:get_weather] {"city":"Paris"}'));
  });

  it("hashes multimodal content like the Python SDK", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("A cat"));
    await openaiClient(p).chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: [
        { type: "text", text: "What is this?" },
        { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
      ] }],
    });
    expect((await only(sink)).prompt_hash).toBe(await sha("user: What is this?\n[image_url]"));
  });

  it("propagates provider errors unchanged and records them", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json({ error: { message: "boom", type: "server_error" } }, 500);
    await expect(openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    })).rejects.toBeInstanceOf(OpenAI.InternalServerError);
    const ev = await only(sink);
    expect(ev.status).toBe("error");
    expect(ev.error_code).toBe("500");
  });

  it("records timeouts as status=timeout", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().hang();
    await expect(openaiClient(p, { timeout: 50 }).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    })).rejects.toBeInstanceOf(OpenAI.APIConnectionTimeoutError);
    expect((await only(sink)).status).toBe("timeout");
  });

  it("covers clients created before init()", async () => {
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    const client = openaiClient(p);
    const sink = await initWithSink();
    await client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
    expect(await sink.events()).toHaveLength(1);
  });

  it("covers the CommonJS copy of openai as well as the ES module copy", async () => {
    // `import` and `require` load two separate copies with separate classes.
    const OpenAICjs = createRequire(import.meta.url)("openai");
    const Klass = OpenAICjs.default ?? OpenAICjs.OpenAI ?? OpenAICjs;
    expect(Klass).not.toBe(OpenAI);
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    await new Klass({ apiKey: "x", maxRetries: 0, fetch: p.fetch }).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    });
    expect(await sink.events()).toHaveLength(1);
  });

  it("parse() helper is traced once", async () => {
    const completions: any = (openaiClient(new FakeProvider())).chat.completions;
    if (typeof completions.parse !== "function") return; // openai < 5 keeps it under beta
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    const res = await openaiClient(p).chat.completions.parse({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    });
    expect(res.choices[0].message.content).toBe("Paris");
    expect(await sink.events()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// OpenAI streaming
// ---------------------------------------------------------------------------
describe("openai streaming", () => {
  it("records after the stream is consumed; the Stream object is untouched", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sse(openaiChunks(["Pa", "ris"])));
    const stream = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "Capital?" }], stream: true,
    });
    expect(stream.controller).toBeInstanceOf(AbortController);
    expect(typeof stream.toReadableStream).toBe("function");
    expect(await sink.events()).toHaveLength(0);
    let text = "";
    for await (const chunk of stream) text += chunk.choices[0]?.delta?.content ?? "";
    expect(text).toBe("Paris");
    const ev = await only(sink);
    expect(ev.response_hash).toBe(await sha("Paris"));
    expect(ev.tags?.["otlm.tokens_estimated"]).toBe("true");
    expect(ev.tags?.["otlm.ttft_ms"]).toBeDefined();
  });

  it("uses reported usage when include_usage is set", async () => {
    const sink = await initWithSink();
    const usage = { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 };
    const p = new FakeProvider().sse(sse(openaiChunks(["Pa", "ris"], usage)));
    const stream = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "Capital?" }], stream: true,
      stream_options: { include_usage: true },
    });
    for await (const _ of stream) { /* drain */ }
    const ev = await only(sink);
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([7, 2]);
    expect(ev.tags?.["otlm.tokens_estimated"]).toBeUndefined();
  });

  it("marks streams abandoned early", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sse(openaiChunks(["a", "b", "c", "d"])));
    const stream = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "x" }], stream: true,
    });
    for await (const _ of stream) break;
    expect((await only(sink)).tags?.["otlm.stream_incomplete"]).toBe("true");
  });

  it("tee() still works and the call is recorded once", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sse(openaiChunks(["Pa", "ris"])));
    const stream = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "x" }], stream: true,
    });
    const [left, right] = stream.tee();
    let a = "", b = "";
    for await (const c of left) a += c.choices[0]?.delta?.content ?? "";
    for await (const c of right) b += c.choices[0]?.delta?.content ?? "";
    expect([a, b]).toEqual(["Paris", "Paris"]);
    expect(await sink.events()).toHaveLength(1);
  });

  it("chat.completions.stream() helper is traced once", async () => {
    const c: any = openaiClient(new FakeProvider());
    const completions = typeof c.chat.completions.stream === "function" ? "chat" : "beta";
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sse(openaiChunks(["Pa", "ris"])));
    const client: any = openaiClient(p);
    const target = completions === "chat" ? client.chat.completions : client.beta.chat.completions;
    const runner = target.stream({ model: "gpt-4o", messages: [{ role: "user", content: "x" }] });
    const final = await runner.finalChatCompletion();
    expect(final.choices[0].message.content).toBe("Paris");
    const ev = await only(sink);
    expect(ev.response_hash).toBe(await sha("Paris"));
  });
});

// ---------------------------------------------------------------------------
// OpenAI Responses API
// ---------------------------------------------------------------------------
describe("openai responses", () => {
  const hasResponses = typeof (new OpenAI({ apiKey: "x" }) as any).responses?.create === "function";

  it.runIf(hasResponses)("records responses.create", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(responsesResult("Paris"));
    const res: any = await (openaiClient(p) as any).responses.create({
      model: "gpt-4.1", input: "Capital of France?", instructions: "Be brief.",
    });
    expect(res.output_text).toBe("Paris");
    const ev = await only(sink);
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([9, 2]);
    expect(ev.prompt_hash).toBe(await sha("system: Be brief.\nCapital of France?"));
    expect(ev.response_hash).toBe(await sha("Paris"));
  });

  it.runIf(hasResponses)("records a streamed response", async () => {
    const sink = await initWithSink();
    const events: Array<[string, unknown]> = [
      ["response.output_text.delta", { type: "response.output_text.delta", item_id: "m1", output_index: 0, content_index: 0, delta: "Pa", sequence_number: 1, logprobs: [] }],
      ["response.output_text.delta", { type: "response.output_text.delta", item_id: "m1", output_index: 0, content_index: 0, delta: "ris", sequence_number: 2, logprobs: [] }],
      ["response.completed", { type: "response.completed", response: responsesResult("Paris"), sequence_number: 3 }],
    ];
    const p = new FakeProvider().sse(sseNamed(events));
    const stream: any = await (openaiClient(p) as any).responses.create({ model: "gpt-4.1", input: "Capital?", stream: true });
    for await (const _ of stream) { /* drain */ }
    const ev = await only(sink);
    expect(ev.response_hash).toBe(await sha("Paris"));
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([9, 2]);
  });
});

// ---------------------------------------------------------------------------
// Anthropic
// ---------------------------------------------------------------------------
describe("anthropic messages", () => {
  it("records a call, counting cached prompt tokens", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(anthropicMessage("Paris", 10, 2, 5));
    const res = await anthropicClient(p).messages.create({
      model: "claude-test-model", max_tokens: 100, system: "Be brief.",
      messages: [{ role: "user", content: "Capital of France?" }],
    });
    expect((res.content[0] as any).text).toBe("Paris");
    const ev = await only(sink);
    expect(ev.provider).toBe("anthropic");
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([15, 2]);
    expect(ev.prompt_hash).toBe(await sha("system: Be brief.\nuser: Capital of France?"));
  });

  it("hashes tool_use / tool_result blocks like the Python SDK", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(anthropicMessage("It is 18C"));
    await anthropicClient(p).messages.create({
      model: "claude-test-model", max_tokens: 100,
      messages: [
        { role: "user", content: "Weather in Paris?" },
        { role: "assistant", content: [{ type: "tool_use", id: "t1", name: "get_weather", input: { city: "Paris" } }] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: [{ type: "text", text: "18C" }] }] },
      ],
    });
    expect((await only(sink)).prompt_hash).toBe(
      await sha('user: Weather in Paris?\nassistant: [tool_use:get_weather] {"city": "Paris"}\nuser: 18C'));
  });

  it("records a streamed create()", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sseNamed(anthropicEvents(["Pa", "ris"], 11, 4)));
    const stream = await anthropicClient(p).messages.create({
      model: "claude-test-model", max_tokens: 100, stream: true,
      messages: [{ role: "user", content: "Capital?" }],
    });
    for await (const _ of stream) { /* drain */ }
    const ev = await only(sink);
    expect([ev.prompt_tokens, ev.completion_tokens]).toEqual([11, 4]);
    expect(ev.response_hash).toBe(await sha("Paris"));
  });

  it("records messages.stream() (it calls create().withResponse() internally)", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().sse(sseNamed(anthropicEvents(["Pa", "ris"])));
    const stream = anthropicClient(p).messages.stream({
      model: "claude-test-model", max_tokens: 100, messages: [{ role: "user", content: "Capital?" }],
    });
    const final = await stream.finalMessage();
    expect((final.content[0] as any).text).toBe("Paris");
    const ev = await only(sink);
    expect(ev.response_hash).toBe(await sha("Paris"));
    expect(ev.completion_tokens).toBe(4);
  });

  it("records messages.stream() request errors", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json({ type: "error", error: { type: "overloaded_error", message: "busy" } }, 529);
    const stream = anthropicClient(p).messages.stream({
      model: "claude-test-model", max_tokens: 100, messages: [{ role: "user", content: "x" }],
    });
    await expect(stream.finalMessage()).rejects.toBeInstanceOf(Anthropic.APIError);
    const ev = await only(sink);
    expect(ev.status).toBe("error");
    expect(ev.error_code).toBe("529");
  });
});

// ---------------------------------------------------------------------------
// Lifecycle, tagging, privacy
// ---------------------------------------------------------------------------
describe("lifecycle", () => {
  it("init() twice does not double-record", async () => {
    const sink = await initWithSink();
    await opentelm.init({ apiKey: "another" });
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    await openaiClient(p).chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
    expect(await sink.events()).toHaveLength(1);
  });

  it("shutdown() restores the original methods", async () => {
    const proto: any = (OpenAI as any).Chat.Completions.prototype;
    const before = proto.create;
    await initWithSink();
    expect(proto.create).not.toBe(before);
    await opentelm.shutdown();
    expect(proto.create).toBe(before);
  });

  it("tags events with the deployment version", async () => {
    const sink = await initWithSink();
    opentelm.setVersion("prompt-v2");
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    await openaiClient(p).chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
    expect((await only(sink)).deployment_version).toBe("prompt-v2");
    opentelm.setVersion(null);
  });

  it("scrubs PII before hashing", async () => {
    const sink = await initWithSink();
    const p = new FakeProvider().json(openaiCompletion("ok"));
    await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "Email ronu@example.com please" }],
    });
    expect((await only(sink)).prompt_hash).toBe(await sha("user: Email [EMAIL] please"));
  });

  it("track() records manual events", async () => {
    const sink = await initWithSink();
    opentelm.track({ model: "llama-3-8b", provider: "other", prompt: "hi", response: "hello",
                     promptTokens: 1, completionTokens: 1, latencyMs: 40, tags: { host: "gpu-1" } });
    const ev = await only(sink);
    expect(ev.model).toBe("llama-3-8b");
    expect(ev.tags?.host).toBe("gpu-1");
  });

  it("a bug in recording never reaches the caller", async () => {
    await initWithSink({ defaultTags: { get boom(): string { throw new Error("tag getter exploded"); } } as any })
      .catch(() => undefined);
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    const res = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    });
    expect(res.choices[0].message.content).toBe("Paris");
  });

  it("without init(), calls pass straight through", async () => {
    const p = new FakeProvider().json(openaiCompletion("Paris"));
    const res = await openaiClient(p).chat.completions.create({
      model: "gpt-4o", messages: [{ role: "user", content: "hi" }],
    });
    expect(res.choices[0].message.content).toBe("Paris");
  });
});

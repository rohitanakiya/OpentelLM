// Test harness: the REAL openai / @anthropic-ai/sdk clients with a fake
// `fetch`, so the SDK is exercised against the providers' actual classes,
// APIPromise, Stream and SSE parsing, with no network access.

import opentelm from "../src/index";
import type { LLMEvent } from "../src/flush";

type Responder = (url: string, init: RequestInit) => Promise<Response> | Response;

export class FakeProvider {
  responses: Responder[] = [];
  requests: Array<{ url: string; body: any }> = [];

  json(payload: unknown, status = 200): this {
    this.responses.push(() => new Response(JSON.stringify(payload), {
      status, headers: { "content-type": "application/json" },
    }));
    return this;
  }

  sse(body: string): this {
    this.responses.push(() => new Response(body, {
      status: 200, headers: { "content-type": "text/event-stream" },
    }));
    return this;
  }

  /** Never answers; rejects when the client aborts (its timeout fires). */
  hang(): this {
    this.responses.push((_url, init) => new Promise((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    return this;
  }

  fetch = async (input: any, init: RequestInit = {}): Promise<Response> => {
    const url = typeof input === "string" ? input : input?.url ?? String(input);
    let body: any = init.body;
    try { body = JSON.parse(String(init.body)); } catch { /* keep raw */ }
    this.requests.push({ url, body });
    const next = this.responses.shift();
    if (!next) throw new Error("FakeProvider: no response queued");
    return next(url, init);
  };
}

export class Sink {
  batches: LLMEvent[][] = [];
  status = 202;
  sender = async (body: string): Promise<number> => {
    this.batches.push(JSON.parse(body).events);
    return this.status;
  };
  async events(): Promise<LLMEvent[]> {
    await opentelm.flush();
    return this.batches.flat();
  }
}

export async function initWithSink(extra: Record<string, unknown> = {}): Promise<Sink> {
  const sink = new Sink();
  await opentelm.init({ apiKey: "opentelm_live_test", sender: sink.sender, flushIntervalMs: 60_000, ...extra });
  return sink;
}

// ---------------------------------------------------------------------------
// Canned provider payloads (same shapes as sdk/python/tests/conftest.py)
// ---------------------------------------------------------------------------
export function openaiCompletion(content: string | null = "Paris", toolCalls?: unknown[],
                                 promptTokens = 12, completionTokens = 3) {
  const message: Record<string, unknown> = { role: "assistant", content };
  if (toolCalls) message.tool_calls = toolCalls;
  return {
    id: "chatcmpl-1", object: "chat.completion", created: 0, model: "gpt-4o-2024-08-06",
    choices: [{ index: 0, message, finish_reason: "stop", logprobs: null }],
    usage: { prompt_tokens: promptTokens, completion_tokens: completionTokens,
             total_tokens: promptTokens + completionTokens },
  };
}

export function openaiChunks(pieces: string[], usage?: Record<string, number>) {
  const base = { id: "chatcmpl-1", object: "chat.completion.chunk", created: 0, model: "gpt-4o" };
  const chunks: unknown[] = pieces.map((p, i) => ({
    ...base,
    choices: [{ index: 0, delta: i === 0 ? { role: "assistant", content: p } : { content: p }, finish_reason: null }],
  }));
  chunks.push({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
  if (usage) chunks.push({ ...base, choices: [], usage });
  return chunks;
}

export function anthropicMessage(text = "Paris", inputTokens = 10, outputTokens = 2, cacheRead = 0) {
  return {
    id: "msg_1", type: "message", role: "assistant", model: "claude-test-model",
    content: [{ type: "text", text }], stop_reason: "end_turn", stop_sequence: null,
    usage: { input_tokens: inputTokens, output_tokens: outputTokens,
             cache_read_input_tokens: cacheRead, cache_creation_input_tokens: 0 },
  };
}

export function anthropicEvents(pieces: string[], inputTokens = 10, outputTokens = 4): Array<[string, unknown]> {
  const msg = { ...anthropicMessage("", inputTokens, 1), content: [] };
  const events: Array<[string, unknown]> = [["message_start", { type: "message_start", message: msg }]];
  events.push(["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }]);
  for (const p of pieces) {
    events.push(["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: p } }]);
  }
  events.push(["content_block_stop", { type: "content_block_stop", index: 0 }]);
  events.push(["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null },
                                   usage: { output_tokens: outputTokens } }]);
  events.push(["message_stop", { type: "message_stop" }]);
  return events;
}

export function responsesResult(text = "Paris") {
  return {
    id: "resp_1", object: "response", created_at: 0, model: "gpt-4.1", status: "completed",
    parallel_tool_calls: true, tool_choice: "auto", tools: [],
    output: [{ type: "message", id: "m1", role: "assistant", status: "completed",
               content: [{ type: "output_text", text, annotations: [] }] }],
    usage: { input_tokens: 9, output_tokens: 2, total_tokens: 11,
             input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } },
  };
}

export const sse = (chunks: unknown[]) =>
  chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";

export const sseNamed = (events: Array<[string, unknown]>) =>
  events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");

export async function sha(text: string): Promise<string> {
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(text, "utf8").digest("hex");
}

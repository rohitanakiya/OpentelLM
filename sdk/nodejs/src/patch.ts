// Monkey-patches OpenAI and Anthropic SDK methods to record telemetry.
//
// We DO NOT bundle the SDK packages as deps. Instead we resolve them from
// the user's node_modules at init() time. If a user only has openai
// installed, the anthropic patch is a silent no-op, and vice versa.
//
// Streaming caveat (documented in README):
//   For streaming responses, the upstream SDKs return an AsyncIterable.
//   We wrap the iterable so we observe every chunk, count chunks, and
//   record the event after the stream closes. Token counts in streaming
//   mode are APPROXIMATIONS — providers don't always return final token
//   usage on every event.

import { createHash, randomUUID } from "node:crypto";
import type { EventFlusher, LLMEvent } from "./flush";
import { scrubPii } from "./pii";

interface PatchOptions {
  flusher: EventFlusher;
  scrubPii: boolean;
  getVersion: () => string | null;
  sdkVersion: string;
}

// Track whether we've already patched a given module so users calling
// init() twice don't double-instrument.
const _patched = new WeakSet<object>();

function sha256(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

function tryRequire<T = unknown>(name: string): T | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require(name) as T;
  } catch {
    return null;
  }
}

export function patchAll(opts: PatchOptions): void {
  patchOpenAI(opts);
  patchAnthropic(opts);
}

// ---------------------------------------------------------------------------
// OpenAI
// ---------------------------------------------------------------------------
function patchOpenAI(opts: PatchOptions): void {
  // The default export is a class; instances have .chat.completions.create.
  // Patching the prototype catches every future instance.
  const mod = tryRequire<{ default: any; OpenAI?: any }>("openai");
  if (!mod) return;

  const Klass = mod.default ?? mod.OpenAI;
  if (!Klass || _patched.has(Klass.prototype)) return;
  _patched.add(Klass.prototype);

  // The client lazy-instantiates a `chat.completions` resource. We patch
  // on first access by wrapping the Completions class prototype.
  const Completions = Klass?.Chat?.Completions;
  if (Completions && !_patched.has(Completions.prototype)) {
    _patched.add(Completions.prototype);
    const original = Completions.prototype.create;
    Completions.prototype.create = async function patchedCreate(...args: any[]) {
      const params = args[0] ?? {};
      const startedAt = Date.now();
      const promptText = JSON.stringify(params.messages ?? "");

      try {
        const result = await original.apply(this, args);
        if (params.stream) {
          // result is an AsyncIterable of chunks
          return wrapOpenAIStream(result, params, promptText, startedAt, opts);
        }
        recordOpenAINonStream(result, params, promptText, startedAt, opts);
        return result;
      } catch (err) {
        recordError("openai", params.model ?? "unknown", promptText, startedAt, err, opts);
        throw err;
      }
    };
  }
}

function recordOpenAINonStream(
  result: any, params: any, promptText: string, startedAt: number, opts: PatchOptions,
): void {
  const usage = result?.usage ?? {};
  const choice = result?.choices?.[0];
  const responseText = JSON.stringify(choice?.message ?? "");
  emitEvent({
    provider: "openai",
    model: params.model ?? "unknown",
    promptText,
    responseText,
    promptTokens: Number(usage.prompt_tokens ?? 0),
    completionTokens: Number(usage.completion_tokens ?? 0),
    startedAt,
    status: "success",
  }, opts);
}

async function* wrapOpenAIStream(
  stream: AsyncIterable<any>, params: any, promptText: string,
  startedAt: number, opts: PatchOptions,
): AsyncIterable<any> {
  const responseChunks: string[] = [];
  let lastUsage: any = null;
  let chunkCount = 0;
  try {
    for await (const chunk of stream) {
      chunkCount++;
      const delta = chunk?.choices?.[0]?.delta?.content;
      if (typeof delta === "string") responseChunks.push(delta);
      // OpenAI emits a final chunk with usage when stream_options.include_usage=true
      if (chunk?.usage) lastUsage = chunk.usage;
      yield chunk;
    }
    const responseText = responseChunks.join("");
    // If we didn't get usage, approximate completion tokens from chunk count
    // (each chunk averages ~4 chars ≈ 1 token in English).
    const completionTokens = Number(lastUsage?.completion_tokens ?? chunkCount);
    const promptTokens     = Number(lastUsage?.prompt_tokens     ?? Math.ceil(promptText.length / 4));
    emitEvent({
      provider: "openai",
      model: params.model ?? "unknown",
      promptText,
      responseText,
      promptTokens,
      completionTokens,
      startedAt,
      status: "success",
    }, opts);
  } catch (err) {
    recordError("openai", params.model ?? "unknown", promptText, startedAt, err, opts);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Anthropic
// ---------------------------------------------------------------------------
function patchAnthropic(opts: PatchOptions): void {
  const mod = tryRequire<{ default: any; Anthropic?: any }>("@anthropic-ai/sdk");
  if (!mod) return;

  const Klass = mod.default ?? mod.Anthropic;
  if (!Klass || _patched.has(Klass.prototype)) return;
  _patched.add(Klass.prototype);

  const Messages = Klass?.Messages;
  if (Messages && !_patched.has(Messages.prototype)) {
    _patched.add(Messages.prototype);
    const original = Messages.prototype.create;
    Messages.prototype.create = async function patchedCreate(...args: any[]) {
      const params = args[0] ?? {};
      const startedAt = Date.now();
      const promptText = JSON.stringify(params.messages ?? "");

      try {
        const result = await original.apply(this, args);
        if (params.stream) {
          return wrapAnthropicStream(result, params, promptText, startedAt, opts);
        }
        recordAnthropicNonStream(result, params, promptText, startedAt, opts);
        return result;
      } catch (err) {
        recordError("anthropic", params.model ?? "unknown", promptText, startedAt, err, opts);
        throw err;
      }
    };
  }
}

function recordAnthropicNonStream(
  result: any, params: any, promptText: string, startedAt: number, opts: PatchOptions,
): void {
  const usage = result?.usage ?? {};
  const responseText = JSON.stringify(result?.content ?? "");
  emitEvent({
    provider: "anthropic",
    model: params.model ?? "unknown",
    promptText,
    responseText,
    promptTokens: Number(usage.input_tokens ?? 0),
    completionTokens: Number(usage.output_tokens ?? 0),
    startedAt,
    status: "success",
  }, opts);
}

async function* wrapAnthropicStream(
  stream: AsyncIterable<any>, params: any, promptText: string,
  startedAt: number, opts: PatchOptions,
): AsyncIterable<any> {
  const responseChunks: string[] = [];
  let inputTokens = 0;
  let outputTokens = 0;
  try {
    for await (const event of stream) {
      // Anthropic events: message_start, content_block_delta, message_delta, message_stop
      if (event?.type === "message_start") {
        inputTokens = Number(event?.message?.usage?.input_tokens ?? 0);
      } else if (event?.type === "content_block_delta") {
        const text = event?.delta?.text;
        if (typeof text === "string") responseChunks.push(text);
      } else if (event?.type === "message_delta") {
        outputTokens = Number(event?.usage?.output_tokens ?? outputTokens);
      }
      yield event;
    }
    emitEvent({
      provider: "anthropic",
      model: params.model ?? "unknown",
      promptText,
      responseText: responseChunks.join(""),
      promptTokens: inputTokens,
      completionTokens: outputTokens || Math.ceil(responseChunks.join("").length / 4),
      startedAt,
      status: "success",
    }, opts);
  } catch (err) {
    recordError("anthropic", params.model ?? "unknown", promptText, startedAt, err, opts);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Common emitter
// ---------------------------------------------------------------------------
interface EmitArgs {
  provider: LLMEvent["provider"];
  model: string;
  promptText: string;
  responseText: string;
  promptTokens: number;
  completionTokens: number;
  startedAt: number;
  status: LLMEvent["status"];
  error?: { code?: string; message?: string };
}

function emitEvent(args: EmitArgs, opts: PatchOptions): void {
  const promptForHash   = opts.scrubPii ? scrubPii(args.promptText)   : args.promptText;
  const responseForHash = opts.scrubPii ? scrubPii(args.responseText) : args.responseText;

  const event: LLMEvent = {
    trace_id: randomUUID(),
    span_id: randomUUID(),
    model: args.model,
    provider: args.provider,
    prompt_tokens: args.promptTokens,
    completion_tokens: args.completionTokens,
    latency_ms: Math.max(0, Date.now() - args.startedAt),
    status: args.status,
    error_code: args.error?.code ?? null,
    error_message: args.error?.message ?? null,
    prompt_hash: sha256(promptForHash),
    response_hash: sha256(responseForHash),
    sdk_version: opts.sdkVersion,
    deployment_version: opts.getVersion(),
    timestamp_ms: Date.now(),
  };

  opts.flusher.enqueue(event);
}

function recordError(
  provider: LLMEvent["provider"],
  model: string,
  promptText: string,
  startedAt: number,
  err: unknown,
  opts: PatchOptions,
): void {
  const e = err as { code?: string; message?: string; status?: number };
  emitEvent({
    provider,
    model,
    promptText,
    responseText: "",
    promptTokens: 0,
    completionTokens: 0,
    startedAt,
    status: "error",
    error: { code: String(e?.status ?? e?.code ?? "unknown"), message: e?.message },
  }, opts);
}

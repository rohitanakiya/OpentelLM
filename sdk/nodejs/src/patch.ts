// Auto-instrumentation for the `openai` and `@anthropic-ai/sdk` packages.
//
// What gets patched:
//   openai              Chat.Completions.prototype.create   (.parse/.stream go through it)
//                       Responses.prototype.create          (openai >= 4.87)
//   @anthropic-ai/sdk   Messages.prototype.create           (.stream() goes through it)
//
// ESM AND CJS. Both packages ship two builds, and Node loads them as two
// separate module instances with separate classes: an app that does
// `import OpenAI from "openai"` uses different classes from one that does
// `require("openai")`. The previous version only patched the require()
// copy, and in our ESM build that require() call failed silently, so for
// `import` users the SDK did nothing at all. We now patch both copies: the
// CJS copy synchronously, the ESM copy via dynamic import() (which is why
// init() returns a promise).
//
// THE RETURN VALUE IS UNTOUCHED. create() returns an `APIPromise`: a
// Promise subclass with .withResponse(), .asResponse() and friends, used
// both by applications and by the SDKs' own helpers (Anthropic's
// messages.stream() calls create(...).withResponse()). The previous version
// replaced create() with an async function, which returned a plain Promise
// and broke all of those. We now return the original object and observe it
// through the per-instance hooks every parse path goes through:
//   * apiPromise.parseResponse  - called with the parsed body (or Stream)
//   * apiPromise.parse()        - rejects on HTTP / network errors
//   * apiPromise._thenUnwrap()  - used by .parse() helpers
// .asResponse() (raw Response, body unread) is deliberately not observed.
//
// STREAMS are observed in place: the SDKs' Stream objects read chunks
// through `stream.iterator()`, which we wrap on the instance. The Stream
// keeps its class, `.controller`, `.tee()` and `.toReadableStream()`.
//
// SAFETY: recording code runs inside safe(); nothing it does can throw into
// the caller or change what the caller sees.

import { createRequire } from "node:module";
import {
  anthropicResponseText,
  get,
  messagesToText,
  openaiResponseText,
  responsesApiResponseText,
} from "./text";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface RecordFields {
  provider: "openai" | "anthropic";
  model: string;
  promptText: string;
  responseText: string;
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
  status: "success" | "error" | "timeout";
  errorCode?: string | null;
  errorMessage?: string | null;
  timestampMs: number;
  extraTags?: Record<string, string>;
}

export interface Recorder {
  record(fields: RecordFields): void;
}

// ---------------------------------------------------------------------------
// Process-wide state, shared by every copy of this SDK. An app can load both
// our CJS and ESM builds (e.g. its own `import` plus a dependency's
// `require`); a Symbol.for() key gives them one recorder and one set of
// patches, so calls are never recorded twice.
// ---------------------------------------------------------------------------
interface GlobalState {
  recorder: Recorder | null;
  patches: Array<[object, string, Any]>;
  debugOnce: Set<string>;
}
const STATE_KEY = Symbol.for("opentelm.node.state");
const WRAPPED = Symbol.for("opentelm.node.wrapped");

function state(): GlobalState {
  const g = globalThis as Any;
  if (!g[STATE_KEY]) g[STATE_KEY] = { recorder: null, patches: [], debugOnce: new Set() };
  return g[STATE_KEY];
}

export function setRecorder(recorder: Recorder | null): void {
  state().recorder = recorder;
}

function debugOnce(key: string, message: string): void {
  const s = state();
  if (s.debugOnce.has(key)) return;
  s.debugOnce.add(key);
  if (process.env.OPENTELM_DEBUG) console.debug(`[opentelm] ${message}`);
}

function safe<T>(fn: () => T): T | undefined {
  try {
    return fn();
  } catch (err) {
    debugOnce(`safe:${(err as Error)?.message}`, `recording step failed: ${(err as Error)?.stack ?? err}`);
    return undefined;
  }
}

const approxTokens = (text: string): number => (text ? Math.ceil(text.length / 4) : 0);

// ---------------------------------------------------------------------------
// Provider adapters (mirror sdk/python/opentelm/monkey_patch.py)
// ---------------------------------------------------------------------------
abstract class StreamState {
  text: string[] = [];
  promptTokens?: number;
  completionTokens?: number;
  failed = false;
  abstract feed(chunk: Any): void;
  responseText(): string {
    return this.text.join("");
  }
}

class OpenAIChatStream extends StreamState {
  feed(chunk: Any): void {
    const usage = get(chunk, "usage");
    if (usage) {
      this.promptTokens = get(usage, "prompt_tokens");
      this.completionTokens = get(usage, "completion_tokens");
    }
    for (const choice of get(chunk, "choices") ?? []) {
      const delta = get(choice, "delta");
      const content = get(delta, "content");
      if (typeof content === "string") this.text.push(content);
      for (const call of get(delta, "tool_calls") ?? []) {
        const fn = get(call, "function");
        const name = get(fn, "name");
        if (name) this.text.push(`[tool_call:${name}] `);
        const args = get(fn, "arguments");
        if (typeof args === "string") this.text.push(args);
      }
    }
  }
}

class OpenAIResponsesStream extends StreamState {
  feed(event: Any): void {
    const type = get(event, "type") ?? "";
    if (type === "response.output_text.delta" || type === "response.function_call_arguments.delta") {
      const delta = get(event, "delta");
      if (typeof delta === "string") this.text.push(delta);
    } else if (type === "response.completed" || type === "response.incomplete" || type === "response.failed") {
      const usage = get(get(event, "response"), "usage");
      if (usage) {
        this.promptTokens = get(usage, "input_tokens");
        this.completionTokens = get(usage, "output_tokens");
      }
      if (type === "response.failed") this.failed = true;
    }
  }
}

function anthropicInputTokens(usage: Any): number {
  // Cached prompt tokens are reported separately; count them so apps that
  // use prompt caching don't look like they send near-empty prompts.
  return ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
    .reduce((sum, k) => sum + (Number(get(usage, k)) || 0), 0);
}

class AnthropicStream extends StreamState {
  feed(event: Any): void {
    const type = get(event, "type");
    if (type === "message_start") {
      const usage = get(get(event, "message"), "usage");
      if (usage) {
        this.promptTokens = anthropicInputTokens(usage);
        this.completionTokens = get(usage, "output_tokens");
      }
    } else if (type === "content_block_start") {
      const block = get(event, "content_block");
      if (get(block, "type") === "tool_use") this.text.push(`[tool_use:${get(block, "name") ?? ""}] `);
    } else if (type === "content_block_delta") {
      const delta = get(event, "delta");
      const dtype = get(delta, "type");
      if (dtype === "text_delta") this.text.push(get(delta, "text") ?? "");
      else if (dtype === "input_json_delta") this.text.push(get(delta, "partial_json") ?? "");
    } else if (type === "message_delta") {
      const usage = get(event, "usage");
      if (get(usage, "output_tokens") !== undefined && get(usage, "output_tokens") !== null) {
        this.completionTokens = get(usage, "output_tokens");
      }
      if (get(usage, "input_tokens") !== undefined && get(usage, "input_tokens") !== null) {
        this.promptTokens = Math.max(this.promptTokens ?? 0, anthropicInputTokens(usage));
      }
    }
  }
}

interface Adapter {
  provider: "openai" | "anthropic";
  promptText(params: Any): string;
  isResponse(value: Any): boolean;
  readResponse(value: Any): [string, number, number];
  newStream(): StreamState;
}

const OPENAI_CHAT: Adapter = {
  provider: "openai",
  promptText: (p) => messagesToText(get(p, "messages")),
  isResponse: (v) => Array.isArray(get(v, "choices")),
  readResponse: (v) => {
    const usage = get(v, "usage");
    return [openaiResponseText(v), Number(get(usage, "prompt_tokens")) || 0, Number(get(usage, "completion_tokens")) || 0];
  },
  newStream: () => new OpenAIChatStream(),
};

const OPENAI_RESPONSES: Adapter = {
  provider: "openai",
  promptText: (p) => messagesToText(get(p, "input"), get(p, "instructions")),
  isResponse: (v) => Array.isArray(get(v, "output")) && get(v, "usage") !== undefined,
  readResponse: (v) => {
    const usage = get(v, "usage");
    return [responsesApiResponseText(v), Number(get(usage, "input_tokens")) || 0, Number(get(usage, "output_tokens")) || 0];
  },
  newStream: () => new OpenAIResponsesStream(),
};

const ANTHROPIC: Adapter = {
  provider: "anthropic",
  promptText: (p) => messagesToText(get(p, "messages"), get(p, "system")),
  isResponse: (v) => get(v, "content") !== undefined && get(v, "usage") !== undefined,
  readResponse: (v) => {
    const usage = get(v, "usage");
    return [anthropicResponseText(v), anthropicInputTokens(usage), Number(get(usage, "output_tokens")) || 0];
  },
  newStream: () => new AnthropicStream(),
};

// ---------------------------------------------------------------------------
// One in-flight call
// ---------------------------------------------------------------------------
class Call {
  private readonly start = performance.now();
  private readonly startWall = Date.now();
  private done = false;

  constructor(
    private readonly recorder: Recorder,
    readonly adapter: Adapter,
    private readonly params: Any,
  ) {}

  elapsedMs(): number {
    return Math.floor(performance.now() - this.start);
  }

  private emit(fields: Omit<RecordFields, "provider" | "model" | "promptText" | "latencyMs" | "timestampMs">): void {
    if (this.done) return;
    this.done = true;
    const model = get(this.params, "model");
    this.recorder.record({
      provider: this.adapter.provider,
      model: typeof model === "string" && model ? model : "unknown",
      promptText: safe(() => this.adapter.promptText(this.params)) ?? "",
      latencyMs: this.elapsedMs(),
      timestampMs: this.startWall,
      ...fields,
    });
  }

  success(value: Any): void {
    const [text, pt, ct] = this.adapter.readResponse(value);
    this.emit({ responseText: text, promptTokens: pt, completionTokens: ct, status: "success" });
  }

  error(err: Any): void {
    const name: string = err?.constructor?.name ?? err?.name ?? "Error";
    const timeout = /timeout/i.test(name) || err?.code === "ETIMEDOUT";
    const status = err?.status;
    this.emit({
      responseText: "", promptTokens: 0, completionTokens: 0,
      status: timeout ? "timeout" : "error",
      errorCode: status !== undefined && status !== null ? String(status) : name,
      errorMessage: String(err?.message ?? err),
    });
  }

  finishStream(st: StreamState, opts: { err?: Any; completed: boolean; ttftMs?: number }): void {
    if (opts.err !== undefined) return this.error(opts.err);
    const tags: Record<string, string> = {};
    let pt = st.promptTokens;
    let ct = st.completionTokens;
    const text = st.responseText();
    if (pt === undefined || pt === null || ct === undefined || ct === null) {
      tags["otlm.tokens_estimated"] = "true";
      if (pt === undefined || pt === null) pt = approxTokens(safe(() => this.adapter.promptText(this.params)) ?? "");
      if (ct === undefined || ct === null) ct = approxTokens(text);
    }
    if (opts.ttftMs !== undefined) tags["otlm.ttft_ms"] = String(opts.ttftMs);
    if (!opts.completed) tags["otlm.stream_incomplete"] = "true";
    this.emit({
      responseText: text, promptTokens: Number(pt) || 0, completionTokens: Number(ct) || 0,
      status: st.failed ? "error" : "success", extraTags: tags,
    });
  }
}

// ---------------------------------------------------------------------------
// Observing APIPromises and Streams
// ---------------------------------------------------------------------------
function onValue(call: Call, value: Any): void {
  if (value && typeof value === "object" && typeof value[Symbol.asyncIterator] === "function") {
    observeStream(call, value);
  } else if (call.adapter.isResponse(value)) {
    call.success(value);
  } else {
    debugOnce(`skip:${value?.constructor?.name}`, `not tracing a call that returned ${value?.constructor?.name}`);
  }
}

function hookParseErrors(apiPromise: Any, call: Call): void {
  const originalParse = apiPromise.parse;
  if (typeof originalParse !== "function") return;
  apiPromise.parse = function (this: Any, ...args: Any[]) {
    const parsed = originalParse.apply(this, args);
    // Observe failures (HTTP errors, network errors, timeouts). The extra
    // .then() resolves either way, so it never creates an unhandled
    // rejection, and the caller's own handlers still see the error.
    safe(() => parsed.then(undefined, (err: Any) => { safe(() => call.error(err)); }));
    return parsed;
  };
}

function observeApiPromise(apiPromise: Any, call: Call): void {
  if (!apiPromise || typeof apiPromise !== "object") return;
  const originalParseResponse = apiPromise.parseResponse;
  if (typeof originalParseResponse !== "function") {
    debugOnce("no-parseResponse", "SDK APIPromise layout not recognised; call not traced");
    return;
  }
  apiPromise.parseResponse = function (this: Any, ...args: Any[]) {
    let out: Any;
    try {
      out = originalParseResponse.apply(this, args);
    } catch (err) {
      safe(() => call.error(err));
      throw err;
    }
    safe(() => Promise.resolve(out).then(
      (value) => { safe(() => onValue(call, value)); },
      (err) => { safe(() => call.error(err)); },
    ));
    return out;
  };
  hookParseErrors(apiPromise, call);
  const originalThenUnwrap = apiPromise._thenUnwrap;
  if (typeof originalThenUnwrap === "function") {
    apiPromise._thenUnwrap = function (this: Any, ...args: Any[]) {
      // Helpers such as chat.completions.parse() build a derived APIPromise.
      // Some SDK versions (openai >= 7) give it a parse closure that does NOT
      // go through our hooked parseResponse, so observe the derived promise
      // too. Call.done makes sure the call is recorded once either way.
      const derived = originalThenUnwrap.apply(this, args);
      safe(() => observeApiPromise(derived, call));
      return derived;
    };
  }
}

function observeStream(call: Call, stream: Any): void {
  const st = call.adapter.newStream();
  let completed = false;
  let ttftMs: number | undefined;
  const finish = (err?: Any) => safe(() => call.finishStream(st, { err, completed, ttftMs }));

  const wrap = (inner: Any) => ({
    async next(...args: Any[]) {
      let result: Any;
      try {
        result = await inner.next(...args);
      } catch (err) {
        finish(err);
        throw err;
      }
      if (result.done) {
        completed = true;
        finish();
      } else {
        if (ttftMs === undefined) ttftMs = call.elapsedMs();
        safe(() => st.feed(result.value));
      }
      return result;
    },
    async return(value?: Any) {
      finish(); // consumer stopped early (break / abort)
      return typeof inner.return === "function" ? inner.return(value) : { done: true, value };
    },
    async throw(err?: Any) {
      finish(err);
      if (typeof inner.throw === "function") return inner.throw(err);
      throw err;
    },
    [Symbol.asyncIterator]() {
      return this;
    },
  });

  if (typeof stream.iterator === "function") {
    const original = stream.iterator;
    stream.iterator = function (this: Any, ...args: Any[]) {
      return wrap(original.apply(this, args));
    };
  } else {
    const original = stream[Symbol.asyncIterator];
    stream[Symbol.asyncIterator] = function (this: Any) {
      return wrap(original.call(this));
    };
  }
}

// ---------------------------------------------------------------------------
// Patching
// ---------------------------------------------------------------------------
function wrapCreate(original: Any, adapter: Adapter): Any {
  const wrapped = function (this: Any, ...args: Any[]) {
    const recorder = state().recorder;
    if (!recorder) return original.apply(this, args);
    const call = safe(() => new Call(recorder, adapter, args[0] ?? {}));
    let result: Any;
    try {
      result = original.apply(this, args);
    } catch (err) {
      if (call) safe(() => call.error(err));
      throw err;
    }
    if (call) safe(() => observeApiPromise(result, call));
    return result;
  };
  Object.defineProperty(wrapped, "name", { value: original.name });
  (wrapped as Any)[WRAPPED] = original;
  return wrapped;
}

function patchMethod(proto: Any, method: string, adapter: Adapter): boolean {
  if (!proto || typeof proto[method] !== "function") return false;
  if (proto[method][WRAPPED]) return true; // already patched (any copy of this SDK)
  const original = proto[method];
  proto[method] = wrapCreate(original, adapter);
  state().patches.push([proto, method, original]);
  return true;
}

function pickClass(mod: Any, marker: string): Any {
  for (const c of [mod, mod?.default, mod?.default?.default, mod?.OpenAI, mod?.Anthropic]) {
    if (c && (typeof c === "function" || typeof c === "object") && c[marker]) return c;
  }
  return null;
}

/** Patch one loaded module instance. Returns the number of methods patched. */
export function patchOpenAIModule(mod: Any): number {
  const K = pickClass(mod, "Chat");
  if (!K) return 0;
  let n = 0;
  if (patchMethod(K.Chat?.Completions?.prototype, "create", OPENAI_CHAT)) n++;
  if (patchMethod(K.Responses?.prototype, "create", OPENAI_RESPONSES)) n++;
  return n;
}

export function patchAnthropicModule(mod: Any): number {
  const K = pickClass(mod, "Messages");
  if (!K) return 0;
  return patchMethod(K.Messages?.prototype, "create", ANTHROPIC) ? 1 : 0;
}

const PACKAGES: Array<[string, (mod: Any) => number]> = [
  ["openai", patchOpenAIModule],
  ["@anthropic-ai/sdk", patchAnthropicModule],
];

function localRequire(): NodeRequire | null {
  try {
    return createRequire(import.meta.url);
  } catch {
    return null;
  }
}

/** Patch the CommonJS copies (synchronous) and the ES module copies
 *  (asynchronous: dynamic import). Missing packages are skipped. */
export async function patchAll(): Promise<void> {
  const req = localRequire();
  for (const [name, patch] of PACKAGES) {
    if (!req) break;
    try {
      patch(req(name));
    } catch {
      // not installed, or no CJS build
    }
  }
  await Promise.all(PACKAGES.map(async ([name, patch]) => {
    try {
      patch(await import(name));
    } catch {
      // not installed, or no ESM build
    }
  }));
}

export function unpatchAll(): void {
  const s = state();
  while (s.patches.length) {
    const [proto, method, original] = s.patches.pop()!;
    (proto as Any)[method] = original;
  }
}

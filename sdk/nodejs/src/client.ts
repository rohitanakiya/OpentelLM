// OpenTelLM Node SDK — module-level client.
//
//   import opentelm from "opentelm-node";
//   await opentelm.init({ apiKey: process.env.OPENTELM_API_KEY });
//   // every openai / @anthropic-ai/sdk call from here on is traced
//
// Mirrors sdk/python/opentelm: same event shape, same hashing, same
// defaults (endpoint http://localhost:8000, env vars OPENTELM_API_KEY /
// OPENTELM_ENDPOINT).

import { createHash, randomUUID } from "node:crypto";
import { EventFlusher, type LLMEvent, type Sender } from "./flush";
import { patchAll, setRecorder, unpatchAll, type RecordFields } from "./patch";
import { scrubPii } from "./pii";
import { SDK_VERSION } from "./version";

export interface InitConfig {
  /** Defaults to process.env.OPENTELM_API_KEY. */
  apiKey?: string;
  /** Ingest API base URL; "/v1/ingest" is appended if missing.
   *  Defaults to process.env.OPENTELM_ENDPOINT, then http://localhost:8000. */
  endpoint?: string;
  /** Flush every N ms. Default 2000. */
  flushIntervalMs?: number;
  /** Max queued events before the oldest are dropped. Default 1000. */
  maxQueueSize?: number;
  /** Replace emails, phone numbers and API keys before hashing. Default true. */
  scrubPii?: boolean;
  /** Initial deployment version tag — see setVersion(). */
  version?: string | null;
  /** Tags added to every event. */
  defaultTags?: Record<string, string>;
  /** Called on delivery problems. Default: a console.warn at most once a minute. */
  onError?: (err: Error) => void;
  /** Replace the HTTP transport (tests). */
  sender?: Sender;
}

export interface ManualEvent {
  model: string;
  provider: LLMEvent["provider"];
  prompt: string;
  response: string;
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
  status?: LLMEvent["status"];
  errorCode?: string;
  errorMessage?: string;
  tags?: Record<string, string>;
}

const PROVIDERS = new Set(["openai", "anthropic", "google", "cohere", "other"]);
const STATUSES = new Set(["success", "error", "timeout"]);
const MAX_ERROR_MESSAGE = 1000;

let flusher: EventFlusher | null = null;
let version: string | null = null;
let scrub = true;
let defaultTags: Record<string, string> = {};
let readyPromise: Promise<void> = Promise.resolve();

function sha256(text: string): string {
  return createHash("sha256").update(scrub ? scrubPii(text) : text, "utf8").digest("hex");
}

function buildEvent(f: {
  provider: string; model: string; promptText: string; responseText: string;
  promptTokens: number; completionTokens: number; latencyMs: number; status: string;
  errorCode?: string | null; errorMessage?: string | null; timestampMs?: number;
  extraTags?: Record<string, string>;
}): LLMEvent {
  let errorMessage = f.errorMessage ?? null;
  if (errorMessage !== null) {
    errorMessage = String(errorMessage);
    if (scrub) errorMessage = scrubPii(errorMessage);
    errorMessage = errorMessage.slice(0, MAX_ERROR_MESSAGE);
  }
  const tags: Record<string, string> = { ...defaultTags };
  for (const [k, v] of Object.entries(f.extraTags ?? {})) tags[String(k)] = String(v);
  return {
    trace_id: randomUUID(),
    span_id: randomUUID(),
    model: String(f.model || "unknown"),
    provider: (PROVIDERS.has(f.provider) ? f.provider : "other") as LLMEvent["provider"],
    prompt_tokens: Math.max(0, Math.floor(Number(f.promptTokens) || 0)),
    completion_tokens: Math.max(0, Math.floor(Number(f.completionTokens) || 0)),
    latency_ms: Math.max(0, Math.floor(Number(f.latencyMs) || 0)),
    status: (STATUSES.has(f.status) ? f.status : "error") as LLMEvent["status"],
    error_code: f.errorCode === undefined || f.errorCode === null ? null : String(f.errorCode).slice(0, 100),
    error_message: errorMessage,
    prompt_hash: sha256(f.promptText ?? ""),
    response_hash: sha256(f.responseText ?? ""),
    sdk_version: SDK_VERSION,
    deployment_version: version,
    tags,
    timestamp_ms: Math.floor(f.timestampMs ?? Date.now()),
  };
}

const recorder = {
  record(fields: RecordFields): void {
    try {
      flusher?.enqueue(buildEvent(fields));
    } catch {
      // never let telemetry reach the caller
    }
  },
};

/**
 * Initialize the SDK and auto-instrument openai / @anthropic-ai/sdk.
 *
 * Returns a promise that resolves once BOTH the CommonJS and the ES module
 * copies of those packages are patched. In an ES module app, `await` it (or
 * `ready()`) if your very first LLM call happens right after init();
 * otherwise there's no need to wait. Calling init() again is a no-op.
 */
export function init(cfg: InitConfig = {}): Promise<void> {
  if (flusher) return readyPromise;
  const apiKey = cfg.apiKey ?? process.env.OPENTELM_API_KEY;
  if (!apiKey) throw new Error("opentelm.init(): pass apiKey or set OPENTELM_API_KEY");

  scrub = cfg.scrubPii !== false;
  version = cfg.version ?? null;
  defaultTags = Object.fromEntries(Object.entries(cfg.defaultTags ?? {}).map(([k, v]) => [String(k), String(v)]));
  flusher = new EventFlusher({
    apiKey,
    endpoint: cfg.endpoint ?? process.env.OPENTELM_ENDPOINT ?? "http://localhost:8000",
    flushIntervalMs: cfg.flushIntervalMs,
    maxQueueSize: cfg.maxQueueSize,
    onError: cfg.onError,
    sender: cfg.sender,
  });
  flusher.start();
  setRecorder(recorder);
  readyPromise = patchAll().catch(() => undefined);
  return readyPromise;
}

/** Resolves once instrumentation is fully in place. */
export function ready(): Promise<void> {
  return readyPromise;
}

/** Tag every subsequent event with a deployment / prompt version (used by
 *  prompt-regression detection). Pass null to clear. */
export function setVersion(v: string | null): void {
  version = v;
}

export function getVersion(): string | null {
  return version;
}

/** Send queued events now, e.g. before a serverless handler returns. */
export async function flush(): Promise<void> {
  await flusher?.flush();
}

/** Remove the patches and flush queued events. Normal processes don't need
 *  this (a `beforeExit` hook flushes); call it from your own SIGTERM
 *  handler, or in tests. */
export async function shutdown(): Promise<void> {
  setRecorder(null);
  unpatchAll();
  const f = flusher;
  flusher = null;
  readyPromise = Promise.resolve();
  await f?.shutdown();
}

/** Manually record a call to a provider the SDK does not auto-patch. */
export function track(event: ManualEvent): void {
  if (!flusher) throw new Error("opentelm: call init() before track()");
  try {
    flusher.enqueue(buildEvent({
      provider: event.provider,
      model: event.model,
      promptText: event.prompt,
      responseText: event.response,
      promptTokens: event.promptTokens,
      completionTokens: event.completionTokens,
      latencyMs: event.latencyMs,
      status: event.status ?? "success",
      errorCode: event.errorCode,
      errorMessage: event.errorMessage,
      extraTags: event.tags,
    }));
  } catch {
    // never throw from telemetry
  }
}

/** Internal — for tests. */
export function _flusher(): EventFlusher | null {
  return flusher;
}

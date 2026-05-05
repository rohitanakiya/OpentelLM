// OpenTelLM client — module-level singleton.
//
// Mirrors the Python SDK API:
//   import { init, setVersion, track } from "opentelm-node";
//   init({ apiKey: "opentelm_live_..." });
//   setVersion("v2");
//   // ... all subsequent OpenAI / Anthropic calls auto-instrumented.

import { randomUUID, createHash } from "node:crypto";
import { EventFlusher, type LLMEvent } from "./flush";
import { patchAll } from "./patch";
import { scrubPii } from "./pii";

const SDK_VERSION = "0.1.0";

export interface InitConfig {
  apiKey: string;
  /** Defaults to https://api.opentelm.dev/v1/ingest. Override for self-hosted. */
  endpoint?: string;
  /** Flush every N ms. Default 2000. */
  flushIntervalMs?: number;
  /** Max queued events before oldest are dropped. Default 1000. */
  maxQueueSize?: number;
  /** Disable PII scrubbing. Default false (scrub on). */
  scrubPii?: boolean;
  /** Initial deployment version tag — see setVersion(). */
  version?: string | null;
  /** Logger for transport errors. Defaults to a no-op. */
  onError?: (err: Error) => void;
}

let _flusher: EventFlusher | null = null;
let _version: string | null = null;
let _scrubPii = true;

export function init(cfg: InitConfig): void {
  if (!cfg?.apiKey) throw new Error("opentelm: apiKey is required");
  if (_flusher) {
    // Idempotent — don't double-patch on accidental second init().
    return;
  }
  _scrubPii = cfg.scrubPii !== false;
  _version  = cfg.version ?? null;

  _flusher = new EventFlusher({
    apiKey:          cfg.apiKey,
    endpoint:        cfg.endpoint ?? "https://api.opentelm.dev/v1/ingest",
    flushIntervalMs: cfg.flushIntervalMs,
    maxQueueSize:    cfg.maxQueueSize,
    onError:         cfg.onError,
  });
  _flusher.start();

  patchAll({
    flusher:    _flusher,
    scrubPii:   _scrubPii,
    getVersion: () => _version,
    sdkVersion: SDK_VERSION,
  });
}

/** Tag every subsequent event with a deployment version. */
export function setVersion(version: string | null): void {
  _version = version;
}

export function getVersion(): string | null {
  return _version;
}

/** Best-effort flush, e.g. before a serverless function returns. */
export async function flush(): Promise<void> {
  await _flusher?.flush();
}

export async function shutdown(): Promise<void> {
  await _flusher?.shutdown();
  _flusher = null;
}

// ---------------------------------------------------------------------------
// Manual tracking — for non-OpenAI / non-Anthropic providers (Cohere, local
// models, custom HTTP wrappers). The user is responsible for measuring
// latency and counting tokens.
// ---------------------------------------------------------------------------
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
}

export function track(event: ManualEvent): void {
  if (!_flusher) {
    throw new Error("opentelm: call init() before track()");
  }
  const promptForHash   = _scrubPii ? scrubPii(event.prompt)   : event.prompt;
  const responseForHash = _scrubPii ? scrubPii(event.response) : event.response;

  _flusher.enqueue({
    trace_id:           randomUUID(),
    span_id:            randomUUID(),
    model:              event.model,
    provider:           event.provider,
    prompt_tokens:      event.promptTokens,
    completion_tokens:  event.completionTokens,
    latency_ms:         event.latencyMs,
    status:             event.status ?? "success",
    error_code:         event.errorCode ?? null,
    error_message:      event.errorMessage ?? null,
    prompt_hash:        createHash("sha256").update(promptForHash).digest("hex"),
    response_hash:      createHash("sha256").update(responseForHash).digest("hex"),
    sdk_version:        SDK_VERSION,
    deployment_version: _version,
    timestamp_ms:       Date.now(),
  });
}

// Internal — exposed for tests, not part of the public API surface.
export function _unsafeGetFlusher(): EventFlusher | null {
  return _flusher;
}

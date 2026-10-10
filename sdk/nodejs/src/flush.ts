// Background event queue with batched delivery to the ingest API.
//
// Design rules (same as the Python SDK):
//   - enqueue() is synchronous and never throws, never does I/O.
//   - The queue is bounded; when full the OLDEST event is dropped.
//   - Delivery runs on an unref()'d timer, so it never keeps a finished
//     process alive.
//   - On `beforeExit` (the event loop is about to empty) one last flush is
//     attempted.
//
// What this deliberately does NOT do: listen for SIGINT / SIGTERM. Adding a
// listener for those signals replaces Node's default "exit now" behaviour,
// so the previous version made apps ignore the first Ctrl+C and made
// Kubernetes wait out the whole termination grace period. If you handle
// signals yourself, call `await opentelm.shutdown()` in your handler.
//
// HTTP goes through node:http(s) rather than global fetch: frameworks such
// as Next.js patch fetch (caching, instrumentation), and telemetry must not
// be affected by that.

import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

export interface LLMEvent {
  trace_id: string;
  span_id?: string;
  model: string;
  provider: "openai" | "anthropic" | "google" | "cohere" | "other";
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  status: "success" | "error" | "timeout";
  error_code?: string | null;
  error_message?: string | null;
  prompt_hash: string;
  response_hash: string;
  sdk_version: string;
  deployment_version?: string | null;
  tags?: Record<string, string>;
  timestamp_ms?: number;
}

/** Sends one JSON body, resolves with the HTTP status, rejects on network error. */
export type Sender = (body: string) => Promise<number>;

export interface FlushConfig {
  endpoint: string;
  apiKey: string;
  flushIntervalMs?: number;
  maxQueueSize?: number;
  batchSize?: number;
  timeoutMs?: number;
  onError?: (err: Error) => void;
  /** Replace the HTTP transport (tests). */
  sender?: Sender;
}

const DEFAULT_INTERVAL_MS = 2_000;
const DEFAULT_MAX_QUEUE = 1_000;
const DEFAULT_BATCH = 100; // ingest-api accepts up to 500
const DEFAULT_TIMEOUT_MS = 5_000;
const WARN_EVERY_MS = 60_000;

export function ingestUrl(endpoint: string): string {
  const e = endpoint.replace(/\/+$/, "");
  return e.endsWith("/v1/ingest") ? e : `${e}/v1/ingest`;
}

export class EventFlusher {
  private queue: LLMEvent[] = [];
  private timer: NodeJS.Timeout | null = null;
  private inflight: Promise<void> | null = null;
  private stopped = false;
  private readonly url: string;
  private readonly interval: number;
  private readonly maxQueue: number;
  private readonly batchSize: number;
  private readonly timeoutMs: number;
  private readonly send: Sender;
  private readonly onError: (err: Error) => void;
  private readonly onBeforeExit = () => { void this.shutdown(); };
  private lastWarn = new Map<string, number>();

  dropped = 0; // dropped because the queue was full
  failed = 0;  // rejected by the ingest API (4xx other than 429)
  sent = 0;    // accepted by the ingest API

  constructor(private readonly cfg: FlushConfig) {
    this.url = ingestUrl(cfg.endpoint);
    this.interval = cfg.flushIntervalMs ?? DEFAULT_INTERVAL_MS;
    this.maxQueue = Math.max(1, cfg.maxQueueSize ?? DEFAULT_MAX_QUEUE);
    this.batchSize = Math.min(500, Math.max(1, cfg.batchSize ?? DEFAULT_BATCH));
    this.timeoutMs = cfg.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.send = cfg.sender ?? ((body) => this.httpSend(body));
    this.onError = cfg.onError ?? ((err) => this.warn(err.message, err.message));
  }

  start(): void {
    if (this.timer || this.stopped) return;
    this.timer = setInterval(() => { void this.flush(); }, this.interval);
    this.timer.unref();
    process.once("beforeExit", this.onBeforeExit);
  }

  enqueue(event: LLMEvent): void {
    if (this.stopped) return;
    if (this.queue.length >= this.maxQueue) {
      this.queue.shift();
      this.dropped++;
    }
    this.queue.push(event);
    if (this.queue.length === this.batchSize) {
      // A full batch is ready: send it now rather than waiting for the timer.
      setTimeout(() => { void this.flush(); }, 0).unref();
    }
  }

  size(): number {
    return this.queue.length;
  }

  /** Send everything queued so far. Never rejects. */
  async flush(): Promise<void> {
    while (this.inflight) await this.inflight;
    if (this.queue.length === 0) return;
    this.inflight = this.drain().finally(() => { this.inflight = null; });
    await this.inflight;
  }

  /** Stop the timer and flush what is left. Idempotent. */
  async shutdown(): Promise<void> {
    if (this.stopped) {
      while (this.inflight) await this.inflight;
      return;
    }
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    process.removeListener("beforeExit", this.onBeforeExit);
    await this.flush();
  }

  private async drain(): Promise<void> {
    while (this.queue.length > 0) {
      const batch = this.queue.splice(0, this.batchSize);
      let status: number;
      try {
        status = await this.send(JSON.stringify({ events: batch }));
      } catch (err) {
        this.requeue(batch);
        this.onError(new Error(`opentelm: could not reach ingest API at ${this.url}: ${(err as Error).message}`));
        return; // try again on the next tick
      }
      if (status >= 200 && status < 300) {
        this.sent += batch.length;
      } else if (status === 429 || status >= 500) {
        this.requeue(batch);
        this.onError(new Error(`opentelm: ingest API returned HTTP ${status}; will retry`));
        return;
      } else {
        // Bad API key or invalid payload: retrying cannot help.
        this.failed += batch.length;
        this.onError(new Error(`opentelm: ingest API rejected ${batch.length} events with HTTP ${status}`));
      }
    }
  }

  private requeue(batch: LLMEvent[]): void {
    const room = this.maxQueue - this.queue.length;
    const keep = room > 0 ? batch.slice(-room) : [];
    this.dropped += batch.length - keep.length;
    this.queue.unshift(...keep);
  }

  private warn(key: string, message: string): void {
    const now = Date.now();
    if (now - (this.lastWarn.get(key) ?? -Infinity) >= WARN_EVERY_MS) {
      this.lastWarn.set(key, now);
      console.warn(message);
    }
  }

  private httpSend(body: string): Promise<number> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.url);
      const send = url.protocol === "https:" ? httpsRequest : httpRequest;
      const payload = Buffer.from(body);
      const req = send(
        url,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": payload.byteLength,
            Authorization: `Bearer ${this.cfg.apiKey}`,
          },
          timeout: this.timeoutMs,
        },
        (res) => {
          res.resume(); // drain so the socket can be reused
          resolve(res.statusCode ?? 0);
        },
      );
      req.on("error", reject);
      req.on("timeout", () => req.destroy(new Error("request timed out")));
      req.end(payload);
    });
  }
}

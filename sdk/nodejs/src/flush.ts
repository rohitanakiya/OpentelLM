// Async event queue with periodic batched flush.
//
// Design rules (mirror Python SDK):
//   - flush MUST NOT block the caller. Every push is sync, network I/O
//     happens on a setInterval timer.
//   - the timer is unref()'d so it never holds the Node event loop open
//     and prevent a normal process exit.
//   - on overflow (>1000 events queued) we DROP the oldest, never the
//     newest, never the caller's call.
//   - on SIGTERM/SIGINT/beforeExit we attempt one final flush.

import { request as httpsRequest } from "node:https";
import { request as httpRequest } from "node:http";
import { URL } from "node:url";

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

export interface FlushConfig {
  endpoint: string;        // e.g. "https://opentelm.example.com/v1/ingest"
  apiKey: string;          // raw opentelm_live_... key
  flushIntervalMs?: number;
  maxQueueSize?: number;
  onError?: (err: Error) => void;
}

const DEFAULT_INTERVAL = 2_000;
const DEFAULT_MAX = 1_000;

export class EventFlusher {
  private queue: LLMEvent[] = [];
  private timer: NodeJS.Timeout | null = null;
  private inflight = false;
  private stopped = false;

  constructor(private cfg: Required<Pick<FlushConfig, "endpoint" | "apiKey">> &
                          FlushConfig) {
    this.cfg.flushIntervalMs ??= DEFAULT_INTERVAL;
    this.cfg.maxQueueSize    ??= DEFAULT_MAX;
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.flush().catch((err) => this.cfg.onError?.(err as Error));
    }, this.cfg.flushIntervalMs);

    // CRITICAL: unref so the process can exit even if our queue is empty.
    // Without this a tiny CLI app would hang for 2s after main() returns.
    this.timer.unref();

    // Drain on shutdown signals — best-effort, sync-style fire-and-forget.
    const shutdown = () => { void this.shutdown(); };
    process.once("SIGTERM",     shutdown);
    process.once("SIGINT",      shutdown);
    process.once("beforeExit",  shutdown);
  }

  enqueue(event: LLMEvent): void {
    if (this.stopped) return;
    if (this.queue.length >= this.cfg.maxQueueSize!) {
      // Drop the oldest. We could LIFO instead but FIFO matches Python.
      this.queue.shift();
    }
    this.queue.push(event);
  }

  size(): number { return this.queue.length; }

  /** Public for tests; the timer calls this. */
  async flush(): Promise<void> {
    if (this.inflight || this.queue.length === 0) return;
    const batch = this.queue.splice(0, this.queue.length);
    this.inflight = true;
    try {
      await this.send(batch);
    } catch (err) {
      // On send failure, push the batch back in to retry on next tick.
      // Cap the requeue to avoid unbounded growth: only requeue if the
      // queue still has headroom, otherwise drop.
      const headroom = this.cfg.maxQueueSize! - this.queue.length;
      if (headroom > 0) {
        this.queue.unshift(...batch.slice(0, headroom));
      }
      this.cfg.onError?.(err as Error);
    } finally {
      this.inflight = false;
    }
  }

  async shutdown(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    // One last flush attempt
    try { await this.flush(); } catch { /* swallow on shutdown */ }
  }

  // ---------------------------------------------------------------
  // HTTP — minimal, no axios/node-fetch dep so the SDK stays tiny.
  // ---------------------------------------------------------------
  private send(events: LLMEvent[]): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.cfg.endpoint);
      const lib = url.protocol === "https:" ? httpsRequest : httpRequest;

      const body = Buffer.from(JSON.stringify({ events }));
      const req = lib(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": body.byteLength,
            Authorization: `Bearer ${this.cfg.apiKey}`,
          },
          // Bound the request — telemetry should never sit on a stuck socket.
          timeout: 5_000,
        },
        (res) => {
          // Drain the response so the socket can be reused
          res.resume();
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve();
          } else {
            reject(new Error(`ingest returned ${res.statusCode}`));
          }
        },
      );

      req.on("error", reject);
      req.on("timeout", () => req.destroy(new Error("ingest request timed out")));
      req.write(body);
      req.end();
    });
  }
}

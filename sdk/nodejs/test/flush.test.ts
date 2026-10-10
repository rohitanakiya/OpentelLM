import { afterEach, beforeEach, describe, expect, it } from "vitest";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { EventFlusher, ingestUrl, type LLMEvent } from "../src/flush";

function ev(i: number): LLMEvent {
  return {
    trace_id: `t-${i}`, model: "gpt-4o", provider: "openai", prompt_tokens: 1, completion_tokens: 1,
    latency_ms: 10, status: "success", prompt_hash: "a".repeat(64), response_hash: "b".repeat(64),
    sdk_version: "test",
  };
}

/** A real HTTP server standing in for ingest-api. */
class IngestStub {
  status = 202;
  requests: Array<{ path: string; auth: string | undefined; events: LLMEvent[] }> = [];
  private server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => {
      this.requests.push({ path: req.url ?? "", auth: req.headers.authorization, events: JSON.parse(body).events });
      res.writeHead(this.status);
      res.end("{}");
    });
  });
  url = "";
  async start() {
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", r));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
  }
  stop() {
    return new Promise<void>((r) => this.server.close(() => r()));
  }
  events() {
    return this.requests.flatMap((r) => r.events);
  }
}

let ingest: IngestStub;
const errors: Error[] = [];
const make = (extra: Partial<ConstructorParameters<typeof EventFlusher>[0]> = {}) =>
  new EventFlusher({ endpoint: ingest.url, apiKey: "opentelm_live_abc", flushIntervalMs: 60_000,
                     onError: (e) => errors.push(e), ...extra });

beforeEach(async () => {
  ingest = new IngestStub();
  await ingest.start();
  errors.length = 0;
});
afterEach(async () => {
  await ingest.stop();
});

describe("EventFlusher", () => {
  it("POSTs batches to /v1/ingest with the bearer key", async () => {
    const f = make();
    for (let i = 0; i < 3; i++) f.enqueue(ev(i));
    await f.flush();
    expect(ingest.requests[0].path).toBe("/v1/ingest");
    expect(ingest.requests[0].auth).toBe("Bearer opentelm_live_abc");
    expect(ingest.events().map((e) => e.trace_id)).toEqual(["t-0", "t-1", "t-2"]);
    expect(f.sent).toBe(3);
  });

  it("splits into batches", async () => {
    const f = make({ batchSize: 2 });
    for (let i = 0; i < 5; i++) f.enqueue(ev(i));
    await f.flush();
    expect(ingest.requests.map((r) => r.events.length)).toEqual([2, 2, 1]);
  });

  it("sends a full batch without waiting for the timer", async () => {
    const f = make({ batchSize: 5 });
    for (let i = 0; i < 5; i++) f.enqueue(ev(i));
    for (let t = 0; t < 100 && ingest.events().length < 5; t++) await new Promise((r) => setTimeout(r, 20));
    expect(ingest.events()).toHaveLength(5);
  });

  it("drops the oldest events when the queue is full", async () => {
    const f = make({ maxQueueSize: 3 });
    for (let i = 0; i < 5; i++) f.enqueue(ev(i));
    expect(f.dropped).toBe(2);
    await f.flush();
    expect(ingest.events().map((e) => e.trace_id)).toEqual(["t-2", "t-3", "t-4"]);
  });

  it("does not retry 4xx responses", async () => {
    ingest.status = 401;
    const f = make();
    f.enqueue(ev(1));
    await f.flush();
    await f.flush();
    expect(ingest.requests).toHaveLength(1);
    expect(f.failed).toBe(1);
    expect(f.size()).toBe(0);
    expect(errors[0].message).toMatch(/401/);
  });

  it("keeps events for a later retry on 5xx", async () => {
    ingest.status = 503;
    const f = make();
    f.enqueue(ev(1));
    await f.flush();
    expect(f.size()).toBe(1);
    ingest.status = 202;
    await f.flush();
    expect(f.size()).toBe(0);
    expect(f.sent).toBe(1);
  });

  it("never throws when the ingest API is unreachable", async () => {
    const f = new EventFlusher({ endpoint: "http://127.0.0.1:9", apiKey: "k", onError: (e) => errors.push(e) });
    f.enqueue(ev(1));
    await expect(f.flush()).resolves.toBeUndefined();
    expect(errors[0].message).toMatch(/could not reach/);
  });

  it("shutdown() flushes and later enqueues are ignored", async () => {
    const f = make();
    f.start();
    f.enqueue(ev(1));
    await f.shutdown();
    expect(ingest.events()).toHaveLength(1);
    f.enqueue(ev(2));
    expect(f.size()).toBe(0);
  });

  it("does not install SIGINT / SIGTERM handlers", () => {
    // Regression: listening for these replaced Node's default exit
    // behaviour (first Ctrl+C ignored, Kubernetes waiting out the grace period).
    const before = [process.listenerCount("SIGINT"), process.listenerCount("SIGTERM")];
    const f = make();
    f.start();
    expect([process.listenerCount("SIGINT"), process.listenerCount("SIGTERM")]).toEqual(before);
    void f.shutdown();
  });

  it.each([
    ["http://x:8000", "http://x:8000/v1/ingest"],
    ["http://x:8000/", "http://x:8000/v1/ingest"],
    ["http://x:8000/v1/ingest", "http://x:8000/v1/ingest"],
  ])("normalises %s", (input, expected) => {
    expect(ingestUrl(input)).toBe(expected);
  });
});

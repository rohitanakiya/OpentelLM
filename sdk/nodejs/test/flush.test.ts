import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EventFlusher, type LLMEvent } from "../src/flush";

function makeEvent(i: number): LLMEvent {
  return {
    trace_id: `t-${i}`,
    model: "gpt-4o",
    provider: "openai",
    prompt_tokens: 1,
    completion_tokens: 1,
    latency_ms: 10,
    status: "success",
    prompt_hash: "a".repeat(64),
    response_hash: "b".repeat(64),
    sdk_version: "test",
  };
}

describe("EventFlusher", () => {
  let flusher: EventFlusher;
  let originalRequestStub: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    flusher = new EventFlusher({
      endpoint: "http://localhost:1/v1/ingest",
      apiKey:   "opentelm_live_test",
      flushIntervalMs: 60_000, // long — we trigger flush() manually
    });
  });

  afterEach(async () => {
    await flusher.shutdown();
  });

  it("drops the OLDEST event when queue exceeds maxQueueSize", () => {
    const f = new EventFlusher({
      endpoint: "http://localhost:1/v1/ingest",
      apiKey: "opentelm_live_test",
      maxQueueSize: 3,
      flushIntervalMs: 60_000,
    });
    f.enqueue(makeEvent(1));
    f.enqueue(makeEvent(2));
    f.enqueue(makeEvent(3));
    f.enqueue(makeEvent(4));    // pushes out 1
    f.enqueue(makeEvent(5));    // pushes out 2
    expect(f.size()).toBe(3);

    // Force a flush by stubbing the network call to capture the batch.
    const sent: LLMEvent[][] = [];
    // @ts-expect-error reach in for the test
    f.send = async (events: LLMEvent[]) => { sent.push(events); };
    return f.flush().then(() => {
      expect(sent[0].map((e) => e.trace_id)).toEqual(["t-3", "t-4", "t-5"]);
      return f.shutdown();
    });
  });

  it("flush() clears the queue when the network call succeeds", async () => {
    flusher.enqueue(makeEvent(1));
    flusher.enqueue(makeEvent(2));
    expect(flusher.size()).toBe(2);

    // @ts-expect-error stub network
    flusher.send = async () => {};
    await flusher.flush();
    expect(flusher.size()).toBe(0);
  });

  it("flush() requeues events when the network call fails", async () => {
    flusher.enqueue(makeEvent(1));
    flusher.enqueue(makeEvent(2));

    // @ts-expect-error stub network to reject
    flusher.send = async () => { throw new Error("boom"); };
    await flusher.flush();
    expect(flusher.size()).toBe(2);
  });

  it("the periodic timer is unref()'d so it doesn't keep the process open", () => {
    const f = new EventFlusher({
      endpoint: "http://localhost:1/v1/ingest",
      apiKey: "opentelm_live_test",
      flushIntervalMs: 50,
    });
    // Spy on setInterval so we can grab the returned Timer and assert unref was called.
    const realSetInterval = global.setInterval;
    const fakeTimer = { unref: vi.fn(), [Symbol.toPrimitive]: () => 1 } as unknown as NodeJS.Timeout;
    const intervalSpy = vi.fn(() => fakeTimer);
    // @ts-expect-error replacing setInterval for the duration of this test
    global.setInterval = intervalSpy;

    f.start();
    expect(intervalSpy).toHaveBeenCalledOnce();
    expect(fakeTimer.unref).toHaveBeenCalledOnce();

    global.setInterval = realSetInterval;
    return f.shutdown();
  });

  it("shutdown() flushes one last time", async () => {
    flusher.enqueue(makeEvent(1));
    let flushed = 0;
    // @ts-expect-error stub
    flusher.send = async (events: LLMEvent[]) => { flushed += events.length; };
    await flusher.shutdown();
    expect(flushed).toBe(1);
  });

  it("rejects further enqueues after shutdown", async () => {
    await flusher.shutdown();
    flusher.enqueue(makeEvent(1));
    expect(flusher.size()).toBe(0);
  });
});

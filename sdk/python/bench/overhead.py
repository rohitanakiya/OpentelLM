"""
Measure the per-call overhead the SDK adds to an OpenAI chat completion.

The provider is an in-memory transport returning a canned response, so the
only difference between the two runs is the instrumentation itself
(wrapper, text extraction, PII scrub, hashing, queueing). Network flushes
happen on a background thread and are disabled here via a no-op sender.

    python bench/overhead.py [--calls 2000] [--prompt-chars 4000]
"""
from __future__ import annotations

import argparse
import statistics
import time

import httpx2
import openai

import opentelm


def make_client(prompt_chars: int) -> tuple[openai.OpenAI, list[dict]]:
    body = {
        "id": "c", "object": "chat.completion", "created": 0, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "x" * 800}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    }
    transport = httpx2.MockTransport(lambda req: httpx2.Response(200, json=body))
    client = openai.OpenAI(api_key="sk-test", max_retries=0, http_client=httpx2.Client(transport=transport))
    messages = [
        {"role": "system", "content": "You are helpful. " * 10},
        {"role": "user", "content": "Contact me at a@b.com. " + "lorem ipsum " * (prompt_chars // 12)},
    ]
    return client, messages


def timed_calls(client: openai.OpenAI, messages: list[dict], n: int) -> list[float]:
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        client.chat.completions.create(model="gpt-4o", messages=messages)
        out.append((time.perf_counter() - t0) * 1000)
    return out


def summarize(xs: list[float]) -> tuple[float, float]:
    xs = sorted(xs)
    return statistics.median(xs), xs[int(len(xs) * 0.99) - 1]


def measure(calls: int = 2000, prompt_chars: int = 4000) -> dict[str, float]:
    client, messages = make_client(prompt_chars)
    timed_calls(client, messages, 200)  # warm-up
    base = timed_calls(client, messages, calls)

    opentelm.init(api_key="bench", sender=lambda body, timeout: 202,
                  flush_interval_s=3600, max_queue=calls + 10, register_atexit=False)
    try:
        timed_calls(client, messages, 200)
        inst = timed_calls(client, messages, calls)
    finally:
        opentelm.shutdown()

    b50, b99 = summarize(base)
    i50, i99 = summarize(inst)
    return {"base_p50": b50, "base_p99": b99, "inst_p50": i50, "inst_p99": i99,
            "overhead_p50": i50 - b50, "overhead_p99": i99 - b99}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=2000)
    ap.add_argument("--prompt-chars", type=int, default=4000)
    args = ap.parse_args()
    r = measure(args.calls, args.prompt_chars)
    print(f"uninstrumented  p50 {r['base_p50']:.3f} ms   p99 {r['base_p99']:.3f} ms")
    print(f"instrumented    p50 {r['inst_p50']:.3f} ms   p99 {r['inst_p99']:.3f} ms")
    print(f"SDK overhead    p50 {r['overhead_p50'] * 1000:.0f} us   p99 {r['overhead_p99'] * 1000:.0f} us")

# opentelm

Zero-config observability for LLM apps in Python. Call `init()` once and
every `openai` / `anthropic` call in your app is traced: latency, tokens,
cost, errors and a privacy-preserving hash of the prompt and response.
Sync, async and streaming calls are all covered, and you don't change any
existing LLM code.

## Install

```bash
pip install opentelm
```

The package has no runtime dependencies. It never pulls in or pins `httpx`,
so it can't conflict with your `openai` / `anthropic` versions.

## Quickstart

```python
import opentelm
from openai import OpenAI

opentelm.init(api_key="opentelm_live_...", endpoint="https://your-ingest-host")

client = OpenAI()
res = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Capital of France?"}],
)
# ↑ traced, hashed and queued for ingest in the background
```

`init()` can run before or after you create your clients. The patch is
applied to the SDK classes, so existing client instances are covered too.
`api_key` and `endpoint` can also come from `OPENTELM_API_KEY` and
`OPENTELM_ENDPOINT`.

## What gets traced

| SDK       | Calls                                                        | sync | async | streaming |
|-----------|--------------------------------------------------------------|:----:|:-----:|:---------:|
| openai    | `chat.completions.create` / `.parse` / `.stream`             | ✓    | ✓     | ✓         |
| openai    | `responses.create` / `.parse` / `.stream` (openai ≥ 1.66)    | ✓    | ✓     | ✓         |
| anthropic | `messages.create` / `messages.stream`                        | ✓    | ✓     | ✓         |

Tested against openai 1.40 → 3.x and anthropic 0.34 → 1.x on Python 3.10–3.13.

Each event records: model, provider, prompt/completion tokens, latency,
status (`success` / `error` / `timeout`), error code and message, the
deployment version, your tags, and SHA-256 hashes of the prompt and response.

**Streaming.** A streamed call is recorded when the stream finishes, fails
or is closed. Events also carry `otlm.ttft_ms` (time to first token). An
OpenAI chat stream only reports token usage if you pass
`stream_options={"include_usage": True}`. Without it, token counts are
estimated (about 4 characters per token) and the event is tagged
`otlm.tokens_estimated=true`. A stream you abandon early is tagged
`otlm.stream_incomplete=true`.

**Not traced yet:** `with_raw_response` / `with_streaming_response` calls
(they still work normally), embeddings, and the Batch API.

## Safety guarantees

- Your call's return value and exceptions are exactly what they'd be
  without the SDK. The stream objects you get back are the SDK's own
  `Stream` / `AsyncStream` instances.
- A bug in the SDK's recording code is caught and logged at DEBUG level. It
  never raises into your code.
- The calling thread does no I/O. Events go onto a bounded in-memory queue,
  and a daemon thread sends them in batches.

## Overhead

Measured with `python bench/overhead.py` using an in-memory transport,
so the only difference between runs is the instrumentation:

| prompt size | added latency, p50 | p99      |
|-------------|--------------------|----------|
| 400 chars   | ~35 µs             | ~0.1 ms  |
| 4 KB        | ~60 µs             | ~0.5 ms  |
| 20 KB       | ~0.2 ms            | ~0.8 ms  |

Most of the cost is PII scrubbing and SHA-256 hashing, and it grows with
prompt size. `tests/test_overhead.py` fails the build if the median
end-to-end overhead ever exceeds 2 ms.

## Privacy

Prompts and responses are never sent, only their SHA-256 hashes. Before
hashing, emails, phone numbers and API keys (OpenAI, Anthropic, OpenTelLM)
are replaced with placeholders. Identical prompts still group together,
and PII never goes into the hash. Pass `scrub_pii=False` to `init()` to
hash the raw text instead.

## Tagging deployments

```python
opentelm.set_version("prompt-v2")   # later events are tagged "prompt-v2"
opentelm.set_version(None)          # back to untagged
```

The prompt-regression detector uses these tags. `tag_version()` is an alias.

## Manual tracking

For providers the SDK doesn't patch:

```python
import time
import opentelm

t0 = time.perf_counter()
result = call_my_custom_llm(prompt)
opentelm.track(
    model="my-finetuned-llama",
    provider="other",
    prompt=prompt,
    response=result.text,
    prompt_tokens=result.input_tokens,
    completion_tokens=result.output_tokens,
    latency_ms=int((time.perf_counter() - t0) * 1000),
    tags={"host": "gpu-1"},
)
```

## Short-lived processes

Queued events are flushed automatically when the interpreter exits. In
serverless handlers, flush before returning, because the runtime may
freeze the process:

```python
def handler(event, context):
    ...
    opentelm.flush()
```

## Options

```python
opentelm.init(
    api_key="opentelm_live_...",
    endpoint="https://ingest.example.com",   # "/v1/ingest" is appended if missing
    default_tags={"env": "prod"},
    scrub_pii=True,
    flush_interval_s=2.0,
    max_queue=1000,        # when full, the oldest events are dropped
)
```

## How it works

1. `init()` wraps the create/stream methods on the SDKs' resource classes.
2. Each wrapped call times the request, extracts token usage, flattens the
   messages (including tool calls, images and tool results) to text,
   scrubs PII, hashes the text and appends an event to a bounded deque.
3. For streams, the SDK swaps the stream's internal chunk iterator for one
   that watches each chunk as it passes through. The stream object itself
   is untouched.
4. A daemon thread POSTs batches of up to 100 events to `/v1/ingest` every
   2 seconds, or sooner when a batch fills up. It retries 5xx and 429 errors
   twice and never retries other 4xx errors. Warnings are rate-limited, so
   an unreachable ingest endpoint can't flood your logs.
5. An `atexit` hook flushes what's left, with a 2-second cap.

## Development

```bash
pip install -e ".[test]"
pytest                      # runs against the real openai/anthropic SDKs with a mocked transport
python bench/overhead.py    # measure per-call overhead
```

## License

MIT.

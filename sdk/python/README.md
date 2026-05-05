# opentelm

Zero-config observability for LLM apps in Python. Auto-instruments
`openai` and `anthropic` calls — no code changes in your existing LLM
logic.

## Install

```bash
pip install opentelm
```

## Quickstart

```python
import opentelm
from openai import OpenAI

opentelm.init(api_key="opentelm_live_...")

client = OpenAI()
res = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Capital of France?"}],
)
# ↑ this call is traced, hashed, and queued for ingest.
```

## Tagging deployments

```python
opentelm.set_version("prompt-v2")
# subsequent calls tagged "prompt-v2" — used by the prompt regression detector
opentelm.set_version(None)  # back to untagged
```

`tag_version()` is the legacy name; both work.

## Manual tracking

For providers we don't auto-patch:

```python
from opentelm import get_client
import time

client = get_client()
t0 = time.time()
result = call_my_custom_llm(prompt)
client.track(
    model="my-finetuned-llama",
    provider="other",
    prompt=prompt,
    response=result.text,
    prompt_tokens=result.input_tokens,
    completion_tokens=result.output_tokens,
    latency_ms=int((time.time() - t0) * 1000),
)
```

## How it works

1. `init()` monkey-patches `openai` and `anthropic`'s entry points.
2. Each patched call records latency, hashes the prompt and response with
   SHA-256, and pushes an event onto a bounded `deque`.
3. A daemon thread drains the queue every 2 seconds via a single batched
   HTTP POST to your ingest endpoint.
4. `atexit` flushes any remaining events on interpreter shutdown.

The queue has a hard cap of 1000 events — overflow drops the oldest, so
your application code never blocks on telemetry.

## License

MIT.

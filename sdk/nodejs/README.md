# opentelm-node

Zero-config observability for LLM apps in Node.js. Auto-instruments
`openai` and `@anthropic-ai/sdk` calls — no code changes in your existing
LLM logic. Adds < 2 ms of overhead per call via a non-blocking async
queue.

## Install

```bash
npm install opentelm-node
```

## Quickstart

```ts
import opentelm from "opentelm-node";
import OpenAI from "openai";

opentelm.init({ apiKey: process.env.OPENTELM_API_KEY! });

const client = new OpenAI();
const res = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Capital of France?" }],
});
// ↑ this call is now traced, hashed, and queued for ingest.
console.log(res.choices[0].message.content);
```

That's it. Five lines plus your existing LLM call. Open the OpenTelLM
dashboard and the call shows up in /traces within seconds.

## Configuration

```ts
opentelm.init({
  apiKey:           "opentelm_live_...",         // required
  endpoint:         "https://your-host/v1/ingest", // default https://api.opentelm.dev/v1/ingest
  flushIntervalMs:  2000,                         // default 2000
  maxQueueSize:     1000,                         // default 1000 — overflow drops oldest
  scrubPii:         true,                         // default true
  version:          "v1",                         // initial deployment tag
  onError:          (err) => console.warn(err),   // transport errors
});
```

| Option            | Type                  | Default                         | Notes |
|-------------------|-----------------------|---------------------------------|-------|
| `apiKey`          | string                | —                               | Required. From the dashboard's Settings → API Keys page. |
| `endpoint`        | string                | `https://api.opentelm.dev/v1/ingest` | Self-hosted? Point at your ingest-api. |
| `flushIntervalMs` | number                | 2000                            | How often the queue is drained to the wire. |
| `maxQueueSize`    | number                | 1000                            | Backpressure. Newest events always make it in. |
| `scrubPii`        | boolean               | true                            | See [PII scrubbing](#pii-scrubbing). |
| `version`         | string \| null        | null                            | Sets the initial `prompt_version` tag. |
| `onError`         | (err: Error) => void  | no-op                           | Called when a flush fails. |

## Streaming

Streaming is fully supported. Just pass `stream: true` to OpenAI or
Anthropic as you normally would — the SDK wraps the async iterable so
your code reads the same.

```ts
const stream = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Tell me a story" }],
  stream: true,
  // Recommended: ask OpenAI to include final usage so token counts are exact.
  stream_options: { include_usage: true },
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}
// The OpenTelLM event is enqueued automatically when the stream closes.
```

> **Token counts in streams are exact when you set `stream_options.include_usage: true`** for OpenAI, or when Anthropic's `message_delta` event arrives with `usage`. Without those, completion tokens are approximated from chunk count (≈ 1 token per chunk).

## prompt_version tagging

Tag every subsequent event with a deployment version. This is what powers
the prompt-regression detector — when you ship `v2`, the platform
compares its error rate and latency against `v1`.

```ts
opentelm.setVersion("prompt-v2");
// ... all OpenAI/Anthropic calls below this point are tagged "prompt-v2"
opentelm.setVersion(null);  // back to untagged
```

## Manual tracking

For providers we don't auto-patch (Cohere, local models, custom HTTP
wrappers):

```ts
import { track } from "opentelm-node";

const t0 = Date.now();
const res = await callMyCustomLLM(prompt);
track({
  model: "my-finetuned-llama",
  provider: "other",
  prompt:           prompt,
  response:         res.text,
  promptTokens:     res.input_tokens,
  completionTokens: res.output_tokens,
  latencyMs:        Date.now() - t0,
  status:           "success",
});
```

## PII scrubbing

Default-on. The SDK never sends raw prompt text — it sends a SHA-256
hash. Before hashing, these patterns are replaced with placeholders so
distinct PII still produces distinct hashes:

| Pattern                       | Replaced with     |
|-------------------------------|-------------------|
| Email addresses               | `[EMAIL]`         |
| Indian +91 phone numbers      | `[PHONE]`         |
| International phone numbers   | `[PHONE]`         |
| `opentelm_live_*` keys        | `[OPENTELM_KEY]`  |
| OpenAI `sk-...` keys          | `[OPENAI_KEY]`    |
| Anthropic `sk-ant-...` keys   | `[ANTHROPIC_KEY]` |

Disable with `init({ scrubPii: false })` if you have your own scrubbing
upstream and want hashes computed on raw text.

## TypeScript

Full type definitions ship in the package. No `@types/opentelm-node`
needed.

```ts
import { type LLMEvent, type InitConfig } from "opentelm-node";
```

## How it works

1. `init()` resolves `openai` and `@anthropic-ai/sdk` from your
   `node_modules` and patches their prototype `create` methods. If you
   don't have one of them installed, the corresponding patch is a silent
   no-op — the SDK has no hard dependency on either.
2. Each patched call records `latency_ms`, hashes the prompt and response,
   and enqueues an event onto an in-process queue.
3. A `setInterval(2000)` timer (which is `unref()`'d so it never holds
   the event loop open) drains the queue to your ingest endpoint via a
   single batched HTTP POST.
4. On `SIGTERM` / `SIGINT` / `beforeExit`, one final flush runs so
   in-flight events aren't lost.

The queue is bounded at 1000 events. If you're producing more than that
in 2 seconds the oldest events are dropped — telemetry must never block
your production code path.

## License

MIT.

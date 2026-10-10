# opentelm-node

Zero-config observability for LLM apps in Node.js. Call `init()` once and
every `openai` and `@anthropic-ai/sdk` call in your app is traced: latency,
tokens, errors and a privacy-preserving hash of the prompt and response.
It works in ES module and CommonJS apps, with regular and streaming calls,
and you don't change any existing LLM code.

## Install

```bash
npm install opentelm-node
```

The package has no runtime dependencies. `openai` and `@anthropic-ai/sdk`
are optional peer dependencies: the SDK patches whichever ones you have.

## Quickstart

```ts
import opentelm from "opentelm-node";
import OpenAI from "openai";

await opentelm.init({ apiKey: process.env.OPENTELM_API_KEY, endpoint: "https://your-ingest-host" });

const client = new OpenAI();
const res = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Capital of France?" }],
});
// ↑ traced, hashed and queued for ingest in the background
```

CommonJS works the same way: `const opentelm = require("opentelm-node")`.

**Why `await init()`?** `openai` and `@anthropic-ai/sdk` each ship two
copies, one for `import` and one for `require`, with separate classes. The
SDK patches both. The `require` copy is patched immediately, and the
`import` copy a moment later via dynamic `import()`. `init()` returns a
promise that resolves when both are done. If your first LLM call happens
right after `init()` in an ES module app, await it (or `opentelm.ready()`).
If init runs at startup and calls happen later in request handlers, you
don't need to wait.

`apiKey` and `endpoint` can also come from `OPENTELM_API_KEY` and
`OPENTELM_ENDPOINT`.

## What gets traced

| Package             | Calls                                                    | streaming |
|---------------------|----------------------------------------------------------|:---------:|
| `openai`            | `chat.completions.create` / `.parse` / `.stream`         | ✓         |
| `openai`            | `responses.create`, incl. `stream: true` (openai ≥ 4.87) | ✓         |
| `@anthropic-ai/sdk` | `messages.create` / `messages.stream`                    | ✓         |

Tested in CI against openai 4.20 → 7.x and @anthropic-ai/sdk 0.20 →
0.133, on Node 18 → 24, as both an ES module app and a CommonJS app.

Each event records model, provider, prompt/completion tokens, latency,
status (`success` / `error` / `timeout`), error code and message, the
deployment version, your tags, and SHA-256 hashes of the prompt and
response.

**Streaming.** A streamed call is recorded when the stream finishes, fails
or is closed, and gets an `otlm.ttft_ms` tag (time to first token). OpenAI
chat streams only report token usage if you pass
`stream_options: { include_usage: true }`. Without it, token counts are
estimated (about 4 characters per token) and the event is tagged
`otlm.tokens_estimated=true`. Streams abandoned early are tagged
`otlm.stream_incomplete=true`.

**Not traced:** `.asResponse()` (raw response, body unread), embeddings,
and the Batch API.

## Safety guarantees

- Nothing about the return value changes. `create()` still returns the
  SDK's own `APIPromise`, so `.withResponse()`, `.asResponse()` and the
  SDKs' helpers keep working. Streams keep their class, `.controller`,
  `.tee()` and `.toReadableStream()`. They're observed in place, not
  wrapped.
- Errors from the provider reach you unchanged. A bug in the SDK's
  recording code is caught and never reaches your code.
- No I/O on your request path. Events go onto a bounded queue (the oldest
  are dropped when it's full), and an `unref()`'d timer sends them in
  batches.
- No signal handlers. The SDK does not listen for `SIGINT` / `SIGTERM`,
  so Ctrl+C and Kubernetes shutdowns behave exactly as before. Queued
  events are flushed on `beforeExit`. If you handle signals yourself, add
  `await opentelm.shutdown()` to your handler.

## Overhead

Measured with `npm run bench` (in-memory `fetch`, so only the
instrumentation differs):

| prompt size | added latency, p50 |
|-------------|--------------------|
| 400 chars   | below measurement noise |
| 4 KB        | ~80 µs             |
| 20 KB       | ~0.3 ms            |

Most of it is PII scrubbing and SHA-256 hashing, and it grows with prompt
size.

## Privacy

Prompts and responses are never sent, only their SHA-256 hashes. Before
hashing, these are replaced with placeholders:

| Pattern                         | Replaced with     |
|---------------------------------|-------------------|
| Email addresses                 | `[EMAIL]`         |
| Phone numbers (international, Indian) | `[PHONE]`   |
| `opentelm_live_*` keys          | `[OPENTELM_KEY]`  |
| Anthropic `sk-ant-...` keys     | `[ANTHROPIC_KEY]` |
| OpenAI `sk-...` keys            | `[OPENAI_KEY]`    |

Identical prompts still group together, and PII never goes into the hash.
Disable with `init({ scrubPii: false })`.

**Same hash as the Python SDK.** Text extraction and scrubbing are a port
of the Python SDK, checked against a shared fixture
(`sdk/testdata/hash_parity.json`). A prompt sent from a Python service and
from a Node service lands in the same group.

## Tagging deployments

```ts
opentelm.setVersion("prompt-v2"); // later events are tagged "prompt-v2"
opentelm.setVersion(null);        // back to untagged
```

This is what the prompt-regression detector compares.

## Manual tracking

For providers the SDK doesn't patch:

```ts
import { track } from "opentelm-node";

const t0 = Date.now();
const res = await callMyCustomLLM(prompt);
track({
  model: "my-finetuned-llama",
  provider: "other",
  prompt,
  response: res.text,
  promptTokens: res.inputTokens,
  completionTokens: res.outputTokens,
  latencyMs: Date.now() - t0,
  tags: { host: "gpu-1" },
});
```

## Serverless

Flush before your handler returns, because the runtime may freeze the
process:

```ts
export async function handler(event) {
  // ...
  await opentelm.flush();
}
```

## Options

```ts
await opentelm.init({
  apiKey: "opentelm_live_...",
  endpoint: "https://ingest.example.com", // "/v1/ingest" is appended if missing
  defaultTags: { env: "prod" },
  scrubPii: true,
  version: "v1",
  flushIntervalMs: 2000,
  maxQueueSize: 1000,                       // oldest events are dropped when full
  onError: (err) => logger.warn(err),      // default: console.warn, at most once a minute
});
```

## Development

```bash
npm install
npm run build && npm test   # tests use the real openai / anthropic packages with a fake fetch,
                            # plus smoke tests of the built package as a CJS and an ESM app
npm run bench
```

## License

MIT.

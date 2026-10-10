// ES module app: `import` everything. This is the case the previous SDK
// silently failed on (it only ever patched the require() copy).
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import opentelm from "opentelm-node";
import { createRequire } from "node:module";
const fake = createRequire(import.meta.url)("./fake-providers.cjs");

await opentelm.init({ apiKey: "k", endpoint: process.env.SINK_URL });

await new OpenAI({ apiKey: "x", maxRetries: 0, fetch: fake.openaiFetch })
  .chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
const stream = await new OpenAI({ apiKey: "x", maxRetries: 0, fetch: fake.openaiStreamFetch })
  .chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }], stream: true });
for await (const _ of stream) { /* drain */ }
await new Anthropic({ apiKey: "x", maxRetries: 0, fetch: fake.anthropicFetch })
  .messages.create({ model: "claude-test-model", max_tokens: 5, messages: [{ role: "user", content: "hi" }] });

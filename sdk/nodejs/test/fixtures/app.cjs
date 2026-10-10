// CommonJS app: require() everything, as an Express/CJS project would.
const OpenAI = require("openai");
const Anthropic = require("@anthropic-ai/sdk");
const opentelm = require("opentelm-node");
const fake = require("./fake-providers.cjs");

opentelm.init({ apiKey: "k", endpoint: process.env.SINK_URL });

(async () => {
  const OAI = OpenAI.default ?? OpenAI;
  const ANT = Anthropic.default ?? Anthropic;
  await new OAI({ apiKey: "x", maxRetries: 0, fetch: fake.openaiFetch })
    .chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
  const stream = await new OAI({ apiKey: "x", maxRetries: 0, fetch: fake.openaiStreamFetch })
    .chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }], stream: true });
  for await (const _ of stream) { /* drain */ }
  await new ANT({ apiKey: "x", maxRetries: 0, fetch: fake.anthropicFetch })
    .messages.create({ model: "claude-test-model", max_tokens: 5, messages: [{ role: "user", content: "hi" }] });
  // No explicit flush: the beforeExit hook must deliver the events.
})();

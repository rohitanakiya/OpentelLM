// Per-call overhead the SDK adds to an OpenAI chat completion.
// In-memory fetch, so the only difference between runs is the
// instrumentation. Run after `npm run build`:  node bench/overhead.mjs
import OpenAI from "openai";
import opentelm from "../dist/index.mjs";

const promptChars = Number(process.argv[2] ?? 4000);
const calls = 3000;
const body = JSON.stringify({
  id: "c", object: "chat.completion", created: 0, model: "gpt-4o",
  choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "x".repeat(800) } }],
  usage: { prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200 },
});
const fetch = async () => new Response(body, { status: 200, headers: { "content-type": "application/json" } });
const client = new OpenAI({ apiKey: "x", maxRetries: 0, fetch });
const messages = [
  { role: "system", content: "You are helpful. ".repeat(10) },
  { role: "user", content: "Contact me at a@b.com. " + "lorem ipsum ".repeat(Math.floor(promptChars / 12)) },
];

async function timed(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t0 = performance.now();
    await client.chat.completions.create({ model: "gpt-4o", messages });
    out.push(performance.now() - t0);
  }
  return out.sort((a, b) => a - b);
}
const p = (xs, q) => xs[Math.floor(xs.length * q)];

await timed(300);
const base = await timed(calls);
await opentelm.init({ apiKey: "bench", sender: async () => 202, flushIntervalMs: 3_600_000, maxQueueSize: calls + 10 });
await timed(300);
const inst = await timed(calls);
await opentelm.shutdown();
console.log(`prompt ${promptChars} chars`);
console.log(`uninstrumented  p50 ${p(base, 0.5).toFixed(3)} ms   p99 ${p(base, 0.99).toFixed(3)} ms`);
console.log(`instrumented    p50 ${p(inst, 0.5).toFixed(3)} ms   p99 ${p(inst, 0.99).toFixed(3)} ms`);
console.log(`SDK overhead    p50 ${((p(inst, 0.5) - p(base, 0.5)) * 1000).toFixed(0)} us`);

// Fake fetch implementations shared by the dist smoke-test apps.
const json = (body) => async () =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

const completion = {
  id: "c", object: "chat.completion", created: 0, model: "gpt-4o",
  choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "Paris" } }],
  usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 },
};
const message = {
  id: "m", type: "message", role: "assistant", model: "claude-test-model",
  content: [{ type: "text", text: "Paris" }], stop_reason: "end_turn", stop_sequence: null,
  usage: { input_tokens: 10, output_tokens: 2 },
};
const chunk = (delta) => ({ id: "c", object: "chat.completion.chunk", created: 0, model: "gpt-4o",
                            choices: [{ index: 0, delta, finish_reason: null }] });
const sse = [chunk({ role: "assistant", content: "Pa" }), chunk({ content: "ris" })]
  .map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";

module.exports = {
  openaiFetch: json(completion),
  openaiStreamFetch: async () => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }),
  anthropicFetch: json(message),
};

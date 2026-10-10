// Turn LLM request/response payloads into plain text for hashing.
//
// This is a line-for-line port of sdk/python/opentelm/_text.py. Both SDKs
// must produce the SAME text for the same payload, so a project that uses
// both Python and Node services gets the same prompt_hash for the same
// prompt. sdk/testdata/hash_parity.json pins that down: both test suites
// run against it.
//
// Nothing here may throw: callers also wrap these in try/catch.

const PLACEHOLDER_TYPES = new Set([
  "image", "image_url", "input_image", "input_audio", "audio",
  "document", "file", "input_file", "video",
]);
const MAX_DEPTH = 6;

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any

export function get(obj: Any, key: string): Any {
  if (obj === null || obj === undefined) return undefined;
  if (typeof obj !== "object" && typeof obj !== "function") return undefined;
  try {
    return obj[key];
  } catch {
    return undefined;
  }
}

/** Python's dict.get(key, default): the default only applies when the key
 *  is missing, NOT when it is present with a null value. */
function getOr(obj: Any, key: string, dflt: Any): Any {
  const v = get(obj, key);
  return v === undefined ? dflt : v;
}

/** Python's str() for the scalars that can show up in f-strings. */
function pyStr(v: Any): string {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  return String(v);
}

function escapeNonAscii(s: string): string {
  return s.replace(/[\u007f-￿]/g, (c) =>
    c.charCodeAt(0) === 0x7f ? c : "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
}

/** json.dumps(value, sort_keys=True, default=str): Python's separators
 *  (", " and ": "), sorted keys, non-ASCII escaped. */
export function pyJson(value: Any, depth = 0): string {
  if (depth > 50) return '"..."';
  if (value === null || value === undefined) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return Number.isNaN(value) ? "NaN" : value > 0 ? "Infinity" : "-Infinity";
    return String(value);
  }
  if (typeof value === "string") return escapeNonAscii(JSON.stringify(value));
  if (Array.isArray(value)) return "[" + value.map((v) => pyJson(v, depth + 1)).join(", ") + "]";
  if (typeof value === "object") {
    const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
    return "{" + keys.map((k) => `${escapeNonAscii(JSON.stringify(k))}: ${pyJson(value[k], depth + 1)}`).join(", ") + "}";
  }
  return escapeNonAscii(JSON.stringify(String(value)));
}

export function contentToText(content: Any, depth = 0): string {
  if (content === null || content === undefined || depth > MAX_DEPTH) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((p) => contentToText(p, depth + 1)).filter((p) => p).join("\n");
  }

  const partType = get(content, "type");
  const text = get(content, "text");
  if (typeof text === "string") return text;
  if (PLACEHOLDER_TYPES.has(partType)) return `[${partType}]`;
  if (partType === "tool_use" || partType === "server_tool_use") {
    return `[tool_use:${pyStr(getOr(content, "name", ""))}] ${pyJson(get(content, "input"))}`;
  }
  if (partType === "tool_result" || partType === "web_search_tool_result") {
    return contentToText(get(content, "content"), depth + 1);
  }
  if (partType === "thinking" || partType === "redacted_thinking" || partType === "reasoning") {
    return "";
  }
  if (partType === "function_call") {
    const args = getOr(content, "arguments", "");
    return `[tool_call:${pyStr(getOr(content, "name", ""))}] ${typeof args === "string" ? args : pyJson(args)}`;
  }
  if (partType === "function_call_output") {
    return contentToText(get(content, "output"), depth + 1);
  }
  const inner = get(content, "content");
  if (inner !== undefined && inner !== null && inner !== content) {
    return contentToText(inner, depth + 1);
  }
  return "";
}

function toolCallsToText(toolCalls: Any): string {
  if (!Array.isArray(toolCalls)) return "";
  const out: string[] = [];
  for (const call of toolCalls) {
    const fn = get(call, "function");
    const hasFn = fn !== undefined && fn !== null;
    const name = hasFn ? getOr(fn, "name", "") : getOr(call, "name", "");
    const args = hasFn ? getOr(fn, "arguments", "") : getOr(call, "arguments", "");
    out.push(`[tool_call:${pyStr(name)}] ${typeof args === "string" ? args : pyJson(args)}`);
  }
  return out.join("\n");
}

/** One "role: text" line per message, plus an optional Anthropic-style
 *  `system` (or Responses API `instructions`) first. */
export function messagesToText(messages: Any, system?: Any): string {
  const lines: string[] = [];
  if (system !== undefined && system !== null) {
    const sysText = contentToText(system);
    if (sysText) lines.push(`system: ${sysText}`);
  }
  let items: Any[];
  if (typeof messages === "string") items = [messages];
  else if (Array.isArray(messages)) items = messages;
  else return lines.join("\n");

  for (const msg of items) {
    if (typeof msg === "string") {
      lines.push(msg);
      continue;
    }
    const role = getOr(msg, "role", "") || "";
    const text = role ? contentToText(get(msg, "content")) : contentToText(msg);
    const tools = toolCallsToText(get(msg, "tool_calls"));
    const body = [text, tools].filter((t) => t).join("\n");
    lines.push(role ? `${role}: ${body}` : body);
  }
  return lines.join("\n");
}

export function openaiResponseText(response: Any): string {
  const choices = get(response, "choices");
  if (!Array.isArray(choices)) return "";
  return choices
    .map((choice) => {
      const msg = get(choice, "message");
      const text = contentToText(get(msg, "content"));
      const tools = toolCallsToText(get(msg, "tool_calls"));
      return [text, tools].filter((t) => t).join("\n");
    })
    .join("\n");
}

export function anthropicResponseText(response: Any): string {
  return contentToText(get(response, "content"));
}

export function responsesApiResponseText(response: Any): string {
  const output = get(response, "output");
  if (!Array.isArray(output)) return "";
  const out: string[] = [];
  for (const item of output) {
    const t = get(item, "type");
    if (t === "message") out.push(contentToText(get(item, "content")));
    else if (t === "function_call") out.push(contentToText(item));
  }
  return out.filter((t) => t).join("\n");
}

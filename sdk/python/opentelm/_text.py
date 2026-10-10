"""
Turn LLM request/response payloads into plain text for hashing.

Everything here is defensive. Real payloads mix plain dicts, TypedDicts,
pydantic models and SDK objects; content can be None (assistant tool-call
messages), a string, or a list of typed parts (text, images, tool_use,
tool_result, ...). None of these helpers may raise: a failure here must
never reach the developer's code. The call sites also wrap them in
try/except as a second line of defence.
"""
from __future__ import annotations

import json
from typing import Any

# Non-text parts are represented by a stable placeholder so they still
# contribute to the hash without embedding large binary payloads.
_PLACEHOLDER_TYPES = {
    "image", "image_url", "input_image", "input_audio", "audio",
    "document", "file", "input_file", "video",
}

_MAX_DEPTH = 6


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict-like or attribute-style object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def content_to_text(content: Any, _depth: int = 0) -> str:
    """Flatten a message ``content`` field of any supported shape to text."""
    if content is None or _depth > _MAX_DEPTH:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = (content_to_text(p, _depth + 1) for p in content)
        return "\n".join(p for p in parts if p)

    part_type = get(content, "type")
    text = get(content, "text")
    if isinstance(text, str):
        return text
    if part_type in _PLACEHOLDER_TYPES:
        return f"[{part_type}]"
    if part_type in ("tool_use", "server_tool_use"):
        return f"[tool_use:{get(content, 'name', '')}] {_json(get(content, 'input'))}"
    if part_type == "tool_result" or part_type == "web_search_tool_result":
        return content_to_text(get(content, "content"), _depth + 1)
    if part_type in ("thinking", "redacted_thinking", "reasoning"):
        return ""  # model-internal; not part of the prompt identity
    if part_type == "function_call":  # OpenAI Responses API input/output item
        args = get(content, "arguments", "")
        return f"[tool_call:{get(content, 'name', '')}] {args if isinstance(args, str) else _json(args)}"
    if part_type == "function_call_output":
        return content_to_text(get(content, "output"), _depth + 1)
    inner = get(content, "content")
    if inner is not None and inner is not content:
        return content_to_text(inner, _depth + 1)
    return ""


def _tool_calls_to_text(tool_calls: Any) -> str:
    out = []
    for call in tool_calls or []:
        fn = get(call, "function")
        name = get(fn, "name", "") if fn is not None else get(call, "name", "")
        args = get(fn, "arguments", "") if fn is not None else get(call, "arguments", "")
        out.append(f"[tool_call:{name}] {args if isinstance(args, str) else _json(args)}")
    return "\n".join(out)


def messages_to_text(messages: Any, system: Any = None) -> str:
    """Flatten a chat ``messages`` list (plus an optional Anthropic-style
    ``system`` argument) into one ``role: text`` line per message."""
    lines: list[str] = []
    if system is not None:
        sys_text = content_to_text(system)
        if sys_text:
            lines.append(f"system: {sys_text}")
    if isinstance(messages, (str, bytes)):
        messages = [messages]
    try:
        iterator = iter(messages or [])
    except TypeError:
        return "\n".join(lines)
    for msg in iterator:
        if isinstance(msg, str):
            lines.append(msg)
            continue
        role = get(msg, "role", "") or ""
        # Chat messages carry `content`; Responses API items (function_call,
        # function_call_output, ...) are typed parts in their own right.
        text = content_to_text(get(msg, "content")) if role else content_to_text(msg)
        tools = _tool_calls_to_text(get(msg, "tool_calls"))
        body = "\n".join(t for t in (text, tools) if t)
        lines.append(f"{role}: {body}" if role else body)
    return "\n".join(lines)


def openai_response_text(response: Any) -> str:
    out = []
    for choice in get(response, "choices") or []:
        msg = get(choice, "message")
        text = content_to_text(get(msg, "content"))
        tools = _tool_calls_to_text(get(msg, "tool_calls"))
        out.append("\n".join(t for t in (text, tools) if t))
    return "\n".join(out)


def anthropic_response_text(response: Any) -> str:
    return content_to_text(get(response, "content"))


def responses_api_response_text(response: Any) -> str:
    """Text of an OpenAI Responses API result: message text plus tool calls."""
    out = []
    for item in get(response, "output") or []:
        item_type = get(item, "type")
        if item_type == "message":
            out.append(content_to_text(get(item, "content")))
        elif item_type == "function_call":
            out.append(content_to_text(item))
    return "\n".join(t for t in out if t)

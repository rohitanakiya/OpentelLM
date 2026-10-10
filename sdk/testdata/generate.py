"""
Regenerate hash_parity.json from the Python SDK (the reference implementation).

Both SDKs' test suites assert against hash_parity.json, so the same prompt
produces the same prompt_hash whether it was sent from Python or Node.
If you change text extraction or PII scrubbing in one SDK, change the other,
then run:  python sdk/testdata/generate.py
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from opentelm._text import (  # noqa: E402
    anthropic_response_text,
    messages_to_text,
    openai_response_text,
    responses_api_response_text,
)
from opentelm.pii import scrub_pii  # noqa: E402

MESSAGE_CASES = [
    ("plain_user", [{"role": "user", "content": "Capital of France?"}], None),
    ("system_and_turns", [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"},
                          {"role": "assistant", "content": "hello"}], None),
    ("anthropic_system_param", [{"role": "user", "content": "hi"}], "You are helpful."),
    ("anthropic_system_blocks", [{"role": "user", "content": "hi"}],
     [{"type": "text", "text": "Block one"}, {"type": "text", "text": "Block two"}]),
    ("tool_call_null_content", [
        {"role": "user", "content": "Weather?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Paris\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "18C"}], None),
    ("multimodal", [{"role": "user", "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}], None),
    ("anthropic_tool_use_and_result", [
        {"role": "user", "content": "Weather in Paris?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t1", "name": "get_weather",
             "input": {"unit": "C", "city": "Paris", "nested": {"b": [1, 2], "a": None, "flag": True}}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "18C"}]}]}], None),
    ("unicode_tool_input", [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "say", "input": {"text": "café ☕ 😀"}}]}], None),
    ("thinking_is_skipped", [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Answer"}]}], None),
    ("documents_and_audio", [{"role": "user", "content": [
        {"type": "document", "source": {}}, {"type": "input_audio", "input_audio": {}},
        {"type": "text", "text": "Summarise"}]}], None),
    ("responses_string_input", "Capital of France?", "Be brief."),
    ("responses_items", [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "function_call", "call_id": "c", "name": "lookup", "arguments": "{\"q\":1}"},
        {"type": "function_call_output", "call_id": "c", "output": "42"}], None),
    ("missing_role", [{"content": "no role here"}], None),
    ("null_tool_name", [{"role": "assistant", "content": None,
                         "tool_calls": [{"function": {"name": None, "arguments": {"x": 1}}}]}], None),
    ("empty", [], None),
    ("none_messages", None, None),
]

RESPONSE_CASES = [
    ("openai_text", "openai", {"choices": [{"message": {"role": "assistant", "content": "Paris"}}]}),
    ("openai_tool_calls", "openai", {"choices": [{"message": {"role": "assistant", "content": None,
                                                              "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}}]}),
    ("openai_two_choices", "openai", {"choices": [{"message": {"content": "A"}}, {"message": {"content": "B"}}]}),
    ("anthropic_blocks", "anthropic", {"content": [{"type": "text", "text": "Hello"},
                                                   {"type": "tool_use", "name": "f", "input": {"b": 2, "a": 1}}]}),
    ("responses_message_and_call", "responses", {"output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "Paris"}]},
        {"type": "function_call", "name": "f", "arguments": "{}"}]}),
]

PII_INPUTS = [
    "mail ronu@example.com now", "two: a@b.co and c.d+e@x-y.org", "call 9876543210", "call +91 9876543210",
    "call +919876543210", "call 91-9876543210", "call +44 7911123456", "+1 4155552671 is US",
    "key opentelm_live_0123456789abcdef0123", "key sk-proj-abcdefghijklmnopqrstuvwx",
    "key sk-ant-api03-abcdefghijklmnopqrstuv", "order 12345 shipped 2026-10-11", "nothing sensitive here", "",
]

RESPONSE_FNS = {"openai": openai_response_text, "anthropic": anthropic_response_text,
                "responses": responses_api_response_text}


def build() -> dict:
    return {
        "_comment": "Generated by sdk/testdata/generate.py from the Python SDK. Both SDK test suites "
                    "assert against this file, so the same prompt hashes the same in Python and Node.",
        "messages": [{"name": n, "messages": m, "system": s, "expected": messages_to_text(m, system=s)}
                     for n, m, s in MESSAGE_CASES],
        "responses": [{"name": n, "kind": k, "response": r, "expected": RESPONSE_FNS[k](r)}
                      for n, k, r in RESPONSE_CASES],
        "pii": [{"input": t, "expected": scrub_pii(t)} for t in PII_INPUTS],
    }


if __name__ == "__main__":
    out = HERE / "hash_parity.json"
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")

from __future__ import annotations

import pytest

from opentelm._text import content_to_text, messages_to_text
from opentelm.pii import detect_pii, scrub_pii

WEIRD_CONTENT = [
    None, "", "plain", 42, 3.5, b"bytes", [], [None], [None, "a"],
    [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {"url": "x"}}],
    {"type": "tool_result", "content": [{"type": "text", "text": "nested"}]},
    {"type": "tool_use", "name": "f", "input": {"x": object()}},
    {"type": "unknown_future_block", "payload": 1},
    [[[[[[[["deep"]]]]]]]],
    object(),
]


@pytest.mark.parametrize("content", WEIRD_CONTENT)
def test_content_to_text_never_raises(content):
    assert isinstance(content_to_text(content), str)


@pytest.mark.parametrize("messages", [
    None, [], "just a string", 123,
    [{"role": "user"}], [{"content": "no role"}], [None],
    [{"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}],
    [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    [{"type": "function_call_output", "call_id": "c", "output": "42"}],
])
def test_messages_to_text_never_raises(messages):
    assert isinstance(messages_to_text(messages), str)


def test_attribute_style_objects_are_supported():
    class Part:
        type = "text"
        text = "hello"

    class Msg:
        role = "user"
        content = [Part()]

    assert messages_to_text([Msg()]) == "user: hello"


def test_system_prompt_is_part_of_prompt_identity():
    msgs = [{"role": "user", "content": "hi"}]
    assert messages_to_text(msgs, system="A") != messages_to_text(msgs, system="B")


@pytest.mark.parametrize("text,expected", [
    ("mail ronu@example.com now", "mail [EMAIL] now"),
    ("call 9876543210", "call [PHONE]"),
    ("call +91 9876543210", "call [PHONE]"),
    ("call +44 7911123456", "call [PHONE]"),
    ("key opentelm_live_0123456789abcdef0123", "key [OPENTELM_KEY]"),
    ("key sk-proj-abcdefghijklmnopqrstuvwx", "key [OPENAI_KEY]"),
    ("key sk-ant-api03-abcdefghijklmnopqrstuv", "key [ANTHROPIC_KEY]"),
    ("nothing sensitive here", "nothing sensitive here"),
])
def test_scrub_pii(text, expected):
    assert scrub_pii(text) == expected


def test_detect_pii():
    assert detect_pii("a@b.co 9876543210") == ["email", "phone_in"]

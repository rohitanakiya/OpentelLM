"""Cross-SDK parity: sdk/testdata/hash_parity.json is generated from this
SDK and the Node SDK's tests assert against the same file. This test fails
if the Python code changes without regenerating the fixture
(python sdk/testdata/generate.py), which would silently break parity."""
from __future__ import annotations

import json
import pathlib

import pytest

from opentelm._text import (
    anthropic_response_text,
    messages_to_text,
    openai_response_text,
    responses_api_response_text,
)
from opentelm.pii import scrub_pii

FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "testdata" / "hash_parity.json"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="sdk/testdata not in this checkout")
DATA = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {"messages": [], "responses": [], "pii": []}
FNS = {"openai": openai_response_text, "anthropic": anthropic_response_text, "responses": responses_api_response_text}


@pytest.mark.parametrize("case", DATA["messages"], ids=lambda c: c["name"])
def test_messages(case):
    assert messages_to_text(case["messages"], system=case["system"]) == case["expected"]


@pytest.mark.parametrize("case", DATA["responses"], ids=lambda c: c["name"])
def test_responses(case):
    assert FNS[case["kind"]](case["response"]) == case["expected"]


@pytest.mark.parametrize("case", DATA["pii"], ids=lambda c: c["input"] or "empty")
def test_pii(case):
    assert scrub_pii(case["input"]) == case["expected"]

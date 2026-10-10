"""
PII scrubbing, applied to prompt/response text BEFORE hashing.

Covers the same categories as sdk/nodejs/src/pii.ts. Matches are replaced
with fixed placeholders, which keeps the hash stable: two prompts that
differ only in the email address hash the same, which is what prompt
grouping wants.

The patterns are deliberately conservative: a false positive only changes
which prompts get grouped together, while a false negative would put PII
into the hash input.
"""
from __future__ import annotations

import re

# Each pattern either begins with a literal (which the regex engine can scan
# for quickly) or is guarded by a cheap substring check. Naive versions of
# these patterns cost ~0.5 ms on a 4 KB prompt, which would dominate the
# SDK's per-call overhead.
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_INTL = re.compile(r"\+\d{1,3}[-\s]?\d{4,14}\b")          # +44 7911123456, +919876543210
_PHONE_IN = re.compile(r"[6-9]\d{9}\b")                         # 9876543210
_PHONE_IN_PREFIX = re.compile(r"91[-\s]?\[PHONE\]")               # "91 9876543210" leftovers
_OPENTELM_KEY = re.compile(r"opentelm_live_[A-Za-z0-9]{16,}")
_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
_OPENAI_KEY = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")

_LOCAL_PART_MAX = 64
_DOMAIN_MAX = 255


def _scrub_emails(text: str) -> str:
    """Replace emails, running the regex only in windows around each '@'."""
    out: list[str] = []
    last = 0
    at = text.find("@")
    while at != -1:
        m = _EMAIL.search(text, max(last, at - _LOCAL_PART_MAX), min(len(text), at + _DOMAIN_MAX))
        if m and m.start() <= at < m.end():
            out.append(text[last:m.start()])
            out.append("[EMAIL]")
            last = m.end()
            at = text.find("@", last)
        else:
            at = text.find("@", at + 1)
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


def scrub_pii(text: str) -> str:
    """Replace known PII / secret patterns with placeholders."""
    if not text:
        return text
    if "@" in text:
        text = _scrub_emails(text)
    if "+" in text:
        text = _PHONE_INTL.sub("[PHONE]", text)
    text = _PHONE_IN.sub("[PHONE]", text)
    if "91" in text and "[PHONE]" in text:
        text = _PHONE_IN_PREFIX.sub("[PHONE]", text).replace("+[PHONE]", "[PHONE]")
    if "opentelm_live_" in text:
        text = _OPENTELM_KEY.sub("[OPENTELM_KEY]", text)
    if "sk-" in text:
        # Anthropic first: sk-ant-... would otherwise match the OpenAI pattern.
        text = _ANTHROPIC_KEY.sub("[ANTHROPIC_KEY]", text)
        text = _OPENAI_KEY.sub("[OPENAI_KEY]", text)
    return text


_DETECTORS: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL),
    ("phone_intl", _PHONE_INTL),
    ("phone_in", _PHONE_IN),
    ("opentelm_key", _OPENTELM_KEY),
    ("anthropic_key", _ANTHROPIC_KEY),
    ("openai_key", _OPENAI_KEY),
]


def detect_pii(text: str) -> list[str]:
    """Return the names of the pattern categories found in ``text``."""
    return [name for name, pattern in _DETECTORS if pattern.search(text or "")]

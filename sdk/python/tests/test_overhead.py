"""Guards the "adds under 2 ms per call" claim. Real overhead is far lower;
the threshold is loose so the test is stable on slow CI runners."""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("openai")
from conftest import httpx2 as _http  # noqa: E402

if _http.__name__ != "httpx2":  # the benchmark targets current (httpx2-based) SDK releases
    pytest.skip("benchmark needs an httpx2-based openai", allow_module_level=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
from overhead import measure  # noqa: E402


def test_median_overhead_under_2ms():
    result = measure(calls=500, prompt_chars=4000)
    assert result["overhead_p50"] < 2.0, result

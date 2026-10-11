"""t-digest accuracy, measured against exact percentiles.

Errors are measured in RANK terms (how far the estimate's position in the
sorted data is from q), which is the guarantee a t-digest makes. Value
errors on a long-tailed distribution can look large near p99.9 just
because neighbouring values are far apart.
"""
from __future__ import annotations

import bisect
import math
import random

import pytest
from tdigest import TDigest


def exact(values: list[float], q: float) -> float:
    """numpy's default (linear) percentile."""
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def rank_error(sorted_values: list[float], estimate: float, q: float) -> float:
    n = len(sorted_values)
    lo = bisect.bisect_left(sorted_values, estimate) / n
    hi = bisect.bisect_right(sorted_values, estimate) / n
    if lo <= q <= hi:
        return 0.0
    return min(abs(lo - q), abs(hi - q))


def digest_of(values) -> TDigest:
    d = TDigest()
    for v in values:
        d.add(v)
    return d


DISTRIBUTIONS = {
    "lognormal": lambda r: r.lognormvariate(6, 0.6),          # typical LLM latency shape
    "uniform":   lambda r: r.uniform(100, 2000),
    "bimodal":   lambda r: r.gauss(200, 20) if r.random() < 0.9 else r.gauss(3000, 300),
    "integers":  lambda r: float(r.randint(1, 50)),           # many duplicates
}


def test_empty_digest_returns_zero():
    assert TDigest().quantile(0.99) == 0.0
    assert len(TDigest()) == 0


def test_single_value():
    d = digest_of([123.0])
    assert d.quantile(0) == d.quantile(0.5) == d.quantile(1) == 123.0


@pytest.mark.parametrize("n", [2, 3, 10, 50])
def test_small_windows_are_exact(n):
    # A quiet minute: every value is still its own centroid, so the answer
    # must match the textbook percentile exactly.
    r = random.Random(n)
    values = [r.lognormvariate(6, 0.6) for _ in range(n)]
    d = digest_of(values)
    for q in (0, 0.25, 0.5, 0.9, 0.95, 0.99, 1):
        assert d.quantile(q) == pytest.approx(exact(values, q), rel=1e-9)


@pytest.mark.parametrize("name", list(DISTRIBUTIONS))
def test_large_windows_are_accurate_in_rank(name):
    r = random.Random(42)
    values = [DISTRIBUTIONS[name](r) for _ in range(20_000)]
    s = sorted(values)
    d = digest_of(values)
    for q in (0.5, 0.9, 0.95, 0.99, 0.999):
        # Duplicate-heavy integer data gets a looser bound: the estimate can
        # fall between two runs of identical values.
        bound = 0.02 if name == "integers" else 0.005
        assert rank_error(s, d.quantile(q), q) <= bound, (name, q)
    assert d.quantile(0) == s[0]
    assert d.quantile(1) == s[-1]


def test_memory_is_bounded():
    r = random.Random(7)
    d = TDigest(compression=100)
    for _ in range(200_000):
        d.add(r.lognormvariate(6, 0.6))
    assert len(d) == 200_000
    assert len(d.centroids()) <= 200


def test_weights_add_up_to_count():
    r = random.Random(3)
    d = digest_of(r.uniform(0, 1000) for _ in range(5_000))
    assert sum(w for _, w in d.centroids()) == pytest.approx(5_000)


def test_merged_digests_match_one_digest_of_everything():
    # This is what ClickHouse does when it merges stored centroids across rows.
    r = random.Random(11)
    parts = [[r.lognormvariate(6, 0.6) for _ in range(r.randint(1, 3_000))] for _ in range(30)]
    merged = TDigest()
    for p in parts:
        merged.merge(digest_of(p))
    everything = sorted(v for p in parts for v in p)
    for q in (0.5, 0.95, 0.99):
        assert rank_error(everything, merged.quantile(q), q) <= 0.005


def test_quantiles_are_monotonic():
    r = random.Random(5)
    d = digest_of(r.lognormvariate(6, 0.6) for _ in range(10_000))
    qs = [i / 200 for i in range(201)]
    vals = [d.quantile(q) for q in qs]
    assert all(a <= b + 1e-9 for a, b in zip(vals, vals[1:]))


def test_bad_inputs_are_ignored():
    d = TDigest()
    d.add(math.nan)
    d.add(10.0, w=0)
    d.add(10.0, w=-1)
    assert len(d) == 0
    d.add(5.0)
    assert d.quantile(0.99) == 5.0

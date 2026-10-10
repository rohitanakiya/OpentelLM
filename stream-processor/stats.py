"""
Small, dependency-free significance tests for prompt-regression detection.

All tests are ONE-SIDED: they ask "is the current version WORSE than the
baseline?" (higher error rate, higher latency). A version that got better
should never raise a regression alert.

Kept in pure Python on purpose: scipy would add ~100 MB to the
stream-processor image for three functions. tests/test_stats.py checks
these implementations against values computed with scipy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def normal_sf(z: float) -> float:
    """P(Z > z) for a standard normal variable."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


# ---------------------------------------------------------------------------
# Error rates: is p_current > p_baseline?
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProportionTest:
    p_value: float
    method: str   # "z-test" or "fisher-exact"


def two_proportion_z_test(x_base: int, n_base: int, x_cur: int, n_cur: int) -> float:
    """One-sided pooled two-proportion z-test. Returns P(observe this big an
    increase | no real difference)."""
    if n_base <= 0 or n_cur <= 0:
        return 1.0
    pooled = (x_base + x_cur) / (n_base + n_cur)
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n_base + 1.0 / n_cur))
    if se == 0.0:
        return 1.0  # both all-success or both all-error: no evidence of a change
    z = (x_cur / n_cur - x_base / n_base) / se
    return normal_sf(z)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_greater(x_base: int, n_base: int, x_cur: int, n_cur: int) -> float:
    """One-sided Fisher exact test: P(current has >= x_cur errors | the
    total errors are spread at random over both versions). Exact for any
    sample size; used when the normal approximation is unreliable."""
    if n_base <= 0 or n_cur <= 0:
        return 1.0
    total_err = x_base + x_cur
    total = n_base + n_cur
    log_denominator = _log_comb(total, total_err)
    hi = min(total_err, n_cur)
    p = 0.0
    for k in range(x_cur, hi + 1):
        if total_err - k > n_base:
            continue
        p += math.exp(_log_comb(n_cur, k) + _log_comb(n_base, total_err - k) - log_denominator)
    return min(1.0, p)


def error_rate_increase_test(x_base: int, n_base: int, x_cur: int, n_cur: int) -> ProportionTest:
    """Pick the right test for the counts. The z-test's normal approximation
    needs at least ~5 expected errors and ~5 expected successes in each
    group; LLM error rates are often around 1%, so small samples routinely
    fail that, and Fisher's exact test is used instead."""
    if n_base <= 0 or n_cur <= 0:
        return ProportionTest(1.0, "z-test")
    pooled = (x_base + x_cur) / (n_base + n_cur)
    min_expected = min(n * q for n in (n_base, n_cur) for q in (pooled, 1.0 - pooled))
    if min_expected >= 5:
        return ProportionTest(two_proportion_z_test(x_base, n_base, x_cur, n_cur), "z-test")
    return ProportionTest(fisher_exact_greater(x_base, n_base, x_cur, n_cur), "fisher-exact")


# ---------------------------------------------------------------------------
# Latency: is the current distribution shifted upwards?
# ---------------------------------------------------------------------------
def mann_whitney_greater(baseline: list[float], current: list[float]) -> float:
    """One-sided Mann-Whitney U test (H1: current tends to be larger), using
    the normal approximation with tie and continuity corrections, the same
    as scipy.stats.mannwhitneyu(current, baseline, alternative="greater",
    method="asymptotic").

    Rank-based, so a handful of 60-second timeouts can't dominate the way
    they would in a t-test on means: latency distributions are long-tailed.
    """
    n1, n2 = len(baseline), len(current)
    if n1 == 0 or n2 == 0:
        return 1.0
    combined = sorted([(v, 0) for v in baseline] + [(v, 1) for v in current])
    n = n1 + n2

    rank_sum_current = 0.0
    tie_term = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0       # ranks are 1-based
        t = j - i + 1
        if t > 1:
            tie_term += t ** 3 - t
        rank_sum_current += avg_rank * sum(1 for k in range(i, j + 1) if combined[k][1] == 1)
        i = j + 1

    u_current = rank_sum_current - n2 * (n2 + 1) / 2.0
    mean = n1 * n2 / 2.0
    var = n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))
    if var <= 0:
        return 1.0  # every value identical
    z = (u_current - mean - 0.5) / math.sqrt(var)
    return normal_sf(z)


def median(values: list[float]) -> float:
    s = sorted(values)
    m = len(s)
    if m == 0:
        return 0.0
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0

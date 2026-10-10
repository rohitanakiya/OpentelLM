"""
The significance tests in stats.py, checked against scipy 1.x.

Reference p-values were computed with:
    scipy.stats.mannwhitneyu(current, baseline, alternative="greater",
                             method="asymptotic", use_continuity=True)
    scipy.stats.fisher_exact([[x_cur, n_cur - x_cur], [x_base, n_base - x_base]],
                             alternative="greater")
    scipy.stats.norm.sf(z) for the pooled two-proportion z statistic
and pasted here so CI does not need scipy installed.
"""
from __future__ import annotations

import pytest
from stats import (
    error_rate_increase_test,
    fisher_exact_greater,
    mann_whitney_greater,
    median,
    two_proportion_z_test,
)

A = [100, 120, 130, 150, 150, 160, 200, 210, 250, 400]
B = [140, 150, 170, 180, 220, 230, 250, 260, 300, 900]


@pytest.mark.parametrize("baseline,current,expected", [
    (A, B, 0.05586747451493413),
    ([1, 1, 2, 2, 2, 3, 3, 4], [2, 3, 3, 3, 4, 4, 5, 5], 0.01545112158851795),   # heavy ties
    (B, A, 0.9521610710129494),                                                  # current is faster
])
def test_mann_whitney_matches_scipy(baseline, current, expected):
    assert mann_whitney_greater(baseline, current) == pytest.approx(expected, rel=1e-9)


def test_mann_whitney_degenerate_inputs():
    assert mann_whitney_greater([], [1.0, 2.0]) == 1.0
    assert mann_whitney_greater([5.0] * 10, [5.0] * 10) == 1.0   # all identical


@pytest.mark.parametrize("xb,nb,xc,nc,fisher,z", [
    (2, 200, 9, 200, 0.03090698291, 0.01616879948),
    (0, 100, 4, 100, 0.06062036069, 0.02167587563),
    (10, 1000, 25, 1000, 0.007874852761, 0.005264659033),
    (50, 1000, 50, 1000, 0.5408281487, 0.5),
    (30, 1000, 20, 1000, 0.9428498847, 0.9239609596),
    (1, 50, 6, 60, 0.0907993988, 0.04349354568),
    (100, 5000, 140, 5000, 0.005341886466, 0.004480455195),
])
def test_proportion_tests_match_scipy(xb, nb, xc, nc, fisher, z):
    assert fisher_exact_greater(xb, nb, xc, nc) == pytest.approx(fisher, rel=1e-8)
    assert two_proportion_z_test(xb, nb, xc, nc) == pytest.approx(z, rel=1e-8)


def test_small_counts_use_fisher_large_counts_use_z():
    # 0 -> 4 errors out of 100: only 2 expected errors per group, the normal
    # approximation would overstate the evidence (p=0.022 vs exact 0.061).
    small = error_rate_increase_test(0, 100, 4, 100)
    assert small.method == "fisher-exact" and small.p_value == pytest.approx(0.06062036069, rel=1e-8)
    large = error_rate_increase_test(100, 5000, 140, 5000)
    assert large.method == "z-test"


def test_proportion_edge_cases():
    assert two_proportion_z_test(0, 100, 0, 100) == 1.0      # no errors anywhere
    assert two_proportion_z_test(100, 100, 100, 100) == 1.0  # all errors everywhere
    assert error_rate_increase_test(0, 0, 5, 10).p_value == 1.0


def test_median():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    assert median([]) == 0.0

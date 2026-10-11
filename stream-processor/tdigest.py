"""
A merging t-digest (Dunning & Ertl, "Computing Extremely Accurate
Quantiles Using t-Digests", 2019) for streaming latency percentiles.

Why not keep the raw latencies? Memory. A list grows with traffic (100k
requests in a minute = 100k floats per window), and the old code then
took a random sample of 1000 to sort, so p99 was computed from ~10 points
and jumped around from minute to minute. A t-digest keeps at most a few
hundred weighted centroids however many values go in, and its error is
smallest at the extremes (p99, p99.9), which is exactly where latency
percentiles matter.

Why centroids matter beyond memory: percentiles can't be combined
(the p99 of a day is NOT the average or max of 1440 minute p99s), but
digests can. Each metrics row stores its centroids (means + weights), so
ClickHouse can merge any range of rows into a true p99 with
quantileTDigestWeightedArray(0.99)(latency_means, latency_weights).

Pure Python, no dependencies. Accuracy is checked against exact
percentiles in tests/test_tdigest.py.
"""
from __future__ import annotations

import math


def _k(q: float, compression: float) -> float:
    # Scale function k1: centroids are tiny near q=0 and q=1 (accurate
    # tails) and large around the median.
    return compression / (2 * math.pi) * math.asin(2 * q - 1)


def _k_inv(k: float, compression: float) -> float:
    return (math.sin(k * 2 * math.pi / compression) + 1) / 2


class TDigest:
    __slots__ = ("compression", "_means", "_weights", "_buffer", "_count", "min", "max")

    def __init__(self, compression: float = 100.0) -> None:
        self.compression = compression
        self._means: list[float] = []
        self._weights: list[float] = []
        self._buffer: list[tuple[float, float]] = []
        self._count = 0.0
        self.min = math.inf
        self.max = -math.inf

    def __len__(self) -> int:
        return int(self._count)

    def add(self, x: float, w: float = 1.0) -> None:
        if w <= 0 or math.isnan(x):
            return
        self._buffer.append((x, w))
        self._count += w
        if x < self.min:
            self.min = x
        if x > self.max:
            self.max = x
        if len(self._buffer) >= 5 * self.compression:
            self._compress()

    def merge(self, other: "TDigest") -> None:
        for m, w in other.centroids():
            self.add(m, w)
        if other._count:
            self.min = min(self.min, other.min)
            self.max = max(self.max, other.max)

    def _compress(self) -> None:
        if not self._buffer:
            return
        points = sorted(list(zip(self._means, self._weights)) + self._buffer)
        self._buffer = []
        total = self._count
        means: list[float] = []
        weights: list[float] = []
        cur_m, cur_w = points[0]
        so_far = 0.0  # weight of all finished centroids
        limit = total * _k_inv(_k(0.0, self.compression) + 1, self.compression)
        for m, w in points[1:]:
            if so_far + cur_w + w <= limit:
                # Fold into the current centroid (running weighted mean).
                cur_w += w
                cur_m += (m - cur_m) * w / cur_w
            else:
                means.append(cur_m)
                weights.append(cur_w)
                so_far += cur_w
                limit = total * _k_inv(_k(so_far / total, self.compression) + 1, self.compression)
                cur_m, cur_w = m, w
        means.append(cur_m)
        weights.append(cur_w)
        self._means, self._weights = means, weights

    def centroids(self) -> list[tuple[float, float]]:
        self._compress()
        return list(zip(self._means, self._weights))

    def quantile(self, q: float) -> float:
        """Estimated value at quantile q in [0, 1]; 0.0 when empty.

        Centroid i "sits" at the middle of its weight on the rank axis.
        The target rank q*(n-1) + 0.5 puts q=0 on the first value and q=1
        on the last, and makes the answer exactly numpy's / ClickHouse
        quantileExactInclusive's linear interpolation while every centroid
        is still a single value (small windows).
        """
        self._compress()
        if not self._count:
            return 0.0
        q = min(max(q, 0.0), 1.0)
        means, weights, n = self._means, self._weights, self._count
        if len(means) == 1:
            return means[0]
        target = q * (n - 1) + 0.5

        # Below the first centroid's centre: interpolate from the minimum.
        first_half = weights[0] / 2
        if target <= first_half:
            if first_half <= 0.5:
                return means[0]
            return self.min + (means[0] - self.min) * (target - 0.5) / (first_half - 0.5)

        cum = first_half  # rank of the current centroid's centre
        for i in range(len(means) - 1):
            step = (weights[i] + weights[i + 1]) / 2
            if target <= cum + step:
                return means[i] + (means[i + 1] - means[i]) * (target - cum) / step
            cum += step

        # Above the last centroid's centre: interpolate to the maximum.
        last_half = weights[-1] / 2
        if last_half <= 0.5:
            return means[-1]
        frac = (target - (n - last_half)) / (last_half - 0.5)
        return means[-1] + (self.max - means[-1]) * min(frac, 1.0)

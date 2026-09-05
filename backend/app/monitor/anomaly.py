"""Statistical anomaly detection over metric streams.

Threshold alerts (see app.monitor.alerts) catch metric values that cross a
fixed line. Anomalies are subtler: a value that is *abnormal for that
series' own recent behaviour* — a traffic drop on a link that normally
carries 500 Mbps, or a latency spike on a device that usually answers in
2 ms — even when no static threshold is crossed.

Each series keeps an EWMA (exponentially weighted) baseline of mean and
variance. A sample is anomalous when ALL of these hold:

- its z-score against the baseline exceeds ``z_threshold``;
- the deviation exceeds ``min_rel_deviation`` of the baseline mean
  (relative guard: quiet series must not flag microscopic wiggles);
- the deviation exceeds ``min_abs_deviation`` in native units (absolute
  guard, e.g. latency must move at least a few ms);
- the deviation is on an interesting side of the baseline (``direction``).

Baselines only absorb *normal* samples: an anomalous sample freezes its
baseline, so a sustained shift keeps firing instead of quietly becoming the
new normal (and auto-clearing). The module is pure — no I/O — so policies
and behaviour are unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_Z_THRESHOLD = 3.5

@dataclass(frozen=True)
class SeriesPolicy:
    """Per-metric sensitivity knobs (see module docstring)."""

    metric: str  # label carried on verdicts ("traffic", "latency")
    direction: str  # "both" | "high" | "low"
    min_rel_deviation: float
    min_abs_deviation: float = 0.0

# Built-in series policies. Keys are MetricSample.metric_name values.
SERIES_POLICIES: dict[str, SeriesPolicy] = {
    # latency: only spikes matter, and small wiggles are noise
    "latency_ms": SeriesPolicy(
        metric="latency", direction="high", min_rel_deviation=0.5, min_abs_deviation=5.0
    ),
    # throughput: a drop (link failure/congestion) and a spike (attack,
    # backup job) are both interesting
    "traffic_bps": SeriesPolicy(metric="traffic", direction="both", min_rel_deviation=0.5),
}

@dataclass(frozen=True)
class AnomalyVerdict:
    """One detected anomaly — carried on monitor observations into the alert engine."""

    metric: str
    direction: str  # "high" | "low"
    value: float
    baseline_mean: float
    baseline_stddev: float
    z_score: float

class EwmaBaseline:
    """Rolling mean + variance of one metric series (EWMA recursion)."""

    def __init__(self, alpha: float = 0.25) -> None:
        self.alpha = alpha
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0  # EWMA of squared deviations

    @property
    def stddev(self) -> float:
        return math.sqrt(max(self._m2, 0.0))

    def update(self, value: float) -> None:
        """Absorb one (assumed normal) sample into the baseline."""
        if self.n == 0:
            self.mean = value
        else:
            dev = value - self.mean
            self.mean += self.alpha * dev
            self._m2 = (1 - self.alpha) * (self._m2 + self.alpha * dev * dev)
        self.n += 1

class MetricAnomalyDetector:
    """Per-series baselines keyed by a hashable series key.

    ``observe`` returns an ``AnomalyVerdict`` for anomalous samples and
    ``None`` otherwise (including while the baseline is still warming up);
    normal samples update the baseline, anomalous ones do not.
    """

    def __init__(
        self,
        min_samples: int = 10,
        z_threshold: float = DEFAULT_Z_THRESHOLD,
        alpha: float = 0.25,
        policies: dict[str, SeriesPolicy] | None = None,
    ) -> None:
        self.min_samples = min_samples
        self.z_threshold = z_threshold
        self.alpha = alpha
        self.policies = policies or SERIES_POLICIES
        self._baselines: dict[tuple, EwmaBaseline] = {}

    def observe(self, key: tuple, metric_name: str, value: float) -> AnomalyVerdict | None:
        policy = self.policies.get(metric_name)
        if policy is None:  # unknown series: track nothing, flag nothing
            return None
        baseline = self._baselines.setdefault(key, EwmaBaseline(self.alpha))
        if baseline.n < self.min_samples:
            baseline.update(value)
            return None

        dev = value - baseline.mean
        stddev = max(baseline.stddev, 1e-9)
        z = dev / stddev
        rel = abs(dev) / baseline.mean if baseline.mean > 0 else math.inf
        side_ok = (
            policy.direction == "both"
            or (policy.direction == "high" and dev > 0)
            or (policy.direction == "low" and dev < 0)
        )
        anomalous = (
            abs(z) >= self.z_threshold
            and rel >= policy.min_rel_deviation
            and abs(dev) >= policy.min_abs_deviation
            and side_ok
        )
        if not anomalous:
            baseline.update(value)
            return None
        return AnomalyVerdict(
            metric=policy.metric,
            direction="high" if dev > 0 else "low",
            value=value,
            baseline_mean=baseline.mean,
            baseline_stddev=stddev,
            z_score=abs(z),
        )

def format_bps(value: float) -> str:
    """Human-readable throughput: 546_200_000 → '546.2 Mbps'."""
    for unit, scale in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value:.0f} bps"

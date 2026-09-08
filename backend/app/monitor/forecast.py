"""Metric forecasting: linear trend projection + capacity-risk verdict.

Anomaly detection (app.monitor.anomaly) reacts to what already happened;
forecasting looks ahead. A least-squares trend over a metric's recent window
is extrapolated over a horizon, with a confidence band from the residuals —
and when the projected upper band crosses a capacity line inside the horizon,
the series is flagged as a capacity risk *before* saturation, not after.

A slope-significance gate (|slope| > 2 standard errors) keeps noisy windows
from inventing trends: no significant slope, no capacity risk.

The module is pure — no I/O — so policies and behaviour are unit-testable.
Simplicity is deliberate: over a monitor window of minutes-to-hours a linear
fit is the honest baseline, and its failure mode (a flat or noisy trend) is
"no risk", not a false alarm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class ForecastPoint:
    """One projected sample with its confidence band."""

    timestamp: datetime
    value: float
    lower: float
    upper: float


@dataclass(frozen=True)
class ForecastVerdict:
    """Trend summary of one series, plus the capacity-risk call.

    ``risk`` is True when the projected value's upper confidence band crosses
    ``capacity`` within the horizon; ``eta_seconds`` estimates when the *mean*
    projection reaches capacity (None when the trend never gets there — or is
    flat/falling, which is the good case).
    """

    metric: str
    samples: int
    last_value: float
    slope_per_hour: float
    projected: float  # mean projection at the horizon end
    upper_band: float  # projected + z*residual stddev at the horizon end
    capacity: float
    risk: bool
    eta_seconds: float | None = None


def least_squares(
    ts: list[float], ys: list[float]
) -> tuple[float, float, float, float]:
    """Fit y = slope*t + b by ordinary least squares.

    Returns ``(slope, intercept, residual_stddev, slope_std_error)``; the
    residual stddev is the natural width of the confidence band, and the
    slope standard error lets callers tell a real trend from noise.
    Degenerate input (fewer than 2 points, or zero variance in t) yields
    slope 0.
    """
    n = len(ts)
    if n < 2 or n != len(ys):
        return 0.0, ys[0] if ys else 0.0, 0.0, 0.0
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    s_tt = sum((t - mean_t) ** 2 for t in ts)
    if s_tt <= 0.0:
        return 0.0, mean_y, 0.0, 0.0
    s_ty = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys, strict=True))
    slope = s_ty / s_tt
    intercept = mean_y - slope * mean_t
    residuals = [y - (slope * t + intercept) for t, y in zip(ts, ys, strict=True)]
    stddev = math.sqrt(sum(r * r for r in residuals) / max(n - 2, 1))
    slope_se = stddev / math.sqrt(s_tt)
    return slope, intercept, stddev, slope_se


def saturation_eta(
    slope_per_second: float, last_value: float, capacity: float
) -> float | None:
    """Seconds until a linear trend reaches ``capacity`` (None if never)."""
    if slope_per_second <= 0.0 or last_value >= capacity:
        return 0.0 if last_value >= capacity else None
    return (capacity - last_value) / slope_per_second


def forecast_series(
    samples: list[tuple[datetime, float]],
    *,
    metric: str,
    capacity: float,
    horizon: timedelta,
    step: timedelta = timedelta(minutes=10),
    band_z: float = 2.0,
    max_points: int = 64,
) -> tuple[list[ForecastPoint], ForecastVerdict | None]:
    """Project a metric series linearly over ``horizon``.

    ``samples`` are (timestamp, value) in chronological order. Projection
    points are emitted every ``step`` (capped at ``max_points``); the band is
    the fit ±``band_z`` residual stddevs, floors at 0. Returns the verdict
    (and True ``risk``) only when the window has enough samples to mean
    anything — fewer than 8 points yields no verdict at all.

    Timestamps in ``samples`` must share one timezone; naive datetimes are
    treated as UTC.
    """
    if len(samples) < 8:
        return [], None

    def _epoch(dt: datetime) -> float:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()

    ts = [_epoch(t) for t, _ in samples]
    ys = [float(v) for _, v in samples]
    slope, intercept, stddev, slope_se = least_squares(ts, ys)

    t0, t_end = ts[-1], ts[-1] + horizon.total_seconds()
    n_steps = max(1, min(max_points, int(horizon / step)))
    step_s = (t_end - t0) / n_steps

    points: list[ForecastPoint] = []
    for i in range(1, n_steps + 1):
        t = t0 + i * step_s
        value = max(slope * t + intercept, 0.0)
        band = band_z * stddev
        points.append(
            ForecastPoint(
                timestamp=datetime.fromtimestamp(t, tz=UTC),
                value=value,
                lower=max(value - band, 0.0),
                upper=value + band,
            )
        )

    last_value = ys[-1]
    projected = max(slope * t_end + intercept, 0.0)
    upper = projected + band_z * stddev
    # trend significance gate: a slope within 2 standard errors of zero is
    # noise, and noisy slopes must never read as "heading to saturation"
    significant = abs(slope) > 2.0 * slope_se
    eta = saturation_eta(slope if significant else 0.0, last_value, capacity)
    risk = (
        significant
        and upper >= capacity
        and eta is not None
        and eta <= horizon.total_seconds()
    )
    verdict = ForecastVerdict(
        metric=metric,
        samples=len(samples),
        last_value=last_value,
        slope_per_hour=slope * 3600.0,
        projected=projected,
        upper_band=upper,
        capacity=capacity,
        risk=risk,
        eta_seconds=eta,
    )
    return points, verdict


def format_eta(seconds: float) -> str:
    """Human-readable time-to-capacity: 190_080 → '2.2 days'."""
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5_400:
        return f"{seconds / 60:.0f} min"
    if seconds < 129_600:
        return f"{seconds / 3_600:.1f} h"
    return f"{seconds / 86_400:.1f} days"

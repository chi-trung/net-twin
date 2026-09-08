"""Tests for the forecast engine and capacity-risk alerting.

Pure-forecast tests exercise the least-squares projection, the confidence
band, and the capacity-risk verdict. The engine test stubs the DB series
loader to verify that a rising series raises a capacity_risk alert and a
falling one stays quiet. Alert tests use the shared in-memory db_session.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.monitor.alerts import AlertEngine, Observation, default_rules
from app.monitor.forecast import ForecastVerdict, forecast_series, format_eta, saturation_eta
from app.monitor.forecast_engine import ForecastEngine, SeriesRef

# ── pure forecast ──────────────────────────────────────────────────


def _ramp(n: int, start: float, step: float, base: datetime) -> list[tuple[datetime, float]]:
    """A linear series moving by `step` per 5-minute sample."""
    return [(base + timedelta(minutes=5 * i), start + step * i) for i in range(n)]


def test_forecast_rising_series_projects_growth():
    base = datetime.now(UTC)
    samples = _ramp(20, start=100.0, step=10.0, base=base)  # 100 → 290 Mbps
    points, verdict = forecast_series(
        samples, metric="traffic", capacity=1_000.0, horizon=timedelta(hours=1)
    )
    assert verdict is not None
    # linear fit over a perfect line ≈ exact: last 290, slope 10/5min = 120/h
    assert verdict.last_value == pytest.approx(290.0, abs=1.0)
    assert verdict.slope_per_hour == pytest.approx(120.0, rel=0.05)
    # 1 h later the projection continues the ramp to ~410
    assert verdict.projected == pytest.approx(410.0, abs=15.0)
    # band floors are clamped at zero
    assert all(p.lower >= 0.0 for p in points)
    # far from capacity → no risk
    assert verdict.risk is False


def test_forecast_band_widens_with_residuals():
    base = datetime.now(UTC)
    # linear growth + a deterministic wiggle → nonzero residual stddev
    samples = [
        (t, v + (5.0 if i % 2 else -5.0)) for i, (t, v) in enumerate(_ramp(20, 100.0, 10.0, base))
    ]
    points, verdict = forecast_series(
        samples, metric="traffic", capacity=1_000.0, horizon=timedelta(hours=1)
    )
    assert verdict is not None
    assert points[-1].upper > points[-1].value
    assert verdict.upper_band == pytest.approx(points[-1].upper)


def test_forecast_flags_capacity_risk_inside_horizon():
    base = datetime.now(UTC)
    # rising ~240 Mbps/h from 520 → 980 Mbps, a 1 Gbps line looms in ~1 h
    samples = _ramp(20, start=520.0, step=20.0, base=base)
    _, verdict = forecast_series(
        samples, metric="traffic", capacity=1_000.0, horizon=timedelta(hours=1)
    )
    assert verdict is not None and verdict.risk is True
    assert verdict.eta_seconds is not None
    # last = 520 + 19*20 = 900; ETA = (1000−900) ÷ (240/3600 s⁻¹) = 1500 s
    assert 1_000 < verdict.eta_seconds < 2_000


def test_forecast_flat_series_has_no_risk():
    base = datetime.now(UTC)
    samples = _ramp(20, start=500.0, step=0.0, base=base)
    _, verdict = forecast_series(
        samples, metric="traffic", capacity=1_000.0, horizon=timedelta(hours=1)
    )
    assert verdict is not None
    assert verdict.risk is False
    assert verdict.eta_seconds is None  # a flat trend never saturates


def test_forecast_needs_minimum_samples():
    base = datetime.now(UTC)
    samples = _ramp(7, start=100.0, step=10.0, base=base)  # below the 8 floor
    points, verdict = forecast_series(
        samples, metric="traffic", capacity=1_000.0, horizon=timedelta(hours=1)
    )
    assert points == [] and verdict is None


def test_saturation_eta_edges():
    assert saturation_eta(slope_per_second=0.0, last_value=10.0, capacity=100.0) is None
    assert saturation_eta(slope_per_second=-1.0, last_value=10.0, capacity=100.0) is None
    assert saturation_eta(slope_per_second=1.0, last_value=100.0, capacity=100.0) == 0.0
    assert saturation_eta(slope_per_second=1.0, last_value=50.0, capacity=100.0) == 50.0


def test_format_eta_units():
    assert format_eta(30) == "30s"
    assert format_eta(600) == "10 min"
    assert format_eta(7_200) == "2.0 h"
    assert format_eta(200_000) == "2.3 days"


# ── capacity_risk alert rule ───────────────────────────────────────


def _risk_verdict(risk: bool = True) -> ForecastVerdict:
    return ForecastVerdict(
        metric="traffic",
        samples=30,
        last_value=800.0,
        slope_per_hour=400.0,
        projected=950.0,
        upper_band=1_050.0,
        capacity=1_000.0,
        risk=risk,
        eta_seconds=900.0 if risk else None,
    )


def _risk_obs(risk: bool = True) -> Observation:
    return Observation(
        device_id=1,
        device_name="dist-sw-01",
        anomaly_series=("traffic", 3, "if_in_bps"),
        capacity_risk=_risk_verdict(risk),
    )


@pytest.mark.asyncio
async def test_capacity_risk_rule_raises_and_message_has_eta(db_session):
    engine = AlertEngine(default_rules(200.0, 10.0))
    raised = await engine.evaluate(db_session, _risk_obs())
    await db_session.flush()
    assert len(raised) == 1
    alert = raised[0]
    assert alert.rule == "capacity_risk"
    assert alert.message is not None and "ETA" in alert.message
    assert "dist-sw-01" in alert.message
    # sustained risk → no duplicate raise
    assert await engine.evaluate(db_session, _risk_obs()) == []


@pytest.mark.asyncio
async def test_capacity_risk_clears_when_trend_bends_away(db_session):
    engine = AlertEngine(default_rules(200.0, 10.0))
    raised = await engine.evaluate(db_session, _risk_obs())
    assert len(raised) == 1

    calm = _risk_verdict(risk=False)
    obs = Observation(
        device_id=1,
        device_name="dist-sw-01",
        anomaly_series=("traffic", 3, "if_in_bps"),
        capacity_risk=calm,
    )
    assert await engine.evaluate(db_session, obs) == []
    await db_session.flush()
    assert ("traffic", 3, "if_in_bps") not in engine._firing  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_plain_health_observation_never_touches_capacity_alert(db_session):
    engine = AlertEngine(default_rules(200.0, 10.0))
    await engine.evaluate(db_session, _risk_obs())
    # a plain observation (no series key) must not raise/clear series alerts
    plain = Observation(device_id=1, device_name="dist-sw-01", health="up")
    assert await engine.evaluate(db_session, plain) == []
    assert (1, "capacity_risk", ("traffic", 3, "if_in_bps")) in engine._firing  # type: ignore[attr-defined]


# ── engine sweep ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forecast_engine_runs_series_and_alerts(monkeypatch):
    base = datetime.now(UTC)

    class FakeDB:
        """Alert rows are appended here instead of a real session."""

        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, obj) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            for i, obj in enumerate(self.added, start=1):
                obj.id = i

    engine = ForecastEngine(alerts=AlertEngine(default_rules(200.0, 10.0)))
    rising = _ramp(20, start=520.0, step=20.0, base=base)  # 520 → 900: risk in ~1 h
    falling = _ramp(20, start=900.0, step=-45.0, base=base)  # 900 → 45: calming down
    refs = [
        (
            SeriesRef(
                device_id=1, device_name="core", interface_id=3,
                metric_name="if_in_bps", capacity=1_000.0,
            ),
            rising,
        ),
        (
            SeriesRef(
                device_id=2, device_name="edge", interface_id=7,
                metric_name="if_in_bps", capacity=1_000.0,
            ),
            falling,
        ),
    ]

    async def fake_load(db, window, capacity):
        return refs

    monkeypatch.setattr(engine, "_load_series", fake_load)
    risks = await engine.run_once(FakeDB())
    # one series at risk; the falling one evaluates clean
    assert risks == 1
    firing = [k for k in engine.alerts._firing]  # type: ignore[attr-defined]
    assert firing == [(1, "capacity_risk", ("traffic", 3, "if_in_bps"))]

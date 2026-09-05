"""Tests for statistical anomaly detection and its alert wiring."""

from app.db.models import Device, DeviceType, HealthState
from app.monitor.alerts import AlertEngine, Observation, default_rules
from app.monitor.anomaly import MetricAnomalyDetector, format_bps


def _detector(**overrides) -> MetricAnomalyDetector:
    defaults = dict(min_samples=5, z_threshold=2.5)
    defaults.update(overrides)
    return MetricAnomalyDetector(**defaults)


def test_format_bps_units():
    assert format_bps(546_200_000) == "546.2 Mbps"
    assert format_bps(2.5e9) == "2.5 Gbps"
    assert format_bps(42_000) == "42.0 Kbps"
    assert format_bps(700) == "700 bps"


def test_warmup_phase_produces_no_verdicts():
    det = _detector()
    for i in range(5):
        assert det.observe(("k",), "traffic_bps", 100.0 + i) is None


def test_normal_fluctuation_is_not_anomalous():
    det = _detector()
    for v in (100, 102, 98, 101, 99, 100, 103, 97):
        det.observe(("k",), "traffic_bps", float(v))
    # well within the learned baseline
    assert det.observe(("k",), "traffic_bps", 101.0) is None


def test_traffic_spike_and_drop_detected():
    det = _detector()
    for _ in range(8):
        det.observe(("k",), "traffic_bps", 100.0)
    spike = det.observe(("k",), "traffic_bps", 400.0)  # +300%, z huge
    assert spike is not None and spike.direction == "high" and spike.metric == "traffic"
    # baseline absorbed nothing from the spike → drop still flags
    drop = det.observe(("k",), "traffic_bps", 0.0)
    assert drop is not None and drop.direction == "low"


def test_series_are_independent():
    det = _detector()
    for _ in range(8):
        det.observe(("a",), "traffic_bps", 100.0)
        det.observe(("b",), "traffic_bps", 1_000.0)
    # a's spike must not be normalized by b's bigger numbers
    v = det.observe(("a",), "traffic_bps", 500.0)
    assert v is not None and v.baseline_mean == 100.0


def test_latency_policy_ignores_low_side():
    det = _detector()
    for _ in range(8):
        det.observe(("k",), "latency_ms", 2.0)
    # latency going *down* is good news — no verdict even at extreme z
    assert det.observe(("k",), "latency_ms", 0.01) is None
    assert det.observe(("k",), "latency_ms", 50.0) is not None  # spike flags


def test_relative_guard_blocks_microscopic_wiggles():
    det = _detector()
    for _ in range(8):
        det.observe(("k",), "latency_ms", 2.0)
    # z is enormous (tiny stddev) but the relative guard (50%) blocks it
    assert det.observe(("k",), "latency_ms", 2.4) is None


def test_unknown_metric_name_is_ignored():
    det = _detector()
    for _ in range(8):
        det.observe(("k",), "mystery_metric", 1.0)
    assert det.observe(("k",), "mystery_metric", 9999.0) is None


def test_sustained_anomaly_keeps_firing():
    """Anomalous samples freeze the baseline, so a permanent shift never
    becomes the new normal."""
    det = _detector()
    for _ in range(8):
        det.observe(("k",), "traffic_bps", 100.0)
    assert det.observe(("k",), "traffic_bps", 0.0) is not None
    # still anomalous many samples later — the drop never "normalizes"
    for _ in range(20):
        assert det.observe(("k",), "traffic_bps", 0.0) is not None


# ── alert-engine wiring ────────────────────────────────────────────

def _engine() -> AlertEngine:
    return AlertEngine(default_rules(latency_threshold_ms=200, loss_threshold_pct=10))


async def test_plain_observation_does_not_touch_anomaly_rules(db_session):
    engine = _engine()
    dev = Device(name="r1", ip_address="10.9.9.1", device_type=DeviceType.ROUTER,
                 health=HealthState.UP)
    db_session.add(dev)
    await db_session.commit()

    # a normal observation must raise nothing (including anomaly rules)
    raised = await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", health="up", latency_ms=5, packet_loss_pct=0))
    assert raised == []

    # a traffic anomaly raises exactly one warning
    from app.monitor.anomaly import AnomalyVerdict

    verdict = AnomalyVerdict(metric="traffic", direction="low", value=0.0,
                             baseline_mean=5e8, baseline_stddev=2e7, z_score=25.0)
    raised = await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", anomaly=verdict,
        anomaly_series=("traffic", 1, 11, "in")))
    assert [a.rule for a in raised] == ["traffic_anomaly"]
    await db_session.commit()

    # same series still anomalous → no duplicate
    assert await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", anomaly=verdict,
        anomaly_series=("traffic", 1, 11, "in"))) == []


async def test_normal_sample_clears_series_anomaly(db_session):
    engine = _engine()
    dev = Device(name="r1", ip_address="10.9.9.2", device_type=DeviceType.ROUTER,
                 health=HealthState.UP)
    db_session.add(dev)
    await db_session.commit()

    from app.monitor.anomaly import AnomalyVerdict

    series = ("traffic", 2, 21, "out")
    verdict = AnomalyVerdict(metric="traffic", direction="high", value=9e8,
                             baseline_mean=1e8, baseline_stddev=5e6, z_score=160.0)
    raised = await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", anomaly=verdict, anomaly_series=series))
    assert [a.rule for a in raised] == ["traffic_anomaly"]

    # a plain (non-scoped) health observation must NOT clear the series alert
    await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", health="up", latency_ms=5, packet_loss_pct=0))
    from sqlalchemy import select
    from app.db.models import Alert, AlertStatus

    alerts = (await db_session.scalars(select(Alert))).all()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.ACTIVE

    # recovery: an in-policy sample of the same series (no verdict) clears it
    await engine.evaluate(db_session, Observation(
        device_id=dev.id, device_name="r1", anomaly=None, anomaly_series=series))
    alerts = (await db_session.scalars(select(Alert))).all()
    assert alerts[0].status == AlertStatus.CLEARED
    assert alerts[0].cleared_at is not None

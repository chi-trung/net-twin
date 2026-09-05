"""Tests for the pure root-cause analysis engine."""

from app.analysis.rca import AlertFact, DeviceFacts, analyze


def _facts(n, health="up", dtype="switch") -> DeviceFacts:
    return DeviceFacts(id=n, name=f"dev-{n}", health=health, device_type=dtype)


# classic campus tree: core(1) → dist(2,3) → access(4..7) → hosts(8..15)
LINKS = [
    (1, 2), (1, 3),      # core uplinks
    (2, 4), (2, 5),      # dist-1 → access
    (3, 6), (3, 7),      # dist-2 → access
    (4, 8), (4, 9),      # access → hosts
    (5, 10), (5, 11),
    (6, 12), (6, 13),
    (7, 14), (7, 15),
]
ROOT = 1


def _devices(down=()) -> dict[int, DeviceFacts]:
    return {n: _facts(n, "down" if n in down else "up") for n in range(1, 16)}


def test_symptom_itself_is_top_when_no_other_evidence():
    """host-8 down, upstream healthy, no alerts → the host itself is the
    likeliest cause (a healthy upstream must not be blamed)."""
    devices = _devices(down={8})
    hyps = analyze(8, devices, LINKS, ROOT, [])
    assert hyps, "no hypotheses produced"
    assert hyps[0].device.id == 8


def test_upstream_failure_is_top_hypothesis():
    """host-8 down AND its only uplink acc-4 down → acc-4 explains it."""
    devices = _devices(down={4, 8})
    hyps = analyze(8, devices, LINKS, ROOT, [])
    assert hyps, "no hypotheses produced"
    # acc-4: disconnects host-8 (4.0) — beats "host-8 itself down" (1.0)
    assert hyps[0].device.id == 4
    assert hyps[0].score >= 4.0


def test_alerts_boost_their_device():
    devices = _devices(down={8})
    alerts = [
        AlertFact(id=1, device_id=2, rule="node_down", severity="critical",
                  message="dev-2 is DOWN")
    ]
    hyps = analyze(8, devices, LINKS, ROOT, alerts)
    top = hyps[0]
    # dist-2: alert 3.0 + disconnect 4.0 = 7.0 beats acc-4's 4.0
    assert top.device.id == 2 and top.score >= 7.0
    assert top.evidence_alerts and top.evidence_alerts[0].device_id == 2


def test_redundant_uplink_down_scores_lower():
    """With a redundant link, a down upstream device is a weaker cause."""
    # add redundancy: 5 → 9 (host-9 dual-homed)
    links = LINKS + [(5, 9)]
    devices = _devices(down={5, 9})
    hyps = analyze(9, devices, links, ROOT, [])
    # 9 itself down (1.0); 5 is down but 9 still reaches root via 4 (2.0)
    by_id = {h.device.id: h for h in hyps}
    assert by_id[5].score == 2.0
    assert "alternate path" in by_id[5].reasons[0]


def test_spof_common_cause_for_broad_outage():
    """Core down isolates everything — the fragility rule must name it."""
    devices = _devices(down={1, 2, 3, 4, 8, 12})
    hyps = analyze(8, devices, LINKS, ROOT, [])
    assert hyps[0].device.id == 1  # core named as common cause
    assert any("common cause" in r for r in hyps[0].reasons)


def test_no_evidence_returns_empty():
    """A healthy device with no alerts → no hypotheses, no guessing."""
    hyps = analyze(8, _devices(), LINKS, ROOT, [])
    assert hyps == []


def test_unknown_root_returns_empty():
    hyps = analyze(8, _devices(down={8}), LINKS, None, [])
    assert hyps == []


def test_symptom_healthy_with_down_upstream():
    """A still-up device can be a symptom too: its upstream is down."""
    devices = _devices(down={4})
    hyps = analyze(8, devices, LINKS, ROOT, [])
    assert hyps[0].device.id == 4
    assert hyps[0].score >= 4.0

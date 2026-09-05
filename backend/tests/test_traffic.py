"""Tests for the simulated traffic model."""

from app.db.models import DeviceType
from app.monitor.traffic import link_base_bps, traffic_bps


def test_base_load_follows_role_pair():
    core_uplink = link_base_bps(DeviceType.ROUTER, DeviceType.SWITCH, link_key=1)
    dist_link = link_base_bps(DeviceType.SWITCH, DeviceType.SWITCH, link_key=1)
    host_link = link_base_bps(DeviceType.SWITCH, DeviceType.HOST, link_key=1)
    assert core_uplink > dist_link > host_link


def test_base_load_is_deterministic_and_varies_per_link():
    a = link_base_bps(DeviceType.SWITCH, DeviceType.HOST, link_key=7)
    assert a == link_base_bps(DeviceType.HOST, DeviceType.SWITCH, link_key=7)  # order-free
    b = link_base_bps(DeviceType.SWITCH, DeviceType.HOST, link_key=8)
    assert a != b  # per-link jitter


def test_traffic_bps_is_deterministic_per_second():
    v1 = traffic_bps(100.0, link_key=1, ts_seconds=1_000_000)
    v2 = traffic_bps(100.0, link_key=1, ts_seconds=1_000_000)
    assert v1 == v2
    # same 5s bucket → same noise, slight diurnal change only
    v3 = traffic_bps(100.0, link_key=1, ts_seconds=1_000_002)
    assert abs(v3 - v1) < 5


def test_traffic_directions_differ():
    inp = traffic_bps(100.0, link_key=3, ts_seconds=1_000_000, direction="in")
    out = traffic_bps(100.0, link_key=3, ts_seconds=1_000_000, direction="out")
    assert out > inp  # out gain


def test_traffic_stays_positive_and_bounded():
    for ts in range(0, 86_400, 900):
        v = traffic_bps(1_000.0, link_key=2, ts_seconds=ts)
        assert 0 < v < 2_000  # never zero, never wildly above base

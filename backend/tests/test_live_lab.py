"""Tests for live-source discovery against SNMP-speaking devices.

The snmpsim agents are not available in unit tests; these exercise the
sweep-merge and probe-fallback logic with stubbed collectors, which is
where the live path's correctness lives.
"""

from types import SimpleNamespace

import pytest

from app.discovery import sweeper
from app.discovery.engine import LiveSource
from app.monitor.probes import PingOrSnmpProbe, ProbeResult, SnmpProbe


# ── snmp sweep ─────────────────────────────────────────────────────


async def test_snmp_sweep_subnet_reports_answering_ips(monkeypatch):
    calls: list[str] = []

    class FakeCollector:
        def __init__(self, *a, **k):
            pass

        async def probe(self, ip: str):
            calls.append(ip)
            return "core-rtr-01" if ip == "10.250.0.2" else None

    # snmp_sweep_subnet imports SnmpCollector lazily from app.discovery.snmp
    monkeypatch.setattr(sweeper, "iter_hosts", lambda cidr: ["10.250.0.1", "10.250.0.2"])
    monkeypatch.setattr("app.discovery.snmp.SnmpCollector", FakeCollector)

    found = await sweeper.snmp_sweep_subnet("10.250.0.0/30")
    assert found == ["10.250.0.2"]
    assert calls == ["10.250.0.1", "10.250.0.2"]


async def test_snmp_sweep_subnet_survives_collector_errors(monkeypatch):
    class FakeCollector:
        def __init__(self, *a, **k):
            pass

        async def probe(self, ip: str):
            raise RuntimeError("socket exploded")

    monkeypatch.setattr(sweeper, "iter_hosts", lambda cidr: ["10.250.0.1"])
    monkeypatch.setattr("app.discovery.snmp.SnmpCollector", FakeCollector)

    assert await sweeper.snmp_sweep_subnet("10.250.0.0/30") == []


# ── LiveSource merge of ICMP/TCP + SNMP findings ───────────────────


def _make_source(monkeypatch: pytest.MonkeyPatch, icmp_ips: list[str], snmp_ips: list[str]):
    """A LiveSource with the network stubbed out: ICMP sweep → icmp_ips,
    SNMP sweep → snmp_ips, enrichment returns a named device per IP."""

    class FakeCollector:
        def __init__(self, *a, **k):
            pass

        async def collect(self, ip: str):
            return SimpleNamespace(
                ip_address=ip,
                name=f"dev-{ip}",
                device_type="switch",
                mac_address=None,
                sys_description="lab device",
                interfaces=[],
            )

        async def collect_arp(self, ip: str):
            return {}

        async def collect_lldp(self, ip: str):
            return []

        async def collect_cdp(self, ip: str):
            return []

    async def fake_sweep(cidr, prefer_scapy=True):
        return [(ip, f"aa:00:00:00:00:{i:02x}") for i, ip in enumerate(icmp_ips)]

    async def fake_snmp_sweep(*a, **k):
        return list(snmp_ips)

    # engine imports these names at module top level — patch them there
    import app.discovery.engine as engine_mod

    monkeypatch.setattr(engine_mod, "sweep_subnet", fake_sweep)
    monkeypatch.setattr(engine_mod, "snmp_sweep_subnet", fake_snmp_sweep)
    monkeypatch.setattr("app.discovery.snmp.SnmpCollector", FakeCollector)
    return LiveSource(SimpleNamespace(discovery_subnet="10.250.0.0/24", snmp_community="public",
                                      snmp_timeout_seconds=0.1, snmp_retries=0))


async def test_live_source_merges_snmp_only_devices(monkeypatch):
    """A device invisible to ICMP but answering SNMP joins the inventory."""
    source = _make_source(monkeypatch, icmp_ips=["10.250.0.1"], snmp_ips=["10.250.0.2"])
    result = await source.discover()
    ips = {d.ip_address for d in result.devices}
    assert ips == {"10.250.0.1", "10.250.0.2"}
    # the ICMP-only device keeps its sweep MAC; the SNMP-only one has none
    by_ip = {d.ip_address: d for d in result.devices}
    assert by_ip["10.250.0.1"].mac_address == "aa:00:00:00:00:00"
    assert by_ip["10.250.0.2"].mac_address is None


async def test_live_source_dedupes_overlapping_sweeps(monkeypatch):
    """An IP found by both sweeps is enriched once, not duplicated."""
    source = _make_source(monkeypatch, icmp_ips=["10.250.0.1"], snmp_ips=["10.250.0.1"])
    result = await source.discover()
    assert len(result.devices) == 1
    assert result.devices[0].mac_address == "aa:00:00:00:00:00"


# ── SNMP reachability probing ──────────────────────────────────────


async def test_snmp_probe_down_when_no_answer(monkeypatch):
    probe = SnmpProbe(timeout=0.1, retries=0)

    async def fail(*a, **k):
        return None

    monkeypatch.setattr("app.discovery.snmp.SnmpCollector.probe", fail)
    result = await probe.probe("10.250.0.99")
    assert result.reachable is False
    assert result.packet_loss_pct == 100.0


async def test_ping_or_snmp_uses_ping_when_reachable(monkeypatch):
    probe = PingOrSnmpProbe()

    async def good_ping(ip, count=4):
        return ProbeResult(reachable=True, latency_ms=1.5, packet_loss_pct=0.0)

    async def explode(*a, **k):
        raise AssertionError("SNMP must not be called when ping succeeds")

    probe.ping = SimpleNamespace(probe=good_ping)
    probe.snmp = SimpleNamespace(probe=explode)
    result = await probe.probe("10.250.0.1")
    assert result.reachable is True
    assert result.latency_ms == 1.5


async def test_ping_or_snmp_falls_back_to_snmp(monkeypatch):
    probe = PingOrSnmpProbe()

    async def dead_ping(ip, count=4):
        return ProbeResult(reachable=False, latency_ms=None, packet_loss_pct=100.0)

    async def live_snmp(ip, count=4):
        return ProbeResult(reachable=True, latency_ms=3.0, packet_loss_pct=0.0)

    probe.ping = SimpleNamespace(probe=dead_ping)
    probe.snmp = SimpleNamespace(probe=live_snmp)
    result = await probe.probe("10.250.0.2")
    assert result.reachable is True
    assert result.latency_ms == 3.0

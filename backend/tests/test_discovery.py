"""Unit tests for the discovery engine (pure logic + builder integration)."""

from app.db.models import DeviceType, HealthState
from app.discovery.builder import build_twin
from app.discovery.links import (
    PROTOCOL_RANK,
    dedupe_links,
    links_from_arp,
    links_from_cdp_neighbors,
    links_from_lldp_neighbors,
    protocol_rank,
)
from app.discovery.models import DiscoveredDevice, DiscoveredInterface, DiscoveredLink
from app.discovery.simulator import simulate_topology
from app.discovery.snmp import _fmt_mac, build_interfaces, classify_device
from app.discovery.sweeper import iter_hosts, parse_arp_table

# ── sweeper helpers ────────────────────────────────────────────────

def test_iter_hosts_excludes_network_and_broadcast():
    hosts = iter_hosts("10.0.0.0/30")
    assert hosts == ["10.0.0.1", "10.0.0.2"]


def test_parse_arp_table_windows_style():
    out = (
        "Interface: 10.0.0.5 --- 0x2\n"
        "  Internet Address      Physical Address      Type\n"
        "  10.0.0.1              aa-bb-cc-dd-ee-01     dynamic\n"
        "  224.0.0.251           01-00-5e-00-00-fb     static\n"
    )
    table = parse_arp_table(out)
    assert table == {"10.0.0.1": "aa-bb-cc-dd-ee-01"}


# ── snmp classification ────────────────────────────────────────────

def test_classify_device_by_sysdescr():
    assert classify_device("Cisco IOS Software, Catalyst L3 Switch", []) == DeviceType.SWITCH
    assert classify_device("Juniper Networks, Inc. mx240", []) == DeviceType.ROUTER
    assert classify_device("pfSense", []) == DeviceType.FIREWALL
    assert classify_device("Linux box 6.1", []) == DeviceType.HOST


def test_classify_device_by_interface_count():
    many_eth = [6] * 12
    assert classify_device(None, many_eth) == DeviceType.SWITCH
    assert classify_device(None, [6]) == DeviceType.UNKNOWN


def test_fmt_mac_variants():
    assert _fmt_mac(b"\x00\x11\x22\x33\x44\x55") == "00:11:22:33:44:55"
    assert _fmt_mac("001122334455") == "00:11:22:33:44:55"
    assert _fmt_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert _fmt_mac(None) is None


def test_build_interfaces_maps_columns():
    rows = {
        1: {"descr": "Gi0/1", "type": 6, "mac": b"\x0a\x0b\x0c\x0d\x0e\x0f", "oper": 1,
            "speed": 1_000_000_000},
        2: {"descr": "Gi0/2", "type": 6, "oper": 2},
    }
    ifaces = build_interfaces(rows)
    assert [i.name for i in ifaces] == ["Gi0/1", "Gi0/2"]
    assert ifaces[0].oper_status == "up"
    assert ifaces[1].oper_status == "down"
    assert ifaces[0].speed_mbps == 1000
    assert ifaces[0].mac_address == "0a:0b:0c:0d:0e:0f"


# ── link inference ─────────────────────────────────────────────────

def _dev(ip: str, name: str, macs: list[str] | None = None) -> DiscoveredDevice:
    ifaces = [
        DiscoveredInterface(if_index=i + 1, name=f"eth{i}", mac_address=m)
        for i, m in enumerate(macs or [])
    ]
    return DiscoveredDevice(ip_address=ip, name=name, interfaces=ifaces)


def test_links_from_lldp_matches_by_ip_and_name():
    a = _dev("10.0.0.1", "core-rtr")
    b = _dev("10.0.0.2", "dist-sw")
    by_ip = {a.ip_address: a, b.ip_address: b}
    neighbors = {
        "10.0.0.1": [
            {"remote_mgmt_ip": "10.0.0.2", "local_if_name": "Gi0/1"},
            {"remote_system_name": "unknown-box"},  # not in inventory → skipped
        ]
    }
    links = links_from_lldp_neighbors(neighbors, by_ip)
    assert len(links) == 1
    assert links[0].protocol == "lldp"
    assert {links[0].source_ip, links[0].target_ip} == {"10.0.0.1", "10.0.0.2"}


def test_links_from_arp_correlates_macs():
    sw = _dev("10.0.0.2", "sw-01", macs=["aa:00:00:00:00:02"])
    host = _dev("10.0.0.50", "host-01", macs=["aa:00:00:00:00:50"])
    by_ip = {sw.ip_address: sw, host.ip_address: host}
    arp_tables = {"10.0.0.2": {"10.0.0.50": "AA:00:00:00:00:50"}}
    links = links_from_arp(arp_tables, by_ip)
    assert len(links) == 1
    assert links[0].protocol == "arp"
    assert links[0].target_if_name == "eth0"


def test_dedupe_links_prefers_lldp_over_arp():
    l1 = DiscoveredLink(source_ip="10.0.0.1", target_ip="10.0.0.2", protocol="arp")
    l2 = DiscoveredLink(source_ip="10.0.0.2", target_ip="10.0.0.1", protocol="lldp")
    out = dedupe_links([l1, l2])
    assert len(out) == 1
    assert out[0].protocol == "lldp"


# ── simulator ──────────────────────────────────────────────────────

def test_simulate_topology_is_deterministic_and_connected():
    r1 = simulate_topology(seed=42)
    r2 = simulate_topology(seed=42)
    assert [d.ip_address for d in r1.devices] == [d.ip_address for d in r2.devices]
    assert len(r1.devices) == 15  # 1 core + 2 dist + 4 acc + 8 hosts
    ips = {d.ip_address for d in r1.devices}
    for link in r1.links:
        assert link.source_ip in ips and link.target_ip in ips


async def test_simulator_source_hides_outaged_devices():
    from app.core.outages import get_outages
    from app.discovery.simulator import SimulatorSource

    full = await SimulatorSource(seed=7).discover()
    victim = full.devices[-1].ip_address
    get_outages().add(victim)
    try:
        hidden = await SimulatorSource(seed=7).discover()
        assert len(hidden.devices) == len(full.devices) - 1
        assert all(d.ip_address != victim for d in hidden.devices)
        assert all(victim not in (lnk.source_ip, lnk.target_ip) for lnk in hidden.links)
    finally:
        get_outages().remove(victim)


# ── builder integration (async, in-memory sqlite) ─────────────────

async def test_build_twin_creates_then_updates(db_session):
    result = simulate_topology(seed=1)
    report = await build_twin(db_session, result)
    assert report.devices_created == 15
    assert report.links_created > 0
    assert report.changed is True

    # second identical run: nothing created, nothing changed
    report2 = await build_twin(db_session, result)
    assert report2.devices_created == 0
    assert report2.devices_updated == 0
    assert report2.links_created == 0
    assert report2.changed is False


async def test_build_twin_marks_stale_devices_down(db_session):
    result = simulate_topology(seed=1)
    await build_twin(db_session, result)

    # drop one device from the next discovery round
    dropped = result.devices.pop()
    report = await build_twin(db_session, result)
    assert report.devices_staled == 1

    from sqlalchemy import select

    from app.db.models import Device

    dev = (
        await db_session.scalars(select(Device).where(Device.ip_address == dropped.ip_address))
    ).one()
    assert dev.health == HealthState.DOWN


async def test_build_twin_upgrades_link_protocol(db_session):
    from app.discovery.models import DiscoveryResult

    a = DiscoveredDevice(ip_address="10.9.0.1", name="a")
    b = DiscoveredDevice(ip_address="10.9.0.2", name="b")
    arp_link = DiscoveredLink(source_ip=a.ip_address, target_ip=b.ip_address, protocol="arp")
    await build_twin(db_session, DiscoveryResult(devices=[a, b], links=[arp_link]))

    lldp_link = DiscoveredLink(source_ip=b.ip_address, target_ip=a.ip_address, protocol="lldp")
    report = await build_twin(db_session, DiscoveryResult(devices=[a, b], links=[lldp_link]))
    assert report.links_updated == 1

    from sqlalchemy import select

    from app.db.models import Link

    link = (await db_session.scalars(select(Link))).one()
    assert link.protocol == "lldp"

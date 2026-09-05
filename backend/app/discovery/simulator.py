"""Simulated discovery source.

Generates a plausible campus-network topology so the whole pipeline (builder
→ twin store → API → UI) is demoable without any real devices. Enabled with
DISCOVERY_SOURCE=simulator (the default in .env.example).

The topology is deterministic per seed so repeated runs look stable, with a
little jitter to exercise change-detection paths.
"""

from __future__ import annotations

import random

from app.core.outages import get_outages
from app.db.models import DeviceType

from .models import DiscoveredDevice, DiscoveredInterface, DiscoveredLink, DiscoveryResult


def _iface(index: int, name: str, mac: str, speed: int = 1000) -> DiscoveredInterface:
    return DiscoveredInterface(
        if_index=index, name=name, mac_address=mac, oper_status="up", speed_mbps=speed
    )


def simulate_topology(seed: int | None = None) -> DiscoveryResult:
    """Return a fake campus network: 1 core router, 2 dist switches,
    4 access switches, and a handful of hosts.

    Link provenance mirrors real gear: core↔dist runs LLDP, dist↔access is
    CDP (Cisco-to-Cisco), access↔host correlates by ARP (hosts speak neither).
    """
    rng = random.Random(seed)
    devices: list[DiscoveredDevice] = []
    links: list[DiscoveredLink] = []

    def mac() -> str:
        return ":".join(f"{rng.randrange(16):02x}" for _ in range(6))

    core = DiscoveredDevice(
        ip_address="10.0.0.1",
        name="core-rtr-01",
        device_type=DeviceType.ROUTER,
        mac_address=mac(),
        sys_description="Simulated Cisco IOS Router",
        interfaces=[_iface(1, "Gi0/0/0", mac()), _iface(2, "Gi0/0/1", mac())],
    )
    devices.append(core)

    dist_switches = []
    core_if_seq = 0  # each core port faces one dist switch
    for i in (1, 2):
        sw = DiscoveredDevice(
            ip_address=f"10.0.{i}.1",
            name=f"dist-sw-0{i}",
            device_type=DeviceType.SWITCH,
            mac_address=mac(),
            sys_description="Simulated L3 Distribution Switch",
            interfaces=[_iface(1, "Gi1/0/24", mac(), 10000)],
        )
        devices.append(sw)
        dist_switches.append(sw)
        core_if_seq += 1
        links.append(
            DiscoveredLink(
                source_ip=core.ip_address,
                target_ip=sw.ip_address,
                source_if_name=f"Gi0/0/{core_if_seq}",
                target_if_name="Gi1/0/24",
                protocol="lldp",
            )
        )

    host_counter = 10
    access_idx = 0
    for dist in dist_switches:
        for _ in range(2):
            access_idx += 1
            # access VLANs 100+ so mgmt IPs never collide with dist (10.0.1/2.1)
            acc = DiscoveredDevice(
                ip_address=f"10.0.{100 + access_idx}.1",
                name=f"acc-sw-0{access_idx}",
                device_type=DeviceType.SWITCH,
                mac_address=mac(),
                sys_description="Simulated access switch",
                interfaces=[_iface(1, "Gi0/1", mac()), _iface(2, "Gi0/2", mac())],
            )
            devices.append(acc)
            links.append(
                DiscoveredLink(
                    source_ip=dist.ip_address,
                    target_ip=acc.ip_address,
                    source_if_name="Gi1/0/24",
                    target_if_name="Gi0/1",
                    protocol="cdp",
                )
            )
            for _ in range(2):
                host_counter += 1
                host = DiscoveredDevice(
                    ip_address=f"10.0.{access_idx}.{host_counter}",
                    name=f"host-{host_counter:03d}",
                    device_type=DeviceType.HOST,
                    mac_address=mac(),
                    sys_description="Simulated workstation",
                    interfaces=[_iface(1, "eth0", mac())],
                )
                devices.append(host)
                links.append(
                    DiscoveredLink(
                        source_ip=acc.ip_address,
                        target_ip=host.ip_address,
                        source_if_name="Gi0/2",
                        target_if_name="eth0",
                        protocol="arp",
                    )
                )

    return DiscoveryResult(devices=devices, links=links)


class SimulatorSource:
    """Discovery-source interface-compatible wrapper around simulate_topology.

    Devices with an active simulated outage are hidden from the result, so the
    builder's stale-detection path marks them DOWN exactly like a real device
    that stopped answering the sweep.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed

    async def discover(self) -> DiscoveryResult:
        result = simulate_topology(self._seed)
        outages = get_outages()
        if outages.list():
            down = outages.list()
            result.devices = [d for d in result.devices if d.ip_address not in down]
            result.links = [
                lnk
                for lnk in result.links
                if lnk.source_ip not in down and lnk.target_ip not in down
            ]
        return result

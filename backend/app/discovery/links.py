"""Link inference: turn neighbor data into graph edges.

Two strategies, in priority order:

1. **LLDP/CDP neighbor tables** — a switch port literally reports the chassis
   id / system name / management address on the far end. This is the gold
   standard when devices run LLDP (or CDP on Cisco).

2. **ARP + MAC correlation** — fallback for unmanaged gear: if device A's ARP
   table maps IP_x → MAC_m, and device B advertises MAC_m on one of its
   interfaces, then A—B are adjacent (B is A's next-hop / the switch port
   facing A). Aggregated across all devices this reconstructs the graph.

All functions here are pure (dicts in, DiscoveredLink list out) → unit tests
need no network.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from .models import DiscoveredDevice, DiscoveredLink

logger = logging.getLogger(__name__)

# Link provenance ranking — lower is stronger evidence. Shared by the dedupe
# pass here and by the builder's protocol-upgrade logic so both agree.
# "manual" outranks everything: an operator-pinned edge must never be
# superseded by automated discovery evidence.
PROTOCOL_RANK: dict[str, int] = {
    "manual": 0,
    "lldp": 1,  # 802.1AB neighbor tables — the gold standard
    "cdp": 2,  # Cisco Discovery Protocol — gold standard, Cisco-only
    "arp": 3,  # MAC correlation — solid, but no port evidence on one end
    "inferred": 4,  # weakest
}

_UNRANKED = len(PROTOCOL_RANK)


def protocol_rank(protocol: str) -> int:
    """Evidence strength of a link protocol; unknown protocols rank last."""
    return PROTOCOL_RANK.get(protocol, _UNRANKED)


def links_from_lldp_neighbors(
    neighbors: dict[str, list[dict]],
    devices_by_ip: dict[str, DiscoveredDevice],
) -> list[DiscoveredLink]:
    """Build links from per-device LLDP neighbor tables (protocol="lldp")."""
    return _links_from_neighbor_tables(neighbors, devices_by_ip, protocol="lldp")


def links_from_cdp_neighbors(
    neighbors: dict[str, list[dict]],
    devices_by_ip: dict[str, DiscoveredDevice],
) -> list[DiscoveredLink]:
    """Build links from per-device CDP neighbor tables (protocol="cdp").

    CDP records share the LLDP matcher's shape (the CDP-MIB exposes the same
    chassis-id / system-name / mgmt-address / port-id concepts), so the same
    matching rules apply — only the provenance tag differs.
    """
    return _links_from_neighbor_tables(neighbors, devices_by_ip, protocol="cdp")


def _links_from_neighbor_tables(
    neighbors: dict[str, list[dict]],
    devices_by_ip: dict[str, DiscoveredDevice],
    protocol: str,
) -> list[DiscoveredLink]:
    """Build links from per-device LLDP/CDP neighbor tables.

    `neighbors` maps device IP → list of neighbor records:
        {"remote_chassis_id": mac, "remote_system_name": str,
         "remote_mgmt_ip": str|None, "local_if_name": str}
    A neighbor is linked only when its far end is a known device (matched by
    management IP first, then system name).
    """
    links: list[DiscoveredLink] = []
    seen: set[frozenset[str]] = set()
    by_name = {d.name.lower(): d for d in devices_by_ip.values()}

    for local_ip, records in neighbors.items():
        if local_ip not in devices_by_ip:
            continue
        for rec in records:
            remote_ip = rec.get("remote_mgmt_ip")
            remote = devices_by_ip.get(remote_ip) if remote_ip else None
            if remote is None:
                name = (rec.get("remote_system_name") or "").lower()
                remote = by_name.get(name)
            if remote is None or remote.ip_address == local_ip:
                continue
            pair = frozenset({local_ip, remote.ip_address})
            if pair in seen:
                continue
            seen.add(pair)
            links.append(
                DiscoveredLink(
                    source_ip=local_ip,
                    target_ip=remote.ip_address,
                    source_if_name=rec.get("local_if_name"),
                    protocol=protocol,
                )
            )
    return links


def links_from_arp(
    arp_tables: dict[str, dict[str, str]],
    devices_by_ip: dict[str, DiscoveredDevice],
) -> list[DiscoveredLink]:
    """Infer adjacency from ARP tables + advertised MACs.

    `arp_tables` maps observer device IP → {remote_ip: remote_mac}.
    For every entry whose MAC belongs to a known device's interface, the
    observer and that device are adjacent.
    """
    mac_owner: dict[str, tuple[str, str | None]] = {}
    for ip, dev in devices_by_ip.items():
        for iface in dev.interfaces:
            if iface.mac_address:
                mac_owner[iface.mac_address.lower()] = (ip, iface.name)

    links: list[DiscoveredLink] = []
    seen: set[frozenset[str]] = set()
    for observer_ip, table in arp_tables.items():
        if observer_ip not in devices_by_ip:
            continue
        for remote_ip, mac in table.items():
            owner = mac_owner.get((mac or "").lower())
            if owner is None:
                continue
            far_ip = owner[0]
            if far_ip == observer_ip:
                continue
            # The ARP entry must point at a device we know by IP too,
            # otherwise the MAC is just some host on the segment.
            if remote_ip not in devices_by_ip and far_ip not in devices_by_ip:
                continue
            pair = frozenset({observer_ip, far_ip})
            if pair in seen:
                continue
            seen.add(pair)
            links.append(
                DiscoveredLink(
                    source_ip=observer_ip,
                    target_ip=far_ip,
                    target_if_name=owner[1],
                    protocol="arp",
                )
            )
    return links


def dedupe_links(links: list[DiscoveredLink]) -> list[DiscoveredLink]:
    """Collapse duplicate edges; strongest provenance wins, ties keep the first."""
    best: dict[frozenset[str], DiscoveredLink] = {}
    for link in links:
        pair = frozenset({link.source_ip, link.target_ip})
        current = best.get(pair)
        if current is None or protocol_rank(link.protocol) < protocol_rank(
            current.protocol
        ):
            best[pair] = link
    return list(best.values())


def degree_summary(links: list[DiscoveredLink]) -> dict[str, int]:
    """ip -> number of incident links (used by the builder for sanity checks)."""
    deg: dict[str, int] = defaultdict(int)
    for link in links:
        deg[link.source_ip] += 1
        deg[link.target_ip] += 1
    return dict(deg)

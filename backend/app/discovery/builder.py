"""Topology builder: merge a DiscoveryResult into the persistent twin graph.

Upsert semantics (idempotent across discovery runs):
- Device matched by ip_address → update identity fields, keep id stable.
- Interface matched by (device, if_index).
- Link matched by unordered device pair; protocol upgraded when a better
  source (manual > lldp > cdp > arp > inferred) reports the same edge.
- Devices not seen in this run are marked DOWN (stale) rather than deleted —
  the twin remembers what it knew.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, HealthState, Interface, Link

from .links import protocol_rank
from .models import DiscoveredDevice, DiscoveryResult

logger = logging.getLogger(__name__)


@dataclass
class BuildReport:
    devices_created: int = 0
    devices_updated: int = 0
    devices_staled: int = 0
    links_created: int = 0
    links_updated: int = 0
    changed: bool = field(default=False)


async def build_twin(db: AsyncSession, result: DiscoveryResult) -> BuildReport:
    """Persist discovered devices/links into the twin store. Returns a report."""
    report = BuildReport()

    existing_devices = {d.ip_address: d for d in (await db.scalars(select(Device))).all()}
    seen_ips: set[str] = set()

    for disc in result.devices:
        seen_ips.add(disc.ip_address)
        device = existing_devices.get(disc.ip_address)
        if device is None:
            device = Device(
                ip_address=disc.ip_address,
                name=disc.name,
                mac_address=disc.mac_address,
                device_type=disc.device_type,
                health=HealthState(disc.health),
                sys_description=disc.sys_description,
                source=disc.source,
            )
            db.add(device)
            existing_devices[disc.ip_address] = device
            report.devices_created += 1
            report.changed = True
            await db.flush()  # assign device.id before attaching interfaces
        else:
            if (
                device.name != disc.name
                or device.device_type != disc.device_type
                or device.mac_address != disc.mac_address
            ):
                device.name = disc.name
                device.device_type = disc.device_type
                device.mac_address = disc.mac_address
                report.devices_updated += 1
                report.changed = True
        await _sync_interfaces(db, device, disc, report)

    # Stale devices: known but not discovered this round → mark DOWN.
    for ip, device in existing_devices.items():
        if ip not in seen_ips and device.health != HealthState.DOWN:
            device.health = HealthState.DOWN
            report.devices_staled += 1
            report.changed = True

    await _sync_links(db, result, existing_devices, report)
    await db.commit()
    logger.info(
        "twin build: +%d devices, ~%d, %d stale; +%d links",
        report.devices_created,
        report.devices_updated,
        report.devices_staled,
        report.links_created,
    )
    return report


async def _sync_interfaces(
    db: AsyncSession, device: Device, disc: DiscoveredDevice, report: BuildReport
) -> None:
    """Upsert interfaces by if_index; flag report.changed on any difference."""
    await db.refresh(device, ["interfaces"])
    by_index = {i.if_index: i for i in device.interfaces}
    for di in disc.interfaces:
        iface = by_index.get(di.if_index)
        if iface is None:
            db.add(
                Interface(
                    device_id=device.id,
                    if_index=di.if_index,
                    name=di.name,
                    mac_address=di.mac_address,
                    ip_address=di.ip_address,
                    admin_status=di.admin_status,
                    oper_status=di.oper_status,
                    speed_mbps=di.speed_mbps,
                )
            )
            report.changed = True
            continue
        if (
            iface.name != di.name
            or iface.oper_status != di.oper_status
            or iface.mac_address != di.mac_address
            or iface.speed_mbps != di.speed_mbps
        ):
            iface.name = di.name
            iface.oper_status = di.oper_status
            iface.mac_address = di.mac_address
            iface.speed_mbps = di.speed_mbps
            report.changed = True


async def _sync_links(
    db: AsyncSession,
    result: DiscoveryResult,
    devices_by_ip: dict[str, Device],
    report: BuildReport,
) -> None:
    existing_links = (await db.scalars(select(Link))).all()
    pair_to_link: dict[frozenset[int], Link] = {
        frozenset({lnk.source_device_id, lnk.target_device_id}): lnk for lnk in existing_links
    }

    async def _iface_id(device: Device, if_name: str | None) -> int | None:
        """Resolve a discovered interface name to its twin id, if known."""
        if if_name is None:
            return None
        await db.refresh(device, ["interfaces"])
        for iface in device.interfaces:
            if iface.name == if_name:
                return iface.id
        return None

    for dl in result.links:
        src = devices_by_ip.get(dl.source_ip)
        dst = devices_by_ip.get(dl.target_ip)
        if src is None or dst is None:
            continue  # link endpoint unknown → skip this round
        pair = frozenset({src.id, dst.id})
        link = pair_to_link.get(pair)
        if link is None:
            link = Link(
                source_device_id=src.id,
                target_device_id=dst.id,
                protocol=dl.protocol,
                health=HealthState.UP,
            )
            db.add(link)
            pair_to_link[pair] = link
            report.links_created += 1
            report.changed = True
        else:
            if protocol_rank(dl.protocol) < protocol_rank(link.protocol):
                # better evidence for the same edge
                link.protocol = dl.protocol
                report.links_updated += 1
                report.changed = True
        # keep endpoint interfaces resolved as discovery evidence improves
        new_src_if = await _iface_id(src, dl.source_if_name)
        new_dst_if = await _iface_id(dst, dl.target_if_name)
        if new_src_if != link.source_interface_id or new_dst_if != link.target_interface_id:
            link.source_interface_id = new_src_if or link.source_interface_id
            link.target_interface_id = new_dst_if or link.target_interface_id
            report.changed = True

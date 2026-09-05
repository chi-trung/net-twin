"""Discovery orchestrator.

Runs one full discovery cycle and returns a BuildReport:

    live source:      sweep → SNMP enrich → link inference → build_twin
    simulator source: simulate_topology → build_twin

The engine is source-agnostic: it consumes a DiscoveryResult from any source
and persists it. This keeps the "digital twin" loop testable and lets the
monitoring scheduler call `run_once()` on a timer.
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

from .builder import BuildReport, build_twin
from .links import (
    dedupe_links,
    links_from_arp,
    links_from_cdp_neighbors,
    links_from_lldp_neighbors,
)
from .models import DiscoveredDevice, DiscoveryResult
from .simulator import SimulatorSource
from .sweeper import sweep_subnet

logger = logging.getLogger(__name__)


class DiscoverySource(Protocol):
    async def discover(self) -> DiscoveryResult: ...


class LiveSource:
    """Real discovery: sweep the subnet, SNMP-enrich, infer links."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def discover(self) -> DiscoveryResult:
        from .snmp import SnmpCollector  # optional dep, import lazily

        sweep = await sweep_subnet(self.settings.discovery_subnet)
        collector = SnmpCollector(
            community=self.settings.snmp_community,
            timeout=self.settings.snmp_timeout_seconds,
            retries=self.settings.snmp_retries,
        )

        devices: list[DiscoveredDevice] = []
        for ip, mac in sweep:
            enriched = await collector.collect(ip)
            if enriched is not None:
                enriched.mac_address = enriched.mac_address or mac
                devices.append(enriched)
            else:
                devices.append(
                    DiscoveredDevice(
                        ip_address=ip,
                        name=ip,
                        mac_address=mac,
                        source="sweep",
                    )
                )

        by_ip = {d.ip_address: d for d in devices}
        # Neighbor/ARP tables would be collected via SNMP here; with the data we
        # have, correlate by MAC to seed ARP links between known devices.
        arp_tables = await self._collect_arp_tables(collector, by_ip)
        lldp_neighbors = await self._collect_lldp(collector, by_ip)
        cdp_neighbors = await self._collect_cdp(collector, by_ip)

        links = dedupe_links(
            links_from_lldp_neighbors(lldp_neighbors, by_ip)
            + links_from_cdp_neighbors(cdp_neighbors, by_ip)
            + links_from_arp(arp_tables, by_ip)
        )
        return DiscoveryResult(devices=devices, links=links)

    async def _collect_arp_tables(self, collector, by_ip) -> dict[str, dict[str, str]]:
        """Best-effort ARP tables via ipNetToMediaTable. Empty when unavailable."""
        tables: dict[str, dict[str, str]] = {}
        for ip in by_ip:
            try:
                table = await collector.collect_arp(ip)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                table = {}
            if table:
                tables[ip] = table
        return tables

    async def _collect_lldp(self, collector, by_ip) -> dict[str, list[dict]]:
        """Best-effort LLDP neighbors. Empty when the MIB is not supported."""
        neighbors: dict[str, list[dict]] = {}
        for ip in by_ip:
            try:
                recs = await collector.collect_lldp(ip)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                recs = []
            if recs:
                neighbors[ip] = recs
        return neighbors

    async def _collect_cdp(self, collector, by_ip) -> dict[str, list[dict]]:
        """Best-effort CDP neighbors (Cisco gear). Empty when unavailable."""
        neighbors: dict[str, list[dict]] = {}
        for ip in by_ip:
            try:
                recs = await collector.collect_cdp(ip)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                recs = []
            if recs:
                neighbors[ip] = recs
        return neighbors


def make_source(settings: Settings | None = None) -> DiscoverySource:
    settings = settings or get_settings()
    if settings.discovery_source == "live":
        return LiveSource(settings)
    return SimulatorSource()


async def run_discovery(db: AsyncSession, source: DiscoverySource | None = None) -> BuildReport:
    """Execute one discovery cycle and persist the result into the twin."""
    source = source or make_source()
    result = await source.discover()
    logger.info(
        "discovery produced %d devices, %d links", len(result.devices), len(result.links)
    )
    return await build_twin(db, result)

"""Intermediate representation shared by all discovery sources.

Every collector (subnet sweep, SNMP, simulator, ...) produces these plain
dataclasses; the topology builder then merges and persists them. Keeping the
IR decoupled from ORM models makes sources trivially testable.
"""

from dataclasses import dataclass, field

from app.db.models import DeviceType


@dataclass
class DiscoveredInterface:
    if_index: int
    name: str
    mac_address: str | None = None
    ip_address: str | None = None
    admin_status: str = "up"
    oper_status: str = "up"
    speed_mbps: int | None = None


@dataclass
class DiscoveredDevice:
    ip_address: str
    name: str
    device_type: DeviceType = DeviceType.UNKNOWN
    mac_address: str | None = None
    sys_description: str | None = None
    health: str = "up"
    source: str = "discovery"
    interfaces: list[DiscoveredInterface] = field(default_factory=list)


@dataclass
class DiscoveredLink:
    """A link between two endpoints, identified by IP (and optionally interface)."""

    source_ip: str
    target_ip: str
    source_if_name: str | None = None
    target_if_name: str | None = None
    protocol: str = "inferred"  # lldp | cdp | arp | manual


@dataclass
class DiscoveryResult:
    devices: list[DiscoveredDevice] = field(default_factory=list)
    links: list[DiscoveredLink] = field(default_factory=list)

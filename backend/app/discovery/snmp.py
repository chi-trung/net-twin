"""SNMP collector: enrich discovered IPs with device identity.

Reads RFC1213 `system` scalars and IF-MIB tables via SNMPv2c (pysnmp).
The pure classification logic is separated from the SNMP I/O so it can be
unit-tested without a network.
"""

from __future__ import annotations

import logging

from app.db.models import DeviceType

from .models import DiscoveredDevice, DiscoveredInterface

logger = logging.getLogger(__name__)

# --- OIDs (RFC1213 + IF-MIB) ---
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"  # ifDescr
OID_IF_TYPE = "1.3.6.1.2.1.2.2.1.3"  # ifType
OID_IF_MAC = "1.3.6.1.2.1.2.2.1.6"  # ifPhysAddress
OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"  # ifOperStatus
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"  # ifSpeed (bps)
# ipNetToMediaTable (RFC1213): ifIndex / physAddress / netToMediaIP
OID_ARP_IFINDEX = "1.3.6.1.2.1.4.22.1.2"
OID_ARP_MAC = "1.3.6.1.2.1.4.22.1.3"
OID_ARP_IP = "1.3.6.1.2.1.4.22.1.4"
# LLDP-MIB remote system name / management address (802.1AB)
OID_LLDP_REM_SYSNAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_MGMT_ADDR = "1.0.8802.1.1.2.1.4.1.2.1"  # lldpRemManAddrTable (subset)

# ifType values of interest (IANAifType-MIB)
IFTYPE_ETHERNET = 6
IFTYPE_L2_VLAN = 23  # propVirtual-ish; commonly seen on switches
IFTYPE_LOOPBACK = 24


def classify_device(sys_descr: str | None, if_types: list[int]) -> DeviceType:
    """Heuristic device classification from sysDescr text and interface types.

    Rules of thumb used by most NMS tools:
    - sysDescr mentioning 'switch'/'catalyst'/'powerconnect' → SWITCH
    - sysDescr mentioning 'router'/'junos'/'ios' w/o switch → ROUTER
    - sysDescr mentioning 'firewall'/'asa'/'pfsense' → FIREWALL
    - many L3 interfaces + routing hints → ROUTER; many bridged ports → SWITCH
    - otherwise HOST
    """
    text = (sys_descr or "").lower()
    if any(k in text for k in ("firewall", "asa", "pfsense", "fortigate", "sophos")):
        return DeviceType.FIREWALL
    if any(k in text for k in ("switch", "catalyst", "powerconnect", "procurve", "aruba",
                               "transceiver", "bridge")):
        return DeviceType.SWITCH
    if any(k in text for k in ("router", "junos", "juniper", "ios xr", "routeros", "vyos",
                               "quagga", "frr")):
        return DeviceType.ROUTER
    if any(k in text for k in ("access point", "unifi", "openwrt")):
        return DeviceType.AP
    if any(k in text for k in ("windows", "linux", "macos", "darwin", "ubuntu", "debian")):
        return DeviceType.HOST
    # No textual clue: a device exposing many ethernet ports is likely a switch.
    eth_count = sum(1 for t in if_types if t == IFTYPE_ETHERNET)
    if eth_count >= 8:
        return DeviceType.SWITCH
    if eth_count >= 1 and "router" in text:
        return DeviceType.ROUTER
    return DeviceType.UNKNOWN


def build_interfaces(
    rows: dict[int, dict[str, object]],
) -> list[DiscoveredInterface]:
    """Merge per-index SNMP columns into DiscoveredInterface list."""
    interfaces: list[DiscoveredInterface] = []
    for if_index in sorted(rows):
        row = rows[if_index]
        speed_bps = row.get("speed")
        speed_mbps = (
            int(speed_bps / 1_000_000)
            if isinstance(speed_bps, int) and speed_bps
            else None
        )
        oper = row.get("oper")
        oper_status = (
            {1: "up", 2: "down"}.get(oper, "unknown") if isinstance(oper, int) else "unknown"
        )
        interfaces.append(
            DiscoveredInterface(
                if_index=if_index,
                name=str(row.get("descr") or f"if-{if_index}"),
                mac_address=_fmt_mac(row.get("mac")),
                admin_status="up",
                oper_status=oper_status,
                speed_mbps=speed_mbps,
            )
        )
    return interfaces


def _fmt_mac(raw: object) -> str | None:
    """Render an SNMP physAddress (bytes or hex string) as aa:bb:cc:dd:ee:ff."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return ":".join(f"{b:02x}" for b in raw) or None
    text = str(raw).strip()
    if not text:
        return None
    # pysnmp may hand back "00:11:22:..." or "001122334455"
    if ":" in text:
        return text.lower()
    if len(text) == 12 and all(c in "0123456789abcdefABCDEF" for c in text):
        return ":".join(text[i : i + 2] for i in range(0, 12, 2)).lower()
    return text.lower()


class SnmpCollector:
    """Async SNMPv2c collector built on pysnmp's asyncio API.

    pysnmp is an optional dependency ([net] extra); when it is missing or a
    device does not answer, collect() returns None and the caller keeps the
    bare sweep result.
    """

    def __init__(self, community: str = "public", timeout: float = 2.0, retries: int = 1) -> None:
        self.community = community
        self.timeout = timeout
        self.retries = retries

    async def collect(self, ip: str) -> DiscoveredDevice | None:
        try:
            raw = await self._snmp_walk_all(ip)
        except ImportError:
            logger.warning("pysnmp not installed; skipping SNMP enrichment")
            return None
        except Exception as exc:  # noqa: BLE001 — device unreachable is normal
            logger.debug("SNMP collect failed for %s: %s", ip, exc)
            return None

        sys_descr, sys_name, if_rows = raw
        if sys_descr is None and sys_name is None and not if_rows:
            return None  # device answered nothing

        if_types = [int(r.get("type", 0) or 0) for r in if_rows.values()]
        device_type = classify_device(sys_descr, if_types)
        name = sys_name or ip
        return DiscoveredDevice(
            ip_address=ip,
            name=name,
            device_type=device_type,
            sys_description=sys_descr,
            interfaces=build_interfaces(if_rows),
        )

    async def _hlapi(self):
        """Import pysnmp asyncio helpers (raises ImportError when not installed)."""
        from pysnmp.hlapi.v3arch.asyncio import (  # type: ignore[import-not-found]
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            get_cmd,
            walk_cmd,
        )

        return {
            "CommunityData": CommunityData,
            "ContextData": ContextData,
            "ObjectIdentity": ObjectIdentity,
            "ObjectType": ObjectType,
            "SnmpEngine": SnmpEngine,
            "UdpTransportTarget": UdpTransportTarget,
            "get_cmd": get_cmd,
            "walk_cmd": walk_cmd,
        }

    async def _snmp_walk_all(
        self, ip: str
    ) -> tuple[str | None, str | None, dict[int, dict[str, object]]]:
        """One-shot GET/WALK for system + interface tables. Raises on import errors."""
        h = await self._hlapi()
        engine = h["SnmpEngine"]()
        target = await h["UdpTransportTarget"].create(
            (ip, 161), timeout=self.timeout, retries=self.retries
        )
        comm = h["CommunityData"](self.community, mpModel=1)  # v2c

        async def _get(oid: str) -> str | None:
            err, errind, _, varbinds = await h["get_cmd"](
                engine, comm, target, h["ContextData"](), h["ObjectType"](h["ObjectIdentity"](oid))
            )
            if err or errind or not varbinds:
                return None
            value = varbinds[0][1]
            return str(value) if value is not None else None

        sys_descr = await _get(OID_SYS_DESCR)
        sys_name = await _get(OID_SYS_NAME)

        rows: dict[int, dict[str, object]] = {}

        async def _collect_column(oid: str, key: str) -> None:
            err, errind, _, varbinds = await h["walk_cmd"](
                engine, comm, target, h["ContextData"](), h["ObjectType"](h["ObjectIdentity"](oid))
            )
            if err or errind:
                return
            for vb in varbinds or []:
                idx = vb[0].get_indices()
                if not idx:
                    continue
                if_index = int(idx[-1])
                rows.setdefault(if_index, {})[key] = vb[1].getValue()

        await _collect_column(OID_IF_DESCR, "descr")
        await _collect_column(OID_IF_TYPE, "type")
        await _collect_column(OID_IF_MAC, "mac")
        await _collect_column(OID_IF_OPER, "oper")
        await _collect_column(OID_IF_SPEED, "speed")

        return sys_descr, sys_name, rows

    async def _walk_table(self, ip: str, oids: list[str]) -> dict[tuple, dict[str, object]]:
        """Walk several column OIDs and group results by shared row index tuple."""
        h = await self._hlapi()
        engine = h["SnmpEngine"]()
        target = await h["UdpTransportTarget"].create(
            (ip, 161), timeout=self.timeout, retries=self.retries
        )
        comm = h["CommunityData"](self.community, mpModel=1)
        rows: dict[tuple, dict[str, object]] = {}

        for oid, key in zip(oids, [f"c{i}" for i in range(len(oids))], strict=True):
            err, errind, _, varbinds = await h["walk_cmd"](
                engine, comm, target, h["ContextData"](), h["ObjectType"](h["ObjectIdentity"](oid))
            )
            if err or errind:
                continue
            for vb in varbinds or []:
                idx = vb[0].get_indices()
                if not idx:
                    continue
                rows.setdefault(tuple(int(i) for i in idx), {})[key] = vb[1].getValue()
        return rows

    async def collect_arp(self, ip: str) -> dict[str, str]:
        """Return {remote_ip: remote_mac} from the device's ipNetToMediaTable."""
        try:
            rows = await self._walk_table(ip, [OID_ARP_MAC, OID_ARP_IP])
        except ImportError:
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ARP table walk failed for %s: %s", ip, exc)
            return {}
        table: dict[str, str] = {}
        for row in rows.values():
            remote_ip = row.get("c1")
            mac = _fmt_mac(row.get("c0"))
            if remote_ip and mac:
                table[str(remote_ip)] = mac
        return table

    async def collect_lldp(self, ip: str) -> list[dict]:
        """Return LLDP neighbor records [{remote_system_name, remote_mgmt_ip}]."""
        try:
            rows = await self._walk_table(ip, [OID_LLDP_REM_SYSNAME])
        except ImportError:
            return []
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLDP walk failed for %s: %s", ip, exc)
            return []
        neighbors: list[dict] = []
        for row in rows.values():
            name = row.get("c0")
            if name:
                neighbors.append({"remote_system_name": str(name), "remote_mgmt_ip": None})
        return neighbors

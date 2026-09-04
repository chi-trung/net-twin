"""Subnet sweep: find live hosts in a CIDR range.

Strategy (best-effort, in order):
1. Scapy ARP sweep when available and running with raw-socket privileges
   (Npcap on Windows) — the most accurate on LAN segments.
2. Fallback: concurrent TCP connect probe on common ports + local ARP table
   parsing (`arp -a`), which works unprivileged on Windows/Linux.

Both paths return the same shape: list of (ip, mac|None).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re

logger = logging.getLogger(__name__)

# Ports probed by the fallback sweep — cheap and usually filtered, but enough
# to confirm a host is alive when at least one answers.
_FALLBACK_PORTS = (22, 80, 443, 445, 3389)
_ARP_LINE_RE = re.compile(
    r"(\d+\.\d+\.\d+\.\d+)\s+[:\s]\s*([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})"
)


def iter_hosts(cidr: str) -> list[str]:
    """All usable host addresses for a CIDR (e.g. '10.0.0.0/24')."""
    network = ipaddress.ip_network(cidr, strict=False)
    if network.num_addresses <= 2:  # /31, /32
        return [str(ip) for ip in network]
    return [str(ip) for ip in network.hosts()]


def parse_arp_table(output: str) -> dict[str, str]:
    """Map ip -> mac from `arp -a` style output."""
    table: dict[str, str] = {}
    for ip, mac in _ARP_LINE_RE.findall(output):
        if not ip.startswith(("224.", "239.", "255.")):  # skip multicast/broadcast
            table[ip] = mac.lower()
    return table


async def _tcp_probe(ip: str, timeout: float = 0.4) -> bool:
    for port in _FALLBACK_PORTS:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return True
        except (TimeoutError, OSError):
            continue
    return False


async def _scapy_arp_sweep(cidr: str, timeout: float = 3.0) -> list[tuple[str, str | None]]:
    """ARP sweep via Scapy. Raises ImportError/RuntimeError when unavailable."""
    from scapy.all import ARP, Ether, srp  # type: ignore[import-not-found]

    def _run() -> list[tuple[str, str | None]]:
        pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=cidr)
        answered, _ = srp(pkt, timeout=timeout, retry=1, verbose=False)
        return [(r.sprintf("%ARP.psrc%"), r.sprintf("%ARP.hwsrc%").lower()) for _, r in answered]

    return await asyncio.to_thread(_run)


async def _fallback_sweep(cidr: str) -> list[tuple[str, str | None]]:
    hosts = iter_hosts(cidr)
    results = await asyncio.gather(*(_tcp_probe(ip) for ip in hosts))
    alive = [ip for ip, ok in zip(hosts, results, strict=True) if ok]

    macs: dict[str, str] = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        macs = parse_arp_table(stdout.decode(errors="replace"))
    except OSError:
        logger.debug("arp table unavailable")

    return [(ip, macs.get(ip)) for ip in alive]


async def sweep_subnet(cidr: str, prefer_scapy: bool = True) -> list[tuple[str, str | None]]:
    """Return (ip, mac|None) for live hosts in `cidr`."""
    if prefer_scapy:
        try:
            found = await _scapy_arp_sweep(cidr)
            if found:
                logger.info("scapy ARP sweep found %d live hosts in %s", len(found), cidr)
                return found
        except Exception as exc:  # noqa: BLE001 — any scapy failure → fallback
            logger.info("scapy sweep unavailable (%s); using TCP fallback", exc)

    found = await _fallback_sweep(cidr)
    logger.info("fallback sweep found %d live hosts in %s", len(found), cidr)
    return found

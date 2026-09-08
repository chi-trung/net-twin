"""Reachability probes.

A Probe returns (latency_ms, packet_loss_pct) for one target. Two
implementations:

- SystemPingProbe: shells out to the OS `ping` command. Works unprivileged on
  Windows and Linux, which matters because raw ICMP sockets need admin/root.
- NullProbe: reports "up, 0 ms" for the simulator source so the demo never
  depends on real reachability; devices with a simulated outage report down.

Parsing is isolated in pure functions so it is unit-testable.
"""

from __future__ import annotations

import asyncio
import platform
import re
import time
from dataclasses import dataclass
from typing import Protocol

from app.core.outages import get_outages

_LATENCY_RE = re.compile(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_LOSS_RE = re.compile(r"(\d{1,3})%\s*loss", re.IGNORECASE)
# Linux summary: "3 packets transmitted, 3 received, 0% packet loss"
_LOSS_RE_LINUX = re.compile(r"(\d+(?:\.\d+)?)%\s+packet loss")


@dataclass
class ProbeResult:
    reachable: bool
    latency_ms: float | None
    packet_loss_pct: float


class Probe(Protocol):
    async def probe(self, ip: str, count: int = 4) -> ProbeResult: ...


def parse_ping_output(output: str) -> ProbeResult:
    """Extract latency and loss from localized-agnostic `ping` output.

    Takes the *last* time= value seen (the statistics line / final reply) and
    the reported loss percentage. Falls back to "unreachable" when no reply
    and no loss figure can be parsed.
    """
    latencies = _LATENCY_RE.findall(output)
    latency = float(latencies[-1]) if latencies else None

    loss: float | None = None
    m = _LOSS_RE_LINUX.search(output) or _LOSS_RE.search(output)
    if m:
        loss = float(m.group(1))

    if loss is None:
        # No summary parsed: infer from whether we saw any reply at all.
        loss = 0.0 if latency is not None else 100.0

    reachable = loss < 100.0
    return ProbeResult(reachable=reachable, latency_ms=latency, packet_loss_pct=loss)


class SystemPingProbe:
    """Ping via the platform's `ping` binary (no raw-socket privileges needed)."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout = timeout_seconds

    async def probe(self, ip: str, count: int = 4) -> ProbeResult:
        args = self._build_args(ip, count)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except (TimeoutError, OSError):
            return ProbeResult(reachable=False, latency_ms=None, packet_loss_pct=100.0)
        return parse_ping_output(stdout.decode(errors="replace"))

    def _build_args(self, ip: str, count: int) -> list[str]:
        if platform.system().lower() == "windows":
            # Windows: -n count, -w timeout(ms)
            return ["ping", "-n", str(count), "-w", "1000", ip]
        # POSIX: -c count, -W timeout(s)
        return ["ping", "-c", str(count), "-W", "1", ip]


class NullProbe:
    """Simulator-mode probe: always up, except for devices with an active
    simulated outage (see app.core.outages), which report unreachable."""

    def __init__(self, latency_ms: float = 1.0) -> None:
        self.latency_ms = latency_ms

    async def probe(self, ip: str, count: int = 4) -> ProbeResult:  # noqa: ARG002
        if get_outages().is_down(ip):
            return ProbeResult(reachable=False, latency_ms=None, packet_loss_pct=100.0)
        return ProbeResult(reachable=True, latency_ms=self.latency_ms, packet_loss_pct=0.0)


class SnmpProbe:
    """SNMP-based reachability for devices that drop ICMP.

    One GET of sysName.0 per cycle doubles as the latency sample (the SNMP
    round-trip is the network latency at this granularity). Meant for lab
    agents and hardened gear that answer SNMP but not ping.
    """

    def __init__(self, community: str = "public", timeout: float = 2.0, retries: int = 1) -> None:
        self.community = community
        self.timeout = timeout
        self.retries = retries

    async def probe(self, ip: str, count: int = 4) -> ProbeResult:  # noqa: ARG002
        from app.discovery.snmp import SnmpCollector

        collector = SnmpCollector(
            community=self.community, timeout=self.timeout, retries=self.retries
        )
        started = time.monotonic()
        name = await collector.probe(ip)
        latency_ms = (time.monotonic() - started) * 1000.0
        if name is None:
            return ProbeResult(reachable=False, latency_ms=None, packet_loss_pct=100.0)
        return ProbeResult(reachable=True, latency_ms=round(latency_ms, 2), packet_loss_pct=0.0)


class PingOrSnmpProbe:
    """Live-mode probe: ping first (cheap), SNMP fallback (ICMP-less gear).

    A device is down only when *both* fail — hardened-but-alive gear that
    answers SNMP while dropping ping must not flap the twin.
    """

    def __init__(
        self,
        snmp_community: str = "public",
        snmp_timeout: float = 2.0,
        snmp_retries: int = 1,
        ping: SystemPingProbe | None = None,
    ) -> None:
        self.ping = ping or SystemPingProbe()
        self.snmp = SnmpProbe(
            community=snmp_community, timeout=snmp_timeout, retries=snmp_retries
        )

    async def probe(self, ip: str, count: int = 4) -> ProbeResult:
        result = await self.ping.probe(ip, count)
        if result.reachable:
            return result
        return await self.snmp.probe(ip, count)

"""In-process registry of simulated outages, for demos and chaos testing.

"Unplugging" a device here makes the whole twin react through the real
pipeline — no manual DB edits:

- the simulator discovery source hides the device, so the builder marks it
  stale → DOWN and emits `topology.updated`;
- the simulator probe reports it unreachable, so health flips to DOWN
  (`device.health_changed`) and the alert engine raises `node_down`.

Clearing the outage lets the normal discovery/monitor loops heal the twin
back to UP on their next cycle. The registry is per-process by design: it is
a demo/ops tool for a single-backend deployment, not distributed state.
"""

from __future__ import annotations


class OutageRegistry:
    def __init__(self) -> None:
        self._ips: set[str] = set()

    def add(self, ip: str) -> None:
        self._ips.add(ip)

    def remove(self, ip: str) -> bool:
        """Return True when an outage existed and was cleared."""
        if ip in self._ips:
            self._ips.discard(ip)
            return True
        return False

    def is_down(self, ip: str) -> bool:
        return ip in self._ips

    def list(self) -> list[str]:
        return sorted(self._ips)


_outages = OutageRegistry()


def get_outages() -> OutageRegistry:
    return _outages

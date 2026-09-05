"""Simulated link-traffic model for the demo topology.

Real SNMP would diff ``ifInOctets``/``ifOutOctets`` counters between polls;
in simulator mode there is no hardware, so this model produces plausible
per-link throughput instead:

- a deterministic per-link base load derived from the link's id and the
  device roles it connects (core uplinks carry far more than host ports);
- a slow diurnal sine wave so charts show a believable daily curve;
- fast small noise so each poll differs a little.

Deterministic in (link_key, timestamp): the same second yields the same bps
across processes and restarts, keeping the twin reproducible.
"""

from __future__ import annotations

import hashlib
import math

from app.db.models import DeviceType

# plausible busy-hour loads per role pair (bps)
_BASE_LOAD: dict[tuple[str, str], float] = {
    ("router", "switch"): 420_000_000,  # core ↔ dist: ~420 Mbps
    ("switch", "switch"): 95_000_000,  # dist ↔ access
    ("switch", "host"): 12_000_000,  # access ↔ host
}
_DEFAULT_LOAD = 8_000_000


def _role_of(device_type: DeviceType) -> str:
    if device_type == DeviceType.ROUTER:
        return "router"
    if device_type == DeviceType.FIREWALL:
        return "router"
    if device_type in (DeviceType.SWITCH, DeviceType.AP):
        return "switch"
    return "host"


def link_base_bps(source_type: DeviceType, target_type: DeviceType, link_key: int) -> float:
    """Deterministic base load for a link: role pair × per-link hash jitter."""
    lo, hi = sorted((_role_of(source_type), _role_of(target_type)))
    base = _BASE_LOAD.get((lo, hi), _DEFAULT_LOAD)
    digest = hashlib.blake2b(f"link-{link_key}".encode(), digest_size=4).digest()
    jitter = 0.55 + (int.from_bytes(digest) % 90) / 100  # 0.55× … 1.44×
    return base * jitter


def traffic_bps(
    base_bps: float, link_key: int, ts_seconds: float, direction: str = "in"
) -> float:
    """Instantaneous throughput at ``ts_seconds`` (unix) for one direction."""
    # diurnal wave: full cycle per 24h, ±35% around the base
    diurnal = 1 + 0.35 * math.sin(2 * math.pi * (ts_seconds % 86_400) / 86_400)
    # per-link fast noise, direction-split so in/out differ
    digest = hashlib.blake2b(
        f"{link_key}-{int(ts_seconds // 5)}-{direction}".encode(), digest_size=4
    ).digest()
    noise = 0.92 + (int.from_bytes(digest) % 160) / 1000  # 0.92× … 1.08×
    # out direction on uplinks is typically busier (server → user traffic)
    direction_gain = 1.25 if direction == "out" else 1.0
    return max(base_bps * diurnal * noise * direction_gain, 0.0)

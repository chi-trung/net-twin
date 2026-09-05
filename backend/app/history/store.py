"""Snapshot capture and time-travel diffing for the twin graph.

A snapshot is a point-in-time copy of the twin graph stored in the existing
`Snapshot` model (JSON blob). Capture happens automatically when the twin
actually changes — topology changes on discovery, health changes on monitor —
plus manual captures from the API. Diffing two snapshots (or a snapshot vs
the live graph) yields added/removed devices and links so the frontend can
show what the network looked like "then" vs "now".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import Device, Link, Snapshot

logger = logging.getLogger(__name__)

# keep the snapshot table bounded: one graph copy per change can grow fast
MAX_SNAPSHOTS = 200


def capture_graph(devices: list[Device], links: list[Link]) -> dict:
    """Serialize the twin graph into the JSON shape stored on a Snapshot."""
    return {
        "nodes": [
            {
                "id": d.id,
                "name": d.name,
                "ip_address": d.ip_address,
                "device_type": d.device_type.value,
                "health": d.health.value,
            }
            for d in devices
        ],
        "edges": [
            {
                "id": lnk.id,
                "source_device_id": lnk.source_device_id,
                "target_device_id": lnk.target_device_id,
                "protocol": lnk.protocol,
                "health": lnk.health.value,
            }
            for lnk in links
        ],
    }


async def take_snapshot(
    db: AsyncSession,
    trigger: str,
    captured_at: datetime | None = None,
    graph: dict | None = None,
) -> Snapshot:
    """Persist a snapshot of the current graph (or a pre-captured one) and
    prune the table down to MAX_SNAPSHOTS rows."""
    if graph is None:
        devices = (await db.scalars(select(Device))).all()
        links = (await db.scalars(select(Link))).all()
        graph = capture_graph(list(devices), list(links))

    snap = Snapshot(taken_at=captured_at or utcnow(), trigger=trigger, graph=graph)
    db.add(snap)
    await db.flush()

    stale_ids = (
        await db.scalars(
            select(Snapshot.id)
            .order_by(Snapshot.taken_at.desc(), Snapshot.id.desc())
            .offset(MAX_SNAPSHOTS)
        )
    ).all()
    if stale_ids:
        for old in await db.scalars(select(Snapshot).where(Snapshot.id.in_(stale_ids))):
            await db.delete(old)
    return snap


@dataclass
class GraphDiff:
    """Structural difference between two graph states (old → new)."""

    added_nodes: list[dict] = field(default_factory=list)
    removed_nodes: list[dict] = field(default_factory=list)
    added_edges: list[dict] = field(default_factory=list)
    removed_edges: list[dict] = field(default_factory=list)
    # node id → (old health, new health); only meaningful when the node
    # exists in both states
    health_changes: dict[int, tuple[str, str]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.added_edges
            or self.removed_edges
            or self.health_changes
        )


def diff_graphs(old: dict, new: dict) -> GraphDiff:
    """Diff two snapshot-shaped graph dicts (nodes/edges keyed by id)."""
    old_nodes = {n["id"]: n for n in old.get("nodes", [])}
    new_nodes = {n["id"]: n for n in new.get("nodes", [])}
    old_edges = {e["id"]: e for e in old.get("edges", [])}
    new_edges = {e["id"]: e for e in new.get("edges", [])}

    d = GraphDiff()
    for nid, node in new_nodes.items():
        if nid not in old_nodes:
            d.added_nodes.append(node)
        elif old_nodes[nid]["health"] != node["health"]:
            d.health_changes[nid] = (old_nodes[nid]["health"], node["health"])
    for nid, node in old_nodes.items():
        if nid not in new_nodes:
            d.removed_nodes.append(node)
    for eid, edge in new_edges.items():
        if eid not in old_edges:
            d.added_edges.append(edge)
    for eid in old_edges:
        if eid not in new_edges:
            d.removed_edges.append(old_edges[eid])
    return d

"""Root-cause analysis over the twin graph.

Given a *symptom* device (usually one that is DOWN), the analyzer proposes
ranked hypotheses for why. It combines three evidence sources:

- **correlated alerts**: active alerts whose device is an upstream neighbor
  of the symptom device (or the device itself) — alerts on the direct
  uplink carry the strongest signal;
- **topology**: if removing one of the symptom device's *upstream* (toward
  the root) neighbors would cut the symptom device off, that neighbor is a
  candidate root cause — the symptom may be a downstream casualty;
- **fragility**: a device whose failure isolates many others is a plausible
  common cause for broad outages; the hypothesis only fires when several
  devices share the symptom.

The module is pure (plain adjacency + row-like objects in, hypotheses out)
so ranking logic is unit-testable without a database.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.analysis.graph import Graph, _reachable_from, build_adjacency


@dataclass
class DeviceFacts:
    """Everything the analyzer may know about one device."""

    id: int
    name: str
    health: str
    device_type: str
    hop_to_root: int = 0  # BFS distance to root; 0 == root itself


@dataclass
class AlertFact:
    """An active alert relevant to the analysis."""

    id: int
    device_id: int | None
    rule: str
    severity: str
    message: str


@dataclass
class Hypothesis:
    """One ranked root-cause candidate with its supporting evidence."""

    device: DeviceFacts
    score: float
    reasons: list[str] = field(default_factory=list)
    evidence_alerts: list[AlertFact] = field(default_factory=list)

    @property
    def headline(self) -> str:
        return self.reasons[0] if self.reasons else "candidate"


def _bfs_hops(
    graph: Graph, root: int, edge_alive: Callable[[int, int], bool] | None = None
) -> dict[int, int]:
    """Hop distance from root to every reachable device.

    ``edge_alive(u, v)`` lets callers treat devices as removed without
    mutating the adjacency (dead devices don't forward traffic).
    """
    hops: dict[int, int] = {root: 0}
    queue = deque([root])
    while queue:
        node = queue.popleft()
        for nbr in graph.get(node, ()):
            if edge_alive is not None and not edge_alive(node, nbr):
                continue
            if nbr not in hops:
                hops[nbr] = hops[node] + 1
                queue.append(nbr)
    return hops


def _upstream_map(
    graph: Graph, root: int
) -> dict[int, list[int]]:
    """For each device, its neighbors one hop closer to the root (parents in
    the BFS tree). A device may have several parents when redundant links
    exist — all are genuine uplinks."""
    hops = _bfs_hops(graph, root)
    parents: dict[int, list[int]] = {}
    for node, nbrs in graph.items():
        for nbr in nbrs:
            if hops.get(nbr, 10**9) == hops.get(node, 10**9) - 1:
                parents.setdefault(node, []).append(nbr)
    return parents


def analyze(
    symptom_id: int,
    devices: dict[int, DeviceFacts],
    links: list[tuple[int, int]],
    root_id: int | None,
    active_alerts: list[AlertFact],
) -> list[Hypothesis]:
    """Rank root-cause hypotheses for a symptom device.

    Returns hypotheses sorted by score (descending). An empty result means
    the twin has no structural or alert evidence to reason from — the caller
    should fall back to "unknown cause".
    """
    if root_id is None or symptom_id not in devices:
        return []

    device_ids = list(devices)
    graph = build_adjacency(device_ids, links)
    parents = _upstream_map(graph, root_id)
    down_ids = {d for d, f in devices.items() if f.health == "down"}
    alerts_by_device: dict[int, list[AlertFact]] = {}
    for a in active_alerts:
        if a.device_id is not None:
            alerts_by_device.setdefault(a.device_id, []).append(a)

    # ── candidate pool: the symptom itself + every device on its upstream
    # path toward the root (direct parents first, then grandparents…), since
    # a failure two hops up can still be the root cause of the symptom
    candidates = [symptom_id]
    frontier = [symptom_id]
    seen = {symptom_id}
    while frontier:
        node = frontier.pop()
        for p in parents.get(node, ()):
            if p not in seen:
                seen.add(p)
                candidates.append(p)
                frontier.append(p)

    hypotheses: list[Hypothesis] = []
    for cand in candidates:
        facts = devices[cand]
        reasons: list[str] = []
        evidence: list[AlertFact] = []
        score = 0.0

        # (1) correlated alerts on the candidate
        cand_alerts = alerts_by_device.get(cand, [])
        for a in cand_alerts:
            weight = 3.0 if a.severity == "critical" else 1.5
            score += weight
            evidence.append(a)
            reasons.append(f"active alert: {a.message}")

        # (2) upstream casualty: an upstream candidate with evidence (down
        # health or its own alerts) that also sits on every path from the
        # symptom to the root is a strong root-cause candidate
        if cand != symptom_id and (cand in down_ids or cand_alerts):
            survivors = {n: {m for m in nbrs if m != cand} for n, nbrs in graph.items() if n != cand}
            reach = _reachable_from(survivors, root_id)
            if symptom_id not in reach:
                score += 4.0
                reasons.append(
                    f"removing {facts.name} disconnects {devices[symptom_id].name} "
                    "from the core — it is the only uplink path"
                )
            elif cand in down_ids:
                # down but redundant path exists — still a real link failure
                score += 2.0
                reasons.append(
                    f"{facts.name} is DOWN; {devices[symptom_id].name} lost this "
                    "uplink but has an alternate path"
                )

        # the symptom's own down health is weak evidence for itself
        if cand == symptom_id and facts.health == "down":
            score += 1.0
            reasons.append(f"{facts.name} itself is DOWN")

        if reasons:
            hypotheses.append(Hypothesis(device=facts, score=score, reasons=reasons, evidence_alerts=evidence))

    # (3) broad-outage fragility: many devices are down; a high-impact
    # (SPOF) device that is ALSO down is the likely common cause
    if len(down_ids) >= 3:
        spof_impact: dict[int, int] = {}
        for cand in down_ids:
            survivors = {n: {m for m in nbrs if m != cand} for n, nbrs in graph.items() if n != cand}
            reach = _reachable_from(survivors, root_id)
            spof_impact[cand] = len(set(devices) - reach - {cand})
        best = max(spof_impact, key=spof_impact.get)
        if spof_impact[best] >= len(down_ids) - 1 and best != symptom_id:
            h = next((h for h in hypotheses if h.device.id == best), None)
            if h is not None:
                h.score += 5.0
                h.reasons.append(
                    f"common cause: {h.device.name} failing alone cuts off "
                    f"{spof_impact[best]} device(s) — matches the outage scale"
                )
            else:
                hypotheses.append(
                    Hypothesis(
                        device=devices[best],
                        score=5.0 + 1.0,
                        reasons=[
                            f"common cause: {devices[best].name} is DOWN and failing "
                            f"alone would cut off {spof_impact[best]} device(s), matching "
                            "the outage scale"
                        ],
                    )
                )

    hypotheses.sort(key=lambda h: h.score, reverse=True)
    return hypotheses

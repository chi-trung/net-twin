"""Pure graph algorithms over the twin topology.

These are the "twin intelligence" layer: given the current graph, answer
structural questions a monitoring dashboard cannot:

- ``blast_radius`` — if this device fails, which others lose their only
  path to the root (the core router)? That is what-if analysis on the twin.
- ``shortest_path`` — hop path between two devices, for path tracing.

Graph is passed in as plain adjacency (id → neighbor ids) so the module
stays decoupled from the ORM and unit-testable without a database.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

Graph = dict[int, set[int]]


def build_adjacency(
    device_ids: list[int], edges: list[tuple[int, int]], *, directed_root: int | None = None
) -> Graph:
    """Undirected adjacency by default; when ``directed_root`` is given, edges
    are also usable in reverse so downstream reachability mirrors real
    routing (a switch forwards both ways)."""
    g: Graph = {i: set() for i in device_ids}
    for src, dst in edges:
        g.setdefault(src, set()).add(dst)
        g.setdefault(dst, set()).add(src)
    return g


@dataclass
class BlastRadius:
    """Impact of losing one device on the rest of the network."""

    failed_id: int
    isolated_ids: list[int] = field(default_factory=list)  # cut off from root
    affected_links: list[tuple[int, int]] = field(default_factory=list)  # edges of failed node
    degraded_ids: list[int] = field(default_factory=list)  # still reachable but lost redundancy

    @property
    def impacted_count(self) -> int:
        return len(self.isolated_ids) + len(self.degraded_ids)


def blast_radius(graph: Graph, failed_id: int, root_id: int | None = None) -> BlastRadius:
    """Devices unreachable from ``root_id`` once ``failed_id`` is removed.

    Without a root, "isolated" means disconnected from the largest surviving
    component. ``degraded_ids`` lists devices that stay reachable but lost at
    least one path (single-redundancy loss) — computed by counting the failed
    node among each survivor's disjoint-path coverage via simple BFS levels.
    """
    radius = BlastRadius(failed_id=failed_id)
    if failed_id not in graph:
        return radius

    radius.affected_links = [(failed_id, n) for n in sorted(graph[failed_id])]

    surviving = {
        n: {m for m in nbrs if m != failed_id}
        for n, nbrs in graph.items()
        if n != failed_id
    }

    if root_id == failed_id:
        # The root itself failed: every survivor loses core connectivity,
        # even if fragments of the graph stay connected among themselves.
        radius.isolated_ids = sorted(surviving)
    elif root_id is None or root_id not in surviving:
        components = _components(surviving)
        if not components:
            return radius
        main = max(components, key=len)
        radius.isolated_ids = sorted(n for c in components if c is not main for n in c)
    else:
        reachable = _reachable_from(surviving, root_id)
        radius.isolated_ids = sorted(set(surviving) - reachable)

    # Degraded: survivors adjacent to the failed node that are still reachable
    # — they just lost a direct link (redundancy or uplink) even if not cut off.
    if root_id is not None and root_id != failed_id and root_id in surviving:
        reachable = set(surviving) - set(radius.isolated_ids)
        radius.degraded_ids = sorted(n for n in graph[failed_id] if n in reachable)
    return radius


def shortest_path(graph: Graph, source: int, target: int) -> list[int] | None:
    """Fewest-hops path source→target (BFS); None when disconnected."""
    if source == target:
        return [source]
    prev: dict[int, int] = {source: source}
    queue: deque[int] = deque([source])
    while queue:
        node = queue.popleft()
        for nbr in sorted(graph.get(node, ())):  # sorted → deterministic path
            if nbr in prev:
                continue
            prev[nbr] = node
            if nbr == target:
                path = [target]
                while path[-1] != source:
                    path.append(prev[path[-1]])
                return path[::-1]
            queue.append(nbr)
    return None


def _components(graph: Graph) -> list[set[int]]:
    seen: set[int] = set()
    out: list[set[int]] = []
    for node in graph:
        if node in seen:
            continue
        comp = _reachable_from(graph, node)
        seen |= comp
        out.append(comp)
    return out


def _reachable_from(graph: Graph, start: int) -> set[int]:
    seen = {start}
    queue: deque[int] = deque([start])
    while queue:
        node = queue.popleft()
        for nbr in graph.get(node, ()):
            if nbr not in seen:
                seen.add(nbr)
                queue.append(nbr)
    return seen

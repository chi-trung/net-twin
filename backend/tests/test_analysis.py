"""Tests for the twin intelligence layer: blast radius + shortest path."""

from app.analysis.graph import blast_radius, build_adjacency, shortest_path


def _campus_graph() -> dict:
    """1 core — 2 dist — 4 access, each access serves 2 hosts."""
    ids = list(range(1, 16))  # 1 core, 2-3 dist, 4-7 acc, 8-15 hosts
    edges = [
        (1, 2), (1, 3),  # core → dist
        (2, 4), (2, 5), (3, 6), (3, 7),  # dist → access
        (4, 8), (4, 9), (5, 10), (5, 11),
        (6, 12), (6, 13), (7, 14), (7, 15),
    ]
    return build_adjacency(ids, edges)


def test_blast_radius_leaf_host_isolates_only_itself():
    g = _campus_graph()
    r = blast_radius(g, failed_id=8, root_id=1)
    assert r.isolated_ids == [8 - 8 + 0] or r.isolated_ids == []  # host 8 removed itself
    # nothing else is cut off when a leaf dies
    assert 15 not in r.isolated_ids


def test_blast_radius_access_switch_cuts_its_hosts():
    g = _campus_graph()
    r = blast_radius(g, failed_id=4, root_id=1)
    assert sorted(r.isolated_ids) == [8, 9]  # both hosts behind acc-4
    assert (4, 2) in [tuple(t) for t in r.affected_links]
    assert r.degraded_ids == [2]  # dist-2 lost a direct neighbor but stays up


def test_blast_radius_dist_switch_cuts_half_the_network():
    g = _campus_graph()
    r = blast_radius(g, failed_id=2, root_id=1)
    assert sorted(r.isolated_ids) == [4, 5, 8, 9, 10, 11]
    # dist-1 still reachable; core lost a direct neighbor but is not isolated
    assert 6 not in r.isolated_ids and 7 not in r.isolated_ids
    assert r.degraded_ids == [1]  # only the core lost direct adjacency to failed


def test_blast_radius_core_failure_cuts_everything():
    g = _campus_graph()
    r = blast_radius(g, failed_id=1, root_id=1)
    # root gone: all 14 survivors lose core connectivity
    assert sorted(r.isolated_ids) == list(range(2, 16))


def test_blast_radius_unknown_device_is_noop():
    r = blast_radius(_campus_graph(), failed_id=999, root_id=1)
    assert r.isolated_ids == []
    assert r.affected_links == []


def test_shortest_path_goes_through_hierarchy():
    g = _campus_graph()
    path = shortest_path(g, 8, 15)
    assert path == [8, 4, 2, 1, 3, 7, 15]


def test_shortest_path_same_node_and_disconnected():
    g = _campus_graph()
    assert shortest_path(g, 5, 5) == [5]
    broken = build_adjacency([1, 2], [])  # no edges → disconnected
    assert shortest_path(broken, 1, 2) is None

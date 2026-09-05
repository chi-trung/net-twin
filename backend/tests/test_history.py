"""Tests for topology history: capture, pruning and time-travel diffing."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Device, DeviceType, HealthState, Link, Snapshot
from app.history.store import MAX_SNAPSHOTS, capture_graph, diff_graphs, take_snapshot


def _device(idx: int, health: HealthState = HealthState.UP) -> Device:
    return Device(
        name=f"dev-{idx}",
        ip_address=f"10.0.0.{idx}",
        device_type=DeviceType.SWITCH,
        health=health,
    )


async def _seed_pair(db) -> tuple[Device, Device, Link]:
    a, b = _device(1), _device(2)
    db.add_all([a, b])
    await db.commit()
    link = Link(source_device_id=a.id, target_device_id=b.id, protocol="lldp")
    db.add(link)
    await db.commit()
    return a, b, link


async def test_capture_graph_shape(db_session):
    await _seed_pair(db_session)
    devices = list((await db_session.scalars(select(Device))).all())
    links = list((await db_session.scalars(select(Link))).all())

    graph = capture_graph(devices, links)
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    node = graph["nodes"][0]
    assert set(node) == {"id", "name", "ip_address", "device_type", "health"}
    edge = graph["edges"][0]
    assert set(edge) == {"id", "source_device_id", "target_device_id", "protocol", "health"}


async def test_take_snapshot_stores_graph(db_session):
    await _seed_pair(db_session)
    snap = await take_snapshot(db_session, trigger="manual")
    await db_session.commit()

    stored = await db_session.get(Snapshot, snap.id)
    assert stored is not None
    assert stored.trigger == "manual"
    assert len(stored.graph["nodes"]) == 2
    assert len(stored.graph["edges"]) == 1


async def test_snapshot_pruning_keeps_recent(db_session):
    # seed old rows in the *past* so the new snapshot is genuinely the newest
    base = datetime.now(UTC) - timedelta(hours=2)
    for i in range(MAX_SNAPSHOTS + 3):
        db_session.add(
            Snapshot(
                taken_at=base + timedelta(seconds=i),
                trigger="health",
                graph={"nodes": [], "edges": []},
            )
        )
    await db_session.commit()

    snap = await take_snapshot(db_session, trigger="manual")
    await db_session.commit()

    count = await db_session.scalar(select(func.count(Snapshot.id)))
    assert count == MAX_SNAPSHOTS
    remaining = (
        await db_session.scalars(select(Snapshot.id).order_by(Snapshot.taken_at.desc()))
    ).all()
    assert snap.id == remaining[0]  # newest survives at the head


async def test_diff_graphs_added_removed_nodes():
    old = {"nodes": [{"id": 1, "name": "a", "health": "up"}], "edges": []}
    new = {
        "nodes": [
            {"id": 1, "name": "a", "health": "up"},
            {"id": 2, "name": "b", "health": "down"},
        ],
        "edges": [],
    }
    diff = diff_graphs(old, new)
    assert [n["id"] for n in diff.added_nodes] == [2]
    assert diff.removed_nodes == []
    assert diff.empty is False


async def test_diff_graphs_health_change_and_edges():
    old = {
        "nodes": [
            {"id": 1, "name": "a", "health": "up"},
            {"id": 2, "name": "b", "health": "up"},
        ],
        "edges": [{"id": 10, "source_device_id": 1, "target_device_id": 2}],
    }
    new = {
        "nodes": [
            {"id": 1, "name": "a", "health": "down"},
            {"id": 2, "name": "b", "health": "up"},
        ],
        "edges": [],
    }
    diff = diff_graphs(old, new)
    assert diff.health_changes == {1: ("up", "down")}
    assert diff.removed_edges and diff.removed_edges[0]["id"] == 10
    assert diff.added_edges == []


async def test_diff_identical_graphs_empty():
    g = {
        "nodes": [{"id": 1, "name": "a", "health": "up"}],
        "edges": [{"id": 5, "source_device_id": 1, "target_device_id": 2}],
    }
    same = {"nodes": list(g["nodes"]), "edges": list(g["edges"])}
    assert diff_graphs(g, same).empty is True

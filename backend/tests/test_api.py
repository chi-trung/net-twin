"""API smoke tests for Phase 2."""

from app.db.models import Alert, AlertSeverity, AlertStatus, Device, DeviceType, HealthState, Link


async def test_healthz(client):
    resp = await client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_topology_empty(client):
    resp = await client.get("/api/v1/topology")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []


async def test_list_devices_and_topology(client, db_session):
    r1 = Device(name="core-sw-01", ip_address="10.0.0.1",
                device_type=DeviceType.SWITCH, health=HealthState.UP)
    r2 = Device(name="host-01", ip_address="10.0.0.50",
                device_type=DeviceType.HOST, health=HealthState.UP)
    db_session.add_all([r1, r2])
    await db_session.commit()
    db_session.add(Link(source_device_id=r1.id, target_device_id=r2.id, protocol="arp"))
    await db_session.commit()

    resp = await client.get("/api/v1/devices")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get("/api/v1/topology")
    body = resp.json()
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1
    assert body["edges"][0]["protocol"] == "arp"


async def test_device_detail_404(client):
    resp = await client.get("/api/v1/devices/999")
    assert resp.status_code == 404


# ── twin intelligence endpoints ────────────────────────────────────

async def _seed_campus(db_session):
    """core — dist — host chain, like the simulator's hierarchy."""
    core = Device(name="core-rtr-01", ip_address="10.0.0.1",
                  device_type=DeviceType.ROUTER, health=HealthState.UP)
    dist = Device(name="dist-sw-01", ip_address="10.0.1.1",
                  device_type=DeviceType.SWITCH, health=HealthState.UP)
    acc = Device(name="acc-sw-01", ip_address="10.0.101.1",
                 device_type=DeviceType.SWITCH, health=HealthState.UP)
    host = Device(name="host-011", ip_address="10.0.101.11",
                  device_type=DeviceType.HOST, health=HealthState.UP)
    db_session.add_all([core, dist, acc, host])
    await db_session.commit()
    db_session.add_all([
        Link(source_device_id=core.id, target_device_id=dist.id, protocol="lldp"),
        Link(source_device_id=dist.id, target_device_id=acc.id, protocol="lldp"),
        Link(source_device_id=acc.id, target_device_id=host.id, protocol="arp"),
    ])
    await db_session.commit()
    return core, dist, acc, host


async def test_whatif_access_switch_cuts_host(client, db_session):
    core, _dist, acc, host = await _seed_campus(db_session)

    resp = await client.post(f"/api/v1/analysis/whatif/{acc.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed_device"]["id"] == acc.id
    assert [d["id"] for d in body["isolated"]] == [host.id]
    assert [d["id"] for d in body["degraded"]] == [_dist.id]
    assert body["impacted_count"] == 2
    assert len(body["affected_links"]) == 2


async def test_whatif_unknown_device_404(client, db_session):
    await _seed_campus(db_session)
    resp = await client.post("/api/v1/analysis/whatif/999")
    assert resp.status_code == 404


async def test_rca_points_at_down_uplink(client, db_session):
    """host down with its access switch down → RCA names the switch."""
    core, _dist, acc, host = await _seed_campus(db_session)
    acc.health = HealthState.DOWN
    host.health = HealthState.DOWN
    await db_session.commit()

    resp = await client.get(f"/api/v1/analysis/rca/{host.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symptom"]["id"] == host.id
    assert body["hypotheses"], "no hypotheses for a down host"
    assert body["hypotheses"][0]["device"]["id"] == acc.id
    assert any("only uplink" in r for r in body["hypotheses"][0]["reasons"])


async def test_rca_uses_active_alerts_as_evidence(client, db_session):
    core, dist, acc, host = await _seed_campus(db_session)
    acc.health = HealthState.DOWN
    host.health = HealthState.DOWN
    alert = Alert(
        device_id=dist.id, rule="node_down", severity=AlertSeverity.CRITICAL,
        status=AlertStatus.ACTIVE, message="dist-sw-01 is DOWN",
    )
    db_session.add(alert)
    await db_session.commit()

    resp = await client.get(f"/api/v1/analysis/rca/{host.id}")
    body = resp.json()
    top = body["hypotheses"][0]
    # dist carries a critical alert + disconnects host → outranks acc
    assert top["device"]["id"] == dist.id
    assert top["evidence"][0]["alert_id"] == alert.id


async def test_rca_unknown_device_404(client, db_session):
    await _seed_campus(db_session)
    resp = await client.get("/api/v1/analysis/rca/999")
    assert resp.status_code == 404


async def test_trace_path_found_and_404(client, db_session):
    core, _dist, acc, host = await _seed_campus(db_session)

    resp = await client.get(f"/api/v1/topology/path?from={core.id}&to={host.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["hops"] == 3
    assert body["device_ids"] == [core.id, _dist.id, acc.id, host.id]
    assert len(body["link_ids"]) == 3

    resp = await client.get("/api/v1/topology/path?from=999&to=1")
    assert resp.status_code == 404


async def test_overview_counts(client, db_session):
    core, _dist, acc, host = await _seed_campus(db_session)
    host.health = HealthState.DOWN
    await db_session.commit()

    resp = await client.get("/api/v1/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_devices"] == 4
    assert body["up"] == 3
    assert body["down"] == 1
    assert body["total_links"] == 3
    assert body["active_alerts"] == 0


# ── link traffic endpoint ──────────────────────────────────────────

async def test_link_traffic_series(client, db_session):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db.models import Interface, MetricSample

    core, dist, _acc, _host = await _seed_campus(db_session)
    if_core = Interface(device_id=core.id, if_index=1, name="Gi0/0/0", oper_status="up")
    if_dist = Interface(device_id=dist.id, if_index=1, name="Gi1/0/24", oper_status="up")
    db_session.add_all([if_core, if_dist])
    await db_session.commit()
    # reuse the core↔dist link the seeder already created
    link = (await db_session.scalars(
        select(Link).where(Link.source_device_id == core.id, Link.target_device_id == dist.id)
    )).one()
    link.source_interface_id = if_core.id
    link.target_interface_id = if_dist.id
    await db_session.commit()

    base = datetime.now(UTC).replace(tzinfo=None)
    for i in range(3):
        ts = base + timedelta(seconds=i)
        db_session.add_all([
            MetricSample(device_id=core.id, interface_id=if_core.id,
                         metric_name="if_out_bps", value=100.0 + i, timestamp=ts),
            MetricSample(device_id=dist.id, interface_id=if_dist.id,
                         metric_name="if_in_bps", value=80.0 + i, timestamp=ts),
        ])
    await db_session.commit()

    resp = await client.get(f"/api/v1/links/{link.id}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["link_id"] == link.id
    assert len(body["points"]) == 3
    assert body["points"][0]["in_bps"] == 80.0
    assert body["points"][0]["out_bps"] == 100.0

    resp = await client.get("/api/v1/links/999/metrics")
    assert resp.status_code == 404


async def test_health_report_pdf(client, db_session):
    from datetime import UTC, datetime

    from app.db.models import MetricSample

    core, _dist, _acc, _host = await _seed_campus(db_session)
    # exercise the top-talker section: needs at least one traffic sample
    db_session.add(MetricSample(device_id=core.id, interface_id=None,
                                metric_name="if_out_bps", value=42.0,
                                timestamp=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    resp = await client.get("/api/v1/reports/health.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.content
    assert body[:5] == b"%PDF-"  # real PDF magic
    assert body.rstrip().endswith(b"%%EOF")  # complete document
    assert len(body) > 1000  # non-trivial document


# ── topology history / time travel ─────────────────────────────────

async def test_snapshot_lifecycle_and_diff(client, db_session):
    from app.history.store import take_snapshot

    core, dist, acc, host = await _seed_campus(db_session)

    # snapshot 1: all healthy
    snap1 = await take_snapshot(db_session, trigger="manual")
    await db_session.commit()

    # evolve the network: dist goes down, host is removed, a new device joins
    dist.health = HealthState.DOWN
    await db_session.delete(host)
    newcomer = Device(name="host-new", ip_address="10.0.101.99",
                      device_type=DeviceType.HOST, health=HealthState.UP)
    db_session.add(newcomer)
    await db_session.commit()

    resp = await client.post("/api/v1/snapshots?trigger=manual")
    assert resp.status_code == 200
    snap2 = resp.json()
    assert snap2["trigger"] == "manual"
    assert snap2["node_count"] == 4

    # diff past → live
    resp = await client.get(f"/api/v1/snapshots/{snap1.id}/diff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["added_nodes"] == 1
    assert body["summary"]["removed_nodes"] == 1
    assert body["summary"]["health_changes"] == 1
    assert body["added_nodes"][0]["name"] == "host-new"
    assert body["removed_nodes"][0]["name"] == "host-011"
    assert body["health_changes"][0][0] == "dist-sw-01"
    assert body["health_changes"][0][2] == "up"
    assert body["health_changes"][0][3] == "down"

    # diff snapshot → snapshot
    resp = await client.get(f"/api/v1/snapshots/{snap1.id}/diff?against={snap2['id']}")
    assert resp.status_code == 200
    body2 = resp.json()
    assert body2["snapshot_id"] == snap2["id"]
    assert body2["summary"] == body["summary"]

    # timeline lists both
    resp = await client.get("/api/v1/snapshots")
    ids = [s["id"] for s in resp.json()]
    assert snap1.id in ids and snap2["id"] in ids

    # full graph fetch
    resp = await client.get(f"/api/v1/snapshots/{snap1.id}")
    assert resp.status_code == 200
    assert len(resp.json()["graph"]["nodes"]) == 4

    # unknown snapshot → 404
    resp = await client.get("/api/v1/snapshots/999/diff")
    assert resp.status_code == 404
    resp = await client.get("/api/v1/snapshots/999")
    assert resp.status_code == 404


async def test_snapshot_diff_against_bad_value_400(client, db_session):
    await _seed_campus(db_session)
    from app.history.store import take_snapshot

    snap = await take_snapshot(db_session, trigger="manual")
    await db_session.commit()
    resp = await client.get(f"/api/v1/snapshots/{snap.id}/diff?against=notanid")
    assert resp.status_code == 400

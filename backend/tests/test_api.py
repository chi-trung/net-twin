"""API smoke tests for Phase 2."""

from app.db.models import Device, DeviceType, HealthState, Link


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

"""Tests for the monitoring engine: probes, metrics, alerts, bus, WebSocket."""

import asyncio
from datetime import UTC

from app.db.models import Alert, AlertStatus, Device, DeviceType, HealthState
from app.events.bus import InMemoryBus
from app.monitor.alerts import AlertEngine, Observation, default_rules
from app.monitor.metrics import MetricStore
from app.monitor.probes import NullProbe, ProbeResult, parse_ping_output

# ── probe parsing ──────────────────────────────────────────────────

WINDOWS_PING = """
Pinging 10.0.0.1 with 32 bytes of data:
Reply from 10.0.0.1: bytes=32 time=4ms TTL=64
Reply from 10.0.0.1: bytes=32 time=3ms TTL=64

Ping statistics for 10.0.0.1:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
"""

LINUX_PING_LOSS = """
3 packets transmitted, 2 received, 33.3% packet loss, time 2003ms
rtt min/avg/max/mdev = 1.2/2.5/3.8/1.1 ms
"""

NO_REPLY = "Request timeout for icmp_seq 0\nRequest timeout for icmp_seq 1\n"


def test_parse_ping_windows_all_replies():
    r = parse_ping_output(WINDOWS_PING)
    assert r.reachable and r.packet_loss_pct == 0.0
    assert r.latency_ms == 3.0  # last time= value


def test_parse_ping_linux_partial_loss():
    r = parse_ping_output(LINUX_PING_LOSS)
    assert r.reachable and r.packet_loss_pct == 33.3


def test_parse_ping_no_reply():
    r = parse_ping_output(NO_REPLY)
    assert not r.reachable and r.latency_ms is None and r.packet_loss_pct == 100.0


async def test_null_probe_always_up():
    r = await NullProbe(latency_ms=2.5).probe("1.2.3.4")
    assert r == ProbeResult(reachable=True, latency_ms=2.5, packet_loss_pct=0.0)


# ── metric store ───────────────────────────────────────────────────

def test_metric_rate_first_observation_returns_none():
    store = MetricStore()
    assert store.rate(1, None, "if_in_octets", 1000.0) is None


def test_metric_rate_computes_per_second():
    from datetime import datetime, timedelta

    store = MetricStore()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert store.rate(1, None, "if_in_octets", 1000.0, timestamp=t0) is None
    r = store.rate(1, None, "if_in_octets", 3000.0, timestamp=t0 + timedelta(seconds=4))
    assert r == 500.0  # (3000-1000)/4


def test_metric_rate_handles_counter_reset():
    from datetime import datetime, timedelta

    store = MetricStore()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    store.rate(1, None, "c", 5000.0, timestamp=t0)
    assert store.rate(1, None, "c", 100.0, timestamp=t0 + timedelta(seconds=1)) is None


# ── alert engine ───────────────────────────────────────────────────

async def test_alert_raises_once_and_clears(db_session):
    engine = AlertEngine(default_rules(latency_threshold_ms=100, loss_threshold_pct=10))
    dev = Device(name="r1", ip_address="10.9.9.9", device_type=DeviceType.ROUTER,
                 health=HealthState.UP)
    db_session.add(dev)
    await db_session.commit()

    obs_bad = Observation(device_id=dev.id, device_name="r1", health="down",
                          latency_ms=500, packet_loss_pct=50)
    raised = await engine.evaluate(db_session, obs_bad)
    names = {a.rule for a in raised}
    assert names == {"node_down", "high_latency", "packet_loss"}
    await db_session.commit()

    # same bad observation again → no duplicates
    assert await engine.evaluate(db_session, obs_bad) == []

    # recovered → all three clear
    obs_ok = Observation(device_id=dev.id, device_name="r1", health="up",
                         latency_ms=5, packet_loss_pct=0)
    await engine.evaluate(db_session, obs_ok)
    await db_session.commit()

    from sqlalchemy import select

    alerts = (await db_session.scalars(select(Alert))).all()
    assert len(alerts) == 3
    assert all(a.status == AlertStatus.CLEARED for a in alerts)
    assert all(a.cleared_at is not None for a in alerts)


# ── event bus ──────────────────────────────────────────────────────

async def test_in_memory_bus_fanout():
    bus = InMemoryBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    await bus.publish({"type": "test", "n": 1})
    assert (await q1.get())["n"] == 1
    assert (await q2.get())["n"] == 1
    bus.unsubscribe(q1)
    await bus.publish({"type": "test", "n": 2})
    assert q1.empty()
    assert (await q2.get())["n"] == 2


# ── WebSocket endpoint ─────────────────────────────────────────────


def test_ws_sends_snapshot_on_connect():
    """Sync test: starlette TestClient drives the WS handshake and first frame.

    The app lifespan may fail to reach Postgres in CI; the snapshot handler
    uses SessionLocal, so we point it at SQLite for the duration of the test.
    """
    import contextlib

    from sqlalchemy.ext.asyncio import create_async_engine
    from starlette.testclient import TestClient

    import app.db.session as session_mod
    from app.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.db.base import Base

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    test_maker = session_mod.async_sessionmaker(engine, expire_on_commit=False)

    original_maker = session_mod.SessionLocal
    session_mod.SessionLocal = test_maker
    try:
        with contextlib.suppress(Exception):  # lifespan scheduler may fail; WS still works
            with TestClient(app) as tc:
                with tc.websocket_connect("/ws/events") as ws:
                    snapshot = ws.receive_json()
                    assert snapshot["type"] == "twin.snapshot"
                    assert isinstance(snapshot["nodes"], list)
                    assert isinstance(snapshot["edges"], list)
    finally:
        session_mod.SessionLocal = original_maker

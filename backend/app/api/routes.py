"""REST API routes: health, topology, devices, metrics, alerts, analysis."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis.graph import blast_radius, build_adjacency, shortest_path
from app.core.outages import get_outages
from app.db.models import Alert, AlertStatus, Device, DeviceType, HealthState, Link, MetricSample
from app.db.session import get_session

from .schemas import (
    AlertOut,
    DeviceDetail,
    DeviceOut,
    LinkOut,
    MetricPoint,
    MetricSeriesOut,
    OverviewOut,
    PathOut,
    TopologyOut,
    WhatIfOut,
)

router = APIRouter(prefix="/api/v1")


@router.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "net-twin-backend"}


@router.get("/topology", response_model=TopologyOut, tags=["topology"])
async def get_topology(db: AsyncSession = Depends(get_session)) -> TopologyOut:
    """Return the full twin graph: all nodes and edges."""
    nodes = (await db.scalars(select(Device).order_by(Device.id))).all()
    edges = (await db.scalars(select(Link).order_by(Link.id))).all()
    return TopologyOut(
        nodes=[DeviceOut.model_validate(n) for n in nodes],
        edges=[LinkOut.model_validate(e) for e in edges],
    )


@router.get("/devices", response_model=list[DeviceOut], tags=["topology"])
async def list_devices(
    db: AsyncSession = Depends(get_session),
    device_type: str | None = Query(default=None),
    health: str | None = Query(default=None),
) -> list[DeviceOut]:
    stmt = select(Device).order_by(Device.id)
    if device_type:
        stmt = stmt.where(Device.device_type == device_type)
    if health:
        stmt = stmt.where(Device.health == health)
    return [DeviceOut.model_validate(d) for d in (await db.scalars(stmt)).all()]


@router.get("/devices/{device_id}", response_model=DeviceDetail, tags=["topology"])
async def get_device(device_id: int, db: AsyncSession = Depends(get_session)) -> DeviceDetail:
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    stmt = select(Device).options(selectinload(Device.interfaces)).where(Device.id == device_id)
    device = (await db.scalars(stmt)).one()
    return DeviceDetail.model_validate(device)


@router.get("/devices/{device_id}/metrics", response_model=MetricSeriesOut, tags=["metrics"])
async def get_device_metrics(
    device_id: int,
    db: AsyncSession = Depends(get_session),
    metric: str = Query(default="latency_ms"),
    limit: int = Query(default=500, le=5000),
) -> MetricSeriesOut:
    """Return recent time-series points for one metric of one device."""
    stmt = (
        select(MetricSample)
        .where(MetricSample.device_id == device_id, MetricSample.metric_name == metric)
        .order_by(MetricSample.timestamp.desc())
        .limit(limit)
    )
    rows = list(reversed((await db.scalars(stmt)).all()))
    return MetricSeriesOut(
        device_id=device_id,
        metric_name=metric,
        points=[MetricPoint(timestamp=r.timestamp, value=r.value) for r in rows],
    )


@router.get("/alerts", response_model=list[AlertOut], tags=["alerts"])
async def list_alerts(
    db: AsyncSession = Depends(get_session),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Alert.status == status)
    return [AlertOut.model_validate(a) for a in (await db.scalars(stmt)).all()]


@router.post("/discovery/run", tags=["system"])
async def trigger_discovery(db: AsyncSession = Depends(get_session)) -> dict:
    """Run one discovery cycle immediately (the scheduler also runs it on a timer)."""
    from app.discovery.engine import run_discovery

    report = await run_discovery(db)
    return {
        "devices_created": report.devices_created,
        "devices_updated": report.devices_updated,
        "devices_staled": report.devices_staled,
        "links_created": report.links_created,
        "changed": report.changed,
    }


@router.post("/monitor/run", tags=["system"])
async def trigger_monitor() -> dict:
    """Run one monitoring cycle immediately using the live scheduler."""
    from app.monitor.scheduler import get_scheduler

    scheduler = get_scheduler()
    if scheduler is None:
        raise HTTPException(status_code=503, detail="scheduler not running")
    await scheduler.run_monitor_cycle()
    return {"status": "ok"}

# ── demo / chaos controls (simulator mode) ─────────────────────────

class OutageIn(BaseModel):
    ip_address: str

@router.get("/sim/outages", tags=["simulation"])
async def list_outages() -> dict:
    """IPs currently hidden by the simulated-outage registry."""
    return {"outages": get_outages().list()}

@router.post("/sim/outages", tags=["simulation"])
async def create_outage(body: OutageIn) -> dict:
    """Simulate a device failure: the next discovery/monitor cycles will mark
    it DOWN, emit device.health_changed and raise a node_down alert."""
    get_outages().add(body.ip_address)
    return {"outages": get_outages().list()}

@router.delete("/sim/outages/{ip_address}", tags=["simulation"])
async def clear_outage(ip_address: str) -> dict:
    """Heal a simulated failure; the twin recovers on its own next cycles."""
    cleared = get_outages().remove(ip_address)
    if not cleared:
        raise HTTPException(status_code=404, detail=f"no outage for {ip_address}")
    return {"outages": get_outages().list()}


# ── twin intelligence ──────────────────────────────────────────────

async def _load_graph(db: AsyncSession) -> tuple[dict[int, Device], list[tuple[int, int]]]:
    """Fetch devices + edges and build plain adjacency for the analysis engine."""
    devices = {d.id: d for d in (await db.scalars(select(Device))).all()}
    edges = [
        (lnk.source_device_id, lnk.target_device_id)
        for lnk in (await db.scalars(select(Link))).all()
    ]
    adjacency = build_adjacency(list(devices), edges)
    return devices, adjacency


def _root_id(devices: dict[int, Device]) -> int | None:
    """The twin root is the first router found (core of the campus model)."""
    for d in devices.values():
        if d.device_type == DeviceType.ROUTER:
            return d.id
    return None


@router.post("/analysis/whatif/{device_id}", response_model=WhatIfOut, tags=["analysis"])
async def whatif_failure(device_id: int, db: AsyncSession = Depends(get_session)) -> WhatIfOut:
    """What-if analysis: simulate losing one device and report the impact
    (isolated, degraded devices) computed on the current twin graph."""
    devices, adjacency = await _load_graph(db)
    device = devices.get(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")

    radius = blast_radius(adjacency, failed_id=device_id, root_id=_root_id(devices))
    affected_link_ids = {frozenset(pair) for pair in radius.affected_links}
    affected = [
        lnk
        for lnk in (await db.scalars(select(Link))).all()
        if frozenset((lnk.source_device_id, lnk.target_device_id)) in affected_link_ids
    ]
    return WhatIfOut(
        failed_device=DeviceOut.model_validate(device),
        isolated=[DeviceOut.model_validate(devices[i]) for i in radius.isolated_ids],
        degraded=[DeviceOut.model_validate(devices[i]) for i in radius.degraded_ids],
        affected_links=[LinkOut.model_validate(lnk) for lnk in affected],
        impacted_count=radius.impacted_count,
    )


@router.get("/topology/path", response_model=PathOut, tags=["analysis"])
async def trace_path(
    from_id: int = Query(alias="from"),
    to_id: int = Query(alias="to"),
    db: AsyncSession = Depends(get_session),
) -> PathOut:
    """Shortest path between two devices on the twin graph."""
    devices, adjacency = await _load_graph(db)
    for dev_id in (from_id, to_id):
        if dev_id not in devices:
            raise HTTPException(status_code=404, detail=f"device {dev_id} not found")

    path_ids = shortest_path(adjacency, from_id, to_id) or []
    links = {
        frozenset((lnk.source_device_id, lnk.target_device_id)): lnk
        for lnk in (await db.scalars(select(Link))).all()
    }
    link_ids: list[int] = []
    for a, b in zip(path_ids, path_ids[1:], strict=False):
        lnk = links.get(frozenset((a, b)))
        if lnk is not None:
            link_ids.append(lnk.id)
    return PathOut(
        found=bool(path_ids),
        hops=max(len(path_ids) - 1, 0),
        device_ids=path_ids,
        devices=[DeviceOut.model_validate(devices[i]) for i in path_ids],
        link_ids=link_ids,
    )


@router.get("/overview", response_model=OverviewOut, tags=["analysis"])
async def overview(db: AsyncSession = Depends(get_session)) -> OverviewOut:
    """Fleet KPIs for the dashboard header: health counts, alerts, latency."""
    devices = (await db.scalars(select(Device))).all()
    by_health = {h: 0 for h in HealthState}
    for d in devices:
        by_health[d.health] += 1

    alerts = (await db.scalars(select(Alert).where(Alert.status == AlertStatus.ACTIVE))).all()
    # avg latency over the most recent samples (portable across PG/SQLite)
    vals = [
        r[0]
        for r in (
            await db.execute(
                select(MetricSample.value)
                .where(MetricSample.metric_name == "latency_ms")
                .order_by(MetricSample.timestamp.desc())
                .limit(200)
            )
        ).all()
    ]
    avg_latency = sum(vals) / len(vals) if vals else None

    return OverviewOut(
        total_devices=len(devices),
        up=by_health[HealthState.UP],
        down=by_health[HealthState.DOWN],
        degraded=by_health[HealthState.DEGRADED],
        unknown=by_health[HealthState.UNKNOWN],
        total_links=(await db.scalar(select(func.count(Link.id))) or 0),
        active_alerts=len(alerts),
        critical_alerts=sum(1 for a in alerts if a.severity.value == "critical"),
        avg_latency_ms=avg_latency,
        healthiest_updated_at=max((d.updated_at for d in devices), default=None),
    )

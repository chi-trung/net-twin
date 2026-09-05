"""REST API routes: health, topology, devices, metrics, alerts, analysis."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis.graph import blast_radius, build_adjacency, shortest_path
from app.core.outages import get_outages
from app.db.models import (
    Alert,
    AlertStatus,
    Device,
    DeviceType,
    HealthState,
    Interface,
    Link,
    MetricSample,
    Snapshot,
)
from app.db.session import get_session

from .schemas import (
    AlertOut,
    DeviceDetail,
    DeviceOut,
    LinkOut,
    LinkTrafficOut,
    MetricPoint,
    MetricSeriesOut,
    OverviewOut,
    PathOut,
    RcaDeviceOut,
    RcaEvidenceOut,
    RcaHypothesisOut,
    RcaOut,
    SnapshotDiffCounts,
    SnapshotDiffOut,
    SnapshotNodeOut,
    SnapshotEdgeOut,
    SnapshotOut,
    SnapshotSummaryOut,
    TopologyOut,
    TrafficPoint,
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


@router.get("/links/{link_id}/metrics", response_model=LinkTrafficOut, tags=["metrics"])
async def get_link_traffic(
    link_id: int,
    db: AsyncSession = Depends(get_session),
    limit: int = Query(default=500, le=5000),
) -> LinkTrafficOut:
    """Recent in/out throughput (bps) for one link, both directions merged
    into one series keyed by timestamp."""
    lnk = await db.get(Link, link_id)
    if lnk is None:
        raise HTTPException(status_code=404, detail=f"link {link_id} not found")

    stmt = (
        select(MetricSample)
        .where(
            MetricSample.interface_id.in_(
                select(Interface.id)
                .where(Interface.id.in_(_link_interface_ids(lnk)))
            ),
            MetricSample.metric_name.in_(["if_in_bps", "if_out_bps"]),
        )
        .order_by(MetricSample.timestamp.desc())
        .limit(limit)
    )
    rows = list(reversed((await db.scalars(stmt)).all()))

    by_ts: dict[datetime, dict[str, float | None]] = {}
    for r in rows:
        by_ts.setdefault(r.timestamp, {})[r.metric_name] = r.value
    points = [
        TrafficPoint(timestamp=ts, in_bps=v.get("if_in_bps"), out_bps=v.get("if_out_bps"))
        for ts, v in sorted(by_ts.items())
    ]
    return LinkTrafficOut(link_id=link_id, points=points)


def _link_interface_ids(lnk: Link) -> list[int]:
    ids = [lnk.source_interface_id, lnk.target_interface_id]
    return [i for i in ids if i is not None]


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


@router.get("/reports/health.pdf", tags=["reports"])
async def health_report(db: AsyncSession = Depends(get_session)) -> Response:
    """Render the current twin state as a downloadable PDF health report."""
    from app.reports.generator import generate_health_report

    pdf_bytes = await generate_health_report(db)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="net-twin-health-{stamp}.pdf"'},
    )


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


@router.get("/analysis/rca/{device_id}", response_model=RcaOut, tags=["analysis"])
async def root_cause_analysis(
    device_id: int, db: AsyncSession = Depends(get_session)
) -> RcaOut:
    """Ranked root-cause hypotheses for a symptom device, from live alerts
    and the twin topology."""
    from app.analysis.rca import AlertFact, DeviceFacts, analyze

    devices, _adjacency = await _load_graph(db)
    device = devices.get(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")

    link_pairs = [
        (lnk.source_device_id, lnk.target_device_id)
        for lnk in (await db.scalars(select(Link))).all()
    ]

    root_id = _root_id(devices)
    facts = {
        d.id: DeviceFacts(
            id=d.id, name=d.name, health=d.health.value, device_type=d.device_type.value
        )
        for d in devices.values()
    }
    active = (await db.scalars(select(Alert).where(Alert.status == AlertStatus.ACTIVE))).all()
    alert_facts = [
        AlertFact(
            id=a.id, device_id=a.device_id, rule=a.rule,
            severity=a.severity.value, message=a.message,
        )
        for a in active
    ]

    hypotheses = analyze(
        device_id, facts, link_pairs, root_id, alert_facts
    )
    return RcaOut(
        symptom=RcaDeviceOut(
            id=device.id, name=device.name, health=device.health.value,
            device_type=device.device_type.value,
        ),
        hypotheses=[
            RcaHypothesisOut(
                device=RcaDeviceOut(
                    id=h.device.id, name=h.device.name, health=h.device.health,
                    device_type=h.device.device_type,
                ),
                score=h.score,
                headline=h.headline,
                reasons=h.reasons,
                evidence=[
                    RcaEvidenceOut(
                        alert_id=e.id, rule=e.rule, severity=e.severity, message=e.message
                    )
                    for e in h.evidence_alerts
                ],
            )
            for h in hypotheses
        ],
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


# ── topology history / time travel ─────────────────────────────────

MAX_SNAPSHOT_LIMIT = 500


@router.get("/snapshots", response_model=list[SnapshotSummaryOut], tags=["history"])
async def list_snapshots(
    db: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, le=MAX_SNAPSHOT_LIMIT),
) -> list[SnapshotSummaryOut]:
    """Timeline of stored twin-graph snapshots, newest first."""
    rows = (await db.scalars(select(Snapshot).order_by(Snapshot.taken_at.desc()).limit(limit))).all()
    out: list[SnapshotSummaryOut] = []
    for s in rows:
        graph = s.graph or {}
        out.append(
            SnapshotSummaryOut(
                id=s.id,
                taken_at=s.taken_at,
                trigger=s.trigger,
                node_count=len(graph.get("nodes", [])),
                edge_count=len(graph.get("edges", [])),
            )
        )
    return out


@router.post("/snapshots", response_model=SnapshotSummaryOut, tags=["history"])
async def create_snapshot(
    db: AsyncSession = Depends(get_session),
    trigger: str = Query(default="manual"),
) -> SnapshotSummaryOut:
    """Capture a snapshot of the current twin graph right now."""
    from app.history.store import take_snapshot

    snap = await take_snapshot(db, trigger=trigger)
    await db.commit()
    graph = snap.graph or {}
    return SnapshotSummaryOut(
        id=snap.id,
        taken_at=snap.taken_at,
        trigger=snap.trigger,
        node_count=len(graph.get("nodes", [])),
        edge_count=len(graph.get("edges", [])),
    )


@router.get("/snapshots/{snapshot_id}/diff", response_model=SnapshotDiffOut, tags=["history"])
async def snapshot_diff(
    snapshot_id: int,
    db: AsyncSession = Depends(get_session),
    against: str = Query(default="live"),
) -> SnapshotDiffOut:
    """Diff a past snapshot against the live graph (default) or another
    snapshot id (`against=<id>`) — what changed between "then" and "now"."""
    from app.history.store import capture_graph, diff_graphs

    snap = await db.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"snapshot {snapshot_id} not found")

    if against == "live":
        devices = (await db.scalars(select(Device))).all()
        links = (await db.scalars(select(Link))).all()
        new_graph = capture_graph(list(devices), list(links))
        new_id = 0  # 0 = live (not a stored snapshot)
    else:
        try:
            other_id = int(against)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"bad against value {against!r}")
        other = await db.get(Snapshot, other_id)
        if other is None:
            raise HTTPException(status_code=404, detail=f"snapshot {other_id} not found")
        new_graph = other.graph or {}
        new_id = other.id

    diff = diff_graphs(snap.graph or {}, new_graph)
    name_by_id = {n["id"]: n for n in (snap.graph or {}).get("nodes", [])}
    if against != "live":
        name_by_id.update({n["id"]: n for n in new_graph.get("nodes", [])})

    return SnapshotDiffOut(
        snapshot_id=new_id,
        summary=SnapshotDiffCounts(
            added_nodes=len(diff.added_nodes),
            removed_nodes=len(diff.removed_nodes),
            added_edges=len(diff.added_edges),
            removed_edges=len(diff.removed_edges),
            health_changes=len(diff.health_changes),
        ),
        added_nodes=[SnapshotNodeOut.model_validate(n) for n in diff.added_nodes],
        removed_nodes=[SnapshotNodeOut.model_validate(n) for n in diff.removed_nodes],
        added_edges=[SnapshotEdgeOut.model_validate(e) for e in diff.added_edges],
        removed_edges=[SnapshotEdgeOut.model_validate(e) for e in diff.removed_edges],
        health_changes=[
            (
                name_by_id.get(nid, {}).get("name", f"device {nid}"),
                name_by_id.get(nid, {}).get("ip_address", "?"),
                old_h,
                new_h,
            )
            for nid, (old_h, new_h) in diff.health_changes.items()
        ],
    )


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut, tags=["history"])
async def get_snapshot(
    snapshot_id: int, db: AsyncSession = Depends(get_session)
) -> SnapshotOut:
    """Full graph captured in one snapshot (for rendering the past topology)."""
    snap = await db.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"snapshot {snapshot_id} not found")
    return SnapshotOut.model_validate(snap)


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

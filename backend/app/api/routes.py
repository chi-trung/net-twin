"""REST API routes: health, topology, devices, metrics, alerts."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Alert, Device, Link, MetricSample
from app.db.session import get_session

from .schemas import AlertOut, DeviceDetail, DeviceOut, LinkOut, MetricPoint, MetricSeriesOut, TopologyOut

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

"""Pydantic schemas for the REST API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import AlertSeverity, AlertStatus, DeviceType, HealthState


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InterfaceOut(ORMModel):
    id: int
    if_index: int
    name: str
    mac_address: str | None = None
    ip_address: str | None = None
    admin_status: str
    oper_status: str
    speed_mbps: int | None = None


class DeviceOut(ORMModel):
    id: int
    name: str
    ip_address: str
    mac_address: str | None = None
    device_type: DeviceType
    health: HealthState
    sys_description: str | None = None
    source: str
    created_at: datetime
    updated_at: datetime


class DeviceDetail(DeviceOut):
    interfaces: list[InterfaceOut] = []


class LinkOut(ORMModel):
    id: int
    source_device_id: int
    target_device_id: int
    source_interface_id: int | None = None
    target_interface_id: int | None = None
    protocol: str
    health: HealthState


class TopologyOut(BaseModel):
    nodes: list[DeviceOut]
    edges: list[LinkOut]

# ── twin intelligence ──────────────────────────────────────────────

class WhatIfOut(BaseModel):
    failed_device: DeviceOut
    isolated: list[DeviceOut]  # cut off from the root after the failure
    degraded: list[DeviceOut]  # reachable but lost a direct link
    affected_links: list[LinkOut]
    impacted_count: int

class PathOut(BaseModel):
    found: bool
    hops: int
    device_ids: list[int]
    devices: list[DeviceOut]
    link_ids: list[int]

class OverviewOut(BaseModel):
    total_devices: int
    up: int
    down: int
    degraded: int
    unknown: int
    total_links: int
    active_alerts: int
    critical_alerts: int
    avg_latency_ms: float | None
    healthiest_updated_at: datetime | None = None

# ── link traffic ───────────────────────────────────────────────────

class TrafficPoint(BaseModel):
    timestamp: datetime
    in_bps: float | None = None
    out_bps: float | None = None

class LinkTrafficOut(BaseModel):
    link_id: int
    points: list[TrafficPoint]


class MetricPoint(BaseModel):
    timestamp: datetime
    value: float


class MetricSeriesOut(BaseModel):
    device_id: int
    metric_name: str
    points: list[MetricPoint]


class AlertOut(ORMModel):
    id: int
    device_id: int | None
    rule: str
    severity: AlertSeverity
    status: AlertStatus
    message: str
    value: float | None = None
    threshold: float | None = None
    created_at: datetime
    cleared_at: datetime | None = None

"""Twin store ORM models.

The twin is a graph: Device (node) + Link (edge), with Interfaces as sub-nodes
of devices. MetricSample is the time-series table; Alert and Snapshot capture
events and point-in-time copies of the twin.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow


class DeviceType(str, enum.Enum):
    ROUTER = "router"
    SWITCH = "switch"
    HOST = "host"
    FIREWALL = "firewall"
    AP = "access_point"
    UNKNOWN = "unknown"


class HealthState(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class Device(Base, TimestampMixin):
    """A network node in the twin (router, switch, host, ...)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    mac_address: Mapped[str | None] = mapped_column(String(32))
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, native_enum=False, length=32), default=DeviceType.UNKNOWN
    )
    health: Mapped[HealthState] = mapped_column(
        Enum(HealthState, native_enum=False, length=32), default=HealthState.UNKNOWN
    )
    sys_description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="discovery")
    extra: Mapped[dict | None] = mapped_column(JSON)

    interfaces: Mapped[list["Interface"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class Interface(Base, TimestampMixin):
    """A network interface belonging to a device."""

    __tablename__ = "interfaces"
    __table_args__ = (UniqueConstraint("device_id", "if_index", name="uq_iface_device_ifindex"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    if_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(32))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    admin_status: Mapped[str] = mapped_column(String(16), default="up")
    oper_status: Mapped[str] = mapped_column(String(16), default="unknown")
    speed_mbps: Mapped[int | None] = mapped_column(Integer)

    device: Mapped[Device] = relationship(back_populates="interfaces")


class Link(Base, TimestampMixin):
    """An edge in the twin: a physical/logical link between two devices."""

    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint("source_device_id", "target_device_id", name="uq_link_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    target_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    source_interface_id: Mapped[int | None] = mapped_column(
        ForeignKey("interfaces.id", ondelete="SET NULL")
    )
    target_interface_id: Mapped[int | None] = mapped_column(
        ForeignKey("interfaces.id", ondelete="SET NULL")
    )
    protocol: Mapped[str] = mapped_column(String(32), default="inferred")  # lldp|cdp|arp|manual
    health: Mapped[HealthState] = mapped_column(
        Enum(HealthState, native_enum=False, length=32), default=HealthState.UNKNOWN
    )

    source_device: Mapped[Device] = relationship(foreign_keys=[source_device_id])
    target_device: Mapped[Device] = relationship(foreign_keys=[target_device_id])


class MetricSample(Base):
    """Time-series metric point.

    metric examples: latency_ms, packet_loss_pct, if_in_octets_rate,
    if_out_octets_rate, cpu_pct, mem_pct.
    """

    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_metric_device_ts", "device_id", "timestamp"),
        Index("ix_metric_name_ts", "metric_name", "timestamp"),
    )

    # BigInteger gives 64-bit ids on Postgres; SQLite only auto-increments INTEGER,
    # so tests/dev fall back to a plain Integer via the variant.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    interface_id: Mapped[int | None] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE")
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    CLEARED = "cleared"


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, native_enum=False, length=16), default=AlertSeverity.WARNING
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, native_enum=False, length=16), default=AlertStatus.ACTIVE
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    """Point-in-time copy of the twin graph (nodes + edges + health)."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual|discovery|scheduled
    graph: Mapped[dict] = mapped_column(JSON, nullable=False)

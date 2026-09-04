"""Monitoring scheduler: the heartbeat of the digital twin.

One asyncio task per concern, started from the FastAPI lifespan:

- monitor_loop: every `monitor_interval_seconds`, probe every known device,
  record latency/loss metrics, update health, run alert rules, publish
  `device.health_changed` / `metric.sample` events.
- discovery_loop: every `discovery_interval_seconds`, run a discovery cycle
  and publish `topology.updated` when the graph changed.

Loops are resilient: an exception in one round is logged, not fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.db.models import Device, HealthState
from app.db.session import SessionLocal
from app.discovery.engine import run_discovery
from app.events.bus import publish_event

from .alerts import AlertEngine, Observation, default_rules
from .metrics import MetricStore
from .probes import NullProbe, Probe, SystemPingProbe

logger = logging.getLogger(__name__)


class MonitorScheduler:
    def __init__(self, settings: Settings | None = None, probe: Probe | None = None) -> None:
        self.settings = settings or get_settings()
        self.probe = probe or (
            NullProbe() if self.settings.discovery_source == "simulator" else SystemPingProbe()
        )
        self.metrics = MetricStore()
        self.alerts = AlertEngine(
            default_rules(
                self.settings.alert_latency_threshold_ms,
                self.settings.alert_packet_loss_threshold_pct,
            )
        )
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    # ── lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._monitor_loop(), name="monitor-loop"),
            asyncio.create_task(self._discovery_loop(), name="discovery-loop"),
        ]
        logger.info(
            "scheduler started (monitor=%ss, discovery=%ss, source=%s)",
            self.settings.monitor_interval_seconds,
            self.settings.discovery_interval_seconds,
            self.settings.discovery_source,
        )

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    # ── loops ──────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_monitor_cycle()
            except Exception:  # noqa: BLE001 — one bad round must not kill the loop
                logger.exception("monitor cycle failed")
            await self._sleep(self.settings.monitor_interval_seconds)

    async def _discovery_loop(self) -> None:
        # first discovery runs immediately so the twin is never empty at boot
        while not self._stop.is_set():
            try:
                await self.run_discovery_cycle()
            except Exception:  # noqa: BLE001
                logger.exception("discovery cycle failed")
            await self._sleep(self.settings.discovery_interval_seconds)

    # ── one cycle (also callable from tests / API) ─────────────────

    async def run_discovery_cycle(self) -> None:
        async with SessionLocal() as db:
            report = await run_discovery(db)
            if report.changed:
                await publish_event(
                    "topology.updated",
                    devices_created=report.devices_created,
                    devices_staled=report.devices_staled,
                    links_created=report.links_created,
                )

    async def run_monitor_cycle(self) -> None:
        async with SessionLocal() as db:
            devices = (await db.scalars(select(Device))).all()
            now = utcnow()
            for device in devices:
                result = await self.probe.probe(device.ip_address)

                if result.latency_ms is not None:
                    await self.metrics.record(
                        db, device.id, "latency_ms", result.latency_ms, timestamp=now
                    )
                await self.metrics.record(
                    db, device.id, "packet_loss_pct", result.packet_loss_pct, timestamp=now
                )

                new_health = HealthState.UP if result.reachable else HealthState.DOWN
                if device.health != new_health:
                    device.health = new_health
                    await publish_event(
                        "device.health_changed",
                        device_id=device.id,
                        name=device.name,
                        health=new_health.value,
                    )

                await self.alerts.evaluate(
                    db,
                    Observation(
                        device_id=device.id,
                        device_name=device.name,
                        health=new_health.value,
                        latency_ms=result.latency_ms,
                        packet_loss_pct=result.packet_loss_pct,
                    ),
                )
            await db.commit()
            await publish_event(
                "metrics.flushed", devices=len(devices), timestamp=now.isoformat()
            )
            logger.debug("monitor cycle done for %d devices", len(devices))


# process-wide scheduler, wired in the app lifespan
_scheduler: MonitorScheduler | None = None


def get_scheduler() -> MonitorScheduler | None:
    return _scheduler


def start_scheduler(settings: Settings | None = None) -> MonitorScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MonitorScheduler(settings)
    _scheduler.start()
    return _scheduler


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None

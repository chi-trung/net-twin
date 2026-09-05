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
from app.db.models import Device, HealthState, Link, MetricSample
from app.db.session import SessionLocal
from app.discovery.engine import run_discovery
from app.events.bus import publish_event

from .alerts import AlertEngine, Observation, default_rules
from .anomaly import MetricAnomalyDetector
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
        self.anomalies = (
            MetricAnomalyDetector(
                min_samples=self.settings.anomaly_min_samples,
                z_threshold=self.settings.anomaly_z_threshold,
            )
            if self.settings.anomaly_detection_enabled
            else None
        )
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    # ── lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._startup_adopt(), name="alert-adopt"),
            asyncio.create_task(self._monitor_loop(), name="monitor-loop"),
            asyncio.create_task(self._discovery_loop(), name="discovery-loop"),
        ]
        logger.info(
            "scheduler started (monitor=%ss, discovery=%ss, source=%s)",
            self.settings.monitor_interval_seconds,
            self.settings.discovery_interval_seconds,
            self.settings.discovery_source,
        )

    async def _startup_adopt(self) -> None:
        """Reconcile alert firing state with ACTIVE rows left by a previous
        process, so restarts don't leave zombie active alerts."""
        async with SessionLocal() as db:
            await self.alerts.adopt_active(db)
            await db.commit()

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
            device_by_id = {d.id: d for d in devices}
            links = (await db.scalars(select(Link))).all()
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

                # statistical anomaly check on the latency series; evaluated
                # every cycle (verdict may be None) so in-policy samples can
                # clear a firing alert
                if self.anomalies is not None and result.latency_ms is not None:
                    series_key = ("latency", device.id)
                    verdict = self.anomalies.observe(series_key, "latency_ms", result.latency_ms)
                    await self.alerts.evaluate(
                        db,
                        Observation(
                            device_id=device.id,
                            device_name=device.name,
                            anomaly=verdict,
                            anomaly_series=series_key,
                        ),
                    )

            if self.settings.discovery_source == "simulator":
                await self._sample_link_traffic(db, links, device_by_id, now)

            await db.commit()
            await publish_event(
                "metrics.flushed", devices=len(devices), timestamp=now.isoformat()
            )
            logger.debug("monitor cycle done for %d devices", len(devices))

    async def _sample_link_traffic(
        self,
        db,
        links: list[Link],
        device_by_id: dict[int, Device],
        now,
    ) -> None:
        """Record simulated per-link throughput (bps) for each direction.

        Live deployments would read SNMP ifInOctets/ifOutOctets and convert
        via MetricStore.rate; the simulator has no counters, so the traffic
        model generates plausible values directly. Each series runs through
        the anomaly detector every cycle (verdict may be None, which clears);
        failed endpoints drop their links' traffic to zero, which reads as a
        low-side anomaly against a normally busy link.
        """
        from app.monitor.traffic import link_base_bps, traffic_bps

        ts = now.timestamp()
        for lnk in links:
            src = device_by_id.get(lnk.source_device_id)
            dst = device_by_id.get(lnk.target_device_id)
            if src is None or dst is None:
                continue
            base = link_base_bps(src.device_type, dst.device_type, lnk.id)
            for interface_id, direction in (
                (lnk.source_interface_id, "out"),  # source → target egress
                (lnk.target_interface_id, "in"),  # target ingress
            ):
                if interface_id is None:
                    continue
                endpoint = src if direction == "out" else dst
                down = endpoint.health == HealthState.DOWN
                # a dead endpoint carries no traffic — the detector sees the
                # drop against the link's usual load
                bps = 0.0 if down else traffic_bps(base, lnk.id, ts, direction)
                name = f"if_{direction}_bps"
                db.add(
                    MetricSample(
                        device_id=endpoint.id,
                        interface_id=interface_id,
                        metric_name=name,
                        value=round(bps, 1),
                        timestamp=now,
                    )
                )
                if self.anomalies is not None:
                    series_key = ("traffic", lnk.id, interface_id, direction)
                    verdict = self.anomalies.observe(series_key, "traffic_bps", bps)
                    await self.alerts.evaluate(
                        db,
                        Observation(
                            device_id=endpoint.id,
                            device_name=endpoint.name,
                            anomaly=verdict,
                            anomaly_series=series_key,
                        ),
                    )


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

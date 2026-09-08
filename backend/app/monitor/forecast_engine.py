"""Forecast engine: project metric series and raise capacity-risk alerts.

Runs on its own slow loop (default every 5 min), after the monitor has had
time to accumulate samples. For every traffic series (per interface and
direction) it fits a linear trend over the recent window, projects it over
the forecast horizon, and — when the projected band crosses the line rate
within the horizon — feeds a ``capacity_risk`` verdict into the same alert
engine the anomaly detector uses, keyed by the series so each direction of
each link alarms independently and auto-clears when the trend bends away.

Reads are read-only; the only writes are the Alert rows the alert engine
persists. Latency is deliberately not capacity-forecast (latency anomalies
already have their own detector); the engine keys off traffic series where a
"line rate" makes sense.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import Device, Interface, MetricSample
from app.monitor.alerts import AlertEngine, Observation
from app.monitor.forecast import forecast_series

logger = logging.getLogger(__name__)

_TRAFFIC_METRICS = ("if_in_bps", "if_out_bps")


@dataclass(frozen=True)
class SeriesRef:
    """Identity of one forecasted series — the alert firing key."""

    device_id: int
    device_name: str
    interface_id: int
    metric_name: str
    capacity: float


class ForecastEngine:
    """Sweeps recent metric samples, forecasts each series, alerts on risk."""

    def __init__(self, alerts: AlertEngine, settings: Settings | None = None) -> None:
        self.alerts = alerts
        self.settings = settings or get_settings()

    async def run_once(self, db: AsyncSession) -> int:
        """Forecast all traffic series once; return the number of risk series."""
        window = self.settings.forecast_window_points
        horizon = timedelta(minutes=self.settings.forecast_horizon_minutes)
        capacity = self.settings.forecast_capacity_bps

        risks = 0
        for ref, samples in await self._load_series(db, window, capacity):
            _, verdict = forecast_series(
                samples, metric="traffic", capacity=ref.capacity, horizon=horizon
            )
            if verdict is None:
                continue
            series_key = ("traffic", ref.interface_id, ref.metric_name)
            obs = Observation(
                device_id=ref.device_id,
                device_name=ref.device_name,
                anomaly_series=series_key,
                capacity_risk=verdict,
            )
            await self.alerts.evaluate(db, obs)
            if verdict.risk:
                risks += 1
                eta = (
                    f"{verdict.eta_seconds:.0f}s" if verdict.eta_seconds is not None else "never"
                )
                logger.info(
                    "capacity risk on %s: projecting %.0f Mbps, ETA %s",
                    ref.device_name,
                    verdict.projected / 1e6,
                    eta,
                )
        return risks

    async def _load_series(
        self, db: AsyncSession, window: int, capacity: float
    ) -> list[tuple[SeriesRef, list[tuple[datetime, float]]]]:
        """Recent samples per (interface, direction), with device context."""
        # newest `window` samples per series: windowed latest-per-group via
        # a single ordered scan (sample counts are bounded in this app)
        rows = (
            await db.execute(
                select(
                    MetricSample.interface_id,
                    MetricSample.metric_name,
                    MetricSample.timestamp,
                    MetricSample.value,
                    Interface.device_id,
                    Device.name,
                )
                .join(Interface, Interface.id == MetricSample.interface_id)
                .join(Device, Device.id == Interface.device_id)
                .where(MetricSample.metric_name.in_(_TRAFFIC_METRICS))
                .order_by(MetricSample.timestamp.desc())
                .limit(50_000)
            )
        ).all()

        seen: dict[tuple[int, str], int] = {}
        samples: dict[tuple[int, str], list[tuple[datetime, float]]] = {}
        meta: dict[tuple[int, str], tuple[int, str]] = {}
        for iface_id, metric, ts, value, device_id, device_name in reversed(rows):
            key = (iface_id, metric)
            n = seen.get(key, 0)
            if n >= window:
                continue
            seen[key] = n + 1
            samples.setdefault(key, []).append((ts, float(value)))
            meta[key] = (device_id, device_name)

        out: list[tuple[SeriesRef, list[tuple[datetime, float]]]] = []
        for key, pts in samples.items():
            device_id, device_name = meta[key]
            out.append(
                (
                    SeriesRef(
                        device_id=device_id,
                        device_name=device_name,
                        interface_id=key[0],
                        metric_name=key[1],
                        capacity=capacity,
                    ),
                    pts,
                )
            )
        return out


# process-wide engine, wired in the app lifespan
_engine: ForecastEngine | None = None


def get_forecast_engine() -> ForecastEngine | None:
    return _engine


def start_forecast_engine(alerts: AlertEngine, settings: Settings | None = None) -> ForecastEngine:
    global _engine
    if _engine is None:
        _engine = ForecastEngine(alerts, settings)
    return _engine

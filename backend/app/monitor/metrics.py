"""Metric store: write time-series samples and compute rates.

Keeps a small in-process cache of the last counter value per (device,
interface, metric) so SNMP octet counters (monotonic totals) can be turned
into per-second rates between polls.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import MetricSample

logger = logging.getLogger(__name__)


class MetricStore:
    def __init__(self) -> None:
        # (device_id, interface_id|None, metric_name) -> (value, timestamp)
        self._last_counter: dict[tuple[int, int | None, str], tuple[float, datetime]] = {}

    async def record(
        self,
        db: AsyncSession,
        device_id: int,
        metric_name: str,
        value: float,
        interface_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> MetricSample:
        sample = MetricSample(
            device_id=device_id,
            interface_id=interface_id,
            metric_name=metric_name,
            value=value,
            timestamp=timestamp or utcnow(),
        )
        db.add(sample)
        return sample

    def rate(
        self,
        device_id: int,
        interface_id: int | None,
        metric_name: str,
        counter_value: float,
        timestamp: datetime | None = None,
    ) -> float | None:
        """Convert a monotonic counter to a per-second rate.

        Returns None on the first observation or on counter reset (value went
        down), so callers can skip writing a bogus point.
        """
        now = timestamp or utcnow()
        key = (device_id, interface_id, metric_name)
        prev = self._last_counter.get(key)
        self._last_counter[key] = (counter_value, now)
        if prev is None:
            return None
        prev_value, prev_time = prev
        dt = (now - prev_time).total_seconds()
        if dt <= 0 or counter_value < prev_value:  # reset / rollover
            return None
        return (counter_value - prev_value) / dt

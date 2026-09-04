"""Alert engine: threshold and state-change rules with deduplication.

An AlertEngine is fed observations (device health, metric values) and emits
`alert.raised` / `alert.cleared` events. It tracks which rules are currently
firing per device so a sustained condition raises once, not every poll.

Rules are plain functions (Observation -> bool) so new checks are one-liners.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.db.base import utcnow
from app.db.models import Alert, AlertSeverity, AlertStatus
from app.events.bus import publish_event

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    device_id: int
    device_name: str
    health: str | None = None
    latency_ms: float | None = None
    packet_loss_pct: float | None = None


@dataclass
class Rule:
    name: str
    severity: AlertSeverity
    predicate: Callable[[Observation], bool]
    message: Callable[[Observation], str]
    value: Callable[[Observation], float | None] = lambda o: None
    threshold: float | None = None


def default_rules(
    latency_threshold_ms: float, loss_threshold_pct: float
) -> list[Rule]:
    """The built-in rule set: node-down, high-latency, packet-loss."""
    return [
        Rule(
            name="node_down",
            severity=AlertSeverity.CRITICAL,
            predicate=lambda o: o.health == "down",
            message=lambda o: f"{o.device_name} is DOWN",
        ),
        Rule(
            name="high_latency",
            severity=AlertSeverity.WARNING,
            predicate=lambda o: o.latency_ms is not None and o.latency_ms > latency_threshold_ms,
            message=lambda o: f"{o.device_name} latency {o.latency_ms:.0f}ms "
            f"> {latency_threshold_ms:.0f}ms",
            value=lambda o: o.latency_ms,
            threshold=latency_threshold_ms,
        ),
        Rule(
            name="packet_loss",
            severity=AlertSeverity.WARNING,
            predicate=lambda o: o.packet_loss_pct is not None
            and o.packet_loss_pct >= loss_threshold_pct,
            message=lambda o: f"{o.device_name} packet loss {o.packet_loss_pct:.0f}% "
            f">= {loss_threshold_pct:.0f}%",
            value=lambda o: o.packet_loss_pct,
            threshold=loss_threshold_pct,
        ),
    ]


class AlertEngine:
    """Stateful rule evaluator. Persists Alert rows and publishes events."""

    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules
        # (device_id, rule_name) -> active Alert row id
        self._firing: dict[tuple[int, str], int] = {}

    async def evaluate(self, db, observation: Observation) -> list[Alert]:
        """Run all rules against one observation; raise/clear as needed."""
        changed: list[Alert] = []
        for rule in self.rules:
            key = (observation.device_id, rule.name)
            firing = key in self._firing
            triggered = rule.predicate(observation)

            if triggered and not firing:
                alert = await self._raise(db, rule, observation)
                changed.append(alert)
            elif not triggered and firing:
                await self._clear(db, rule, observation, key)
            # triggered and already firing → no duplicate alert
        return changed

    async def _raise(self, db, rule: Rule, obs: Observation) -> Alert:
        alert = Alert(
            device_id=obs.device_id,
            rule=rule.name,
            severity=rule.severity,
            status=AlertStatus.ACTIVE,
            message=rule.message(obs),
            value=rule.value(obs),
            threshold=rule.threshold,
        )
        db.add(alert)
        await db.flush()  # get alert.id
        self._firing[(obs.device_id, rule.name)] = alert.id
        await publish_event(
            "alert.raised",
            alert_id=alert.id,
            device_id=obs.device_id,
            rule=rule.name,
            severity=rule.severity.value,
            message=alert.message,
        )
        logger.info("alert raised: %s on %s", rule.name, obs.device_name)
        return alert

    async def _clear(self, db, rule: Rule, obs: Observation, key) -> None:
        alert_id = self._firing.pop(key, None)
        if alert_id is None:
            return
        alert = await db.get(Alert, alert_id)
        if alert is not None:
            alert.status = AlertStatus.CLEARED
            alert.cleared_at = utcnow()
        await publish_event(
            "alert.cleared", alert_id=alert_id, device_id=obs.device_id, rule=rule.name
        )
        logger.info("alert cleared: %s on %s", rule.name, obs.device_name)

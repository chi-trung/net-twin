"""Event bus: decouples producers (monitor/discovery) from consumers (WS).

Two implementations behind one interface:
- InMemoryBus: asyncio.Queue fan-out, used in development and tests (no Redis).
- RedisBus: pub/sub across processes, used when multiple backend replicas run.

Events are small dicts with a required "type" field. The bus is intentionally
transport-agnostic so the WebSocket layer and the alert engine never import
redis directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

TOPIC = "net-twin:events"


class EventBus(Protocol):
    async def publish(self, event: dict) -> None: ...
    def subscribe(self) -> asyncio.Queue[dict]: ...
    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None: ...


class InMemoryBus:
    """Single-process fan-out bus backed by asyncio queues."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()

    async def publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            # drop rather than block if a slow consumer fills its queue
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)


class RedisBus:
    """Cross-process bus via Redis pub/sub. publish() is fire-and-forget."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client
        self._pubsub = redis_client.pubsub()
        self._pubsub.subscribe(TOPIC)

    async def publish(self, event: dict) -> None:
        await self._redis.publish(TOPIC, json.dumps(event, default=str))

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        asyncio.create_task(self._pump(queue))
        return queue

    async def _pump(self, queue: asyncio.Queue[dict]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    queue.put_nowait(json.loads(message["data"]))
                except (asyncio.QueueFull, json.JSONDecodeError):
                    continue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:  # noqa: ARG002
        # pump task ends when the queue is garbage-collected / WS closes
        return None


_bus: EventBus | None = None


def get_bus() -> EventBus:
    """Process-wide singleton bus (in-memory unless Redis is configured)."""
    global _bus
    if _bus is None:
        _bus = InMemoryBus()
    return _bus


def set_bus(bus: EventBus) -> None:
    global _bus
    _bus = bus


async def publish_event(event_type: str, **payload) -> None:
    """Convenience wrapper: build {type, ...payload} and publish."""
    await get_bus().publish({"type": event_type, **payload})

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
    """Cross-process bus via Redis pub/sub. publish() is fire-and-forget.

    A single dispatcher task reads the shared pub/sub iterator and fans each
    message out to every subscriber queue — one iterator per consumer would
    split the stream instead of duplicating it.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client
        self._pubsub = redis_client.pubsub()
        self._queues: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._pubsub.subscribe(TOPIC)
        self._task = asyncio.create_task(self._dispatch())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(TOPIC)

    async def publish(self, event: dict) -> None:
        await self._redis.publish(TOPIC, json.dumps(event, default=str))

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._queues.discard(queue)

    async def _dispatch(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except json.JSONDecodeError:
                    continue
                for queue in list(self._queues):
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(event)


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

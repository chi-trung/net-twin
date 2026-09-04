"""WebSocket endpoint: fan-out of bus events to connected UI clients.

Each client gets its own queue from the bus; a pump task forwards events as
JSON. On disconnect the queue is unsubscribed. A `twin.snapshot` message is
sent on connect so the UI can bootstrap without a separate REST call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.models import Device, Link
from app.db.session import SessionLocal

from .bus import get_bus

logger = logging.getLogger(__name__)

router = APIRouter()


async def _snapshot_message() -> dict:
    async with SessionLocal() as db:
        from sqlalchemy import select

        devices = (await db.scalars(select(Device))).all()
        links = (await db.scalars(select(Link))).all()
    return {
        "type": "twin.snapshot",
        "nodes": [
            {
                "id": d.id,
                "name": d.name,
                "ip": d.ip_address,
                "device_type": d.device_type.value,
                "health": d.health.value,
            }
            for d in devices
        ],
        "edges": [
            {
                "id": lnk.id,
                "source": lnk.source_device_id,
                "target": lnk.target_device_id,
                "protocol": lnk.protocol,
                "health": lnk.health.value,
            }
            for lnk in links
        ],
    }


@router.websocket("/ws/events")
async def events_ws(ws: WebSocket) -> None:
    await ws.accept()
    bus = get_bus()
    queue = bus.subscribe()
    pump = asyncio.create_task(_pump(ws, queue))
    try:
        await ws.send_text(json.dumps(await _snapshot_message(), default=str))
        # keep the connection open; inbound messages are ignored (read-only feed)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        bus.unsubscribe(queue)


async def _pump(ws: WebSocket, queue: asyncio.Queue[dict]) -> None:
    with contextlib.suppress(asyncio.CancelledError, RuntimeError):
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event, default=str))

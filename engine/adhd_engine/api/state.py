"""Process-wide runtime state for the FastAPI app.

Holds:

* The dictionary of active worker subprocesses + their IPC channels
* The set of WebSocket subscribers (gaze stream + event stream)

This is the only place where mutable global state lives. All routes pull from
this module rather than holding their own dictionaries, so the lifecycle
(spawn / cancel / cleanup) stays in one consistent flow.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from fastapi import WebSocket

from adhd_engine.ipc.bridge import IpcChannel


@dataclass
class ActiveWorker:
    """Tracking info for a running worker subprocess."""

    session_id: str
    mode: str
    process: mp.Process
    channel: IpcChannel
    pid: Optional[int] = None


# session_id -> ActiveWorker
ACTIVE_WORKERS: Dict[str, ActiveWorker] = {}

# WebSocket subscribers — separated by stream so we don't broadcast gaze
# frames (30/sec) into the events stream
GAZE_SUBSCRIBERS: Set[WebSocket] = set()
EVENT_SUBSCRIBERS: Set[WebSocket] = set()


async def broadcast_gaze(payload: dict) -> None:
    """Send a gaze frame to every subscribed websocket. Drops dead sockets."""
    dead: list = []
    for ws in list(GAZE_SUBSCRIBERS):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        GAZE_SUBSCRIBERS.discard(ws)


async def broadcast_event(payload: dict) -> None:
    """Send a task event to every subscribed websocket."""
    dead: list = []
    for ws in list(EVENT_SUBSCRIBERS):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        EVENT_SUBSCRIBERS.discard(ws)

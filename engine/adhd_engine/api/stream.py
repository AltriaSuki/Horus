"""WebSocket endpoints — live gaze stream and task event stream.

Both endpoints just register the connection in the global subscriber set
(``adhd_engine.api.state``). The actual broadcasting is done by the IPC
drain thread for each active session, via :func:`state.broadcast_gaze` /
:func:`state.broadcast_event`.

Clients can subscribe before, during, or after a session — they'll just
receive nothing until a session is active. There is no per-session
filtering at the protocol level (the engine assumes one screening at a
time, see plan §1.2). When the unity game runs as a separate session it
shares the same /gaze channel.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from adhd_engine.api import state

router = APIRouter(tags=["stream"])


@router.websocket("/gaze")
async def ws_gaze(ws: WebSocket):
    await ws.accept()
    state.GAZE_SUBSCRIBERS.add(ws)
    try:
        while True:
            # Keep the connection alive — the worker pushes data to us
            # via state.broadcast_gaze, we don't expect client messages.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.GAZE_SUBSCRIBERS.discard(ws)


@router.websocket("/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    state.EVENT_SUBSCRIBERS.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.EVENT_SUBSCRIBERS.discard(ws)

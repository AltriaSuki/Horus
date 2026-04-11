"""Session lifecycle: create (spawn worker), poll status, cancel.

This is where the parent <-> worker IPC plumbing meets the FastAPI request
layer. ``POST /sessions`` spawns a worker subprocess and registers an
``IpcChannel`` whose drain thread forwards each message to:

* the WebSocket broadcaster (so live front-end / Unity clients see gaze + events)
* the SQLite store (so reports survive past the websocket subscription)
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from adhd_engine.api import state
from adhd_engine.ipc.bridge import make_channel
from adhd_engine.ipc.messages import (
    EventMsg,
    GazeFrameMsg,
    ReportReadyMsg,
    WorkerErrorMsg,
    WorkerReadyMsg,
)
from adhd_engine.storage import repo
from adhd_engine.worker.runner import spawn_worker

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    subject_id: str
    kind: str = Field(default="screening", pattern="^(screening|game)$")
    mode: str = Field(default="sternberg",
                      pattern="^(sternberg|track|game_[a-z_]+)$")


class SessionOut(BaseModel):
    id: str
    subject_id: str
    kind: str
    mode: str
    status: str
    error_message: Optional[str] = None


def _to_out(s) -> SessionOut:
    return SessionOut(
        id=s.id, subject_id=s.subject_id, kind=s.kind, mode=s.mode,
        status=s.status, error_message=s.error_message,
    )


# ---------------------------------------------------------------------------
# Message handler — wires worker IPC into broadcast + storage
# ---------------------------------------------------------------------------


def _cleanup_active(session_id: str) -> None:
    """Tear down the IPC channel + remove from ACTIVE_WORKERS.

    Called from the handler when the worker reaches a terminal state
    (report ready or error). The drain thread continues to run briefly
    to flush any in-flight messages, then the channel is stopped from a
    background task to avoid blocking the asyncio loop.
    """
    active = state.ACTIVE_WORKERS.pop(session_id, None)
    if active is None:
        return
    try:
        active.process.join(timeout=2.0)
    except Exception:
        pass
    try:
        active.channel.stop()
    except Exception:
        pass


def _make_handler(session_id: str):
    async def handler(msg) -> None:
        if isinstance(msg, GazeFrameMsg):
            await state.broadcast_gaze(msg.to_dict())
        elif isinstance(msg, EventMsg):
            await state.broadcast_event(msg.to_dict())
        elif isinstance(msg, WorkerReadyMsg):
            repo.update_session_status(
                session_id, "running", worker_pid=msg.pid)
            await state.broadcast_event({
                "type": "session_running", "session_id": session_id,
                "pid": msg.pid,
            })
        elif isinstance(msg, ReportReadyMsg):
            try:
                repo.save_report(session_id, msg.report)
                repo.update_session_status(session_id, "completed")
            except Exception as exc:
                repo.update_session_status(
                    session_id, "failed",
                    error=f"save_report failed: {exc}")
            await state.broadcast_event({
                "type": "report_ready", "session_id": session_id,
            })
            # Schedule cleanup off the asyncio loop's critical path so the
            # current handler can finish first (otherwise channel.stop()
            # would join its own drain thread → deadlock).
            asyncio.get_running_loop().call_later(
                0.5, _cleanup_active, session_id,
            )
        elif isinstance(msg, WorkerErrorMsg):
            repo.update_session_status(
                session_id, "failed",
                error=f"{msg.code}: {msg.message}")
            await state.broadcast_event({
                "type": "error", "session_id": session_id,
                "code": msg.code, "message": msg.message,
            })
            asyncio.get_running_loop().call_later(
                0.5, _cleanup_active, session_id,
            )

    return handler


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=SessionOut)
async def create_session(payload: SessionIn):
    if repo.get_subject(payload.subject_id) is None:
        raise HTTPException(404, f"subject {payload.subject_id} not found")

    # Reject if there's already an active worker — we only support one
    # session at a time because pygame fullscreen + camera are exclusive.
    if state.ACTIVE_WORKERS:
        active = next(iter(state.ACTIVE_WORKERS))
        raise HTTPException(409, f"another session is active: {active}")

    sess = repo.create_session(
        subject_id=payload.subject_id, kind=payload.kind, mode=payload.mode,
    )

    channel = make_channel(session_id=sess.id)
    handler = _make_handler(sess.id)
    channel.start_drain(asyncio.get_running_loop(), handler)

    proc = spawn_worker(
        session_id=sess.id, mode=payload.mode,
        subject_id=payload.subject_id, queue_handle=channel.queue,
    )

    state.ACTIVE_WORKERS[sess.id] = state.ActiveWorker(
        session_id=sess.id, mode=payload.mode,
        process=proc, channel=channel, pid=proc.pid,
    )

    sess = repo.get_session(sess.id)
    return _to_out(sess)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str):
    s = repo.get_session(session_id)
    if s is None:
        raise HTTPException(404, f"session {session_id} not found")
    return _to_out(s)


@router.post("/{session_id}/cancel", response_model=SessionOut)
def cancel_session(session_id: str):
    active = state.ACTIVE_WORKERS.get(session_id)
    if active is None:
        raise HTTPException(404, f"no active worker for {session_id}")
    try:
        active.process.terminate()
        active.process.join(timeout=3.0)
    except Exception:
        pass
    active.channel.stop()
    state.ACTIVE_WORKERS.pop(session_id, None)
    repo.update_session_status(session_id, "cancelled")
    return _to_out(repo.get_session(session_id))


@router.get("/{session_id}/report")
def get_report(session_id: str):
    report = repo.get_report(session_id)
    if report is None:
        raise HTTPException(404, "no report yet")
    import json
    return {
        "session_id": report.session_id,
        "prediction": report.prediction,
        "adhd_probability": report.adhd_probability,
        "control_probability": report.control_probability,
        "risk_level": report.risk_level,
        "feature_values": json.loads(report.feature_values_json),
        "feature_importance": json.loads(report.feature_importance_json),
        "model_info": report.model_info,
        "quality_warnings": report.quality_warnings,
        "created_at": report.created_at.isoformat(),
    }

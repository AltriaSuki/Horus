"""IPC message types exchanged between FastAPI parent and worker child.

Plain ``dataclass`` types — kept simple so they survive ``multiprocessing.Queue``
pickling unchanged. The wire format is also serialised straight to JSON for
the WebSocket layer (see ``adhd_engine.api.stream``), so all fields must be
JSON-friendly primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional


@dataclass
class GazeFrameMsg:
    """One captured gaze sample, pushed by the worker every camera frame."""

    session_id: str
    t: float           # monotonic seconds since worker started
    x: float           # screen pixel X (NaN if invalid)
    y: float           # screen pixel Y (NaN if invalid)
    pupil: float       # 5-frame smoothed pupil proxy (NaN if invalid)
    valid: bool
    fps: int = 30
    type: str = "gaze"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EventMsg:
    """A high-level task event (calibration phase, trial boundary, error)."""

    session_id: str
    type: str          # e.g. "trial_start", "trial_end", "task_done", "error"
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "type": self.type,
                **self.payload}


@dataclass
class WorkerReadyMsg:
    """Sent once when the worker has finished init and is awaiting commands."""

    session_id: str
    pid: int
    type: str = "worker_ready"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReportReadyMsg:
    """Carries the final ADHD prediction back to the parent process."""

    session_id: str
    report: Dict[str, Any]
    type: str = "report_ready"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerErrorMsg:
    """Worker terminated with an exception or unrecoverable error."""

    session_id: str
    code: str
    message: str
    type: str = "worker_error"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Union of all message types — used for type hints on queues / handlers.
WorkerMessage = (
    GazeFrameMsg | EventMsg | WorkerReadyMsg | ReportReadyMsg | WorkerErrorMsg
)

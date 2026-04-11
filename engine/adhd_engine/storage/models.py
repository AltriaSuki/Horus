"""SQLModel tables for the ADHD engine.

See plan §1.4 for the schema rationale.

The screening pipeline writes to: ``Subject``, ``Session``, ``Calibration``,
``Trial``, ``GazeFrame``, ``AdhdReport``.

The game module is a placeholder — ``GameRun`` table is created but the engine
doesn't enforce any writes to it. Phase 2 will populate it once the Unity
client is wired up.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Subject(SQLModel, table=True):
    """A child being screened. Long-lived across sessions."""

    id: str = Field(primary_key=True)
    display_name: str
    birth_date: Optional[date] = None
    sex: Optional[str] = None  # 'M' | 'F' | None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str = ""


class Session(SQLModel, table=True):
    """One run of either screening or training. The unit of work the worker
    subprocess executes."""

    id: str = Field(primary_key=True)  # uuid
    subject_id: str = Field(foreign_key="subject.id", index=True)
    kind: str  # 'screening' | 'game'
    mode: str  # 'sternberg' | 'track' | 'game_xxx'
    status: str = "pending"  # pending|running|completed|failed|cancelled
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    fps_actual: Optional[float] = None
    error_message: Optional[str] = None
    worker_pid: Optional[int] = None


class Calibration(SQLModel, table=True):
    """Result of the 13-point click calibration for a session."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    n_points: int
    mean_error_px: float
    pipeline_blob: Optional[bytes] = None  # joblib-pickled Ridge pipeline
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Trial(SQLModel, table=True):
    """One Sternberg trial — metadata + behavioural response + summary."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    trial_num: int
    block_num: int
    load: int
    distractor_type: int  # 3-6
    distractor_name: str
    image_file: Optional[str] = None
    is_target: bool
    correct_answer: str  # 'f' | 'j'
    response: Optional[str] = None
    reaction_time_ms: Optional[float] = None
    correct: bool = False
    pupil_mean: Optional[float] = None
    pupil_max: Optional[float] = None
    n_valid_frames: int = 0


class GazeFrame(SQLModel, table=True):
    """Per-frame gaze sample. Optional — large volume.

    Plan §6 (Known risks) — at 30 Hz × ~1500 s × 160 trials this can be ~50k
    rows per session. We accept that for Phase 1; Phase 2 may move this to
    parquet on disk with a pointer here instead.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    trial_id: Optional[int] = Field(default=None, foreign_key="trial.id")
    t: float
    x: float
    y: float
    pupil: float
    valid: bool


class AdhdReport(SQLModel, table=True):
    """The final RF prediction and explanation for a screening session."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", unique=True)
    prediction: str  # 'ADHD' | 'Control'
    adhd_probability: float
    control_probability: float
    risk_level: str  # HIGH|MODERATE|LOW|MINIMAL
    feature_values_json: str  # JSON of 12 selected features
    feature_importance_json: str  # JSON of RF feature importances
    quality_warnings: str = ""  # comma-separated tags (Phase 2 OOD checks)
    model_info: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GameRun(SQLModel, table=True):
    """Placeholder for game telemetry. Phase 1 only creates the table."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    game_name: str
    score: Optional[int] = None
    duration_sec: Optional[float] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    payload_json: str = "{}"

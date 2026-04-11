"""High-level CRUD helpers for the engine.

Keep them small and dependency-free so the FastAPI route modules stay thin.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Iterable, List, Optional

from sqlmodel import select

from adhd_engine.storage.db import session_scope
from adhd_engine.storage.models import (
    AdhdReport,
    Calibration,
    GameRun,
    GazeFrame,
    Session as SessionModel,
    Subject,
    Trial,
)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


def create_subject(subject_id: str, display_name: str, **extra) -> Subject:
    with session_scope() as s:
        existing = s.get(Subject, subject_id)
        if existing is not None:
            return existing
        sub = Subject(id=subject_id, display_name=display_name, **extra)
        s.add(sub)
        s.flush()
        s.refresh(sub)
        return sub


def get_subject(subject_id: str) -> Optional[Subject]:
    with session_scope() as s:
        return s.get(Subject, subject_id)


def list_subjects() -> List[Subject]:
    with session_scope() as s:
        return list(s.exec(select(Subject).order_by(Subject.created_at.desc())))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(subject_id: str, kind: str, mode: str) -> SessionModel:
    new_id = str(uuid.uuid4())
    with session_scope() as s:
        sess = SessionModel(
            id=new_id,
            subject_id=subject_id,
            kind=kind,
            mode=mode,
            status="pending",
            started_at=datetime.utcnow(),
        )
        s.add(sess)
        s.flush()
        s.refresh(sess)
        return sess


def update_session_status(session_id: str, status: str,
                          *, error: Optional[str] = None,
                          worker_pid: Optional[int] = None) -> None:
    with session_scope() as s:
        sess = s.get(SessionModel, session_id)
        if sess is None:
            return
        sess.status = status
        if error is not None:
            sess.error_message = error
        if worker_pid is not None:
            sess.worker_pid = worker_pid
        if status in ("completed", "failed", "cancelled"):
            sess.ended_at = datetime.utcnow()
        s.add(sess)


def get_session(session_id: str) -> Optional[SessionModel]:
    with session_scope() as s:
        return s.get(SessionModel, session_id)


def list_sessions_for_subject(subject_id: str) -> List[SessionModel]:
    with session_scope() as s:
        return list(s.exec(
            select(SessionModel)
            .where(SessionModel.subject_id == subject_id)
            .order_by(SessionModel.started_at.desc())
        ))


# ---------------------------------------------------------------------------
# ADHD reports
# ---------------------------------------------------------------------------


def save_report(session_id: str, result: dict) -> AdhdReport:
    """Persist a ``predict_adhd`` result dict as an :class:`AdhdReport`."""
    selected_features = result.get("feature_values", {})
    importance = result.get("feature_importance", {})
    with session_scope() as s:
        existing = s.exec(
            select(AdhdReport).where(AdhdReport.session_id == session_id)
        ).first()
        if existing is not None:
            s.delete(existing)
            s.flush()
        report = AdhdReport(
            session_id=session_id,
            prediction=result.get("prediction", ""),
            adhd_probability=float(result.get("adhd_probability", 0.0)),
            control_probability=float(result.get("control_probability", 0.0)),
            risk_level=result.get("risk_level", ""),
            feature_values_json=json.dumps(selected_features, ensure_ascii=False),
            feature_importance_json=json.dumps(importance, ensure_ascii=False),
            model_info=result.get("model_info", ""),
        )
        s.add(report)
        s.flush()
        s.refresh(report)
        return report


def get_report(session_id: str) -> Optional[AdhdReport]:
    with session_scope() as s:
        return s.exec(
            select(AdhdReport).where(AdhdReport.session_id == session_id)
        ).first()


# ---------------------------------------------------------------------------
# Trials & gaze frames (bulk inserts from worker)
# ---------------------------------------------------------------------------


def insert_trials(session_id: str, trials: Iterable[dict]) -> None:
    with session_scope() as s:
        for t in trials:
            s.add(Trial(session_id=session_id, **t))


def insert_gaze_frames(rows: Iterable[dict]) -> None:
    with session_scope() as s:
        for row in rows:
            s.add(GazeFrame(**row))


def insert_calibration(session_id: str, n_points: int,
                       mean_error_px: float,
                       pipeline_blob: Optional[bytes] = None) -> Calibration:
    with session_scope() as s:
        cal = Calibration(
            session_id=session_id,
            n_points=n_points,
            mean_error_px=mean_error_px,
            pipeline_blob=pipeline_blob,
        )
        s.add(cal)
        s.flush()
        s.refresh(cal)
        return cal


# ---------------------------------------------------------------------------
# Game placeholder
# ---------------------------------------------------------------------------


def create_game_run(session_id: str, game_name: str, **extra) -> GameRun:
    with session_scope() as s:
        run = GameRun(session_id=session_id, game_name=game_name, **extra)
        s.add(run)
        s.flush()
        s.refresh(run)
        return run


def finish_game_run(run_id: int, score: Optional[int],
                    payload: Optional[dict] = None) -> None:
    with session_scope() as s:
        run = s.get(GameRun, run_id)
        if run is None:
            return
        run.score = score
        run.ended_at = datetime.utcnow()
        if run.started_at is not None and run.ended_at is not None:
            run.duration_sec = (run.ended_at - run.started_at).total_seconds()
        if payload is not None:
            run.payload_json = json.dumps(payload, ensure_ascii=False)
        s.add(run)

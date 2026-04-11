"""Subject (被试) CRUD endpoints."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from adhd_engine.storage import repo

router = APIRouter(prefix="/subjects", tags=["subjects"])


class SubjectIn(BaseModel):
    id: str
    display_name: str
    birth_date: Optional[date] = None
    sex: Optional[str] = None
    notes: str = ""


class SubjectOut(BaseModel):
    id: str
    display_name: str
    birth_date: Optional[date]
    sex: Optional[str]
    notes: str


@router.post("", response_model=SubjectOut)
def create_subject(payload: SubjectIn):
    sub = repo.create_subject(
        subject_id=payload.id,
        display_name=payload.display_name,
        birth_date=payload.birth_date,
        sex=payload.sex,
        notes=payload.notes,
    )
    return SubjectOut(
        id=sub.id,
        display_name=sub.display_name,
        birth_date=sub.birth_date,
        sex=sub.sex,
        notes=sub.notes,
    )


@router.get("", response_model=List[SubjectOut])
def list_subjects():
    subs = repo.list_subjects()
    return [
        SubjectOut(
            id=s.id, display_name=s.display_name, birth_date=s.birth_date,
            sex=s.sex, notes=s.notes,
        )
        for s in subs
    ]


@router.get("/{subject_id}", response_model=SubjectOut)
def get_subject(subject_id: str):
    sub = repo.get_subject(subject_id)
    if sub is None:
        raise HTTPException(404, f"subject {subject_id} not found")
    return SubjectOut(
        id=sub.id, display_name=sub.display_name, birth_date=sub.birth_date,
        sex=sub.sex, notes=sub.notes,
    )


class SessionSummary(BaseModel):
    id: str
    subject_id: str
    kind: str
    mode: str
    status: str
    started_at: str
    ended_at: Optional[str] = None
    has_report: bool = False


@router.get("/{subject_id}/sessions", response_model=List[SessionSummary])
def list_subject_sessions(subject_id: str):
    if repo.get_subject(subject_id) is None:
        raise HTTPException(404, f"subject {subject_id} not found")
    sessions = repo.list_sessions_for_subject(subject_id)
    out: List[SessionSummary] = []
    for s in sessions:
        report_exists = repo.get_report(s.id) is not None
        out.append(SessionSummary(
            id=s.id, subject_id=s.subject_id, kind=s.kind, mode=s.mode,
            status=s.status,
            started_at=s.started_at.isoformat() if s.started_at else "",
            ended_at=s.ended_at.isoformat() if s.ended_at else None,
            has_report=report_exists,
        ))
    return out

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

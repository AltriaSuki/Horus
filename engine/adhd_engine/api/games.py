"""Game session telemetry — placeholder for the Unity training module.

Phase 1 only stores enough to confirm the integration point works. The Unity
client will use ``WS /gaze`` to read the live gaze stream (same channel the
front-end shell uses) and ``POST /games/runs`` + ``PATCH /games/runs/{id}``
to log scores. The actual game logic is out of scope.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from adhd_engine.storage import repo

router = APIRouter(prefix="/games", tags=["games"])


class GameRunCreate(BaseModel):
    session_id: str
    game_name: str


class GameRunFinish(BaseModel):
    score: Optional[int] = None
    payload: Optional[dict] = None


class GameRunOut(BaseModel):
    id: int
    session_id: str
    game_name: str
    score: Optional[int]
    duration_sec: Optional[float]


@router.post("/runs", response_model=GameRunOut)
def create_game_run(payload: GameRunCreate):
    if repo.get_session(payload.session_id) is None:
        raise HTTPException(404, "session not found")
    run = repo.create_game_run(
        session_id=payload.session_id, game_name=payload.game_name,
    )
    return GameRunOut(
        id=run.id, session_id=run.session_id, game_name=run.game_name,
        score=run.score, duration_sec=run.duration_sec,
    )


@router.patch("/runs/{run_id}", response_model=dict)
def finish_game_run(run_id: int, payload: GameRunFinish):
    repo.finish_game_run(run_id, score=payload.score, payload=payload.payload)
    return {"id": run_id, "status": "finished"}

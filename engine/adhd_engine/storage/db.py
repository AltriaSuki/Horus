"""SQLite engine + session factory.

The DB file lives under :data:`adhd_engine.config.DB_PATH` (per-user via
``platformdirs``). The schema is created on first import (idempotent).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from adhd_engine import config
from adhd_engine.storage import models  # noqa: F401  -- registers tables


_engine = None


def get_engine():
    """Lazy create + return the singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        url = f"sqlite:///{config.DB_PATH}"
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
        SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a SQLModel Session that auto-commits on success / rolls back.

    ``expire_on_commit=False`` keeps loaded attribute values accessible after
    the session closes — without it, every attribute access on a returned
    model object would try to reload from the (now-closed) session and raise
    ``DetachedInstanceError``.
    """
    eng = get_engine()
    sess = Session(eng, expire_on_commit=False)
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()

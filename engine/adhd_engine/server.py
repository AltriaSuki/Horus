"""FastAPI app entrypoint.

Run with::

    uv run python -m adhd_engine.server
    # or
    uvicorn adhd_engine.server:app --host 127.0.0.1 --port 8765

The server is single-process: pygame and torch live in spawned worker
subprocesses, never in the FastAPI event loop.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from adhd_engine import config
from adhd_engine.api import games, sessions, state, stream, subjects
from adhd_engine.storage.db import get_engine

app = FastAPI(
    title="ADHD Engine",
    version="0.1.0",
    description="Gaze tracking + Sternberg ADHD screening + game integration",
)

# Permissive CORS — the engine binds to localhost; in production behind a
# proper reverse proxy you'd lock this down.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    # Force schema creation early so the first request doesn't pay the cost
    get_engine()
    print(f"[engine] DB at {config.DB_PATH}")
    print(f"[engine] Stimuli at {config.STIMULI_DIR}")
    print(f"[engine] Saved models at {config.SAVED_MODELS_DIR}")


@app.on_event("shutdown")
async def _shutdown():
    # Tear down any straggler workers
    for sid, w in list(state.ACTIVE_WORKERS.items()):
        try:
            w.process.terminate()
            w.process.join(timeout=2.0)
        except Exception:
            pass
        try:
            w.channel.stop()
        except Exception:
            pass
        state.ACTIVE_WORKERS.pop(sid, None)


@app.get("/")
def root():
    return {
        "service": "adhd-engine",
        "version": app.version,
        "active_sessions": list(state.ACTIVE_WORKERS.keys()),
        "gaze_subscribers": len(state.GAZE_SUBSCRIBERS),
        "event_subscribers": len(state.EVENT_SUBSCRIBERS),
    }


app.include_router(subjects.router)
app.include_router(sessions.router)
app.include_router(games.router)
app.include_router(stream.router)


def main():
    import uvicorn
    host = os.environ.get("ADHD_ENGINE_HOST", config.DEFAULT_HOST)
    port = int(os.environ.get("ADHD_ENGINE_PORT", str(config.DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()

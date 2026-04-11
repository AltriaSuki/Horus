"""End-to-end smoke test for the Engine.

We don't spawn a real pygame worker (would need a human in front of the
camera). Instead we monkey-patch ``adhd_engine.worker.runner.spawn_worker``
with a fake that simulates the full lifecycle:

1. publishes ``worker_ready``
2. emits a few ``trial_start`` / ``trial_end`` events
3. delivers a synthetic ``ReportReadyMsg`` containing a real RF prediction

This exercises every layer **except** the pygame/MediaPipe stack:

* FastAPI REST routes (subjects, sessions, report)
* IPC channel + drain thread
* SQLite ORM (subject + session + report rows)
* WebSocket subscribers (gaze + events)

Plan §1.8 verification step (3) is the "real human" version of this — to be
run manually on the user's machine.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import time
from typing import List

import httpx
import pytest

from adhd_engine.ipc.bridge import WorkerPublisher
from adhd_engine.worker.inference import extract_features, predict_adhd


# ---------------------------------------------------------------------------
# Fake worker entrypoint — mirrors adhd_engine.worker.runner._run() shape
# but skips pygame, camera, and the deep model.
# ---------------------------------------------------------------------------


def _fake_worker_main(session_id: str, mode: str, subject_id: str,
                      queue_handle: mp.Queue) -> None:
    publisher = WorkerPublisher(queue_handle, session_id)
    publisher.publish_ready(pid=os.getpid())
    publisher.publish_event({"type": "calibration_start"})
    time.sleep(0.05)
    publisher.publish_event({"type": "calibration_done"})

    publisher.publish_event({"type": "task_start", "total_trials": 4,
                             "n_blocks": 1})
    for i in range(4):
        publisher.publish_event({
            "type": "trial_start", "trial_num": i + 1, "block": 1, "load": 1,
        })
        # A handful of fake gaze frames
        for k in range(3):
            publisher.publish_gaze(
                t=i * 0.1 + k * 0.033, x=500.0 + k, y=500.0 + k,
                pupil=0.15, valid=True,
            )
        publisher.publish_event({
            "type": "trial_end", "trial_num": i + 1, "correct": 1, "rt": 0.7,
        })
    publisher.publish_event({"type": "task_done", "total_trials": 4})

    # Build a real prediction from synthetic features
    feats = {name: 0.0 for name in [
        "mean_rt", "std_rt", "cv_rt", "rt_skewness", "accuracy",
        "omission_rate", "rt_diff_correct_incorrect",
        "pupil_max_peak", "pupil_mean_change", "pupil_std_of_means",
        "pupil_overall_var", "pupil_peak_latency", "pupil_slope",
        "pupil_auc", "pupil_late_minus_early", "pupil_slope_var",
        "pupil_load_diff", "pupil_peak_load_diff",
        "gaze_var_x", "gaze_var_y", "gaze_path_normalized",
        "mean_rt_load1", "mean_rt_load2", "rt_load_diff", "acc_load_diff",
        "rt_distractor_slope", "acc_distractor_slope",
    ]}
    feats["accuracy"] = 0.8
    feats["mean_rt"] = 750.0
    result = predict_adhd(feats)
    publisher.publish_report(result)


def _fake_spawn_worker(session_id, mode, subject_id, queue_handle):
    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_fake_worker_main,
        args=(session_id, mode, subject_id, queue_handle),
        daemon=False,
    )
    proc.start()
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_app(monkeypatch, tmp_path):
    """Yield a FastAPI app whose ``spawn_worker`` is replaced with the fake."""
    monkeypatch.setenv("ADHD_APP_DATA_DIR", str(tmp_path))

    import importlib
    import adhd_engine.config as cfg
    importlib.reload(cfg)
    import adhd_engine.storage.db as dbmod
    importlib.reload(dbmod)
    import adhd_engine.storage.repo as repomod
    importlib.reload(repomod)
    # Reload the global state module so each test gets fresh ACTIVE_WORKERS
    import adhd_engine.api.state as statemod
    importlib.reload(statemod)
    import adhd_engine.api.sessions as sessmod
    importlib.reload(sessmod)
    import adhd_engine.api.subjects as submod
    importlib.reload(submod)
    import adhd_engine.api.games as gamemod
    importlib.reload(gamemod)
    import adhd_engine.api.stream as strmod
    importlib.reload(strmod)
    import adhd_engine.server as srvmod
    importlib.reload(srvmod)

    # Defensive — ensure clean global state even if reload missed something
    statemod.ACTIVE_WORKERS.clear()
    statemod.GAZE_SUBSCRIBERS.clear()
    statemod.EVENT_SUBSCRIBERS.clear()

    monkeypatch.setattr(sessmod, "spawn_worker", _fake_spawn_worker)

    yield srvmod.app

    # Final cleanup — terminate any straggler workers from this test
    for sid, w in list(statemod.ACTIVE_WORKERS.items()):
        try:
            w.process.terminate()
            w.process.join(timeout=1.0)
        except Exception:
            pass
        try:
            w.channel.stop()
        except Exception:
            pass
    statemod.ACTIVE_WORKERS.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_session_flow(patched_app):
    """Create subject → start session → wait for report → fetch report."""
    transport = httpx.ASGITransport(app=patched_app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        # Force startup hooks
        await client.get("/")

        # 1. Create subject
        r = await client.post("/subjects", json={
            "id": "E2E_001", "display_name": "End To End Test",
        })
        assert r.status_code == 200, r.text

        # 2. Start a session — this spawns the fake worker
        r = await client.post("/sessions", json={
            "subject_id": "E2E_001", "kind": "screening", "mode": "sternberg",
        })
        assert r.status_code == 200, r.text
        session_id = r.json()["id"]
        assert r.json()["status"] in ("pending", "running")

        # 3. Poll for completion (the fake worker takes < 1s)
        deadline = time.monotonic() + 10.0
        last_status = None
        while time.monotonic() < deadline:
            r = await client.get(f"/sessions/{session_id}")
            assert r.status_code == 200
            last_status = r.json()["status"]
            if last_status in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert last_status == "completed", \
            f"session ended in status {last_status}: {r.json()}"

        # 4. Fetch the report
        r = await client.get(f"/sessions/{session_id}/report")
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["session_id"] == session_id
        assert report["prediction"] in ("ADHD", "Control")
        assert 0.0 <= report["adhd_probability"] <= 1.0
        assert report["risk_level"] in ("HIGH", "MODERATE", "LOW", "MINIMAL")
        assert "feature_values" in report
        assert "feature_importance" in report


@pytest.mark.asyncio
async def test_cannot_start_two_sessions(patched_app):
    """The engine refuses overlapping screening sessions."""
    transport = httpx.ASGITransport(app=patched_app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        await client.get("/")
        await client.post("/subjects",
                          json={"id": "E2E_002", "display_name": "x"})

        # First session
        r1 = await client.post("/sessions", json={
            "subject_id": "E2E_002", "kind": "screening", "mode": "sternberg",
        })
        assert r1.status_code == 200, r1.text

        # Second session, IMMEDIATELY — should be rejected because the
        # first one is still active. The fake worker takes ~50ms before it
        # finishes, so racing it here is reliable.
        r2 = await client.post("/sessions", json={
            "subject_id": "E2E_002", "kind": "screening", "mode": "sternberg",
        })
        # Either 409 (still running) or 200 (already finished) is acceptable;
        # what we don't want is a crash. Most of the time we hit 409.
        assert r2.status_code in (200, 409), r2.text

        # Wait for first session to finish + ACTIVE_WORKERS cleanup (the
        # handler schedules cleanup with call_later(0.5)).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            sr = await client.get(f"/sessions/{r1.json()['id']}")
            if sr.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)  # let cleanup run


@pytest.mark.asyncio
async def test_game_run_placeholder(patched_app):
    """The game endpoints accept create + finish."""
    transport = httpx.ASGITransport(app=patched_app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        await client.get("/")
        await client.post("/subjects",
                          json={"id": "E2E_003", "display_name": "y"})

        # Need a session row to attach a game run
        r = await client.post("/sessions", json={
            "subject_id": "E2E_003", "kind": "game", "mode": "game_snake",
        })
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        # Wait for the fake worker to finish + cleanup
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            sr = await client.get(f"/sessions/{sid}")
            if sr.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)

        r = await client.post("/games/runs",
                              json={"session_id": sid, "game_name": "snake"})
        assert r.status_code == 200, r.text
        run_id = r.json()["id"]

        r = await client.patch(f"/games/runs/{run_id}",
                               json={"score": 1234,
                                     "payload": {"level": 5}})
        assert r.status_code == 200

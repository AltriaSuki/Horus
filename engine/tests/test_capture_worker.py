"""Regression test for the background capture worker + present-phase ordering.

The previous version of ``_present_phase`` blocked every loop iteration on
``cap.read() + MediaPipe + AFFNet`` (50-80 ms on Windows CPU) which:

  1. Made phase transitions feel like the old screen was overlapping the
     new task (user complaint: "前几个阶段提示的东西会出现与任务互相压住")
  2. Made every phase run longer than its paradigm-declared duration
     (user complaint: "每一个阶段的转换卡卡的")

Fix: run the camera + inference on a background daemon thread and make
``_capture_one_frame`` a non-blocking read of the latest sample.

This test guards the behaviour:
  * ``_CaptureWorker`` exists and can start/stop
  * ``_capture_one_frame`` delegates to the worker when alive
  * ``_present_phase`` draws BEFORE capturing (critical for the transition
    fix — otherwise the old screen lingers while the blocking capture
    runs, exactly what we're trying to avoid)
"""

from __future__ import annotations

import os
from unittest import mock

import numpy as np

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from adhd_engine.worker.sternberg import (  # noqa: E402
    SternbergTask, TrialData, _CaptureWorker,
)


class _FakeGazeSystem:
    """Minimal stand-in for GazeSystem — no real camera / model."""

    def __init__(self):
        pygame.init()
        pygame.display.init()
        self.screen = pygame.display.set_mode((800, 600))
        self.sw = 800
        self.sh = 600
        self.hwnd = None
        # Fake camera that returns a blank frame
        self.cap = mock.MagicMock()
        self.cap.isOpened.return_value = True
        self.cap.read.return_value = (
            True, np.zeros((480, 640, 3), dtype=np.uint8))


@pytest.fixture
def task():
    pygame.init()
    pygame.display.init()
    gs = _FakeGazeSystem()
    return SternbergTask(gs)


def _make_trial() -> TrialData:
    return TrialData(
        trial_num=1, block_num=1, load=1,
        distractor_type=3, distractor_name="blank",
        dot_positions=[[(0, 0)], [(1, 1)], [(2, 2)]],
        probe_pos=(0, 0), is_target=True, correct_answer="f",
    )


# ---------------------------------------------------------------------------
# Capture worker lifecycle
# ---------------------------------------------------------------------------


def test_capture_worker_is_importable():
    """The worker class exists at module level."""
    assert _CaptureWorker is not None


def test_capture_worker_does_not_shadow_thread_internals():
    """Regression for a subtle ``threading.Thread`` naming bug.

    ``threading.Thread`` has an internal method called ``_stop()`` that
    gets invoked during thread cleanup (e.g. when the interpreter unwinds
    a KeyboardInterrupt). If a ``Thread`` subclass defines an instance
    attribute *also* called ``_stop`` (say, a ``threading.Event``), the
    Event shadows the method, and the next time Python tries to call
    ``self._stop()`` it raises ``TypeError: 'Event' object is not
    callable``. In our case this crashed the worker every time the user
    pressed ESC mid-task.

    The fix was to rename our Event attribute to ``_stop_event``. This
    test guards the fix by asserting no ``Thread`` internal methods
    are clobbered by our subclass's attributes.
    """
    gs = _FakeGazeSystem()
    worker = _CaptureWorker(gs)

    # None of these Thread internals should be replaced by a non-callable.
    for attr in ("_stop", "_started", "_tstate_lock", "_bootstrap"):
        if not hasattr(worker, attr):
            continue
        value = getattr(worker, attr)
        if callable(value):
            continue  # it's still the method — good
        # Otherwise, the only tolerated non-callable replacements are
        # things Thread itself sets (e.g. ``_started`` is an Event in
        # CPython). Our ``_stop`` override was neither.
        if attr == "_stop":
            pytest.fail(
                "_CaptureWorker defined a non-callable `_stop` attribute, "
                "shadowing threading.Thread._stop(). Rename it to "
                "`_stop_event`."
            )


def test_capture_worker_start_stop_cleanly(monkeypatch):
    """The worker can start, run at least one iteration, and stop without
    raising — including when the internal capture loop would call
    Thread's ``_stop()`` during teardown.
    """
    gs = _FakeGazeSystem()
    # Make _extract return None so the inner loop runs quickly and
    # doesn't try to call MediaPipe.
    gs._extract = mock.MagicMock(return_value=None)
    gs.feat_buf = []
    gs._weighted_mean_feat = mock.MagicMock()
    gs._predict_screen_point = mock.MagicMock(return_value=(0, 0))
    gs.smoother = mock.MagicMock()
    gs.smoother.update = mock.MagicMock(return_value=(0.0, 0.0))

    worker = _CaptureWorker(gs)
    worker.start()
    # Give it a moment to run a few iterations
    import time
    time.sleep(0.05)
    worker.stop()
    worker.join(timeout=2.0)

    assert not worker.is_alive(), "worker did not stop cleanly"


def test_capture_one_frame_falls_back_when_no_worker(task):
    """Without a running worker, _capture_one_frame uses the sync path."""
    assert task._capture_worker is None
    # Sync path will try gs._extract which doesn't exist on our fake.
    # Mock it to return None so the fallback code path is exercised.
    task.gs._extract = mock.MagicMock(return_value=None)
    gx, gy, pupil = task._capture_one_frame()
    assert np.isnan(gx) and np.isnan(gy) and np.isnan(pupil)
    # Crucially, the sync path DID call cap.read()
    task.gs.cap.read.assert_called()


def test_capture_one_frame_delegates_to_worker_when_alive(task, monkeypatch):
    """When a CaptureWorker is alive, latest() is returned directly."""
    fake_worker = mock.MagicMock(spec=_CaptureWorker)
    fake_worker.is_alive.return_value = True
    fake_worker.latest.return_value = (123.4, 567.8, 0.15)
    task._capture_worker = fake_worker

    gx, gy, pupil = task._capture_one_frame()

    assert (gx, gy, pupil) == (123.4, 567.8, 0.15)
    fake_worker.latest.assert_called_once()
    # Sync path must NOT have been taken (no new cap.read call)
    task.gs.cap.read.assert_not_called()


# ---------------------------------------------------------------------------
# Present-phase draw-first ordering
# ---------------------------------------------------------------------------


def test_present_phase_draws_before_capturing(task, monkeypatch):
    """`draw_fn` must be called BEFORE `_capture_one_frame` within each
    iteration of _present_phase. If the order is flipped again the old
    phase transition lag will come back.
    """
    call_sequence: list[str] = []

    def fake_draw():
        call_sequence.append("draw")

    original_capture = task._capture_one_frame

    def traced_capture():
        call_sequence.append("capture")
        return (float("nan"), float("nan"), float("nan"))

    monkeypatch.setattr(task, "_capture_one_frame", traced_capture)

    # Short phase so the test runs fast
    task._present_phase(fake_draw, duration_ms=100, trial=None)

    # First draw must happen before first capture
    draw_idx = call_sequence.index("draw")
    cap_idx = call_sequence.index("capture")
    assert draw_idx < cap_idx, (
        f"draw must precede capture (draw@{draw_idx}, capture@{cap_idx})"
    )

    # And every draw in the sequence must come before the matching capture
    draws = [i for i, s in enumerate(call_sequence) if s == "draw"]
    caps = [i for i, s in enumerate(call_sequence) if s == "capture"]
    assert len(draws) == len(caps)
    for d, c in zip(draws, caps):
        assert d < c, "every draw must come before its capture"


def test_present_phase_captures_from_worker(task, monkeypatch):
    """When a worker is attached, _present_phase uses it (no blocking)."""
    fake_worker = mock.MagicMock(spec=_CaptureWorker)
    fake_worker.is_alive.return_value = True
    fake_worker.latest.return_value = (10.0, 20.0, 0.12)
    task._capture_worker = fake_worker

    trial = _make_trial()

    def fake_draw():
        pass

    task._present_phase(fake_draw, duration_ms=100, trial=trial)

    # At 30 fps, 100 ms → 3 frames, so 3 calls to latest()
    assert fake_worker.latest.call_count >= 3

    # Trial data captured exactly what the worker reported
    assert len(trial.gaze_x_series) >= 3
    assert trial.gaze_x_series[0] == 10.0
    assert trial.gaze_y_series[0] == 20.0
    assert trial.pupil_series[0] == 0.12

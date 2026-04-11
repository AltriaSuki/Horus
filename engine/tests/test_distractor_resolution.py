"""Regression test for the distractor-per-frame bug.

The previous version of ``SternbergTask._draw_distractor`` resolved the
distractor stimulus inside the per-frame draw callback, so a single 500 ms
distractor period at 30 fps loaded **15 different images** (incrementing
``pool_indices`` once per frame). For 80 image-distractor trials in a
session that's 1200 image loads instead of the intended 80, and the user
saw a rapid slideshow rather than the single 500 ms image required by the
Rojas-Líbano paradigm.

This test guards against that bug by:

1. Calling ``_resolve_distractor`` once and verifying it returns a stable
   payload that can be rendered repeatedly without side effects.
2. Calling ``_render_distractor`` 15 times in a row and asserting that
   ``pool_indices`` does NOT advance.
"""

from __future__ import annotations

import os

# Headless pygame BEFORE any pygame import
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from adhd_engine.worker.sternberg import SternbergTask, TrialData  # noqa: E402


class _FakeGazeSystem:
    """Minimal stand-in for ``GazeSystem`` so we can construct a SternbergTask
    without booting MediaPipe / AFFNet / a real camera. Only the attributes
    ``SternbergTask.__init__`` actually reads are populated."""

    def __init__(self):
        pygame.init()
        pygame.display.init()
        # 800x600 fits in headless dummy driver and is enough for the geometry
        self.screen = pygame.display.set_mode((800, 600))
        self.sw = 800
        self.sh = 600
        self.cap = None  # not used in these tests
        self.hwnd = None


@pytest.fixture
def task():
    pygame.init()
    pygame.display.init()
    gs = _FakeGazeSystem()
    return SternbergTask(gs)


def _make_trial(distractor_name: str, distractor_code: int) -> TrialData:
    return TrialData(
        trial_num=1,
        block_num=1,
        load=1,
        distractor_type=distractor_code,
        distractor_name=distractor_name,
        dot_positions=[[(0, 0)], [(1, 1)], [(2, 2)]],
        probe_pos=(0, 0),
        is_target=True,
        correct_answer="f",
    )


def test_image_distractor_loads_only_once_per_trial(task):
    """A single image-distractor trial advances pool_indices by exactly 1."""
    if not task.image_pools["neutral"]:
        pytest.skip("no neutral images in stimuli library")

    start = task.pool_indices["neutral"]

    trial = _make_trial("neutral_image", 4)
    payload = task._resolve_distractor(trial)

    assert payload["type"] == "image"
    assert payload["surface"] is not None
    assert trial.image_file != ""

    # _resolve_distractor advances the pool cursor exactly once
    assert task.pool_indices["neutral"] == start + 1, (
        "pool index should advance by exactly 1 per resolve, "
        f"went from {start} to {task.pool_indices['neutral']}"
    )

    # Now simulate the 15 per-frame draw calls — they MUST NOT advance the
    # cursor or load anything new.
    cursor_after_resolve = task.pool_indices["neutral"]
    for _ in range(15):
        task._render_distractor(payload)

    assert task.pool_indices["neutral"] == cursor_after_resolve, (
        "rendering must be a pure paint operation; pool_indices was mutated"
    )


def test_shape_distractor_positions_are_stable(task):
    """A shape distractor's dot pattern stays the same across frames."""
    trial = _make_trial("shape", 6)
    payload = task._resolve_distractor(trial)
    assert payload["type"] == "shape"

    positions = payload["positions"]
    assert len(positions) >= 4
    assert len(positions) <= 8

    # 15 frames of rendering must not change the positions
    for _ in range(15):
        task._render_distractor(payload)
        assert payload["positions"] == positions, \
            "shape positions changed between frames — re-randomisation bug"


def test_blank_distractor_payload(task):
    trial = _make_trial("blank", 3)
    payload = task._resolve_distractor(trial)
    assert payload == {"type": "blank"}
    # render should not crash
    task._render_distractor(payload)


def test_image_distractor_does_not_repeat_within_short_run(task):
    """Verify that running 5 image-distractor trials shows 5 distinct images
    (cursor advances 5 times, not 75 = 5×15)."""
    if not task.image_pools["neutral"]:
        pytest.skip("no neutral images in stimuli library")

    n_trials = 5
    start = task.pool_indices["neutral"]
    seen_files = set()

    for i in range(n_trials):
        trial = _make_trial("neutral_image", 4)
        payload = task._resolve_distractor(trial)
        seen_files.add(trial.image_file)
        # Render 15 times (simulating one full distractor period)
        for _ in range(15):
            task._render_distractor(payload)

    # We expect exactly n_trials advances of the cursor
    assert task.pool_indices["neutral"] == start + n_trials, (
        f"expected {n_trials} advances, got "
        f"{task.pool_indices['neutral'] - start}"
    )
    # And up to n_trials distinct image filenames
    assert len(seen_files) == min(n_trials,
                                  len(task.image_pools["neutral"]))


def test_run_trial_uses_resolve_render_split(task, monkeypatch):
    """End-to-end check: stub _present_phase to count distractor draws and
    confirm _resolve_distractor is called exactly once per trial."""

    resolve_calls = []
    render_calls = []

    real_resolve = task._resolve_distractor
    real_render = task._render_distractor

    def counting_resolve(trial):
        resolve_calls.append(trial.trial_num)
        return real_resolve(trial)

    def counting_render(payload):
        render_calls.append(payload.get("type"))
        return real_render(payload)

    monkeypatch.setattr(task, "_resolve_distractor", counting_resolve)
    monkeypatch.setattr(task, "_render_distractor", counting_render)

    # Stub _present_phase so we just invoke draw_fn 15 times (no real timing)
    def fake_present_phase(draw_fn, duration_ms, trial=None, check_keys=False):
        for _ in range(15):
            draw_fn()
        return None, float('nan')

    monkeypatch.setattr(task, "_present_phase", fake_present_phase)

    trial = _make_trial("neutral_image", 4)
    if not task.image_pools["neutral"]:
        pytest.skip("no neutral images")

    task.run_trial(trial)

    assert len(resolve_calls) == 1, \
        f"expected 1 resolve, got {len(resolve_calls)}"
    # The 15 render calls correspond to the 15 frames in the distractor phase
    assert len(render_calls) == 15, \
        f"expected 15 renders, got {len(render_calls)}"
    assert all(rc == "image" for rc in render_calls)

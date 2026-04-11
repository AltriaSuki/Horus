"""Worker initialisation smoke tests.

These tests catch the kind of regression we hit when MediaPipe 0.10.33 dropped
the legacy ``mp.solutions`` namespace on macOS arm64 — the engine tests passed
because they only imported the module, but the real worker subprocess crashed
inside ``GazeSystem.__init__`` the first time it tried to create a FaceMesh.

The test runs ``GazeSystem.__init__`` end-to-end with:

* ``SDL_VIDEODRIVER=dummy``  → pygame creates a virtual display, no GUI
* a mocked ``cv2.VideoCapture`` → no real camera required
* the real ModelAdapter / mediapipe / pygame stack

If this passes, the worker can spawn cleanly on a CI box.
"""

from __future__ import annotations

import os
from unittest import mock

import numpy as np
import pytest

# Force pygame into headless mode BEFORE any pygame import
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def test_mediapipe_solutions_namespace_exists():
    """Sanity check — the legacy solutions API must be available.

    See engine/pyproject.toml: mediapipe is pinned to <0.10.20 because newer
    builds removed ``mp.solutions`` on macOS arm64.
    """
    import mediapipe as mp

    assert hasattr(mp, "solutions"), (
        "mediapipe.solutions is missing — likely a too-new mediapipe "
        "release. Pin to <0.10.20 in engine/pyproject.toml."
    )
    assert hasattr(mp.solutions, "face_mesh")
    assert hasattr(mp.solutions.face_mesh, "FaceMesh")


def test_gaze_system_can_initialise_headless():
    """Construct a real ``GazeSystem`` without a camera or a screen.

    We patch ``cv2.VideoCapture`` to return a fake that yields a single
    blank frame, and rely on ``SDL_VIDEODRIVER=dummy`` to keep pygame from
    opening a real window.
    """
    import pygame
    pygame.init()

    fake_cap = mock.MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
    fake_cap.set.return_value = True

    with mock.patch("cv2.VideoCapture", return_value=fake_cap):
        from adhd_engine.worker.gaze_system import GazeSystem

        system = GazeSystem(subject_id="HEADLESS_INIT_TEST")
        try:
            assert system.cap is fake_cap
            assert system.face_mesh is not None
            assert system.model is not None
            # 13 calibration points laid out in screen coords
            assert len(system.calibration_points) == 13
            # Pupil smoothing buffer is wired in
            from collections import deque
            assert isinstance(system._pupil_smooth_buf, deque)
        finally:
            try:
                system.close()
            except Exception:
                pass


def test_gaze_system_extract_handles_blank_frame():
    """Feed a blank frame through ``_extract`` — it should return None
    (no face detected) without crashing.
    """
    import pygame
    pygame.init()

    fake_cap = mock.MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))

    with mock.patch("cv2.VideoCapture", return_value=fake_cap):
        from adhd_engine.worker.gaze_system import GazeSystem

        system = GazeSystem(subject_id="EXTRACT_TEST")
        try:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            result = system._extract(blank)
            # Blank frame has no face → MediaPipe returns no landmarks → None
            assert result is None
        finally:
            try:
                system.close()
            except Exception:
                pass

"""Centralized paths and runtime constants.

All filesystem paths used by the engine should resolve through this module so
they can be overridden cleanly in tests and on different platforms.

The model assets in `model/` are vendored read-only — we resolve their paths
relative to the repository root rather than copying them.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

# ---------------------------------------------------------------------------
# Repository layout (resolved at import time)
# ---------------------------------------------------------------------------

# engine/adhd_engine/config.py → repo root is two parents up
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Vendored model assets — never written to from inside engine/
MODEL_ROOT = REPO_ROOT / "model"
ADHD_CLASSIFIER_DIR = MODEL_ROOT / "adhd_pupil_classifier"
SAVED_MODELS_DIR = ADHD_CLASSIFIER_DIR / "saved_models"
FEATURES_CSV = ADHD_CLASSIFIER_DIR / "features.csv"

EYE_TRACKER_DIR = MODEL_ROOT / "eye_tracker"
AFFNET_CHECKPOINT = EYE_TRACKER_DIR / "model_pth" / "affnet.pth.tar"
STIMULI_DIR = EYE_TRACKER_DIR / "stimuli"

# ---------------------------------------------------------------------------
# User-data directory (per-machine writable storage)
# ---------------------------------------------------------------------------

APP_NAME = "adhd_app"
APP_AUTHOR = "adhd_engine"

USER_DATA_DIR = Path(
    os.environ.get("ADHD_APP_DATA_DIR")
    or user_data_dir(APP_NAME, APP_AUTHOR)
)
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = USER_DATA_DIR / "data.db"
SESSION_LOG_DIR = USER_DATA_DIR / "session_logs"
SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Runtime constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Pupil signal smoothing — see plan §1.5.5
PUPIL_SMOOTH_WINDOW = 5

# Distractor background color — see plan §1.5.4
DISTRACTOR_BG_GRAY = (40, 40, 40)

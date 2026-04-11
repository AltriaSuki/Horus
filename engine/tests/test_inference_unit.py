"""Unit tests for ``adhd_engine.worker.inference``.

The most important assertion here is **plan §1.5.1** — the ``pupil_slope``
feature must produce comparable numerical values whether the underlying pupil
time series was sampled at 30 Hz (webcam) or 1000 Hz (Tobii). The trained
``StandardScaler`` was fit on 1000 Hz statistics, so the inference output must
match that scale or the standardised feature is garbage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pytest

from adhd_engine.worker import inference


# ---------------------------------------------------------------------------
# A minimal stand-in for SternbergTask's TrialData dataclass.
# ``extract_features`` only touches a handful of attributes; we don't want to
# pull in pygame just to construct trial fixtures.
# ---------------------------------------------------------------------------


@dataclass
class FakeTrial:
    trial_num: int
    block_num: int
    load: int
    distractor_type: int
    distractor_name: str
    response: str | None
    reaction_time: float
    correct: int
    pupil_series: List[float] = field(default_factory=list)
    gaze_x_series: List[float] = field(default_factory=list)
    gaze_y_series: List[float] = field(default_factory=list)


def _build_synthetic_trials(
    *,
    fps: int,
    n_trials: int = 60,
    trial_seconds: float = 6.0,
    slope_per_ms: float = 1e-4,
) -> List[FakeTrial]:
    """Create trials whose pupil time series is a known linear ramp.

    The baseline (first 500 ms) is centred on a constant value; the
    post-baseline window rises linearly with rate ``slope_per_ms`` (in units of
    raw pupil per millisecond). After baseline correction the slope becomes
    ``slope_per_ms / |baseline|`` per ms — independent of fps.

    Both 30 Hz and 1000 Hz versions of the same physical signal must produce
    the same ``pupil_slope`` feature value (within tolerance) once the
    inference module's plan-§1.5.1 unit fix is applied.
    """
    n_samples = int(round(trial_seconds * fps))
    baseline_value = 0.20  # raw "pupil proxy" baseline
    baseline_samples = max(1, int(round(0.5 * fps)))  # 500 ms

    trials: List[FakeTrial] = []
    rng = np.random.default_rng(42)

    for i in range(n_trials):
        pupil = np.empty(n_samples, dtype=float)
        pupil[:baseline_samples] = baseline_value

        for k in range(baseline_samples, n_samples):
            # Linear ramp in raw units
            ms_post = (k - baseline_samples) * (1000.0 / fps)
            pupil[k] = baseline_value + slope_per_ms * ms_post

        # Tiny jitter so np.argmax is well-defined
        pupil = pupil + rng.normal(0, 1e-6, size=pupil.shape)

        trials.append(
            FakeTrial(
                trial_num=i + 1,
                block_num=(i // 20) + 1,
                load=1 if i % 2 == 0 else 2,
                distractor_type=3 + (i % 4),
                distractor_name=["blank", "neutral_image",
                                 "emotional_image", "shape"][i % 4],
                response="f" if i % 2 == 0 else "j",
                reaction_time=0.7 + rng.normal(0, 0.05),
                correct=1,
                pupil_series=pupil.tolist(),
                gaze_x_series=[500.0] * n_samples,
                gaze_y_series=[500.0] * n_samples,
            )
        )
    return trials


# ---------------------------------------------------------------------------
# The headline test: slope value must be comparable across sample rates
# ---------------------------------------------------------------------------


def test_pupil_slope_unit_is_invariant_across_sample_rates():
    """Plan §1.5.1 — slope must be in `Δ% per ms` regardless of fps.

    Without the fix, the inference at 30 Hz produced slope values ~33×
    larger than the same physical signal at 1000 Hz. After multiplying by
    `(fps / 1000.0)` the two should agree to ~10%.
    """
    slope_per_ms = 5e-5  # raw pupil per ms

    feats_30 = inference.extract_features(
        _build_synthetic_trials(fps=30, slope_per_ms=slope_per_ms),
        fps=30,
    )
    feats_1000 = inference.extract_features(
        _build_synthetic_trials(fps=1000, slope_per_ms=slope_per_ms),
        fps=1000,
    )

    s30 = feats_30["pupil_slope"]
    s1000 = feats_1000["pupil_slope"]

    assert s30 != 0.0, "30 Hz slope should not be zero for a known ramp"
    assert s1000 != 0.0, "1000 Hz slope should not be zero for a known ramp"
    assert math.copysign(1, s30) == math.copysign(1, s1000), (
        f"slope sign flipped: 30Hz={s30}, 1000Hz={s1000}"
    )

    ratio = abs(s30) / abs(s1000)
    # The fix should bring the two within ~20% (the residual gap is
    # discretization error in the linear-fit window length).
    assert 0.8 < ratio < 1.25, (
        f"slope unit fix failed: 30Hz={s30:.6e}, 1000Hz={s1000:.6e}, "
        f"ratio={ratio:.3f} (expected ~1.0)"
    )


def test_extract_features_returns_27_keys():
    feats = inference.extract_features(
        _build_synthetic_trials(fps=30, n_trials=20),
        fps=30,
    )
    assert isinstance(feats, dict)
    assert len(feats) == 27, f"expected 27 features, got {len(feats)}"

    expected_keys = {
        "mean_rt", "std_rt", "cv_rt", "rt_skewness", "accuracy",
        "omission_rate", "rt_diff_correct_incorrect",
        "pupil_max_peak", "pupil_mean_change", "pupil_std_of_means",
        "pupil_overall_var", "pupil_peak_latency", "pupil_slope",
        "pupil_auc", "pupil_late_minus_early", "pupil_slope_var",
        "pupil_load_diff", "pupil_peak_load_diff",
        "gaze_var_x", "gaze_var_y", "gaze_path_normalized",
        "mean_rt_load1", "mean_rt_load2", "rt_load_diff", "acc_load_diff",
        "rt_distractor_slope", "acc_distractor_slope",
    }
    assert set(feats.keys()) == expected_keys


def test_predict_adhd_smoke():
    """Loads the real RF model and runs prediction on synthetic features."""
    feats = inference.extract_features(
        _build_synthetic_trials(fps=30, n_trials=40),
        fps=30,
    )
    result = inference.predict_adhd(feats)
    assert result["prediction"] in ("ADHD", "Control")
    assert 0.0 <= result["adhd_probability"] <= 1.0
    assert result["risk_level"] in ("HIGH", "MODERATE", "LOW", "MINIMAL")
    assert "feature_importance" in result
    assert "model_info" in result

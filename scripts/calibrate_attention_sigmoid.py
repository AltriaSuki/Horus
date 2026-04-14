#!/usr/bin/env python3
"""Calibrate sigmoid params for compute_attention_profile from features.csv.

For each of the 12 features used by the 6-dim attention profile, compute:
  - center = midpoint of Control P50 and ADHD P50 (on abs values for abs-features)
  - k = steepness so sigmoid passes 0.2→0.8 across the two-group means
  - invert direction based on domain knowledge (higher raw → healthier or not)

Outputs:
  1. JSON file at tauri-adhd/models/attention_sigmoid_params.json
  2. Rust-ready sigmoid_map code snippet on stdout.
"""

import csv
import json
import math
import statistics
from pathlib import Path

CSV_PATH = Path("/Users/feilun/coding/login/model/adhd_pupil_classifier/features.csv")
OUT_JSON = Path("/Users/feilun/coding/login/tauri-adhd/models/attention_sigmoid_params.json")

# Feature name → (use_abs, invert)
# invert = True means "lower raw value = higher attention score"
FEATURE_SPECS = {
    "rt_skewness":           (False, True),
    "pupil_late_minus_early":(False, True),
    "rt_load_diff":          (True,  True),
    "pupil_load_diff":       (True,  True),
    "rt_distractor_slope":   (True,  True),
    "acc_distractor_slope":  (True,  True),
    "cv_rt":                 (False, True),
    "pupil_max_peak":        (False, False),
    "pupil_auc":             (False, False),
    "gaze_var_x":            (False, True),
    "gaze_path_normalized":  (False, True),
}


def quantile(sorted_vals, q):
    """Linear-interpolation quantile (matches numpy/pandas default)."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_params(ctrl_vals, adhd_vals, use_abs):
    if use_abs:
        ctrl_vals = [abs(v) for v in ctrl_vals]
        adhd_vals = [abs(v) for v in adhd_vals]

    cs = sorted(ctrl_vals)
    asv = sorted(adhd_vals)

    c50 = quantile(cs, 0.5)
    a50 = quantile(asv, 0.5)
    c25 = quantile(cs, 0.25)
    c75 = quantile(cs, 0.75)
    a25 = quantile(asv, 0.25)
    a75 = quantile(asv, 0.75)

    center = (c50 + a50) / 2.0
    diff = abs(a50 - c50)
    # k = 2 * ln(4) / D  ≈ 2.7726 / D   →   sigmoid(center ± D/2) ≈ 0.2 / 0.8
    k = 1.0 if diff < 1e-12 else 2.0 * math.log(4.0) / diff

    return {
        "center": center,
        "k": k,
        "control_p50": c50,
        "adhd_p50": a50,
        "control_p25": c25,
        "control_p75": c75,
        "adhd_p25": a25,
        "adhd_p75": a75,
    }


def main():
    # features.csv has a UTF-8 BOM → encoding='utf-8-sig'
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    n_ctrl = sum(1 for r in rows if int(r["label"]) == 0)
    n_adhd = sum(1 for r in rows if int(r["label"]) == 1)
    print(f"Loaded {len(rows)} rows; Control={n_ctrl}, ADHD={n_adhd}")

    def column(feat, label):
        return [float(r[feat]) for r in rows if int(r["label"]) == label]

    results = {}
    for feat, (use_abs, invert) in FEATURE_SPECS.items():
        p = compute_params(column(feat, 0), column(feat, 1), use_abs)
        p["invert"] = invert
        p["use_abs"] = use_abs
        results[feat] = p

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_JSON}")

    # Summary table
    print()
    print(f"{'feature':<28} {'ctrl_p50':>14} {'adhd_p50':>14} {'center':>14} {'k':>12} {'invert':>7}")
    print("-" * 95)
    for feat, p in results.items():
        print(f"{feat:<28} {p['control_p50']:>14.6g} {p['adhd_p50']:>14.6g} "
              f"{p['center']:>14.6g} {p['k']:>12.6g} {str(p['invert']):>7}")

    # Rust-ready snippets
    print()
    print("// === Rust snippet (sigmoid_map calls) ===")
    templates = [
        ("rt_skewness",            'sigmoid_map(skew, {center:.6}, {k:.6}, {invert})'),
        ("pupil_late_minus_early", 'sigmoid_map(late_early, {center:.6}, {k:.6}, {invert})'),
        ("rt_load_diff",           'sigmoid_map(rt_diff, {center:.6}, {k:.6}, {invert})'),
        ("pupil_load_diff",        'sigmoid_map(pupil_diff, {center:.6}, {k:.6}, {invert})'),
        ("rt_distractor_slope",    'sigmoid_map(rt_slope, {center:.6}, {k:.6}, {invert})'),
        ("acc_distractor_slope",   'sigmoid_map(acc_slope, {center:.6}, {k:.6}, {invert})'),
        ("cv_rt",                  'sigmoid_map(cv, {center:.6}, {k:.6}, {invert})'),
        ("pupil_max_peak",         'sigmoid_map(peak, {center:.6}, {k:.6}, {invert})'),
        ("pupil_auc",              'sigmoid_map(auc, {center:.6}, {k:.6}, {invert})'),
        ("gaze_var_x",             'sigmoid_map(var_x, {center:.6}, {k:.6}, {invert})'),
        ("gaze_path_normalized",   'sigmoid_map(path, {center:.6}, {k:.6}, {invert})'),
    ]
    for feat, template in templates:
        p = results[feat]
        invert_str = "true" if p["invert"] else "false"
        print(f"// {feat}: ctrl_p50={p['control_p50']:.4g} adhd_p50={p['adhd_p50']:.4g} "
              f"(use_abs={p['use_abs']})")
        print(template.format(center=p["center"], k=p["k"], invert=invert_str))


if __name__ == "__main__":
    main()

"""
Verify the invert direction of each sigmoid_map in compute_attention_profile().

For each feature used in the 6-D attention profile, compute Control P50 and
ADHD P50 from features.csv, then determine whether the code's current `invert`
flag is consistent with the data:

- ADHD P50 > Control P50  => "high value = ADHD trait" => invert=true
  (特征值越低 => 得分越高)
- ADHD P50 < Control P50  => "high value = healthy trait" => invert=false

For abs-valued features (rt_load_diff, pupil_load_diff, rt_distractor_slope,
acc_distractor_slope) we take the absolute value before computing the medians,
since the compute_attention_profile function uses abs() as its input.

Output: a table with columns
  feature | ctrl_p50 | adhd_p50 | suggested_invert | code_invert | needs_flip?
"""

import numpy as np
import pandas as pd

CSV_PATH = "/Users/feilun/coding/login/model/adhd_pupil_classifier/features.csv"

# (feature name, use_abs, code_invert_current, dimension_name)
# code_invert_current values are taken from inference.rs lines ~584-653
SPEC = [
    # sustained_attention
    ("rt_skewness",            False, True,  "sustained_attention"),
    ("pupil_late_minus_early", False, True,  "sustained_attention"),
    # cognitive_load_sensitivity
    ("rt_load_diff",           True,  True,  "cognitive_load_sensitivity"),
    ("pupil_load_diff",        True,  True,  "cognitive_load_sensitivity"),
    # distractor_resistance
    ("rt_distractor_slope",    True,  True,  "distractor_resistance"),
    ("acc_distractor_slope",   True,  True,  "distractor_resistance"),
    # response_stability
    ("cv_rt",                  False, True,  "response_stability"),
    # pupil_engagement
    ("pupil_max_peak",         False, False, "pupil_engagement"),
    ("pupil_auc",              False, False, "pupil_engagement"),
    # gaze_control
    ("gaze_var_x",             False, True,  "gaze_control"),
    ("gaze_path_normalized",   False, True,  "gaze_control"),
]


def main() -> None:
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    ctrl = df[df["label"] == 0]
    adhd = df[df["label"] == 1]
    print(f"# Loaded {len(df)} rows: {len(ctrl)} Control, {len(adhd)} ADHD")
    print()

    header = (
        f"{'feature':<26} {'dim':<28} {'ctrl_p50':>12} {'adhd_p50':>12} "
        f"{'sug_inv':>8} {'code_inv':>9} {'flip?':>6}"
    )
    print(header)
    print("-" * len(header))

    to_flip = []
    ok_count = 0
    for feat, use_abs, code_invert, dim in SPEC:
        if feat not in df.columns:
            print(f"{feat:<26} MISSING FROM CSV")
            continue
        c = ctrl[feat].dropna().to_numpy()
        a = adhd[feat].dropna().to_numpy()
        if use_abs:
            c = np.abs(c)
            a = np.abs(a)
        c_p50 = float(np.median(c))
        a_p50 = float(np.median(a))
        # Suggestion: if ADHD median > Control median, high value = ADHD trait
        # => invert (score should go down as value goes up).
        suggested_invert = a_p50 > c_p50
        flip = suggested_invert != code_invert
        if flip:
            to_flip.append((feat, code_invert, suggested_invert, dim))
        else:
            ok_count += 1
        print(
            f"{feat:<26} {dim:<28} {c_p50:>12.4f} {a_p50:>12.4f} "
            f"{str(suggested_invert):>8} {str(code_invert):>9} "
            f"{'YES' if flip else 'no':>6}"
        )

    print()
    print(f"Summary: {len(to_flip)} feature(s) need flip, {ok_count} already correct.")
    if to_flip:
        print()
        print("Features that need invert flipped:")
        for feat, old, new, dim in to_flip:
            print(f"  - {feat} ({dim}): {old} -> {new}")


if __name__ == "__main__":
    main()

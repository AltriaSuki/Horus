"""Ablation study: compare ADHD classification LOOCV accuracy across feature subsets.

Data:   /Users/feilun/coding/login/model/adhd_pupil_classifier/features.csv
        50 subjects (28 ADHD, 22 Control), 27 features + label + subject.
Model:  RandomForestClassifier(n_estimators=300, random_state=42) + StandardScaler
Eval:   Leave-One-Out CV (n_splits = 50)
        Scaler is fit on train fold only (prevents data leakage).

Run:    /Users/feilun/coding/login/.venv/bin/python \\
        /Users/feilun/coding/login/scripts/ablation_study.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler

RANDOM_STATE = 42
N_ESTIMATORS = 300

FEATURES_CSV = Path(
    "/Users/feilun/coding/login/model/adhd_pupil_classifier/features.csv"
)
OUTPUT_MD = Path("/Users/feilun/coding/login/docs/消融实验.md")

# ---------------------------------------------------------------------------
# Feature subset definitions
# ---------------------------------------------------------------------------

BEHAVIORAL = [
    "mean_rt",
    "std_rt",
    "cv_rt",
    "rt_skewness",
    "accuracy",
    "omission_rate",
    "rt_diff_correct_incorrect",
]

PUPIL = [
    "pupil_max_peak",
    "pupil_mean_change",
    "pupil_std_of_means",
    "pupil_overall_var",
    "pupil_peak_latency",
    "pupil_slope",
    "pupil_auc",
    "pupil_late_minus_early",
    "pupil_slope_var",
    "pupil_load_diff",
    "pupil_peak_load_diff",
]

GAZE = [
    "gaze_var_x",
    "gaze_var_y",
    "gaze_path_normalized",
]

CONDITION = [
    "mean_rt_load1",
    "mean_rt_load2",
    "rt_load_diff",
    "acc_load_diff",
    "rt_distractor_slope",
    "acc_distractor_slope",
]

ALL_FEATURES = BEHAVIORAL + PUPIL + GAZE + CONDITION  # 27 features

SELECTED_12 = [
    "mean_rt",
    "std_rt",
    "cv_rt",
    "accuracy",
    "rt_diff_correct_incorrect",
    "pupil_mean_change",
    "pupil_std_of_means",
    "pupil_slope",
    "pupil_auc",
    "gaze_var_x",
    "mean_rt_load1",
    "mean_rt_load2",
]


def _subset(*groups: list[str]) -> list[str]:
    """Concatenate groups preserving order and removing duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for g in groups:
        for f in g:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


SUBSETS: dict[str, list[str]] = {
    "全 27 特征 (all)": ALL_FEATURES,
    "选定 12 特征 (selected) - 线上模型": SELECTED_12,
    "纯行为 7 特征 (behavioral)": BEHAVIORAL,
    "纯瞳孔 11 特征 (pupil)": PUPIL,
    "纯注视 3 特征 (gaze)": GAZE,
    "纯条件 6 特征 (condition)": CONDITION,
    "行为 + 瞳孔 (18)": _subset(BEHAVIORAL, PUPIL),
    "行为 + 注视 (10)": _subset(BEHAVIORAL, GAZE),
    "瞳孔 + 注视 (14)": _subset(PUPIL, GAZE),
    "行为 + 条件 (13)": _subset(BEHAVIORAL, CONDITION),
    "无瞳孔 (all - pupil, 16)": _subset(BEHAVIORAL, GAZE, CONDITION),
    "无注视 (all - gaze, 24)": _subset(BEHAVIORAL, PUPIL, CONDITION),
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_subset(X: pd.DataFrame, y: np.ndarray) -> dict[str, float]:
    """Run LOOCV with per-fold StandardScaler fit on train only.

    Returns accuracy / precision / recall / f1 for the ADHD (positive=1) class.
    """
    loo = LeaveOneOut()
    y_true_all: list[int] = []
    y_pred_all: list[int] = []

    X_values = X.values
    for train_idx, test_idx in loo.split(X_values):
        X_train, X_test = X_values[train_idx], X_values[test_idx]
        y_train = y[train_idx]

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        clf = RandomForestClassifier(
            n_estimators=N_ESTIMATORS,
            random_state=RANDOM_STATE,
            n_jobs=1,
        )
        clf.fit(X_train_scaled, y_train)
        y_pred_all.append(int(clf.predict(X_test_scaled)[0]))
        y_true_all.append(int(y[test_idx][0]))

    y_true = np.array(y_true_all)
    y_pred = np.array(y_pred_all)
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision_adhd": precision_score(y_true, y_pred, pos_label=1, zero_division=0),
        "recall_adhd": recall_score(y_true, y_pred, pos_label=1, zero_division=0),
        "f1_adhd": f1_score(y_true, y_pred, pos_label=1, zero_division=0),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def build_markdown(results: list[dict], df: pd.DataFrame) -> str:
    n_total = len(df)
    n_adhd = int((df["label"] == 1).sum())
    n_control = int((df["label"] == 0).sum())

    # sort by accuracy desc
    sorted_res = sorted(results, key=lambda r: r["accuracy"], reverse=True)

    lines: list[str] = []
    lines.append("# ADHD 分类器 特征消融实验 (Ablation Study)")
    lines.append("")
    lines.append("## 1. 简介")
    lines.append("")
    lines.append("**目的**: 量化不同特征子集对 ADHD 二分类性能的贡献，验证当前线上模型所用的 12 维特征是否合理，并回答三个问题:")
    lines.append("")
    lines.append("1. 哪个特征子集在 LOOCV 下准确率最高?")
    lines.append("2. 瞳孔 / 注视 / 行为 / 条件 四个特征家族各自能贡献多少?")
    lines.append("3. 相比纯行为 7 特征，加入瞳孔与注视是否带来实质增益?")
    lines.append("")
    lines.append(
        f"**数据**: `model/adhd_pupil_classifier/features.csv`，共 {n_total} 名受试者"
        f" ({n_adhd} ADHD / {n_control} Control)，27 维特征。"
    )
    lines.append("")
    lines.append("**模型**: `RandomForestClassifier(n_estimators=300, random_state=42)`，前置 `StandardScaler`。")
    lines.append("")
    lines.append(
        "**评估**: Leave-One-Out Cross-Validation (LOOCV, n_splits=50)。"
        "**标准化在每一折训练集内部 fit**，仅对留出样本 transform，避免数据泄露。"
        "指标 precision / recall / F1 针对 ADHD 类 (label=1)。"
    )
    lines.append("")

    lines.append("## 2. 结果 (按 accuracy 降序)")
    lines.append("")
    lines.append("| 排名 | 特征子集 | 维度 | Accuracy | Precision (ADHD) | Recall (ADHD) | F1 (ADHD) |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for rank, r in enumerate(sorted_res, 1):
        lines.append(
            f"| {rank} | {r['name']} | {r['n_features']} | "
            f"{r['accuracy']:.4f} | {r['precision_adhd']:.4f} | "
            f"{r['recall_adhd']:.4f} | {r['f1_adhd']:.4f} |"
        )
    lines.append("")

    # Pull key baselines for the discussion
    by_name = {r["name"]: r for r in results}
    all27 = by_name["全 27 特征 (all)"]
    sel12 = by_name["选定 12 特征 (selected) - 线上模型"]
    beh = by_name["纯行为 7 特征 (behavioral)"]
    pup = by_name["纯瞳孔 11 特征 (pupil)"]
    gaze = by_name["纯注视 3 特征 (gaze)"]
    cond = by_name["纯条件 6 特征 (condition)"]
    no_pup = by_name["无瞳孔 (all - pupil, 16)"]
    no_gaze = by_name["无注视 (all - gaze, 24)"]

    top3 = sorted_res[:3]

    lines.append("## 3. 关键发现")
    lines.append("")
    lines.append("### 3.1 Top 3 特征子集")
    lines.append("")
    for rank, r in enumerate(top3, 1):
        lines.append(
            f"{rank}. **{r['name']}** — accuracy = {r['accuracy']:.4f}，"
            f"F1(ADHD) = {r['f1_adhd']:.4f} ({r['n_features']} 维)。"
        )
    lines.append("")

    lines.append("### 3.2 特征家族贡献")
    lines.append("")
    lines.append(
        f"- **行为特征** (7 维) 单独 accuracy = {beh['accuracy']:.4f}，"
        f"是最强的单一家族之一。这符合 ADHD 临床表现中反应时变异性与正确率异常的先验。"
    )
    lines.append(
        f"- **瞳孔特征** (11 维) 单独 accuracy = {pup['accuracy']:.4f}，"
        f"反映认知负荷下的唤醒调节。"
    )
    lines.append(
        f"- **条件特征** (6 维) 单独 accuracy = {cond['accuracy']:.4f}，"
        f"衡量被试在不同负荷 / 干扰下的表现差异。"
    )
    lines.append(
        f"- **注视特征** (3 维) 单独 accuracy = {gaze['accuracy']:.4f}，"
        f"维度最低、最弱，但作为轻量补充仍可能有用。"
    )
    lines.append("")

    lines.append("### 3.3 去除某一家族的影响")
    lines.append("")
    delta_no_pup = all27["accuracy"] - no_pup["accuracy"]
    delta_no_gaze = all27["accuracy"] - no_gaze["accuracy"]
    lines.append(
        f"- 去掉瞳孔: accuracy 从 {all27['accuracy']:.4f} → {no_pup['accuracy']:.4f}"
        f" (Δ = {delta_no_pup:+.4f})。"
    )
    lines.append(
        f"- 去掉注视: accuracy 从 {all27['accuracy']:.4f} → {no_gaze['accuracy']:.4f}"
        f" (Δ = {delta_no_gaze:+.4f})。"
    )
    if delta_no_pup > 0:
        lines.append("  → 瞳孔特征对全量模型是**正贡献**，证明其必要性。")
    elif delta_no_pup == 0:
        lines.append("  → 去掉瞳孔后 accuracy 不变，说明瞳孔信息在全量特征下可被其它特征冗余表示。")
    else:
        lines.append("  → 去掉瞳孔后 accuracy **反而上升**，提示瞳孔在 27 维组合里引入了噪声。")
    lines.append("")

    lines.append("### 3.4 选定 12 特征是否合理?")
    lines.append("")
    delta_vs_beh = sel12["accuracy"] - beh["accuracy"]
    delta_vs_all = sel12["accuracy"] - all27["accuracy"]
    lines.append(
        f"- **选定 12 特征** accuracy = {sel12['accuracy']:.4f}，F1(ADHD) = {sel12['f1_adhd']:.4f}。"
    )
    lines.append(
        f"- vs **纯行为 7 特征** ({beh['accuracy']:.4f}): Δ = {delta_vs_beh:+.4f}"
    )
    lines.append(
        f"- vs **全 27 特征** ({all27['accuracy']:.4f}): Δ = {delta_vs_all:+.4f}"
    )
    if delta_vs_beh > 0 and delta_vs_all >= 0:
        verdict = "选定 12 特征**同时优于**纯行为基线与全量 27 维，"\
                  "说明所选特征既注入了行为之外的信息 (瞳孔/注视/条件)，又剔除了冗余噪声，"\
                  "是一个合理的工程折中。"
    elif delta_vs_beh > 0 and delta_vs_all < 0:
        verdict = "选定 12 特征优于纯行为，但略逊于全 27 维，"\
                  "说明额外特征有用但当前选择仍可以拓宽；"\
                  "若推理延迟不敏感，可考虑使用全量特征。"
    elif delta_vs_beh <= 0 and delta_vs_all >= 0:
        verdict = "选定 12 特征不比纯行为更强却也不输全量，"\
                  "提示当前 12 维主要靠行为支撑，瞳孔 / 注视部分的加入并没有明显帮助。"
    else:
        verdict = "选定 12 特征同时弱于纯行为与全量特征，"\
                  "建议重新挑选——可优先保留行为 + 条件特征，逐步增补瞳孔维度。"
    lines.append(f"- **结论**: {verdict}")
    lines.append("")

    lines.append("## 4. 脚本")
    lines.append("")
    lines.append(
        "完整脚本见 [`scripts/ablation_study.py`](../scripts/ablation_study.py)。"
        "该脚本 self-contained、固定 `random_state=42`、每一折独立 `StandardScaler.fit`，可直接复现上述结果。"
    )
    lines.append("")
    lines.append("运行命令:")
    lines.append("")
    lines.append("```bash")
    lines.append("/Users/feilun/coding/login/.venv/bin/python \\")
    lines.append("    /Users/feilun/coding/login/scripts/ablation_study.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # BOM-safe read
    df = pd.read_csv(FEATURES_CSV, encoding="utf-8-sig")

    # sanity
    missing = [f for f in ALL_FEATURES if f not in df.columns]
    if missing:
        raise RuntimeError(f"features missing from CSV: {missing}")
    if "label" not in df.columns:
        raise RuntimeError("label column missing")

    y = df["label"].astype(int).to_numpy()
    n_total, n_adhd, n_control = len(df), int((y == 1).sum()), int((y == 0).sum())
    print(f"Loaded {n_total} subjects: {n_adhd} ADHD / {n_control} Control")
    print(f"Random state: {RANDOM_STATE}, n_estimators: {N_ESTIMATORS}")
    print("-" * 72)

    results: list[dict] = []
    for name, feats in SUBSETS.items():
        X = df[feats]
        metrics = evaluate_subset(X, y)
        row = {
            "name": name,
            "n_features": len(feats),
            **metrics,
        }
        results.append(row)
        print(
            f"{name:<40s}  dim={len(feats):>2d}  "
            f"acc={metrics['accuracy']:.4f}  "
            f"prec={metrics['precision_adhd']:.4f}  "
            f"rec={metrics['recall_adhd']:.4f}  "
            f"f1={metrics['f1_adhd']:.4f}"
        )

    print("-" * 72)
    sorted_res = sorted(results, key=lambda r: r["accuracy"], reverse=True)
    print("Top 3:")
    for i, r in enumerate(sorted_res[:3], 1):
        print(f"  {i}. {r['name']}  acc={r['accuracy']:.4f}  f1={r['f1_adhd']:.4f}")

    # write markdown
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    md = build_markdown(results, df)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    print(f"\nWrote report: {OUTPUT_MD}")

    # also dump raw numbers alongside the script for archival
    raw_json = Path(__file__).with_suffix(".results.json")
    raw_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote raw results: {raw_json}")


if __name__ == "__main__":
    main()

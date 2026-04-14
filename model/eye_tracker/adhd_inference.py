"""
ADHD 实时推理模块
================
从 Sternberg 任务的 trial 结果中提取与训练集相同的 27 个特征，
加载保存的随机森林模型进行 ADHD vs Control 预测。
"""

import os
import numpy as np
import joblib
from scipy import stats
from scipy.integrate import trapezoid


def extract_features(trial_results, fps=30):
    """
    从 Sternberg 任务结果中提取 27 个特征,
    与 adhd_classifier.py 中 extract_baseline_corrected_features() 完全对应。

    Args:
        trial_results: list of TrialData (来自 SternbergTask.run())
        fps: 数据采集帧率 (用于瞳孔基线校准)

    Returns:
        dict: {feature_name: value} 27 个特征
    """
    # ====== 收集行为数据 ======
    rtimes = []
    performs = []
    loads = []
    distractors = []

    for t in trial_results:
        if t.response is not None:
            # 注意: 任务中 reaction_time 以秒为单位,
            # 但训练数据(Pupil_dataset)的 RTime 为毫秒, 需要转换
            rtimes.append(t.reaction_time * 1000.0)
            performs.append(float(t.correct))
        else:
            rtimes.append(float('nan'))
            performs.append(float('nan'))
        loads.append(float(t.load))
        distractors.append(float(t.distractor_type))

    rtime = np.array(rtimes)
    perform = np.array(performs)
    load = np.array(loads)
    distractor = np.array(distractors)
    n_trials = len(rtime)

    valid_rt_mask = ~np.isnan(rtime)
    valid_rt = rtime[valid_rt_mask]

    # ====== 行为特征 (7) ======
    mean_rt = float(np.nanmean(rtime)) if len(valid_rt) > 0 else 0.0
    std_rt = float(np.nanstd(rtime)) if len(valid_rt) > 1 else 0.0
    cv_rt = std_rt / mean_rt if mean_rt > 0 else 0.0
    rt_skewness = float(stats.skew(valid_rt, nan_policy='omit')) \
        if len(valid_rt) > 3 else 0.0

    valid_perform = perform[valid_rt_mask]
    accuracy = float(np.nanmean(valid_perform)) \
        if len(valid_perform) > 0 else 0.0
    omission_rate = float(np.sum(np.isnan(rtime))) / max(n_trials, 1)

    correct_mask = valid_rt_mask & (perform == 1)
    incorrect_mask = valid_rt_mask & (perform == 0)
    rt_correct = float(np.nanmean(rtime[correct_mask])) \
        if np.any(correct_mask) else 0.0
    rt_incorrect = float(np.nanmean(rtime[incorrect_mask])) \
        if np.any(incorrect_mask) else 0.0
    if np.isnan(rt_correct) or np.isnan(rt_incorrect):
        rt_diff_ci = 0.0
    else:
        rt_diff_ci = rt_incorrect - rt_correct

    # ====== 瞳孔特征 (11) ======
    # 基线帧数 = fixation 阶段 (500ms)
    baseline_frames = max(1, int(fps * 0.5))

    trial_pupil_feats = []
    trial_pupil_by_load = {1: [], 2: []}

    for t in trial_results:
        pupil = np.array(t.pupil_series, dtype=float)
        if len(pupil) < baseline_frames * 3:
            continue

        valid = ~np.isnan(pupil)
        if np.sum(valid) < baseline_frames:
            continue

        valid_idx = np.where(valid)[0]
        bl_idx = valid_idx[:baseline_frames]
        baseline = np.nanmean(pupil[bl_idx])

        if baseline == 0 or np.isnan(baseline):
            continue

        corrected = (pupil - baseline) / abs(baseline)

        if len(valid_idx) <= baseline_frames:
            continue
        post_idx = valid_idx[baseline_frames:]
        post_data = corrected[post_idx]
        post_data = post_data[~np.isnan(post_data)]

        if len(post_data) < 10:
            continue

        max_peak = float(np.max(post_data))
        mean_change = float(np.mean(post_data))
        peak_latency = float(np.argmax(post_data)) / len(post_data)

        slope_len = min(len(post_data) // 3, int(fps * 1.0))
        if slope_len > 5:
            slope_raw = float(np.polyfit(np.arange(slope_len),
                                         post_data[:slope_len], 1)[0])
            # Unit fix — training data is 1000 Hz (Δ%/ms); inference is
            # at `fps` Hz so polyfit slope is Δ%/frame. Multiply by
            # (fps / 1000.0) to convert to Δ%/ms, matching the trained
            # StandardScaler's distribution. `pupil_slope_var` is the
            # std of per-trial slopes so it inherits the same unit fix
            # (not in top-12 selected features but kept consistent).
            slope = slope_raw * (fps / 1000.0)
        else:
            slope = 0.0

        auc = float(trapezoid(post_data)) / len(post_data)

        mid = len(post_data) // 2
        early_mean = float(np.mean(post_data[:mid]))
        late_mean = float(np.mean(post_data[mid:]))

        feats = {
            'max_peak': max_peak,
            'mean_change': mean_change,
            'peak_latency': peak_latency,
            'slope': slope,
            'auc': auc,
            'late_minus_early': late_mean - early_mean,
        }
        trial_pupil_feats.append(feats)

        if t.load in (1, 2):
            trial_pupil_by_load[t.load].append(feats)

    if len(trial_pupil_feats) > 0:
        pupil_max_peak = float(np.mean(
            [f['max_peak'] for f in trial_pupil_feats]))
        pupil_mean_change = float(np.mean(
            [f['mean_change'] for f in trial_pupil_feats]))
        pupil_std_of_means = float(np.std(
            [f['mean_change'] for f in trial_pupil_feats]))
        pupil_overall_var = float(np.var(
            [f['max_peak'] for f in trial_pupil_feats]))
        pupil_peak_latency = float(np.mean(
            [f['peak_latency'] for f in trial_pupil_feats]))
        pupil_slope = float(np.mean(
            [f['slope'] for f in trial_pupil_feats]))
        pupil_auc = float(np.mean(
            [f['auc'] for f in trial_pupil_feats]))
        pupil_late_minus_early = float(np.mean(
            [f['late_minus_early'] for f in trial_pupil_feats]))
        pupil_slope_var = float(np.std(
            [f['slope'] for f in trial_pupil_feats]))

        if (len(trial_pupil_by_load[1]) > 2 and
                len(trial_pupil_by_load[2]) > 2):
            pl1 = np.mean([f['mean_change']
                           for f in trial_pupil_by_load[1]])
            pl2 = np.mean([f['mean_change']
                           for f in trial_pupil_by_load[2]])
            pupil_load_diff = float(pl2 - pl1)
            pp1 = np.mean([f['max_peak']
                           for f in trial_pupil_by_load[1]])
            pp2 = np.mean([f['max_peak']
                           for f in trial_pupil_by_load[2]])
            pupil_peak_load_diff = float(pp2 - pp1)
        else:
            pupil_load_diff = 0.0
            pupil_peak_load_diff = 0.0
    else:
        pupil_max_peak = 0.0
        pupil_mean_change = 0.0
        pupil_std_of_means = 0.0
        pupil_overall_var = 0.0
        pupil_peak_latency = 0.0
        pupil_slope = 0.0
        pupil_auc = 0.0
        pupil_late_minus_early = 0.0
        pupil_slope_var = 0.0
        pupil_load_diff = 0.0
        pupil_peak_load_diff = 0.0

    # ====== 注视特征 (3) ======
    all_gx = []
    all_gy = []
    for t in trial_results:
        all_gx.extend(t.gaze_x_series)
        all_gy.extend(t.gaze_y_series)

    gx = np.array(all_gx, dtype=float)
    gy = np.array(all_gy, dtype=float)
    valid_g = ~np.isnan(gx) & ~np.isnan(gy)

    if np.sum(valid_g) > 100:
        vx, vy = gx[valid_g], gy[valid_g]
        gaze_var_x = float(np.var(vx))
        gaze_var_y = float(np.var(vy))
        dx, dy = np.diff(vx), np.diff(vy)
        gaze_path = float(np.sum(np.sqrt(dx ** 2 + dy ** 2)))
        gaze_path_normalized = gaze_path / len(vx)
    else:
        gaze_var_x = 0.0
        gaze_var_y = 0.0
        gaze_path_normalized = 0.0

    # ====== 条件特征 (6) ======
    load1_mask = (load == 1) & valid_rt_mask
    load2_mask = (load == 2) & valid_rt_mask

    mean_rt_load1 = float(np.nanmean(rtime[load1_mask])) \
        if np.any(load1_mask) else mean_rt
    mean_rt_load2 = float(np.nanmean(rtime[load2_mask])) \
        if np.any(load2_mask) else mean_rt
    rt_load_diff = mean_rt_load2 - mean_rt_load1

    acc_load1 = float(np.nanmean(perform[load1_mask])) \
        if np.any(load1_mask) else accuracy
    acc_load2 = float(np.nanmean(perform[load2_mask])) \
        if np.any(load2_mask) else accuracy
    acc_load_diff = acc_load1 - acc_load2

    # Distractor 梯度
    dist_levels = [3, 4, 5, 6]
    dist_rt, dist_acc = [], []
    for d in dist_levels:
        dm = (distractor == d) & valid_rt_mask
        dist_rt.append(float(np.nanmean(rtime[dm]))
                       if np.any(dm) else np.nan)
        dist_acc.append(float(np.nanmean(perform[dm]))
                        if np.any(dm) else np.nan)

    vd = [i for i in range(4) if not np.isnan(dist_rt[i])]
    if len(vd) >= 3:
        xd = np.array([dist_levels[i] for i in vd], dtype=float)
        rt_distractor_slope = float(np.polyfit(
            xd, [dist_rt[i] for i in vd], 1)[0])
        acc_distractor_slope = float(np.polyfit(
            xd, [dist_acc[i] for i in vd], 1)[0])
    else:
        rt_distractor_slope = 0.0
        acc_distractor_slope = 0.0

    # ====== 汇总 27 个特征 (与 adhd_classifier 完全一致) ======
    features = {
        'mean_rt': mean_rt,
        'std_rt': std_rt,
        'cv_rt': cv_rt,
        'rt_skewness': rt_skewness,
        'accuracy': accuracy,
        'omission_rate': omission_rate,
        'rt_diff_correct_incorrect': rt_diff_ci,
        'pupil_max_peak': pupil_max_peak,
        'pupil_mean_change': pupil_mean_change,
        'pupil_std_of_means': pupil_std_of_means,
        'pupil_overall_var': pupil_overall_var,
        'pupil_peak_latency': pupil_peak_latency,
        'pupil_slope': pupil_slope,
        'pupil_auc': pupil_auc,
        'pupil_late_minus_early': pupil_late_minus_early,
        'pupil_slope_var': pupil_slope_var,
        'pupil_load_diff': pupil_load_diff,
        'pupil_peak_load_diff': pupil_peak_load_diff,
        'gaze_var_x': gaze_var_x,
        'gaze_var_y': gaze_var_y,
        'gaze_path_normalized': gaze_path_normalized,
        'mean_rt_load1': mean_rt_load1,
        'mean_rt_load2': mean_rt_load2,
        'rt_load_diff': rt_load_diff,
        'acc_load_diff': acc_load_diff,
        'rt_distractor_slope': rt_distractor_slope,
        'acc_distractor_slope': acc_distractor_slope,
    }

    # 清理 NaN / Inf
    for k, v in features.items():
        if np.isnan(v) or np.isinf(v):
            features[k] = 0.0

    return features


def predict_adhd(features, model_dir):
    """
    加载保存的随机森林模型，对新被试进行 ADHD 预测。

    Args:
        features: dict，27 个特征 (来自 extract_features)
        model_dir: saved_models 目录路径

    Returns:
        dict: {prediction, adhd_probability, risk_level, ...}
    """
    rf = joblib.load(os.path.join(model_dir, 'random_forest.joblib'))
    scaler = joblib.load(os.path.join(model_dir, 'scaler.joblib'))
    feat_cfg = joblib.load(os.path.join(model_dir, 'feature_config.joblib'))

    all_feature_names = feat_cfg['all_features']
    selected_indices = feat_cfg['selected_indices']
    selected_names = feat_cfg['selected_features']

    # 构建特征向量 (与训练时相同顺序)
    X_full = np.array([features.get(name, 0.0)
                       for name in all_feature_names])
    X_selected = X_full[selected_indices].reshape(1, -1)
    X_scaled = scaler.transform(X_selected)

    # 预测
    pred = rf.predict(X_scaled)[0]
    prob = rf.predict_proba(X_scaled)[0]
    adhd_prob = float(prob[1])

    # 风险等级
    if adhd_prob >= 0.7:
        risk = "HIGH"
    elif adhd_prob >= 0.5:
        risk = "MODERATE"
    elif adhd_prob >= 0.3:
        risk = "LOW"
    else:
        risk = "MINIMAL"

    # 特征重要性 (用于解释)
    importances = rf.feature_importances_
    feat_imp = {selected_names[i]: float(importances[i])
                for i in range(len(selected_names))}
    feat_imp = dict(sorted(feat_imp.items(), key=lambda x: -x[1]))

    result = {
        'prediction': 'ADHD' if pred == 1 else 'Control',
        'adhd_probability': adhd_prob,
        'control_probability': float(prob[0]),
        'risk_level': risk,
        'feature_importance': feat_imp,
        'feature_values': {name: float(features.get(name, 0.0))
                           for name in selected_names},
        'model_info': 'Random Forest (LOOCV Accuracy: 80.0%)',
    }

    return result

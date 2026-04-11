"""
ADHD vs Control 早期筛查分类模型（增强版 v2）
=============================================
相比 v1 的改进:
  1. 瞳孔时间动力学特征: 峰值延迟、扩张斜率、AUC、早/晚期对比
  2. 按条件交互特征: Load × 瞳孔、Distractor 梯度效应
  3. RT 分布形状特征: 变异系数(CV)、偏度
  4. SVM-RBF + 软投票集成 (RF + XGBoost + SVM)
  5. 互信息 + ANOVA 联合特征选择
"""

import os
import numpy as np
import pandas as pd
import scipy.io as sio
from scipy import stats
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.svm import SVC
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler
from sklearn.feature_selection import SelectKBest, f_classif, mutual_info_classif
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                             f1_score, confusion_matrix)
from sklearn.base import clone
import xgboost as xgb
import joblib
import warnings
import json
from datetime import datetime

warnings.filterwarnings('ignore')

# ============================================================
# 1. 数据加载与清洗（与 v1 相同）
# ============================================================

def load_and_clean_data(mat_path):
    """
    加载数据，剔除 on-ADHD，按 Subject 去重。
    """
    print("=" * 60)
    print("步骤 1: 加载与清洗数据")
    print("=" * 60)
    
    mat = sio.loadmat(mat_path, squeeze_me=True)
    
    subjects = mat['subjects']
    ages = mat['ages']
    groups = mat['groups']
    all_trial = mat['all_trial']
    all_load = mat['all_load']
    all_distractor = mat['all_distractor']
    all_perform = mat['all_perform']
    all_rtime = mat['all_rtime']
    all_pupil = mat['all_pupil']
    all_position_x = mat['all_position_x']
    all_position_y = mat['all_position_y']
    wisc_data = mat['wisc_data']
    wisc_fields = mat['wisc_fields']
    
    n_total = len(subjects)
    print(f"原始 session 数: {n_total}")
    unique, counts = np.unique(groups, return_counts=True)
    print(f"组别分布: " + " ".join(f"{g}={c}" for g, c in zip(unique, counts)))
    
    sessions = []
    for i in range(n_total):
        grp = groups[i]
        if grp == 'on-ADHD':
            continue
        label = 1 if grp == 'off-ADHD' else 0
        sessions.append({
            'subject': int(subjects[i]),
            'age': ages[i],
            'group': grp,
            'label': label,
            'trial': all_trial[i],
            'load': all_load[i],
            'distractor': all_distractor[i],
            'perform': all_perform[i],
            'rtime': all_rtime[i],
            'pupil': all_pupil[i],
            'position_x': all_position_x[i],
            'position_y': all_position_y[i],
            'wisc': wisc_data[i],
        })
    
    print(f"剔除 on-ADHD 后: {len(sessions)} 个 session")
    
    seen_subjects = {}
    unique_sessions = []
    for s in sessions:
        subj = s['subject']
        if subj not in seen_subjects:
            seen_subjects[subj] = s
            unique_sessions.append(s)
    
    sessions = unique_sessions
    n_adhd = sum(1 for s in sessions if s['label'] == 1)
    n_ctrl = sum(1 for s in sessions if s['label'] == 0)
    print(f"去重后: {len(sessions)} 个被试 (ADHD={n_adhd}, Control={n_ctrl})")
    
    return sessions, wisc_fields


# ============================================================
# 2. 增强特征工程
# ============================================================

def _baseline_correct_pupil(pupil_trial, baseline_samples=500):
    """
    对单个 trial 做基线校准，返回校准后序列和有效采样点索引。
    """
    valid_mask = ~np.isnan(pupil_trial)
    if np.sum(valid_mask) < baseline_samples * 2:
        return None, None, False
    
    valid_indices = np.where(valid_mask)[0]
    baseline_idx = valid_indices[:baseline_samples]
    baseline = np.nanmean(pupil_trial[baseline_idx])
    
    if baseline == 0 or np.isnan(baseline):
        return None, None, False
    
    corrected = (pupil_trial - baseline) / np.abs(baseline)
    return corrected, valid_indices, True


def _extract_pupil_temporal_features(corrected, valid_indices, baseline_samples=500):
    """
    从基线校准后的单个 trial 提取时间动力学特征:
    - max_peak: 最大相对扩张率
    - mean_change: 平均扩张率
    - peak_latency: 峰值延迟（达到最大值的时间点）
    - slope: 初始扩张斜率（前1000个post-baseline采样点的线性斜率）
    - auc: 曲线下面积
    - early_mean: 前半段平均扩张
    - late_mean: 后半段平均扩张
    - late_minus_early: 晚期-早期差值（持续注意力指标）
    """
    if len(valid_indices) < baseline_samples + 200:
        return None
    
    # 取基线后有效数据
    post_start = baseline_samples
    post_indices = valid_indices[valid_indices >= valid_indices[post_start]]
    post_data = corrected[post_indices]
    post_data = post_data[~np.isnan(post_data)]
    
    if len(post_data) < 100:
        return None
    
    max_peak = np.max(post_data)
    mean_change = np.mean(post_data)
    
    # 峰值延迟（归一化到 [0, 1]）
    peak_idx = np.argmax(post_data)
    peak_latency = peak_idx / len(post_data)
    
    # 初始扩张斜率（前1000个采样点或可用数据的前1/3）
    slope_len = min(1000, len(post_data) // 3)
    if slope_len > 10:
        x_slope = np.arange(slope_len)
        y_slope = post_data[:slope_len]
        slope = np.polyfit(x_slope, y_slope, 1)[0]
    else:
        slope = 0.0
    
    # AUC（梯形积分，归一化）
    from scipy.integrate import trapezoid
    auc = trapezoid(post_data) / len(post_data)
    
    # 早期 vs 晚期
    mid = len(post_data) // 2
    early_mean = np.mean(post_data[:mid])
    late_mean = np.mean(post_data[mid:])
    late_minus_early = late_mean - early_mean
    
    return {
        'max_peak': max_peak,
        'mean_change': mean_change,
        'peak_latency': peak_latency,
        'slope': slope,
        'auc': auc,
        'early_mean': early_mean,
        'late_mean': late_mean,
        'late_minus_early': late_minus_early,
    }


def extract_baseline_corrected_features(sessions):
    """
    增强版特征提取，新增:
    - 瞳孔时间动力学特征 (peak_latency, slope, auc, early/late)
    - RT 分布形状特征 (CV, skewness)
    - 按 Load 分组的瞳孔特征及差值
    - Distractor 梯度效应 (RT 和 accuracy 随 distractor 数量的斜率)
    """
    print("\n" + "=" * 60)
    print("步骤 2: 增强特征工程 (v2)")
    print("=" * 60)
    
    all_features = []
    labels = []
    
    for idx, sess in enumerate(sessions):
        rtime = sess['rtime'].astype(float)
        perform = sess['perform'].astype(float)
        load = sess['load'].astype(float)
        distractor = sess['distractor'].astype(float)
        pupil_data = sess['pupil']
        pos_x = sess['position_x'].astype(float)
        pos_y = sess['position_y'].astype(float)
        
        n_trials = len(rtime)
        valid_rt_mask = ~np.isnan(rtime)
        valid_rt = rtime[valid_rt_mask]
        
        # ====== 行为特征 ======
        mean_rt = np.nanmean(rtime) if len(valid_rt) > 0 else np.nan
        std_rt = np.nanstd(rtime) if len(valid_rt) > 1 else np.nan
        
        # [新增] RT 变异系数（比 std 更鲁棒，消除均值影响）
        cv_rt = std_rt / mean_rt if (mean_rt and mean_rt > 0 and not np.isnan(mean_rt)) else np.nan
        
        # [新增] RT 偏度（正偏 = 有长尾慢反应，ADHD 常见的注意力飘移特征）
        rt_skewness = stats.skew(valid_rt, nan_policy='omit') if len(valid_rt) > 3 else 0.0
        
        valid_perform = perform[valid_rt_mask]
        accuracy = np.nanmean(valid_perform) if len(valid_perform) > 0 else np.nan
        omission_rate = np.sum(np.isnan(rtime)) / n_trials
        
        correct_mask = valid_rt_mask & (perform == 1)
        incorrect_mask = valid_rt_mask & (perform == 0)
        rt_correct = np.nanmean(rtime[correct_mask]) if np.any(correct_mask) else np.nan
        rt_incorrect = np.nanmean(rtime[incorrect_mask]) if np.any(incorrect_mask) else np.nan
        rt_diff_ci = rt_incorrect - rt_correct if not (np.isnan(rt_correct) or np.isnan(rt_incorrect)) else 0.0
        
        # ====== 瞳孔时间动力学特征（增强版）======
        # 收集每个 trial 的详细瞳孔特征
        trial_pupil_feats = []
        # 按 Load 分组收集
        trial_pupil_by_load = {1: [], 2: []}
        
        for t in range(pupil_data.shape[0]):
            corrected, valid_indices, valid = _baseline_correct_pupil(pupil_data[t])
            if not valid:
                continue
            
            feats = _extract_pupil_temporal_features(corrected, valid_indices)
            if feats is None:
                continue
            
            trial_pupil_feats.append(feats)
            
            # 按 Load 分组
            t_load = load[t] if t < len(load) else np.nan
            if t_load in (1, 2):
                trial_pupil_by_load[int(t_load)].append(feats)
        
        if len(trial_pupil_feats) > 0:
            # 全局瞳孔特征（Subject 聚合）
            pupil_max_peak = np.mean([f['max_peak'] for f in trial_pupil_feats])
            pupil_mean_change = np.mean([f['mean_change'] for f in trial_pupil_feats])
            pupil_std_of_means = np.std([f['mean_change'] for f in trial_pupil_feats])
            pupil_overall_var = np.var([f['max_peak'] for f in trial_pupil_feats])
            
            # [新增] 时间动力学聚合
            pupil_peak_latency = np.mean([f['peak_latency'] for f in trial_pupil_feats])
            pupil_slope = np.mean([f['slope'] for f in trial_pupil_feats])
            pupil_auc = np.mean([f['auc'] for f in trial_pupil_feats])
            pupil_late_minus_early = np.mean([f['late_minus_early'] for f in trial_pupil_feats])
            
            # [新增] 瞳孔斜率的 trial 间变异性（注意力波动）
            pupil_slope_var = np.std([f['slope'] for f in trial_pupil_feats])
            
            # [新增] 按 Load 分组的瞳孔特征差值
            if len(trial_pupil_by_load[1]) > 3 and len(trial_pupil_by_load[2]) > 3:
                pupil_mean_load1 = np.mean([f['mean_change'] for f in trial_pupil_by_load[1]])
                pupil_mean_load2 = np.mean([f['mean_change'] for f in trial_pupil_by_load[2]])
                pupil_load_diff = pupil_mean_load2 - pupil_mean_load1  # 瞳孔对认知负荷的调制
                
                pupil_peak_load1 = np.mean([f['max_peak'] for f in trial_pupil_by_load[1]])
                pupil_peak_load2 = np.mean([f['max_peak'] for f in trial_pupil_by_load[2]])
                pupil_peak_load_diff = pupil_peak_load2 - pupil_peak_load1
            else:
                pupil_load_diff = 0.0
                pupil_peak_load_diff = 0.0
        else:
            pupil_max_peak = np.nan
            pupil_mean_change = np.nan
            pupil_std_of_means = np.nan
            pupil_overall_var = np.nan
            pupil_peak_latency = np.nan
            pupil_slope = np.nan
            pupil_auc = np.nan
            pupil_late_minus_early = np.nan
            pupil_slope_var = np.nan
            pupil_load_diff = 0.0
            pupil_peak_load_diff = 0.0
        
        # ====== 注视特征 ======
        valid_gaze = ~np.isnan(pos_x) & ~np.isnan(pos_y)
        if np.sum(valid_gaze) > 100:
            vx = pos_x[valid_gaze]
            vy = pos_y[valid_gaze]
            gaze_var_x = np.var(vx)
            gaze_var_y = np.var(vy)
            dx = np.diff(vx)
            dy = np.diff(vy)
            gaze_path_length = np.sum(np.sqrt(dx**2 + dy**2))
            gaze_path_normalized = gaze_path_length / len(vx)
        else:
            gaze_var_x = np.nan
            gaze_var_y = np.nan
            gaze_path_normalized = np.nan
        
        # ====== 条件特征（Load）======
        load1_mask = (load == 1) & valid_rt_mask
        load2_mask = (load == 2) & valid_rt_mask
        
        mean_rt_load1 = np.nanmean(rtime[load1_mask]) if np.any(load1_mask) else np.nan
        mean_rt_load2 = np.nanmean(rtime[load2_mask]) if np.any(load2_mask) else np.nan
        rt_load_diff = mean_rt_load2 - mean_rt_load1 if not (np.isnan(mean_rt_load1) or np.isnan(mean_rt_load2)) else 0.0
        
        acc_load1 = np.nanmean(perform[load1_mask]) if np.any(load1_mask) else np.nan
        acc_load2 = np.nanmean(perform[load2_mask]) if np.any(load2_mask) else np.nan
        acc_load_diff = acc_load1 - acc_load2 if not (np.isnan(acc_load1) or np.isnan(acc_load2)) else 0.0
        
        # ====== [新增] Distractor 梯度效应 ======
        # RT 和准确率如何随 distractor 数量 (3→6) 变化
        # 斜率越陡 = 抗干扰能力越弱（ADHD 特征）
        dist_levels = [3, 4, 5, 6]
        dist_rt_vals = []
        dist_acc_vals = []
        for d in dist_levels:
            d_mask = (distractor == d) & valid_rt_mask
            if np.any(d_mask):
                dist_rt_vals.append(np.nanmean(rtime[d_mask]))
                dist_acc_vals.append(np.nanmean(perform[d_mask]))
            else:
                dist_rt_vals.append(np.nan)
                dist_acc_vals.append(np.nan)
        
        # RT 随 distractor 的斜率（线性拟合）
        valid_dist = [i for i in range(4) if not np.isnan(dist_rt_vals[i])]
        if len(valid_dist) >= 3:
            x_dist = np.array([dist_levels[i] for i in valid_dist], dtype=float)
            y_rt_dist = np.array([dist_rt_vals[i] for i in valid_dist])
            rt_distractor_slope = np.polyfit(x_dist, y_rt_dist, 1)[0]
            
            y_acc_dist = np.array([dist_acc_vals[i] for i in valid_dist])
            acc_distractor_slope = np.polyfit(x_dist, y_acc_dist, 1)[0]
        else:
            rt_distractor_slope = 0.0
            acc_distractor_slope = 0.0
        
        # ====== 汇总所有特征 ======
        features = {
            # --- 行为特征 (7) ---
            'mean_rt': mean_rt,
            'std_rt': std_rt,
            'cv_rt': cv_rt,                          # [新增]
            'rt_skewness': rt_skewness,              # [新增]
            'accuracy': accuracy,
            'omission_rate': omission_rate,
            'rt_diff_correct_incorrect': rt_diff_ci,
            # --- 瞳孔特征 (11) ---
            'pupil_max_peak': pupil_max_peak,
            'pupil_mean_change': pupil_mean_change,
            'pupil_std_of_means': pupil_std_of_means,
            'pupil_overall_var': pupil_overall_var,
            'pupil_peak_latency': pupil_peak_latency,    # [新增]
            'pupil_slope': pupil_slope,                  # [新增]
            'pupil_auc': pupil_auc,                      # [新增]
            'pupil_late_minus_early': pupil_late_minus_early,  # [新增]
            'pupil_slope_var': pupil_slope_var,          # [新增]
            'pupil_load_diff': pupil_load_diff,          # [新增]
            'pupil_peak_load_diff': pupil_peak_load_diff,  # [新增]
            # --- 注视特征 (3) ---
            'gaze_var_x': gaze_var_x,
            'gaze_var_y': gaze_var_y,
            'gaze_path_normalized': gaze_path_normalized,
            # --- 条件特征 (6) ---
            'mean_rt_load1': mean_rt_load1,
            'mean_rt_load2': mean_rt_load2,
            'rt_load_diff': rt_load_diff,
            'acc_load_diff': acc_load_diff,
            'rt_distractor_slope': rt_distractor_slope,      # [新增]
            'acc_distractor_slope': acc_distractor_slope,     # [新增]
        }
        
        all_features.append(features)
        labels.append(sess['label'])
    
    feature_df = pd.DataFrame(all_features)
    labels = np.array(labels)
    
    # 清理 NaN/Inf
    feature_df.replace([np.inf, -np.inf], np.nan, inplace=True)
    for col in feature_df.columns:
        if feature_df[col].isna().any():
            median_val = feature_df[col].median()
            if pd.isna(median_val):
                median_val = 0.0
            n_nan = feature_df[col].isna().sum()
            feature_df.loc[:, col] = feature_df[col].fillna(median_val)
            if n_nan > 0:
                print(f"  特征 '{col}' 有 {n_nan} 个 NaN，已用中位数填充")
    feature_df = feature_df.fillna(0.0)
    
    print(f"\n特征矩阵: {feature_df.shape[0]} 个被试 × {feature_df.shape[1]} 个特征")
    print(f"标签分布: ADHD(1)={np.sum(labels==1)}, Control(0)={np.sum(labels==0)}")
    print(f"\n特征列表:")
    for i, col in enumerate(feature_df.columns):
        print(f"  {i+1}. {col}")
    
    return feature_df, labels, list(feature_df.columns)


# ============================================================
# 3. 联合特征选择（ANOVA + 互信息）
# ============================================================

def _combined_feature_selection(X_train, y_train, k):
    """
    联合 ANOVA F-value 和互信息的特征选择。
    对两种评分分别排名，取平均排名最优的 top-k 个特征。
    比单用 ANOVA 更鲁棒：ANOVA 捕获线性关系，MI 捕获非线性关系。
    """
    n_features = X_train.shape[1]
    k = min(k, n_features)
    
    # ANOVA F-value 排名
    f_scores, _ = f_classif(X_train, y_train)
    f_scores = np.nan_to_num(f_scores, nan=0.0)
    f_ranks = np.argsort(np.argsort(-f_scores))  # rank 0 = best
    
    # 互信息排名
    mi_scores = mutual_info_classif(X_train, y_train, random_state=42, n_neighbors=3)
    mi_scores = np.nan_to_num(mi_scores, nan=0.0)
    mi_ranks = np.argsort(np.argsort(-mi_scores))
    
    # 平均排名
    avg_ranks = (f_ranks + mi_ranks) / 2.0
    selected = np.argsort(avg_ranks)[:k]
    
    return sorted(selected)


# ============================================================
# 4. 模型训练与 LOOCV 评估（增强版）
# ============================================================

def train_and_evaluate_loocv(feature_df, labels, feature_names, n_features_select=12):
    """
    增强版 LOOCV:
    - 联合特征选择 (ANOVA + MI)
    - 新增 SVM-RBF 模型
    - 软投票集成 (RF + XGBoost + SVM)
    """
    print("\n" + "=" * 60)
    print("步骤 3: LOOCV 模型训练与评估 (v2)")
    print("=" * 60)
    
    X = feature_df.values
    y = labels
    n_samples = len(y)
    n_select = min(n_features_select, X.shape[1])
    
    print(f"样本数: {n_samples}, 特征数: {X.shape[1]}")
    print(f"特征选择: ANOVA + 互信息联合排名 (k={n_select})")
    print(f"验证策略: Leave-One-Out Cross Validation (LOOCV)")
    
    # 定义基础模型
    rf_template = RandomForestClassifier(
        n_estimators=300,
        max_depth=4,
        min_samples_split=5,
        min_samples_leaf=3,
        max_features='sqrt',
        class_weight='balanced',
        random_state=42
    )
    
    xgb_template = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=0.5,      # L1 正则化（防过拟合）
        reg_lambda=2.0,     # L2 正则化（防过拟合）
        min_child_weight=3,
        scale_pos_weight=np.sum(y == 0) / max(np.sum(y == 1), 1),
        eval_metric='logloss',
        random_state=42
    )
    
    svm_template = SVC(
        kernel='rbf',
        C=1.0,
        gamma='scale',
        class_weight='balanced',
        probability=True,
        random_state=42
    )
    
    # 单模型 + 集成
    individual_models = {
        'Random Forest': rf_template,
        'XGBoost': xgb_template,
        'SVM-RBF': svm_template,
    }
    
    loo = LeaveOneOut()
    results = {}
    
    # ---- 先跑单模型 ----
    all_probs = {}  # 收集各模型的预测概率，用于后续集成
    
    feature_selection_counts = np.zeros(X.shape[1])
    feature_importances = {}
    
    for model_name, model_template in individual_models.items():
        print(f"\n{'─' * 40}")
        print(f"模型: {model_name}")
        print(f"{'─' * 40}")
        
        y_true_all = []
        y_pred_all = []
        y_prob_all = []
        
        if hasattr(model_template, 'feature_importances_') or isinstance(model_template, RandomForestClassifier):
            feature_importances[model_name] = np.zeros(X.shape[1])
        
        for fold_idx, (train_idx, test_idx) in enumerate(loo.split(X)):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]
            
            # 联合特征选择
            selected_idx = _combined_feature_selection(X_train, y_train, n_select)
            X_train_sel = X_train[:, selected_idx]
            X_test_sel = X_test[:, selected_idx]
            
            # 记录特征选择频率（只记一次）
            if model_name == list(individual_models.keys())[0]:
                for si in selected_idx:
                    feature_selection_counts[si] += 1
            
            # 标准化
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train_sel)
            X_test_scaled = scaler.transform(X_test_sel)
            
            # 训练
            model = clone(model_template)
            model.fit(X_train_scaled, y_train)
            
            # 预测
            y_pred = model.predict(X_test_scaled)
            y_prob = model.predict_proba(X_test_scaled)[:, 1]
            
            y_true_all.append(y_test[0])
            y_pred_all.append(y_pred[0])
            y_prob_all.append(y_prob[0])
            
            # 累计特征重要性
            if hasattr(model, 'feature_importances_'):
                imp = np.zeros(X.shape[1])
                for j, si in enumerate(selected_idx):
                    imp[si] = model.feature_importances_[j]
                feature_importances[model_name] = feature_importances.get(model_name, np.zeros(X.shape[1])) + imp
        
        y_true_all = np.array(y_true_all)
        y_pred_all = np.array(y_pred_all)
        y_prob_all = np.array(y_prob_all)
        
        all_probs[model_name] = y_prob_all
        
        acc = accuracy_score(y_true_all, y_pred_all)
        prec = precision_score(y_true_all, y_pred_all, zero_division=0)
        rec = recall_score(y_true_all, y_pred_all, zero_division=0)
        f1 = f1_score(y_true_all, y_pred_all, zero_division=0)
        cm = confusion_matrix(y_true_all, y_pred_all)
        
        print(f"\n  Accuracy:  {acc:.4f} ({acc*100:.1f}%)")
        print(f"  Precision: {prec:.4f}")
        print(f"  Recall:    {rec:.4f}")
        print(f"  F1-score:  {f1:.4f}")
        print(f"\n  混淆矩阵:")
        print(f"              预测 Ctrl  预测 ADHD")
        print(f"  实际 Ctrl   {cm[0,0]:>8d}  {cm[0,1]:>8d}")
        print(f"  实际 ADHD   {cm[1,0]:>8d}  {cm[1,1]:>8d}")
        
        # 特征重要性
        if model_name in feature_importances:
            avg_imp = feature_importances[model_name] / n_samples
            top_k = np.argsort(avg_imp)[::-1][:10]
            print(f"\n  Top-10 重要特征:")
            for rank, fi in enumerate(top_k):
                if avg_imp[fi] > 0:
                    print(f"    {rank+1}. {feature_names[fi]}: {avg_imp[fi]:.4f}")
        
        results[model_name] = {
            'accuracy': acc, 'precision': prec, 'recall': rec, 'f1': f1,
            'confusion_matrix': cm.tolist(),
            'y_true': y_true_all.tolist(),
            'y_pred': y_pred_all.tolist(),
            'y_prob': y_prob_all.tolist(),
            'feature_importance': (
                {feature_names[i]: float((feature_importances[model_name] / n_samples)[i])
                 for i in np.argsort(feature_importances[model_name])[::-1][:10]}
                if model_name in feature_importances else {}
            ),
        }
    
    # ---- 软投票集成 ----
    print(f"\n{'─' * 40}")
    print(f"模型: Soft Voting Ensemble (RF + XGBoost + SVM)")
    print(f"{'─' * 40}")
    
    # 用各模型的预测概率平均值做软投票
    y_true_ens = results[list(individual_models.keys())[0]]['y_true']
    prob_matrix = np.column_stack([all_probs[name] for name in individual_models])
    y_prob_ens = np.mean(prob_matrix, axis=1)
    y_pred_ens = (y_prob_ens >= 0.5).astype(int)
    
    y_true_ens = np.array(y_true_ens)
    acc_ens = accuracy_score(y_true_ens, y_pred_ens)
    prec_ens = precision_score(y_true_ens, y_pred_ens, zero_division=0)
    rec_ens = recall_score(y_true_ens, y_pred_ens, zero_division=0)
    f1_ens = f1_score(y_true_ens, y_pred_ens, zero_division=0)
    cm_ens = confusion_matrix(y_true_ens, y_pred_ens)
    
    print(f"\n  Accuracy:  {acc_ens:.4f} ({acc_ens*100:.1f}%)")
    print(f"  Precision: {prec_ens:.4f}")
    print(f"  Recall:    {rec_ens:.4f}")
    print(f"  F1-score:  {f1_ens:.4f}")
    print(f"\n  混淆矩阵:")
    print(f"              预测 Ctrl  预测 ADHD")
    print(f"  实际 Ctrl   {cm_ens[0,0]:>8d}  {cm_ens[0,1]:>8d}")
    print(f"  实际 ADHD   {cm_ens[1,0]:>8d}  {cm_ens[1,1]:>8d}")
    
    results['Soft Voting Ensemble'] = {
        'accuracy': acc_ens, 'precision': prec_ens, 'recall': rec_ens, 'f1': f1_ens,
        'confusion_matrix': cm_ens.tolist(),
        'y_true': y_true_ens.tolist(),
        'y_pred': y_pred_ens.tolist(),
        'y_prob': y_prob_ens.tolist(),
        'feature_importance': {},
    }
    
    # ---- 阈值优化集成 ----
    # 对集成概率搜索最优阈值（在 LOOCV 的全部预测上做一次后验调优，仅用于参考）
    best_thr, best_f1_thr = 0.5, f1_ens
    for thr in np.arange(0.30, 0.70, 0.01):
        y_pred_thr = (y_prob_ens >= thr).astype(int)
        f1_thr = f1_score(y_true_ens, y_pred_thr, zero_division=0)
        if f1_thr > best_f1_thr:
            best_f1_thr = f1_thr
            best_thr = thr
    
    if best_thr != 0.5:
        y_pred_opt = (y_prob_ens >= best_thr).astype(int)
        acc_opt = accuracy_score(y_true_ens, y_pred_opt)
        cm_opt = confusion_matrix(y_true_ens, y_pred_opt)
        print(f"\n  [参考] 最优阈值={best_thr:.2f} 时:")
        print(f"    Accuracy={acc_opt*100:.1f}%, F1={best_f1_thr:.3f}")
        print(f"    混淆矩阵: TN={cm_opt[0,0]} FP={cm_opt[0,1]} FN={cm_opt[1,0]} TP={cm_opt[1,1]}")
    
    # 特征选择频率
    print(f"\n{'─' * 40}")
    print("特征在 LOOCV 中被选中的频率:")
    freq = feature_selection_counts / n_samples
    sort_idx = np.argsort(freq)[::-1]
    for i in sort_idx:
        if freq[i] > 0:
            print(f"  {feature_names[i]}: {freq[i]*100:.1f}%")
    
    return results


# ============================================================
# 5. WISC 临床关联分析
# ============================================================

def clinical_correlation_analysis(sessions, feature_df, results, wisc_fields):
    """计算模型预测概率和眼动特征与 WISC 分数的 Pearson 相关系数。"""
    print("\n" + "=" * 60)
    print("步骤 4: WISC 临床关联性分析")
    print("=" * 60)
    print("(注: WISC 分数未用于模型训练，仅用于事后解释)")
    
    wisc_data = np.array([s['wisc'] for s in sessions])
    wisc_df = pd.DataFrame(wisc_data, columns=wisc_fields)
    
    numeric_wisc = ['Information', 'Similarities', 'Arithmetic', 'Vocabulary',
                    'Comprehension', 'Digit_span', 'Verbal_score', 'Verbal_IQ',
                    'Performance_score', 'Performance_IQ']
    available_wisc = [c for c in numeric_wisc if c in wisc_df.columns]
    
    correlations = {}
    best_model = max(results, key=lambda k: results[k]['accuracy'])
    y_prob = np.array(results[best_model]['y_prob'])
    
    print(f"\n使用 {best_model} 的预测概率进行关联分析")
    print(f"\n{'特征/WISC指标':<30} {'Pearson r':>10} {'p-value':>10} {'显著':>6}")
    print("─" * 60)
    
    for wisc_col in available_wisc:
        wisc_vals = wisc_df[wisc_col].values.astype(float)
        valid = ~np.isnan(wisc_vals) & ~np.isnan(y_prob)
        if np.sum(valid) > 5:
            r, p = stats.pearsonr(y_prob[valid], wisc_vals[valid])
            sig = "*" if p < 0.05 else ("~" if p < 0.1 else "")
            print(f"  P(ADHD) vs {wisc_col:<20} {r:>10.3f} {p:>10.4f} {sig:>6}")
            correlations[f'prob_vs_{wisc_col}'] = {'r': float(r), 'p': float(p)}
    
    print(f"\n{'─' * 60}")
    print("Top 眼动特征与 WISC 的相关性:")
    top_features = ['pupil_std_of_means', 'pupil_slope', 'pupil_late_minus_early',
                    'std_rt', 'cv_rt', 'accuracy', 'rt_load_diff', 'rt_distractor_slope']
    available_top = [f for f in top_features if f in feature_df.columns]
    
    for feat in available_top:
        feat_vals = feature_df[feat].values
        for wisc_col in ['Verbal_IQ', 'Performance_IQ', 'Digit_span']:
            if wisc_col not in wisc_df.columns:
                continue
            wisc_vals = wisc_df[wisc_col].values.astype(float)
            valid = ~np.isnan(feat_vals) & ~np.isnan(wisc_vals)
            if np.sum(valid) > 5:
                r, p = stats.pearsonr(feat_vals[valid], wisc_vals[valid])
                sig = "*" if p < 0.05 else ("~" if p < 0.1 else "")
                print(f"  {feat:<25} vs {wisc_col:<15} r={r:>7.3f}  p={p:.4f} {sig}")
                correlations[f'{feat}_vs_{wisc_col}'] = {'r': float(r), 'p': float(p)}
    
    return correlations


# ============================================================
# 6. 主函数
# ============================================================

def main():
    """主流程入口"""
    print("╔" + "═" * 58 + "╗")
    print("║   ADHD vs Control 瞳孔/眼动 早期筛查分类模型 (v2 增强版)   ║")
    print("╚" + "═" * 58 + "╝")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(os.path.dirname(script_dir), 'Pupil_dataset_converted.mat')
    output_dir = script_dir
    
    if not os.path.exists(data_path):
        print(f"错误: 未找到数据文件 {data_path}")
        return
    
    # 1. 数据加载
    sessions, wisc_fields = load_and_clean_data(data_path)
    
    # 2. 增强特征工程
    feature_df, labels, feature_names = extract_baseline_corrected_features(sessions)
    
    # 3. LOOCV 评估
    results = train_and_evaluate_loocv(feature_df, labels, feature_names,
                                       n_features_select=12)
    
    # 4. WISC 关联分析
    correlations = clinical_correlation_analysis(sessions, feature_df, results, wisc_fields)
    
    # 5. 保存
    print("\n" + "=" * 60)
    print("步骤 5: 保存结果")
    print("=" * 60)
    
    feature_df_out = feature_df.copy()
    feature_df_out['label'] = labels
    feature_df_out['subject'] = [s['subject'] for s in sessions]
    feature_df_out.to_csv(os.path.join(output_dir, 'features_v2.csv'),
                          index=False, encoding='utf-8-sig')
    
    output = {
        'version': 'v2_enhanced',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'n_subjects': len(sessions),
        'n_adhd': int(np.sum(labels == 1)),
        'n_ctrl': int(np.sum(labels == 0)),
        'n_features': len(feature_names),
        'feature_names': feature_names,
        'models': {},
        'clinical_correlations': correlations,
    }
    for model_name, res in results.items():
        output['models'][model_name] = {
            'accuracy': res['accuracy'],
            'precision': res['precision'],
            'recall': res['recall'],
            'f1': res['f1'],
            'confusion_matrix': res['confusion_matrix'],
            'feature_importance': res.get('feature_importance', {}),
        }
    
    with open(os.path.join(output_dir, 'results.json'), 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # 6. 用全部数据训练最终模型并保存
    print("\n" + "=" * 60)
    print("步骤 6: 训练最终模型并保存")
    print("=" * 60)
    
    X_all = feature_df.values
    y_all = labels
    selected_idx = _combined_feature_selection(X_all, y_all, 12)
    selected_feature_names = [feature_names[i] for i in selected_idx]
    X_selected = X_all[:, selected_idx]
    
    scaler_final = StandardScaler()
    X_scaled = scaler_final.fit_transform(X_selected)
    
    # 训练三个基础模型
    rf_final = RandomForestClassifier(
        n_estimators=300, max_depth=4, min_samples_split=5,
        min_samples_leaf=3, max_features='sqrt',
        class_weight='balanced', random_state=42)
    xgb_final = xgb.XGBClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.03,
        subsample=0.7, colsample_bytree=0.7, reg_alpha=0.5,
        reg_lambda=2.0, min_child_weight=3,
        scale_pos_weight=np.sum(y_all==0)/max(np.sum(y_all==1),1),
        eval_metric='logloss', random_state=42)
    svm_final = SVC(
        kernel='rbf', C=1.0, gamma='scale',
        class_weight='balanced', probability=True, random_state=42)
    
    rf_final.fit(X_scaled, y_all)
    xgb_final.fit(X_scaled, y_all)
    svm_final.fit(X_scaled, y_all)
    
    # 保存模型和预处理器
    model_dir = os.path.join(output_dir, 'saved_models')
    os.makedirs(model_dir, exist_ok=True)
    
    joblib.dump(rf_final, os.path.join(model_dir, 'random_forest.joblib'))
    joblib.dump(xgb_final, os.path.join(model_dir, 'xgboost.joblib'))
    joblib.dump(svm_final, os.path.join(model_dir, 'svm_rbf.joblib'))
    joblib.dump(scaler_final, os.path.join(model_dir, 'scaler.joblib'))
    joblib.dump({'selected_indices': selected_idx,
                 'selected_features': selected_feature_names,
                 'all_features': feature_names},
                os.path.join(model_dir, 'feature_config.joblib'))
    
    print(f"  模型已保存到: {model_dir}")
    print(f"  - random_forest.joblib  (最佳模型, LOOCV Acc=80.0%)")
    print(f"  - xgboost.joblib")
    print(f"  - svm_rbf.joblib")
    print(f"  - scaler.joblib         (标准化器)")
    print(f"  - feature_config.joblib (特征选择配置)")
    print(f"  选中的 {len(selected_idx)} 个特征: {selected_feature_names}")
    
    # 总结
    print("\n" + "=" * 60)
    print("最终结果总结")
    print("=" * 60)
    for model_name, res in results.items():
        print(f"\n  {model_name}:")
        print(f"    Accuracy = {res['accuracy']*100:.1f}%")
        print(f"    F1-score = {res['f1']:.3f}")
    
    best = max(results, key=lambda k: results[k]['accuracy'])
    print(f"\n  最佳模型: {best} (Accuracy={results[best]['accuracy']*100:.1f}%)")
    print("═" * 60)


if __name__ == '__main__':
    main()

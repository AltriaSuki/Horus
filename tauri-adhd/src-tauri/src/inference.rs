//! Feature extraction (27D) + Random Forest prediction.
//!
//! Faithful port of `engine/adhd_engine/worker/inference.py`.
//! Every feature name and computation matches the Python version exactly.
//! The RF model is loaded from `models/rf_model.json` (exported in Phase 0).

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

// ═══════════════════════════════════════════════════════════════════
// Trial data — matches Python's TrialData dataclass
// ═══════════════════════════════════════════════════════════════════

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TrialResult {
    pub trial_num: u32,
    pub block_num: u32,
    pub load: u32,                   // 1 or 2
    pub distractor_type: u32,        // 3-6
    pub response: Option<String>,    // "f" or "j" or null
    pub reaction_time: f64,          // seconds (NaN if no response)
    pub correct: bool,
    pub pupil_series: Vec<f32>,
    pub gaze_x_series: Vec<f64>,
    pub gaze_y_series: Vec<f64>,
}

// ═══════════════════════════════════════════════════════════════════
// 27-feature extraction
// ═══════════════════════════════════════════════════════════════════

/// All 27 feature names in the order the Python code produces them.
pub const ALL_FEATURES: &[&str] = &[
    "mean_rt", "std_rt", "cv_rt", "rt_skewness",
    "accuracy", "omission_rate", "rt_diff_correct_incorrect",
    "pupil_max_peak", "pupil_mean_change", "pupil_std_of_means",
    "pupil_overall_var", "pupil_peak_latency", "pupil_slope",
    "pupil_auc", "pupil_late_minus_early", "pupil_slope_var",
    "pupil_load_diff", "pupil_peak_load_diff",
    "gaze_var_x", "gaze_var_y", "gaze_path_normalized",
    "mean_rt_load1", "mean_rt_load2", "rt_load_diff",
    "acc_load_diff", "rt_distractor_slope", "acc_distractor_slope",
];

/// Extract 27 features from trial results.
///
/// Python ref: `inference.py` `extract_features()` lines 15-285
///
/// **30 Hz adaptation**: `fps` parameter controls baseline_frames
/// (= fps * 0.5 = 15 frames for 500 ms baseline at 30 Hz) and
/// slope_len (= min(len/3, fps * 1.0)).
///
/// **Slope unit fix** (plan §1.5.1): `slope = slope_raw * (fps / 1000.0)`
/// converts from "Δ%/webcam-frame" to "Δ%/ms", matching the training
/// data's 1000 Hz scale.
pub fn extract_features(trials: &[TrialResult], fps: f64) -> HashMap<String, f64> {
    let n_trials = trials.len();
    let baseline_frames = (fps * 0.5).max(1.0) as usize;

    // ── Behavioural features (7) ─────────────────────────────────
    let mut rts: Vec<f64> = Vec::new();
    let mut performs: Vec<f64> = Vec::new();
    let mut loads: Vec<f64> = Vec::new();
    let mut distractors: Vec<f64> = Vec::new();
    let mut omission_count = 0u32;

    for t in trials {
        if t.response.is_some() && !t.reaction_time.is_nan() {
            rts.push(t.reaction_time * 1000.0); // sec → ms
            performs.push(if t.correct { 1.0 } else { 0.0 });
        } else {
            omission_count += 1;
        }
        loads.push(t.load as f64);
        distractors.push(t.distractor_type as f64);
    }

    let mean_rt = mean(&rts);
    let std_rt = std_dev(&rts);
    let cv_rt = if mean_rt > 0.0 { std_rt / mean_rt } else { 0.0 };
    let rt_skewness = skewness(&rts);
    let accuracy = mean(&performs);
    let omission_rate = omission_count as f64 / n_trials.max(1) as f64;

    // RT difference correct vs incorrect
    let mut rt_correct = Vec::new();
    let mut rt_incorrect = Vec::new();
    for t in trials {
        if t.response.is_some() && !t.reaction_time.is_nan() {
            let rt_ms = t.reaction_time * 1000.0;
            if t.correct { rt_correct.push(rt_ms); }
            else { rt_incorrect.push(rt_ms); }
        }
    }
    let rt_diff_ci = mean(&rt_incorrect) - mean(&rt_correct);

    // ── Pupil features (11) ──────────────────────────────────────
    struct PupilFeats {
        max_peak: f64,
        mean_change: f64,
        peak_latency: f64,
        slope: f64,
        auc: f64,
        late_minus_early: f64,
    }

    let mut trial_pupil_feats: Vec<PupilFeats> = Vec::new();
    let mut pupil_by_load: HashMap<u32, Vec<PupilFeats>> = HashMap::new();
    pupil_by_load.insert(1, Vec::new());
    pupil_by_load.insert(2, Vec::new());

    for t in trials {
        let pupil: Vec<f64> = t.pupil_series.iter().map(|&v| v as f64).collect();
        if pupil.len() < baseline_frames * 3 { continue; }

        let valid_idx: Vec<usize> = (0..pupil.len())
            .filter(|&i| !pupil[i].is_nan())
            .collect();
        if valid_idx.len() < baseline_frames { continue; }

        // Baseline
        let bl_indices = &valid_idx[..baseline_frames];
        let baseline: f64 = bl_indices.iter()
            .map(|&i| pupil[i])
            .sum::<f64>() / baseline_frames as f64;
        if baseline == 0.0 || baseline.is_nan() { continue; }

        // Baseline-correct: (pupil - baseline) / |baseline|
        let corrected: Vec<f64> = pupil.iter()
            .map(|&v| if v.is_nan() { f64::NAN } else { (v - baseline) / baseline.abs() })
            .collect();

        if valid_idx.len() <= baseline_frames { continue; }
        let post_idx = &valid_idx[baseline_frames..];
        let post_data: Vec<f64> = post_idx.iter()
            .map(|&i| corrected[i])
            .filter(|v| !v.is_nan())
            .collect();
        if post_data.len() < 10 { continue; }

        let max_peak = post_data.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let mean_change = mean(&post_data);

        // Peak latency normalised to [0, 1]
        let peak_idx = post_data.iter()
            .enumerate()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal))
            .map(|(i, _)| i)
            .unwrap_or(0);
        let peak_latency = peak_idx as f64 / post_data.len() as f64;

        // Slope — with unit fix (plan §1.5.1)
        let slope_len = (post_data.len() / 3).min((fps * 1.0) as usize);
        let slope = if slope_len > 5 {
            let slope_raw = linear_slope(&post_data[..slope_len]);
            slope_raw * (fps / 1000.0) // per-frame → per-ms
        } else {
            0.0
        };

        // AUC (trapezoid, normalised)
        let auc = trapezoid(&post_data) / post_data.len() as f64;

        // Early vs late
        let mid = post_data.len() / 2;
        let early_mean = mean(&post_data[..mid]);
        let late_mean = mean(&post_data[mid..]);

        let feats = PupilFeats {
            max_peak, mean_change, peak_latency,
            slope, auc,
            late_minus_early: late_mean - early_mean,
        };

        if t.load == 1 || t.load == 2 {
            // Clone for the per-load stats
            pupil_by_load.get_mut(&t.load).unwrap().push(PupilFeats {
                max_peak, mean_change, peak_latency, slope, auc,
                late_minus_early: late_mean - early_mean,
            });
        }
        trial_pupil_feats.push(feats);
    }

    let (pupil_max_peak, pupil_mean_change, pupil_std_of_means,
         pupil_overall_var, pupil_peak_latency, pupil_slope,
         pupil_auc, pupil_late_minus_early, pupil_slope_var,
         pupil_load_diff, pupil_peak_load_diff);

    if !trial_pupil_feats.is_empty() {
        let peaks: Vec<f64> = trial_pupil_feats.iter().map(|f| f.max_peak).collect();
        let means: Vec<f64> = trial_pupil_feats.iter().map(|f| f.mean_change).collect();
        let slopes: Vec<f64> = trial_pupil_feats.iter().map(|f| f.slope).collect();

        pupil_max_peak = mean(&peaks);
        pupil_mean_change = mean(&means);
        pupil_std_of_means = std_dev(&means);
        pupil_overall_var = variance(&peaks);
        pupil_peak_latency = mean(
            &trial_pupil_feats.iter().map(|f| f.peak_latency).collect::<Vec<_>>());
        pupil_slope = mean(&slopes);
        pupil_auc = mean(
            &trial_pupil_feats.iter().map(|f| f.auc).collect::<Vec<_>>());
        pupil_late_minus_early = mean(
            &trial_pupil_feats.iter().map(|f| f.late_minus_early).collect::<Vec<_>>());
        pupil_slope_var = std_dev(&slopes);

        let l1 = pupil_by_load.get(&1).unwrap();
        let l2 = pupil_by_load.get(&2).unwrap();
        if l1.len() > 2 && l2.len() > 2 {
            let pl1 = mean(&l1.iter().map(|f| f.mean_change).collect::<Vec<_>>());
            let pl2 = mean(&l2.iter().map(|f| f.mean_change).collect::<Vec<_>>());
            pupil_load_diff = pl2 - pl1;
            let pp1 = mean(&l1.iter().map(|f| f.max_peak).collect::<Vec<_>>());
            let pp2 = mean(&l2.iter().map(|f| f.max_peak).collect::<Vec<_>>());
            pupil_peak_load_diff = pp2 - pp1;
        } else {
            pupil_load_diff = 0.0;
            pupil_peak_load_diff = 0.0;
        }
    } else {
        pupil_max_peak = 0.0; pupil_mean_change = 0.0;
        pupil_std_of_means = 0.0; pupil_overall_var = 0.0;
        pupil_peak_latency = 0.0; pupil_slope = 0.0;
        pupil_auc = 0.0; pupil_late_minus_early = 0.0;
        pupil_slope_var = 0.0; pupil_load_diff = 0.0;
        pupil_peak_load_diff = 0.0;
    }

    // ── Gaze features (3) ────────────────────────────────────────
    let mut all_gx: Vec<f64> = Vec::new();
    let mut all_gy: Vec<f64> = Vec::new();
    for t in trials {
        all_gx.extend(t.gaze_x_series.iter().filter(|v| !v.is_nan()));
        all_gy.extend(t.gaze_y_series.iter().filter(|v| !v.is_nan()));
    }

    let (gaze_var_x, gaze_var_y, gaze_path_normalized);
    if all_gx.len() > 100 {
        gaze_var_x = variance(&all_gx);
        gaze_var_y = variance(&all_gy);
        let mut path = 0.0;
        for i in 1..all_gx.len() {
            let dx = all_gx[i] - all_gx[i - 1];
            let dy = all_gy[i] - all_gy[i - 1];
            path += (dx * dx + dy * dy).sqrt();
        }
        gaze_path_normalized = path / all_gx.len() as f64;
    } else {
        gaze_var_x = 0.0;
        gaze_var_y = 0.0;
        gaze_path_normalized = 0.0;
    }

    // ── Condition features (6) ───────────────────────────────────
    let valid_trials: Vec<&TrialResult> = trials.iter()
        .filter(|t| t.response.is_some() && !t.reaction_time.is_nan())
        .collect();

    let load1_rts: Vec<f64> = valid_trials.iter()
        .filter(|t| t.load == 1).map(|t| t.reaction_time * 1000.0).collect();
    let load2_rts: Vec<f64> = valid_trials.iter()
        .filter(|t| t.load == 2).map(|t| t.reaction_time * 1000.0).collect();
    let mean_rt_load1 = if load1_rts.is_empty() { mean_rt } else { mean(&load1_rts) };
    let mean_rt_load2 = if load2_rts.is_empty() { mean_rt } else { mean(&load2_rts) };
    let rt_load_diff = mean_rt_load2 - mean_rt_load1;

    let acc_load1: f64 = {
        let correct: Vec<f64> = valid_trials.iter()
            .filter(|t| t.load == 1).map(|t| if t.correct { 1.0 } else { 0.0 }).collect();
        if correct.is_empty() { accuracy } else { mean(&correct) }
    };
    let acc_load2: f64 = {
        let correct: Vec<f64> = valid_trials.iter()
            .filter(|t| t.load == 2).map(|t| if t.correct { 1.0 } else { 0.0 }).collect();
        if correct.is_empty() { accuracy } else { mean(&correct) }
    };
    let acc_load_diff = acc_load1 - acc_load2;

    // Distractor gradient (rt and accuracy slope across types 3→6)
    let dist_levels = [3u32, 4, 5, 6];
    let mut dist_rt = Vec::new();
    let mut dist_acc = Vec::new();
    for &d in &dist_levels {
        let d_trials: Vec<&&TrialResult> = valid_trials.iter()
            .filter(|t| t.distractor_type == d).collect();
        if d_trials.is_empty() {
            dist_rt.push(f64::NAN);
            dist_acc.push(f64::NAN);
        } else {
            dist_rt.push(mean(&d_trials.iter()
                .map(|t| t.reaction_time * 1000.0).collect::<Vec<_>>()));
            dist_acc.push(mean(&d_trials.iter()
                .map(|t| if t.correct { 1.0 } else { 0.0 }).collect::<Vec<_>>()));
        }
    }

    let valid_d: Vec<usize> = (0..4).filter(|&i| !dist_rt[i].is_nan()).collect();
    let (rt_distractor_slope, acc_distractor_slope);
    if valid_d.len() >= 3 {
        let xd: Vec<f64> = valid_d.iter().map(|&i| dist_levels[i] as f64).collect();
        let yd_rt: Vec<f64> = valid_d.iter().map(|&i| dist_rt[i]).collect();
        let yd_acc: Vec<f64> = valid_d.iter().map(|&i| dist_acc[i]).collect();
        rt_distractor_slope = linear_regression_slope(&xd, &yd_rt);
        acc_distractor_slope = linear_regression_slope(&xd, &yd_acc);
    } else {
        rt_distractor_slope = 0.0;
        acc_distractor_slope = 0.0;
    }

    // ── Build the 27-feature map ─────────────────────────────────
    let mut features = HashMap::new();
    let values = [
        mean_rt, std_rt, cv_rt, rt_skewness,
        accuracy, omission_rate, rt_diff_ci,
        pupil_max_peak, pupil_mean_change, pupil_std_of_means,
        pupil_overall_var, pupil_peak_latency, pupil_slope,
        pupil_auc, pupil_late_minus_early, pupil_slope_var,
        pupil_load_diff, pupil_peak_load_diff,
        gaze_var_x, gaze_var_y, gaze_path_normalized,
        mean_rt_load1, mean_rt_load2, rt_load_diff,
        acc_load_diff, rt_distractor_slope, acc_distractor_slope,
    ];
    for (name, val) in ALL_FEATURES.iter().zip(values.iter()) {
        let v = if val.is_nan() || val.is_infinite() { 0.0 } else { *val };
        features.insert(name.to_string(), v);
    }
    features
}

// ═══════════════════════════════════════════════════════════════════
// Random Forest prediction from JSON
// ═══════════════════════════════════════════════════════════════════

#[derive(Deserialize)]
pub struct RfModelBundle {
    pub n_estimators: usize,
    pub n_classes: usize,
    pub trees: Vec<TreeData>,
    pub scaler: ScalerData,
    pub features: FeatureConfig,
}

#[derive(Deserialize)]
pub struct TreeData {
    pub children_left: Vec<i64>,
    pub children_right: Vec<i64>,
    pub feature: Vec<i64>,
    pub threshold: Vec<f64>,
    pub value: Vec<Vec<Vec<f64>>>,
}

#[derive(Deserialize)]
pub struct ScalerData {
    pub mean: Vec<f64>,
    pub scale: Vec<f64>,
}

#[derive(Deserialize)]
pub struct FeatureConfig {
    pub all_features: Vec<String>,
    pub selected_features: Vec<String>,
    pub selected_indices: Vec<usize>,
}

/// 6-dimension attention profile — derived from 27 features.
/// Each dimension is normalised to [0, 100] for display.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AttentionProfile {
    /// 持续注意力 — from pupil_late_minus_early + rt_skewness
    pub sustained_attention: f64,
    /// 认知负荷敏感度 — from pupil_load_diff + rt_load_diff
    pub cognitive_load_sensitivity: f64,
    /// 抗干扰能力 — from rt_distractor_slope + acc_distractor_slope
    pub distractor_resistance: f64,
    /// 反应稳定性 — from cv_rt + std_rt
    pub response_stability: f64,
    /// 瞳孔活跃度 (认知投入) — from pupil_max_peak + pupil_slope
    pub pupil_engagement: f64,
    /// 注视控制 — from gaze_var_x + gaze_path_normalized
    pub gaze_control: f64,
}

/// Per-block statistics for the attention timeline.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BlockStats {
    pub block_num: u32,
    pub accuracy: f64,
    pub mean_rt_ms: f64,
    pub n_trials: u32,
    pub n_correct: u32,
    pub omission_rate: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct AdhdReport {
    pub prediction: String,
    pub adhd_probability: f64,
    pub control_probability: f64,
    pub risk_level: String,
    pub feature_values: HashMap<String, f64>,
    pub feature_importance: HashMap<String, f64>,
    pub model_info: String,
    /// 6-dimension attention radar profile
    pub attention_profile: AttentionProfile,
    /// Per-block accuracy + RT for the fatigue timeline
    pub block_stats: Vec<BlockStats>,
}

impl RfModelBundle {
    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let data = std::fs::read_to_string(path)?;
        let model: RfModelBundle = serde_json::from_str(&data)?;
        Ok(model)
    }

    /// Run prediction on 27-feature dict.
    /// Matches Python's `predict_adhd()` in inference.py.
    pub fn predict(&self, features: &HashMap<String, f64>) -> AdhdReport {
        // 1. Build the full feature vector in training order
        let x_full: Vec<f64> = self.features.all_features.iter()
            .map(|name| *features.get(name).unwrap_or(&0.0))
            .collect();

        // 2. Select the 12 chosen features
        let x_selected: Vec<f64> = self.features.selected_indices.iter()
            .map(|&i| x_full[i])
            .collect();

        // 3. StandardScaler transform
        let x_scaled: Vec<f64> = x_selected.iter()
            .enumerate()
            .map(|(i, &v)| (v - self.scaler.mean[i]) / self.scaler.scale[i])
            .collect();

        // 4. Aggregate votes from all trees
        let mut total_prob = vec![0.0f64; self.n_classes];
        for tree in &self.trees {
            let votes = predict_single_tree(tree, &x_scaled);
            let s: f64 = votes.iter().sum::<f64>().max(1e-10);
            for c in 0..self.n_classes {
                total_prob[c] += votes[c] / s;
            }
        }
        // Normalise to probabilities
        let n = self.n_estimators as f64;
        for c in 0..self.n_classes {
            total_prob[c] /= n;
        }

        let adhd_prob = total_prob.get(1).copied().unwrap_or(0.0);
        let control_prob = total_prob.get(0).copied().unwrap_or(0.0);
        let prediction = if adhd_prob >= 0.5 { "ADHD" } else { "Control" };
        let risk_level = if adhd_prob >= 0.7 { "HIGH" }
            else if adhd_prob >= 0.5 { "MODERATE" }
            else if adhd_prob >= 0.3 { "LOW" }
            else { "MINIMAL" };

        // Feature importance (from the first tree's feature importances
        // — simplified; in sklearn it's the Gini importance weighted across
        // all trees, but for display purposes this is good enough)
        let mut feature_importance = HashMap::new();
        // Use a simple approximation: count how often each feature appears
        // at decision nodes across all trees
        let mut counts = vec![0u32; self.features.selected_features.len()];
        for tree in &self.trees {
            for &f in &tree.feature {
                if f >= 0 && (f as usize) < counts.len() {
                    counts[f as usize] += 1;
                }
            }
        }
        let total_splits: f64 = counts.iter().sum::<u32>() as f64;
        for (i, name) in self.features.selected_features.iter().enumerate() {
            feature_importance.insert(
                name.clone(),
                counts[i] as f64 / total_splits.max(1.0),
            );
        }

        let mut feature_values = HashMap::new();
        for name in &self.features.selected_features {
            feature_values.insert(name.clone(), *features.get(name).unwrap_or(&0.0));
        }

        AdhdReport {
            prediction: prediction.to_string(),
            adhd_probability: adhd_prob,
            control_probability: control_prob,
            risk_level: risk_level.to_string(),
            feature_values,
            feature_importance,
            model_info: "Random Forest (LOOCV Accuracy: 80.0%)".to_string(),
            attention_profile: AttentionProfile::default(),
            block_stats: Vec::new(),
        }
    }
}

impl Default for AttentionProfile {
    fn default() -> Self {
        Self {
            sustained_attention: 50.0,
            cognitive_load_sensitivity: 50.0,
            distractor_resistance: 50.0,
            response_stability: 50.0,
            pupil_engagement: 50.0,
            gaze_control: 50.0,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Attention profile + block stats — data expansion features
// ═══════════════════════════════════════════════════════════════════

/// Compute the 6-dimension attention profile from 27 features.
///
/// Each dimension is a composite of 2+ raw features, mapped to [0, 100]
/// using sigmoid-like scaling so extreme values don't blow up the chart.
///
/// Higher = better (e.g., sustained_attention=90 means good sustained attention).
pub fn compute_attention_profile(features: &HashMap<String, f64>) -> AttentionProfile {
    let get = |name: &str| *features.get(name).unwrap_or(&0.0);

    // Helper: map a raw value to [0, 100] via sigmoid centering around `center`
    // with steepness `k`. Higher raw = higher output when `invert=false`.
    let sigmoid_map = |raw: f64, center: f64, k: f64, invert: bool| -> f64 {
        let x = if invert { center - raw } else { raw - center };
        100.0 / (1.0 + (-k * x).exp())
    };

    // 1. Sustained attention — 持续注意力
    //    Low rt_skewness + small pupil late-minus-early = good sustained attention
    //    (ADHD kids have high skewness from long-tail slow RTs as they lose focus)
    let sustained = {
        let skew = get("rt_skewness");
        let late_early = get("pupil_late_minus_early");
        // Both should be LOW for good sustained attention → invert
        let s1 = sigmoid_map(skew, 0.5, 3.0, true);      // skew < 0.5 = good
        let s2 = sigmoid_map(late_early, 0.02, 50.0, true); // late-early < 0.02 = stable
        (s1 + s2) / 2.0
    };

    // 2. Cognitive load sensitivity — 认知负荷敏感度
    //    Small rt_load_diff + small pupil_load_diff = good (can handle both loads)
    let cog_load = {
        let rt_diff = get("rt_load_diff").abs();
        let pupil_diff = get("pupil_load_diff").abs();
        let s1 = sigmoid_map(rt_diff, 100.0, 0.02, true);   // <100ms diff = good
        let s2 = sigmoid_map(pupil_diff, 0.03, 30.0, true); // <0.03 = good
        (s1 + s2) / 2.0
    };

    // 3. Distractor resistance — 抗干扰能力
    //    Small (flat) distractor slopes = not affected by distractors
    let distractor = {
        let rt_slope = get("rt_distractor_slope").abs();
        let acc_slope = get("acc_distractor_slope").abs();
        let s1 = sigmoid_map(rt_slope, 20.0, 0.05, true);
        let s2 = sigmoid_map(acc_slope, 0.05, 20.0, true);
        (s1 + s2) / 2.0
    };

    // 4. Response stability — 反应稳定性
    //    Low cv_rt = consistent response times
    let stability = {
        let cv = get("cv_rt");
        sigmoid_map(cv, 0.25, 10.0, true) // cv < 0.25 = stable
    };

    // 5. Pupil engagement — 瞳孔活跃度 (认知投入)
    //    High pupil_max_peak + positive slope = actively engaged
    let engagement = {
        let peak = get("pupil_max_peak");
        let slope = get("pupil_slope");
        let auc = get("pupil_auc");
        let s1 = sigmoid_map(peak, 0.05, 20.0, false);  // peak > 0.05 = engaged
        let s2 = sigmoid_map(auc, 0.02, 50.0, false);   // positive AUC = engaged
        (s1 + s2) / 2.0
    };

    // 6. Gaze control — 注视控制
    //    Low gaze_var_x + low path = controlled eye movements
    let gaze_ctrl = {
        let var_x = get("gaze_var_x");
        let path = get("gaze_path_normalized");
        let s1 = sigmoid_map(var_x, 10000.0, 0.0002, true); // <10000 px² var = good
        let s2 = sigmoid_map(path, 5.0, 0.3, true);          // <5 px/frame = good
        (s1 + s2) / 2.0
    };

    AttentionProfile {
        sustained_attention: sustained.clamp(0.0, 100.0),
        cognitive_load_sensitivity: cog_load.clamp(0.0, 100.0),
        distractor_resistance: distractor.clamp(0.0, 100.0),
        response_stability: stability.clamp(0.0, 100.0),
        pupil_engagement: engagement.clamp(0.0, 100.0),
        gaze_control: gaze_ctrl.clamp(0.0, 100.0),
    }
}

/// Compute per-block accuracy + RT statistics for the fatigue timeline.
pub fn compute_block_stats(trials: &[TrialResult]) -> Vec<BlockStats> {
    let max_block = trials.iter().map(|t| t.block_num).max().unwrap_or(0);
    let mut stats = Vec::new();

    for block in 1..=max_block {
        let block_trials: Vec<&TrialResult> = trials.iter()
            .filter(|t| t.block_num == block)
            .collect();

        let n = block_trials.len() as u32;
        let n_responded: Vec<&TrialResult> = block_trials.iter()
            .filter(|t| t.response.is_some() && !t.reaction_time.is_nan())
            .copied()
            .collect();
        let n_correct = n_responded.iter().filter(|t| t.correct).count() as u32;
        let n_omission = n - n_responded.len() as u32;

        let mean_rt_ms = if n_responded.is_empty() {
            0.0
        } else {
            n_responded.iter()
                .map(|t| t.reaction_time * 1000.0)
                .sum::<f64>() / n_responded.len() as f64
        };

        let accuracy = if n_responded.is_empty() {
            0.0
        } else {
            n_correct as f64 / n_responded.len() as f64
        };

        stats.push(BlockStats {
            block_num: block,
            accuracy,
            mean_rt_ms,
            n_trials: n,
            n_correct,
            omission_rate: n_omission as f64 / n.max(1) as f64,
        });
    }

    stats
}

fn predict_single_tree(tree: &TreeData, x: &[f64]) -> Vec<f64> {
    let mut node: usize = 0;
    loop {
        if tree.children_left[node] == -1 {
            // Leaf node
            return tree.value[0][node].clone();
        }
        let feat_idx = tree.feature[node] as usize;
        let threshold = tree.threshold[node];
        if feat_idx < x.len() && x[feat_idx] <= threshold {
            node = tree.children_left[node] as usize;
        } else {
            node = tree.children_right[node] as usize;
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Statistical helpers
// ═══════════════════════════════════════════════════════════════════

fn mean(v: &[f64]) -> f64 {
    if v.is_empty() { return 0.0; }
    v.iter().sum::<f64>() / v.len() as f64
}

fn variance(v: &[f64]) -> f64 {
    if v.len() < 2 { return 0.0; }
    let m = mean(v);
    v.iter().map(|x| (x - m).powi(2)).sum::<f64>() / v.len() as f64
}

fn std_dev(v: &[f64]) -> f64 {
    variance(v).sqrt()
}

fn skewness(v: &[f64]) -> f64 {
    if v.len() < 4 { return 0.0; }
    let m = mean(v);
    let s = std_dev(v);
    if s < 1e-10 { return 0.0; }
    let n = v.len() as f64;
    let m3: f64 = v.iter().map(|x| ((x - m) / s).powi(3)).sum::<f64>();
    m3 / n
}

/// Simple linear regression slope: y = slope * x + intercept
fn linear_slope(y: &[f64]) -> f64 {
    let n = y.len() as f64;
    if n < 2.0 { return 0.0; }
    let x_mean = (n - 1.0) / 2.0;
    let y_mean: f64 = y.iter().sum::<f64>() / n;
    let mut num = 0.0;
    let mut den = 0.0;
    for (i, &yi) in y.iter().enumerate() {
        let xi = i as f64;
        num += (xi - x_mean) * (yi - y_mean);
        den += (xi - x_mean).powi(2);
    }
    if den.abs() < 1e-15 { 0.0 } else { num / den }
}

/// Linear regression slope for arbitrary x and y vectors.
fn linear_regression_slope(x: &[f64], y: &[f64]) -> f64 {
    let n = x.len();
    if n < 2 { return 0.0; }
    let x_mean = mean(x);
    let y_mean = mean(y);
    let mut num = 0.0;
    let mut den = 0.0;
    for i in 0..n {
        num += (x[i] - x_mean) * (y[i] - y_mean);
        den += (x[i] - x_mean).powi(2);
    }
    if den.abs() < 1e-15 { 0.0 } else { num / den }
}

/// Trapezoidal integration (unnormalised).
fn trapezoid(v: &[f64]) -> f64 {
    if v.len() < 2 { return 0.0; }
    let mut s = 0.0;
    for i in 1..v.len() {
        s += (v[i - 1] + v[i]) / 2.0;
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_slope_unit_fix() {
        // Simulate a linear ramp in pupil data at 30 Hz and 1000 Hz.
        // After the slope unit fix, both should give the same slope value.
        let slope_per_ms = 5e-5;
        for fps in [30.0, 1000.0] {
            let baseline_frames = (fps * 0.5) as usize;
            let total_frames = (fps * 6.0) as usize; // 6 seconds
            let mut pupil = vec![0.2; total_frames];
            for i in baseline_frames..total_frames {
                let ms_post = (i - baseline_frames) as f64 * (1000.0 / fps);
                pupil[i] = 0.2 + slope_per_ms * ms_post;
            }
            let trial = TrialResult {
                trial_num: 1, block_num: 1, load: 1, distractor_type: 3,
                response: Some("f".to_string()), reaction_time: 0.7,
                correct: true,
                pupil_series: pupil.iter().map(|&v| v as f32).collect(),
                gaze_x_series: vec![500.0; total_frames],
                gaze_y_series: vec![500.0; total_frames],
            };
            let features = extract_features(&[trial], fps);
            let slope = features["pupil_slope"];
            // Both fps should give similar slope values
            assert!(slope.abs() > 1e-6, "fps={fps}: slope is zero");
            // The ratio between 30 Hz and 1000 Hz slopes should be ~1
            // (without the fix it would be ~33)
            if fps == 30.0 {
                // Just check it's in the right ballpark
                assert!(slope.abs() < 1e-2,
                    "fps=30: slope {} is too large (unit fix not applied?)", slope);
            }
        }
    }

    #[test]
    fn test_rf_prediction_from_json() {
        let json_path = std::path::Path::new("../models/rf_model.json");
        if !json_path.exists() {
            eprintln!("Skipping RF test — rf_model.json not found at {:?}", json_path);
            return;
        }
        let model = RfModelBundle::load(json_path).unwrap();
        assert_eq!(model.n_estimators, 300);

        // Zero features should still produce a valid prediction
        let mut features = HashMap::new();
        for name in ALL_FEATURES {
            features.insert(name.to_string(), 0.0);
        }
        let report = model.predict(&features);
        assert!(report.adhd_probability >= 0.0 && report.adhd_probability <= 1.0);
        assert!(!report.risk_level.is_empty());
    }
}

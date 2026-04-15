//! Core gaze-tracking math — ported from `engine/adhd_engine/worker/gaze_system.py`.
//!
//! Every function here has a 1:1 correspondence with a Python function.
//! Comments reference the original Python source line numbers so diffs
//! can be traced. The numerical behaviour MUST match the Python version
//! within float32 tolerance — the trained RF classifier was fit on
//! features computed with this exact math.

use std::collections::VecDeque;

/// A single 2D landmark point (normalised to [0, 1] by MediaPipe).
#[derive(Clone, Copy, Debug, Default)]
pub struct Landmark {
    pub x: f32,
    pub y: f32,
}

/// 478 face mesh landmarks (468 face + 10 iris).
pub type Landmarks478 = Vec<Landmark>;

// ═══════════════════════════════════════════════════════════════════
// Iris feature extraction — from gaze_system.py compute_iris_features()
// ═══════════════════════════════════════════════════════════════════

/// Compute the 6D geometric iris + head-pose feature vector.
///
/// Python ref: `gaze_system.py` line 286-323
///
/// Returns `[r_ratio_x, r_ratio_y, l_ratio_x, l_ratio_y, head_yaw, head_pitch]`
/// or `None` if any eye ratio can't be computed.
pub fn compute_iris_features(lm: &Landmarks478) -> Option<[f32; 6]> {
    let eye_ratio = |c1: usize, c2: usize, iris: usize| -> Option<[f32; 2]> {
        let c1 = [lm[c1].x, lm[c1].y];
        let c2 = [lm[c2].x, lm[c2].y];
        let ir = [lm[iris].x, lm[iris].y];

        let axis = [c2[0] - c1[0], c2[1] - c1[1]];
        let length = (axis[0] * axis[0] + axis[1] * axis[1]).sqrt();
        if length < 1e-6 {
            return None;
        }
        let axis_u = [axis[0] / length, axis[1] / length];
        let normal = [-axis_u[1], axis_u[0]];
        let offset = [ir[0] - c1[0], ir[1] - c1[1]];
        let rx = (offset[0] * axis_u[0] + offset[1] * axis_u[1]) / length;
        let ry = (offset[0] * normal[0] + offset[1] * normal[1]) / length;
        Some([rx, ry])
    };

    // Right eye (anatomical): outer corner 33, inner 133, iris center 468
    let r_ratio = eye_ratio(33, 133, 468)?;
    // Left eye (anatomical): inner 362, outer 263, iris center 473
    let l_ratio = eye_ratio(362, 263, 473)?;

    // Head orientation: nose tip relative to face center
    let nose = [lm[1].x, lm[1].y];
    let chin = [lm[152].x, lm[152].y];
    let forehead = [lm[10].x, lm[10].y];
    let left_cheek = [lm[234].x, lm[234].y];
    let right_cheek = [lm[454].x, lm[454].y];

    let face_w = (right_cheek[0] - left_cheek[0]).abs().max(1e-6);
    let face_h = (chin[1] - forehead[1]).abs().max(1e-6);
    let face_cx = (left_cheek[0] + right_cheek[0]) / 2.0;
    let face_cy = (forehead[1] + chin[1]) / 2.0;
    let head_yaw = (nose[0] - face_cx) / face_w;
    let head_pitch = (nose[1] - face_cy) / face_h;

    Some([
        r_ratio[0], r_ratio[1],
        l_ratio[0], l_ratio[1],
        head_yaw, head_pitch,
    ])
}

// ═══════════════════════════════════════════════════════════════════
// Pupil size estimation — from gaze_system.py estimate_pupil_size()
// ═══════════════════════════════════════════════════════════════════

/// Estimate relative pupil/iris size from MediaPipe iris landmarks.
///
/// Python ref: `gaze_system.py` line 326-350
///
/// Uses the horizontal + vertical diameter of each iris, normalised by
/// the corresponding eye width, so the value is distance-invariant.
pub fn estimate_pupil_size(lm: &Landmarks478) -> f32 {
    let dist = |a: usize, b: usize| -> f32 {
        let dx = lm[a].x - lm[b].x;
        let dy = lm[a].y - lm[b].y;
        (dx * dx + dy * dy).sqrt()
    };

    // Right iris: horizontal 469-471, vertical 470-472
    let r_h = dist(469, 471);
    let r_v = dist(470, 472);
    // Left iris: horizontal 474-476, vertical 475-477
    let l_h = dist(474, 476);
    let l_v = dist(475, 477);

    // Eye widths for normalisation
    let r_eye = dist(33, 133).max(1e-6);
    let l_eye = dist(362, 263).max(1e-6);

    let r_ratio = (r_h + r_v) / (2.0 * r_eye);
    let l_ratio = (l_h + l_v) / (2.0 * l_eye);

    (r_ratio + l_ratio) / 2.0
}

// ═══════════════════════════════════════════════════════════════════
// Eye Aspect Ratio — blink detection
// ═══════════════════════════════════════════════════════════════════

/// Left-eye and right-eye landmark index sets (same as Python).
pub const LEFT_EAR_INDICES: [usize; 6] = [362, 385, 387, 263, 373, 380];
pub const RIGHT_EAR_INDICES: [usize; 6] = [33, 160, 158, 133, 153, 144];

/// Compute the Eye Aspect Ratio for a set of 6 eye landmarks.
///
/// Python ref: `gaze_system.py` line 273-283
///
/// Returns a float — values < 0.12 indicate the eye is closed (blink).
pub fn compute_ear(lm: &Landmarks478, indices: &[usize; 6]) -> f32 {
    let p = |i: usize| [lm[indices[i]].x, lm[indices[i]].y];

    let p1 = p(0);
    let p2 = p(1);
    let p3 = p(2);
    let p4 = p(3);
    let p5 = p(4);
    let p6 = p(5);

    let dist = |a: [f32; 2], b: [f32; 2]| {
        ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
    };

    let vertical = dist(p2, p6) + dist(p3, p5);
    let horizontal = dist(p1, p4).max(1e-6);

    vertical / (2.0 * horizontal)
}

// ═══════════════════════════════════════════════════════════════════
// Fixation-aware adaptive smoother
// ═══════════════════════════════════════════════════════════════════

/// Python ref: `gaze_system.py` line 114-138
pub struct FixationSmoother {
    x: Option<f64>,
    y: Option<f64>,
    fixation_vel_thresh: f64,
    fixation_alpha: f64,
    saccade_alpha: f64,
}

impl FixationSmoother {
    pub fn new() -> Self {
        Self {
            x: None,
            y: None,
            fixation_vel_thresh: 25.0,  // px/frame
            fixation_alpha: 0.4,        // match Python POS_SMOOTH_ALPHA
            saccade_alpha: 0.7,         // fast reaction for saccades
        }
    }

    pub fn update(&mut self, raw_x: f64, raw_y: f64) -> (f64, f64) {
        let (sx, sy) = match (self.x, self.y) {
            (Some(px), Some(py)) => {
                let dx = raw_x - px;
                let dy = raw_y - py;
                let vel = (dx * dx + dy * dy).sqrt();

                let alpha = if vel < self.fixation_vel_thresh {
                    self.fixation_alpha
                } else {
                    (self.saccade_alpha).min(vel / 300.0)
                };

                (px + alpha * dx, py + alpha * dy)
            }
            _ => (raw_x, raw_y),
        };

        self.x = Some(sx);
        self.y = Some(sy);
        (sx, sy)
    }

    pub fn reset(&mut self) {
        self.x = None;
        self.y = None;
    }
}

// ═══════════════════════════════════════════════════════════════════
// Pupil signal smoother (5-frame moving average)
// ═══════════════════════════════════════════════════════════════════

/// Python ref: `gaze_system.py` `_smooth_pupil()` — plan §1.5.5
pub struct PupilSmoother {
    buf: VecDeque<f32>,
    window: usize,
}

impl PupilSmoother {
    pub fn new(window: usize) -> Self {
        Self {
            buf: VecDeque::with_capacity(window),
            window,
        }
    }

    pub fn smooth(&mut self, raw: f32) -> f32 {
        if raw.is_nan() || raw == 0.0 {
            return raw;
        }
        if self.buf.len() >= self.window {
            self.buf.pop_front();
        }
        self.buf.push_back(raw);
        let sum: f32 = self.buf.iter().copied().sum();
        sum / self.buf.len() as f32
    }
}

// ═══════════════════════════════════════════════════════════════════
// Ridge regression (for 13-point calibration)
// ═══════════════════════════════════════════════════════════════════

/// Minimal Ridge regression: fit(X, Y) → predict(x).
///
/// Python ref: `gaze_system.py` `build_regressor()` — sklearn
/// Pipeline([StandardScaler, Ridge(alpha=1.0)])
///
/// We implement StandardScaler + Ridge in one struct for simplicity.
/// The feature dimension is 8 (6D iris + 2D model gaze) and the
/// output dimension is 2 (screen x, y).
pub struct RidgeRegressor {
    // StandardScaler parameters
    mean: Vec<f64>,
    std: Vec<f64>,
    // Ridge weights: W shape [n_features+1, 2] (last row = intercept)
    weights: Vec<[f64; 2]>,
    fitted: bool,
}

impl RidgeRegressor {
    pub fn new() -> Self {
        Self {
            mean: Vec::new(),
            std: Vec::new(),
            weights: Vec::new(),
            fitted: false,
        }
    }

    /// Fit the scaler + ridge on calibration data.
    ///
    /// `x_rows`: each row is an 8D feature vector
    /// `y_rows`: each row is [screen_x, screen_y]
    pub fn fit(&mut self, x_rows: &[[f64; 8]], y_rows: &[[f64; 2]], alpha: f64) {
        let n = x_rows.len();
        let d = 8;
        assert!(n >= 5, "need at least 5 calibration points");

        // 1. StandardScaler: compute mean and std per feature
        self.mean = vec![0.0; d];
        self.std = vec![1.0; d];
        for row in x_rows {
            for j in 0..d {
                self.mean[j] += row[j];
            }
        }
        for j in 0..d {
            self.mean[j] /= n as f64;
        }
        for row in x_rows {
            for j in 0..d {
                self.std[j] += (row[j] - self.mean[j]).powi(2);
            }
        }
        for j in 0..d {
            self.std[j] = (self.std[j] / n as f64).sqrt().max(1e-10);
        }

        // 2. Scale X, add bias column
        let d1 = d + 1;
        let mut xs = vec![vec![0.0; d1]; n];
        for i in 0..n {
            for j in 0..d {
                xs[i][j] = (x_rows[i][j] - self.mean[j]) / self.std[j];
            }
            xs[i][d] = 1.0; // bias
        }

        // 3. Ridge closed form: W = (X^T X + αI)^{-1} X^T Y
        //    Using nalgebra for the matrix solve
        use nalgebra::{DMatrix, DVector};

        let x_mat = DMatrix::from_fn(n, d1, |i, j| xs[i][j]);
        let xt = x_mat.transpose();
        let xtx = &xt * &x_mat;
        let mut xtx_reg = xtx;
        for j in 0..d1 {
            xtx_reg[(j, j)] += alpha;
        }

        self.weights = vec![[0.0; 2]; d1];
        for out_dim in 0..2 {
            let y_vec = DVector::from_fn(n, |i, _| y_rows[i][out_dim]);
            let xty = &xt * &y_vec;
            // Solve (X^T X + αI) w = X^T y
            let decomp = xtx_reg.clone().lu();
            if let Some(w) = decomp.solve(&xty) {
                for j in 0..d1 {
                    self.weights[j][out_dim] = w[j];
                }
            }
        }

        self.fitted = true;

        // Diagnostic: log scaler stats and weight magnitudes
        log::info!("Ridge fit: n={} d={} alpha={}", n, d, alpha);
        log::info!("  Scaler mean: {:?}", &self.mean);
        log::info!("  Scaler std:  {:?}", &self.std);
        for j in 0..d1 {
            log::info!("  W[{}] = [{:.4}, {:.4}]", j, self.weights[j][0], self.weights[j][1]);
        }
    }

    /// Predict screen coordinates from an 8D feature vector.
    pub fn predict(&self, x: &[f64; 8]) -> (f64, f64) {
        if !self.fitted {
            return (0.0, 0.0);
        }
        let d = 8;
        let mut result = [0.0f64; 2];
        for j in 0..d {
            let scaled = (x[j] - self.mean[j]) / self.std[j];
            result[0] += scaled * self.weights[j][0];
            result[1] += scaled * self.weights[j][1];
        }
        // bias
        result[0] += self.weights[d][0];
        result[1] += self.weights[d][1];
        (result[0], result[1])
    }
}

// ═══════════════════════════════════════════════════════════════════
// Feature buffer (exponential weighted mean)
// ═══════════════════════════════════════════════════════════════════

/// Python ref: `gaze_system.py` `_weighted_mean_feat()` line 619-625
pub struct FeatureBuffer {
    buf: VecDeque<[f32; 8]>,
    max_len: usize,
}

impl FeatureBuffer {
    pub fn new(max_len: usize) -> Self {
        Self {
            buf: VecDeque::with_capacity(max_len),
            max_len,
        }
    }

    pub fn push(&mut self, feat: [f32; 8]) {
        if self.buf.len() >= self.max_len {
            self.buf.pop_front();
        }
        self.buf.push_back(feat);
    }

    pub fn len(&self) -> usize {
        self.buf.len()
    }

    /// Exponentially weighted mean: newer frames get higher weight.
    pub fn weighted_mean(&self) -> Option<[f64; 8]> {
        let n = self.buf.len();
        if n < 1 {
            return None;
        }

        // Weights: exp(linspace(-1, 0, n))
        let mut weights = vec![0.0f64; n];
        let step = 1.0 / (n as f64 - 1.0).max(1.0);
        for i in 0..n {
            weights[i] = (-1.0 + step * i as f64).exp();
        }
        let wsum: f64 = weights.iter().sum();

        let mut result = [0.0f64; 8];
        for (i, feat) in self.buf.iter().enumerate() {
            let w = weights[i] / wsum;
            for j in 0..8 {
                result[j] += feat[j] as f64 * w;
            }
        }
        Some(result)
    }

    pub fn clear(&mut self) {
        self.buf.clear();
    }
}

// ═══════════════════════════════════════════════════════════════════
// Gaze frame — the output of each capture iteration
// ═══════════════════════════════════════════════════════════════════

#[derive(Clone, Debug, serde::Serialize)]
pub struct GazeFrame {
    pub t: f64,       // seconds since session start
    pub x: f64,       // screen pixel X (NaN if invalid)
    pub y: f64,       // screen pixel Y
    pub pupil: f32,   // smoothed pupil proxy
    pub valid: bool,
    pub fps: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fixation_smoother_basic() {
        let mut s = FixationSmoother::new();
        // First call returns raw
        let (x, y) = s.update(100.0, 200.0);
        assert!((x - 100.0).abs() < 0.01);
        assert!((y - 200.0).abs() < 0.01);

        // Small movement (fixation) → heavy smoothing
        let (x2, y2) = s.update(102.0, 201.0);
        assert!(x2 > 100.0 && x2 < 102.0); // smoothed towards
        assert!(y2 > 200.0 && y2 < 201.0);
    }

    #[test]
    fn test_pupil_smoother() {
        let mut s = PupilSmoother::new(5);
        assert_eq!(s.smooth(0.10), 0.10);
        assert_eq!(s.smooth(0.12), (0.10 + 0.12) / 2.0);
        // NaN should pass through
        assert!(s.smooth(f32::NAN).is_nan());
    }

    #[test]
    fn test_ridge_regressor_fit_predict() {
        let mut r = RidgeRegressor::new();
        // Simple calibration: identity-ish mapping
        let x_data: Vec<[f64; 8]> = vec![
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        ];
        let y_data: Vec<[f64; 2]> = vec![
            [0.0, 0.0],
            [1920.0, 0.0],
            [0.0, 1080.0],
            [1920.0, 1080.0],
            [960.0, 540.0],
        ];
        r.fit(&x_data, &y_data, 1.0);
        assert!(r.fitted);

        // Predicting the center point should be in the right ballpark.
        // Ridge with alpha=1.0 + StandardScaler on 5 points won't perfectly
        // interpolate, so we use a generous tolerance.
        let (px, py) = r.predict(&[0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
        assert!((px - 960.0).abs() < 400.0, "px={} too far from 960", px);
        assert!((py - 540.0).abs() < 400.0, "py={} too far from 540", py);
    }

    #[test]
    fn test_feature_buffer_weighted_mean() {
        let mut fb = FeatureBuffer::new(10);
        fb.push([1.0; 8]);
        fb.push([2.0; 8]);
        fb.push([3.0; 8]);

        let m = fb.weighted_mean().unwrap();
        // Should be closer to 3.0 than to 1.0 (exp weighting favours recent)
        assert!(m[0] > 2.0);
        assert!(m[0] < 3.0);
    }
}

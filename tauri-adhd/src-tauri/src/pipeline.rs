//! Complete per-frame gaze pipeline — runs face mesh, feature extraction,
//! AFFNet inference, Ridge regression, and smoothing.
//!
//! Port of Python's `GazeSystem._extract()` + main loop.

use std::path::Path;

use anyhow::{Context, Result};
use tract_onnx::prelude::*;

use crate::face_mesh::FaceMeshDetector;
use crate::gaze_math::{
    compute_ear, compute_iris_features, estimate_pupil_size, FeatureBuffer, FixationSmoother,
    GazeFrame, Landmark, PupilSmoother, RidgeRegressor, LEFT_EAR_INDICES, RIGHT_EAR_INDICES,
};

/// Blink detection threshold — eyes considered closed below this EAR value.
const BLINK_EAR_THRESHOLD: f32 = 0.12;

/// Minimum number of buffered features before ridge prediction kicks in.
const MIN_FEATURES_FOR_PREDICTION: usize = 3;

// ═══════════════════════════════════════════════════════════════════
// Type alias for the AFFNet model plan
// ═══════════════════════════════════════════════════════════════════

type OnnxPlan = TypedRunnableModel<TypedModel>;

// ═══════════════════════════════════════════════════════════════════
// AFFNet helpers — eye & face rectangles
// ═══════════════════════════════════════════════════════════════════

/// Eye landmark indices for cropping the eye region (from Python).
const LEFT_EYE_INDICES: &[usize] = &[
    362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398,
];
const RIGHT_EYE_INDICES: &[usize] = &[
    33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
];

/// Compute the bounding box of a set of landmark indices in pixel coordinates.
fn landmark_rect(
    landmarks: &[Landmark],
    indices: &[usize],
    img_w: f32,
    img_h: f32,
    margin: f32,
) -> Option<(u32, u32, u32, u32)> {
    let mut min_x = f32::MAX;
    let mut min_y = f32::MAX;
    let mut max_x = f32::MIN;
    let mut max_y = f32::MIN;

    for &i in indices {
        if i >= landmarks.len() {
            return None;
        }
        let px = landmarks[i].x * img_w;
        let py = landmarks[i].y * img_h;
        min_x = min_x.min(px);
        min_y = min_y.min(py);
        max_x = max_x.max(px);
        max_y = max_y.max(py);
    }

    let w = max_x - min_x;
    let h = max_y - min_y;
    let mx = w * margin;
    let my = h * margin;

    let x0 = (min_x - mx).max(0.0) as u32;
    let y0 = (min_y - my).max(0.0) as u32;
    let x1 = ((max_x + mx).min(img_w)) as u32;
    let y1 = ((max_y + my).min(img_h)) as u32;

    if x1 <= x0 || y1 <= y0 {
        return None;
    }

    Some((x0, y0, x1, y1))
}

/// Compute the face bounding box from all 468 face landmarks.
fn face_rect_from_landmarks(
    landmarks: &[Landmark],
    img_w: f32,
    img_h: f32,
) -> Option<(u32, u32, u32, u32)> {
    let indices: Vec<usize> = (0..468.min(landmarks.len())).collect();
    landmark_rect(landmarks, &indices, img_w, img_h, 0.1)
}

/// Build the 12D rect feature for AFFNet:
/// [left_eye x,y,w,h, right_eye x,y,w,h, face x,y,w,h] all normalised.
fn rect_feature(
    l_xyxy: (u32, u32, u32, u32),
    r_xyxy: (u32, u32, u32, u32),
    f_xyxy: (u32, u32, u32, u32),
    w: f32,
    h: f32,
) -> [f32; 12] {
    let norm = |xyxy: (u32, u32, u32, u32)| -> [f32; 4] {
        [
            xyxy.0 as f32 / w,
            xyxy.1 as f32 / h,
            (xyxy.2 - xyxy.0) as f32 / w,
            (xyxy.3 - xyxy.1) as f32 / h,
        ]
    };
    let l = norm(l_xyxy);
    let r = norm(r_xyxy);
    let f = norm(f_xyxy);
    [
        l[0], l[1], l[2], l[3], r[0], r[1], r[2], r[3], f[0], f[1], f[2], f[3],
    ]
}

/// Crop a rectangular region from RGB bytes, resize, and convert to an NCHW f32 tensor.
fn crop_and_preprocess(
    rgb: &[u8],
    img_w: u32,
    img_h: u32,
    xyxy: (u32, u32, u32, u32),
    out_c: usize,
    out_h: usize,
    out_w: usize,
) -> Option<tract_ndarray::Array4<f32>> {
    let img =
        image::ImageBuffer::<image::Rgb<u8>, _>::from_raw(img_w, img_h, rgb.to_vec())?;

    let (x0, y0, x1, y1) = xyxy;
    let cw = x1.saturating_sub(x0).max(1);
    let ch = y1.saturating_sub(y0).max(1);
    let sub = image::imageops::crop_imm(&img, x0, y0, cw, ch).to_image();
    let resized = image::imageops::resize(
        &sub,
        out_w as u32,
        out_h as u32,
        image::imageops::FilterType::Triangle,
    );

    let mut tensor = tract_ndarray::Array4::<f32>::zeros([1, out_c, out_h, out_w]);
    for y in 0..out_h {
        for x in 0..out_w {
            let px = resized.get_pixel(x as u32, y as u32);
            // Normalise to [0, 1]
            tensor[[0, 0, y, x]] = px[0] as f32 / 255.0;
            if out_c >= 2 {
                tensor[[0, 1, y, x]] = px[1] as f32 / 255.0;
            }
            if out_c >= 3 {
                tensor[[0, 2, y, x]] = px[2] as f32 / 255.0;
            }
        }
    }
    Some(tensor)
}

// ═══════════════════════════════════════════════════════════════════
// GazePipeline
// ═══════════════════════════════════════════════════════════════════

pub struct GazePipeline {
    face_mesh: FaceMeshDetector,
    feat_buf: FeatureBuffer,
    ridge: RidgeRegressor,
    smoother: FixationSmoother,
    pupil_smoother: PupilSmoother,
    affnet: Option<OnnxPlan>,
    pub screen_w: u32,
    pub screen_h: u32,
    frame_count: u64,
}

impl GazePipeline {
    /// Create a new pipeline. Loads all ML models from `models_dir`.
    ///
    /// `screen_w` and `screen_h` define the screen resolution used for
    /// mapping normalised gaze coordinates to pixel coordinates.
    pub fn new(models_dir: &Path, screen_w: u32, screen_h: u32) -> Result<Self> {
        let face_mesh =
            FaceMeshDetector::load(models_dir).context("Loading face mesh detector")?;

        // Load AFFNet (may have external data file affnet.onnx.data)
        let affnet_path = models_dir.join("affnet.onnx");
        let affnet = if affnet_path.exists() {
            match Self::load_affnet(&affnet_path) {
                Ok(plan) => {
                    log::info!("AFFNet loaded successfully");
                    Some(plan)
                }
                Err(e) => {
                    log::warn!("Failed to load AFFNet: {}. Gaze prediction will use iris features only.", e);
                    None
                }
            }
        } else {
            log::warn!("AFFNet model not found at {:?}. Using iris features only.", affnet_path);
            None
        };

        Ok(Self {
            face_mesh,
            feat_buf: FeatureBuffer::new(10),
            ridge: RidgeRegressor::new(),
            smoother: FixationSmoother::new(),
            pupil_smoother: PupilSmoother::new(5),
            affnet,
            screen_w,
            screen_h,
            frame_count: 0,
        })
    }

    fn load_affnet(path: &Path) -> Result<OnnxPlan> {
        // AFFNet inputs:
        //   left_eye:  1x3x112x112
        //   right_eye: 1x3x112x112
        //   face:      1x3x224x224
        //   rect:      1x12
        // Output: gaze 1x2
        let model = tract_onnx::onnx()
            .model_for_path(path)
            .with_context(|| format!("Loading AFFNet from {:?}", path))?
            .with_input_fact(0, f32::fact([1, 3, 112, 112]).into())?
            .with_input_fact(1, f32::fact([1, 3, 112, 112]).into())?
            .with_input_fact(2, f32::fact([1, 3, 224, 224]).into())?
            .with_input_fact(3, f32::fact([1, 12]).into())?
            .into_optimized()?
            .into_runnable()?;
        Ok(model)
    }

    /// Process a single camera frame. Returns a `GazeFrame` with screen
    /// coordinates and pupil size.
    ///
    /// This mirrors Python's `GazeSystem._extract()`:
    /// 1. face_mesh.detect() -> 478 landmarks
    /// 2. EAR blink filter (< 0.12 -> invalid)
    /// 3. compute_iris_features() -> 6D
    /// 4. run_affnet() -> 2D
    /// 5. Concatenate -> 8D, push to feat_buf
    /// 6. weighted_mean -> ridge.predict -> smoother -> screen coords
    /// 7. estimate_pupil_size -> pupil_smoother
    pub fn process_frame(&mut self, rgb: &[u8], w: u32, h: u32, t: f64) -> GazeFrame {
        self.frame_count += 1;

        let invalid = GazeFrame {
            t,
            x: f64::NAN,
            y: f64::NAN,
            pupil: f32::NAN,
            valid: false,
            fps: 0,
        };

        // 1. Face mesh detection
        let landmarks = match self.face_mesh.detect(rgb, w, h) {
            Some(lm) if lm.len() >= 468 => lm,
            _ => {
                log::debug!("No face detected at t={:.3}", t);
                return invalid;
            }
        };

        // 2. EAR blink filter
        let l_ear = compute_ear(&landmarks, &LEFT_EAR_INDICES);
        let r_ear = compute_ear(&landmarks, &RIGHT_EAR_INDICES);
        let ear = 0.5 * (l_ear + r_ear);

        if ear < BLINK_EAR_THRESHOLD {
            log::debug!("Blink detected (EAR={:.3}) at t={:.3}", ear, t);
            return invalid;
        }

        // 3. Iris features (6D)
        let iris_feat = match compute_iris_features(&landmarks) {
            Some(f) => f,
            None => {
                log::debug!("Iris features unavailable at t={:.3}", t);
                return invalid;
            }
        };

        // 4. AFFNet model gaze (2D)
        let model_gaze = self.run_affnet(rgb, w, h, &landmarks);

        // 5. Concatenate -> 8D feature
        let feat_8d: [f32; 8] = [
            iris_feat[0],
            iris_feat[1],
            iris_feat[2],
            iris_feat[3],
            iris_feat[4],
            iris_feat[5],
            model_gaze[0],
            model_gaze[1],
        ];

        // Quality score: how "open" the eyes are
        let _quality = ((ear - BLINK_EAR_THRESHOLD) / 0.2).clamp(0.0, 1.0);

        // 6. Push to feature buffer and predict screen coordinates
        self.feat_buf.push(feat_8d);

        let (screen_x, screen_y) = if self.feat_buf.len() >= MIN_FEATURES_FOR_PREDICTION {
            if let Some(mean_feat) = self.feat_buf.weighted_mean() {
                let (raw_x, raw_y) = self.ridge.predict(&mean_feat);
                // Clamp to screen bounds
                let clamped_x = raw_x.clamp(0.0, self.screen_w as f64);
                let clamped_y = raw_y.clamp(0.0, self.screen_h as f64);
                // Apply fixation smoothing
                self.smoother.update(clamped_x, clamped_y)
            } else {
                (f64::NAN, f64::NAN)
            }
        } else {
            (f64::NAN, f64::NAN)
        };

        // 7. Pupil estimation
        let pupil_raw = estimate_pupil_size(&landmarks);
        let pupil_smooth = self.pupil_smoother.smooth(pupil_raw);

        GazeFrame {
            t,
            x: screen_x,
            y: screen_y,
            pupil: pupil_smooth,
            valid: !screen_x.is_nan() && !screen_y.is_nan(),
            fps: 0, // filled in by the caller / capture loop
        }
    }

    /// Run AFFNet on cropped eye/face regions extracted from the current frame.
    ///
    /// Returns [gaze_x, gaze_y] or [0.0, 0.0] if AFFNet is unavailable or fails.
    fn run_affnet(&self, frame: &[u8], w: u32, h: u32, landmarks: &[Landmark]) -> [f32; 2] {
        let affnet = match &self.affnet {
            Some(m) => m,
            None => return [0.0, 0.0],
        };

        let fw = w as f32;
        let fh = h as f32;

        // Compute eye and face rectangles
        let l_xyxy = match landmark_rect(landmarks, LEFT_EYE_INDICES, fw, fh, 0.3) {
            Some(r) => r,
            None => return [0.0, 0.0],
        };
        let r_xyxy = match landmark_rect(landmarks, RIGHT_EYE_INDICES, fw, fh, 0.3) {
            Some(r) => r,
            None => return [0.0, 0.0],
        };
        let f_xyxy = match face_rect_from_landmarks(landmarks, fw, fh) {
            Some(r) => r,
            None => return [0.0, 0.0],
        };

        // Crop and preprocess
        let left_eye = match crop_and_preprocess(frame, w, h, l_xyxy, 3, 112, 112) {
            Some(t) => t,
            None => return [0.0, 0.0],
        };
        let right_eye = match crop_and_preprocess(frame, w, h, r_xyxy, 3, 112, 112) {
            Some(t) => t,
            None => return [0.0, 0.0],
        };
        let face_img = match crop_and_preprocess(frame, w, h, f_xyxy, 3, 224, 224) {
            Some(t) => t,
            None => return [0.0, 0.0],
        };

        // Rect feature: 1x12
        let rect_feat = rect_feature(l_xyxy, r_xyxy, f_xyxy, fw, fh);
        let rect_tensor =
            tract_ndarray::Array2::<f32>::from_shape_vec([1, 12], rect_feat.to_vec())
                .unwrap_or_else(|_| tract_ndarray::Array2::zeros([1, 12]));

        // Run AFFNet
        let result = affnet.run(tvec![
            left_eye.into_tensor().into(),
            right_eye.into_tensor().into(),
            face_img.into_tensor().into(),
            rect_tensor.into_tensor().into(),
        ]);

        match result {
            Ok(outputs) => {
                if let Some(gaze) = outputs.first() {
                    if let Ok(view) = gaze.to_array_view::<f32>() {
                        let flat = view.as_slice().unwrap_or(&[0.0, 0.0]);
                        if flat.len() >= 2 {
                            return [flat[0], flat[1]];
                        }
                    }
                }
                [0.0, 0.0]
            }
            Err(e) => {
                log::debug!("AFFNet inference failed: {}", e);
                [0.0, 0.0]
            }
        }
    }

    /// Provide calibration data to fit the Ridge regressor.
    ///
    /// `samples`: pairs of (8D feature vector, [screen_x, screen_y])
    pub fn calibrate(&mut self, x_rows: &[[f64; 8]], y_rows: &[[f64; 2]], alpha: f64) {
        self.ridge.fit(x_rows, y_rows, alpha);
        self.smoother.reset();
        self.feat_buf.clear();
        log::info!("Ridge regressor calibrated with {} samples", x_rows.len());
    }

    /// Reset all smoothers and buffers (e.g. between sessions).
    pub fn reset(&mut self) {
        self.feat_buf.clear();
        self.smoother.reset();
        self.frame_count = 0;
    }

    /// Return current raw 8D feature (for calibration data collection).
    /// Returns None if there aren't enough frames yet.
    pub fn current_feature(&self) -> Option<[f64; 8]> {
        self.feat_buf.weighted_mean()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rect_feature() {
        let rf = rect_feature((10, 20, 50, 60), (70, 20, 110, 60), (0, 0, 200, 200), 200.0, 200.0);
        assert_eq!(rf.len(), 12);
        // Left eye x: 10/200 = 0.05
        assert!((rf[0] - 0.05).abs() < 0.001);
    }
}

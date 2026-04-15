//! MediaPipe-style face mesh detection pipeline using tract for ONNX inference.
//!
//! Pipeline stages:
//! 1. Face detection (BlazeFace short-range) — 128x128 input
//! 2. Face landmark (FaceMesh 468-point) — 192x192 input (TFLite via tract)
//! 3. Iris landmark refinement — 64x64 input per eye
//!
//! Output: 478 landmarks (468 face + 5 right iris + 5 left iris).

use std::path::Path;

use anyhow::{Context, Result};
use image::{ImageBuffer, Rgb, RgbImage};
use tract_onnx::prelude::*;

use crate::gaze_math::Landmark;

// ═══════════════════════════════════════════════════════════════════
// Type aliases for tract plans
// ═══════════════════════════════════════════════════════════════════

type OnnxPlan = TypedRunnableModel<TypedModel>;

// ═══════════════════════════════════════════════════════════════════
// BlazeFace SSD anchors
// ═══════════════════════════════════════════════════════════════════

/// Pre-computed anchor centres for the BlazeFace SSD head.
///
/// BlazeFace short-range spec:
/// - Input 128x128
/// - Feature map strides: [8, 16]
/// - 2 anchors per cell
/// - Anchor format: (cx, cy) normalised to [0, 1]
fn generate_blazeface_anchors() -> Vec<[f32; 2]> {
    let strides = [8u32, 16];
    let anchors_per_stride = 2;
    let input_size = 128u32;
    let mut anchors = Vec::new();

    for &stride in &strides {
        let grid = input_size / stride;
        for y in 0..grid {
            for x in 0..grid {
                let cx = (x as f32 + 0.5) / grid as f32;
                let cy = (y as f32 + 0.5) / grid as f32;
                for _ in 0..anchors_per_stride {
                    anchors.push([cx, cy]);
                }
            }
        }
    }
    anchors
}

// ═══════════════════════════════════════════════════════════════════
// Crop transform helpers
// ═══════════════════════════════════════════════════════════════════

/// A rectangle in original-image pixel coordinates.
#[derive(Clone, Copy, Debug)]
struct Rect {
    x: f32,
    y: f32,
    w: f32,
    h: f32,
}

/// Crop a region from an RGB image, resize to `(out_w, out_h)`.
/// Returns the cropped-and-resized image and the Rect used.
fn crop_and_resize(
    img: &RgbImage,
    rect: Rect,
    out_w: u32,
    out_h: u32,
) -> (RgbImage, Rect) {
    let (iw, ih) = (img.width() as f32, img.height() as f32);

    // Clamp the rect to image bounds
    let x0 = (rect.x).max(0.0).min(iw) as u32;
    let y0 = (rect.y).max(0.0).min(ih) as u32;
    let x1 = ((rect.x + rect.w).max(0.0).min(iw) as u32).max(x0 + 1);
    let y1 = ((rect.y + rect.h).max(0.0).min(ih) as u32).max(y0 + 1);

    let clamped_rect = Rect {
        x: x0 as f32,
        y: y0 as f32,
        w: (x1 - x0) as f32,
        h: (y1 - y0) as f32,
    };

    let sub = image::imageops::crop_imm(img, x0, y0, x1 - x0, y1 - y0).to_image();
    let resized = image::imageops::resize(&sub, out_w, out_h, image::imageops::FilterType::Triangle);

    (resized, clamped_rect)
}

// ═══════════════════════════════════════════════════════════════════
// FaceMeshDetector
// ═══════════════════════════════════════════════════════════════════

pub struct FaceMeshDetector {
    face_detector: OnnxPlan,
    /// Optional: MediaPipe face_landmark has TFLite CUSTOM ops that neither
    /// tract-onnx nor tract-tflite can decode. When None, we synthesise the
    /// 468 landmarks from the BlazeFace bbox (degraded precision, still
    /// usable for iris tracking).
    face_landmarker: Option<OnnxPlan>,
    iris_landmarker: OnnxPlan,
    anchors: Vec<[f32; 2]>,
}

impl FaceMeshDetector {
    /// Load all three models from `models_dir`.
    ///
    /// Expects:
    /// - `models_dir/face_detection.onnx`
    /// - `models_dir/face_landmark.tflite` (loaded as ONNX — tract handles both)
    /// - `models_dir/iris_landmark.onnx`
    pub fn load(models_dir: &Path) -> Result<Self> {
        log::info!("Loading face mesh models from {:?}", models_dir);

        // ── Face detector (BlazeFace) ────────────────────────────
        let det_path = models_dir.join("face_detection.onnx");
        let face_detector = tract_onnx::onnx()
            .model_for_path(&det_path)
            .with_context(|| format!("Loading {:?}", det_path))?
            .with_input_fact(0, f32::fact([1, 3, 128, 128]).into())?
            .into_optimized()?
            .into_runnable()?;
        log::info!("Face detector loaded");

        // ── Face landmarker (FaceMesh 468-point) ─────────────────
        // MediaPipe's face_landmark.tflite contains CUSTOM ops (flex delegate
        // kernels) that neither tract-onnx nor tract-tflite can parse. If
        // loading fails we continue WITHOUT face landmarks: iris tracking
        // uses BlazeFace bbox-derived heuristic eye corners instead. Head
        // pose / EAR are degraded but gaze ratios still work.
        let lm_path = models_dir.join("face_landmark.tflite");
        let face_landmarker = match Self::load_tflite_or_onnx(&lm_path, &[1, 192, 192, 3]) {
            Ok(plan) => {
                log::info!("Face landmarker loaded");
                Some(plan)
            }
            Err(e) => {
                log::warn!(
                    "Face landmarker unavailable ({}). Falling back to bbox-heuristic landmarks; \
                     head pose and EAR will be approximate.",
                    e
                );
                None
            }
        };

        // ── Iris landmarker ──────────────────────────────────────
        let iris_path = models_dir.join("iris_landmark.onnx");
        let iris_landmarker = tract_onnx::onnx()
            .model_for_path(&iris_path)
            .with_context(|| format!("Loading {:?}", iris_path))?
            .with_input_fact(0, f32::fact([1, 3, 64, 64]).into())?
            .into_optimized()?
            .into_runnable()?;
        log::info!("Iris landmarker loaded");

        let anchors = generate_blazeface_anchors();
        log::info!("Generated {} BlazeFace anchors", anchors.len());

        Ok(Self {
            face_detector,
            face_landmarker,
            iris_landmarker,
            anchors,
        })
    }

    /// Load a MediaPipe TFLite model via tract-tflite. Falls back to an
    /// .onnx sibling if the tflite path fails.
    fn load_tflite_or_onnx(path: &Path, _input_shape: &[usize]) -> Result<OnnxPlan> {
        // tract-tflite has its own loader; input shapes are embedded in the
        // flat-buffer model so we don't need to specify them manually.
        let tflite_result: Result<OnnxPlan> = (|| {
            let plan = tract_tflite::tflite()
                .model_for_path(path)
                .with_context(|| format!("tract-tflite failed on {:?}", path))?
                .into_optimized()?
                .into_runnable()?;
            Ok(plan)
        })();

        match tflite_result {
            Ok(plan) => Ok(plan),
            Err(e) => {
                log::warn!(
                    "tract-tflite could not load {:?}: {}. Trying .onnx sibling...",
                    path, e
                );
                let onnx_path = path.with_extension("onnx");
                if onnx_path.exists() {
                    let plan = tract_onnx::onnx()
                        .model_for_path(&onnx_path)?
                        .with_input_fact(
                            0,
                            InferenceFact::dt_shape(f32::datum_type(), _input_shape),
                        )?
                        .into_optimized()?
                        .into_runnable()?;
                    Ok(plan)
                } else {
                    Err(e).context("No .onnx fallback found either")
                }
            }
        }
    }

    /// Run the full pipeline: detect face, extract 468 landmarks, refine irises.
    ///
    /// Returns 478 landmarks (468 face + 5 right iris + 5 left iris) in
    /// normalised [0, 1] coordinates relative to the input image, matching
    /// MediaPipe convention. Returns `None` if no face is detected.
    pub fn detect(&self, rgb_frame: &[u8], width: u32, height: u32) -> Option<Vec<Landmark>> {
        let img = ImageBuffer::<Rgb<u8>, _>::from_raw(width, height, rgb_frame.to_vec())?;

        // ── Stage 1: Face detection ──────────────────────────────
        let face_rect = self.detect_face(&img)?;

        // ── Stage 2: Face landmarks ──────────────────────────────
        let mut landmarks = match &self.face_landmarker {
            Some(_) => self.detect_landmarks(&img, &face_rect)?,
            None => synthesize_landmarks_from_bbox(&face_rect, width as f32, height as f32),
        };

        // ── Stage 3: Iris refinement ─────────────────────────────
        self.refine_iris(&img, &mut landmarks);

        if landmarks.len() >= 478 {
            Some(landmarks)
        } else {
            // Pad to 478 if iris refinement didn't produce enough
            while landmarks.len() < 478 {
                landmarks.push(Landmark { x: 0.0, y: 0.0 });
            }
            Some(landmarks)
        }
    }

    // ── Stage 1: BlazeFace detection ─────────────────────────────

    fn detect_face(&self, img: &RgbImage) -> Option<Rect> {
        let (iw, ih) = (img.width() as f32, img.height() as f32);

        // Resize to 128x128, normalise to [-1, 1]
        let resized =
            image::imageops::resize(img, 128, 128, image::imageops::FilterType::Triangle);

        // Build NCHW tensor: 1x3x128x128
        let mut input = tract_ndarray::Array4::<f32>::zeros([1, 3, 128, 128]);
        for y in 0..128usize {
            for x in 0..128usize {
                let px = resized.get_pixel(x as u32, y as u32);
                input[[0, 0, y, x]] = px[0] as f32 / 127.5 - 1.0;
                input[[0, 1, y, x]] = px[1] as f32 / 127.5 - 1.0;
                input[[0, 2, y, x]] = px[2] as f32 / 127.5 - 1.0;
            }
        }

        let input_tv: TValue = input.into_tensor().into();
        let outputs = self.face_detector.run(tvec![input_tv]).ok()?;

        // BlazeFace outputs: [scores, boxes] or [regressors, classificators]
        // Typical shapes:
        //   classificators: [1, num_anchors, 1]  (raw logits → sigmoid)
        //   regressors: [1, num_anchors, 16]  (box offsets + 6 keypoints)
        if outputs.len() < 2 {
            log::warn!("Face detector returned {} outputs, expected 2", outputs.len());
            return None;
        }

        let scores_tensor = outputs[0].to_array_view::<f32>().ok()?;
        let boxes_tensor = outputs[1].to_array_view::<f32>().ok()?;

        // Determine which output is scores vs regressors by shape
        let (scores_view, boxes_view) = if scores_tensor.shape().last() == Some(&1)
            || (scores_tensor.ndim() >= 2
                && scores_tensor.shape()[scores_tensor.ndim() - 1] < boxes_tensor.shape()[boxes_tensor.ndim() - 1])
        {
            (scores_tensor, boxes_tensor)
        } else {
            (boxes_tensor, scores_tensor)
        };

        let scores_flat = scores_view.as_slice()?;
        let boxes_flat = boxes_view.as_slice()?;

        let num_anchors = self.anchors.len();
        // Determine box stride (regressors per anchor, typically 16)
        let box_stride = if boxes_flat.len() >= num_anchors {
            boxes_flat.len() / num_anchors
        } else {
            16
        };

        let mut best_score: f32 = 0.0;
        let mut best_idx: Option<usize> = None;

        for i in 0..num_anchors.min(scores_flat.len()) {
            let raw = scores_flat[i];
            let score = 1.0 / (1.0 + (-raw).exp()); // sigmoid
            if score > best_score && score > 0.5 {
                best_score = score;
                best_idx = Some(i);
            }
        }

        let idx = best_idx?;
        let anchor = self.anchors[idx];
        let off = idx * box_stride;

        if off + 4 > boxes_flat.len() {
            return None;
        }

        // Decode box: offsets are relative to anchor, in 128x128 space
        let cx = anchor[0] + boxes_flat[off] / 128.0;
        let cy = anchor[1] + boxes_flat[off + 1] / 128.0;
        let bw = boxes_flat[off + 2] / 128.0;
        let bh = boxes_flat[off + 3] / 128.0;

        // Convert to pixel coordinates in original image
        let x = (cx - bw / 2.0) * iw;
        let y = (cy - bh / 2.0) * ih;
        let w = bw * iw;
        let h = bh * ih;

        log::debug!(
            "Face detected: score={:.3}, rect=({:.0},{:.0},{:.0},{:.0})",
            best_score, x, y, w, h
        );

        Some(Rect { x, y, w, h })
    }

    // ── Stage 2: FaceMesh 468-point landmarks ────────────────────

    fn detect_landmarks(&self, img: &RgbImage, face_rect: &Rect) -> Option<Vec<Landmark>> {
        let (iw, ih) = (img.width() as f32, img.height() as f32);

        // Add 10% margin to the face rect
        let margin_x = face_rect.w * 0.1;
        let margin_y = face_rect.h * 0.1;
        let crop_rect = Rect {
            x: face_rect.x - margin_x,
            y: face_rect.y - margin_y,
            w: face_rect.w + 2.0 * margin_x,
            h: face_rect.h + 2.0 * margin_y,
        };

        let (cropped, actual_rect) = crop_and_resize(img, crop_rect, 192, 192);

        // Build NHWC tensor: 1x192x192x3, normalised to [0, 1]
        let mut input = tract_ndarray::Array4::<f32>::zeros([1, 192, 192, 3]);
        for y in 0..192usize {
            for x in 0..192usize {
                let px = cropped.get_pixel(x as u32, y as u32);
                input[[0, y, x, 0]] = px[0] as f32 / 255.0;
                input[[0, y, x, 1]] = px[1] as f32 / 255.0;
                input[[0, y, x, 2]] = px[2] as f32 / 255.0;
            }
        }

        let input_tv: TValue = input.into_tensor().into();
        let outputs = self.face_landmarker.as_ref()?.run(tvec![input_tv]).ok()?;

        if outputs.is_empty() {
            log::warn!("Face landmarker returned no outputs");
            return None;
        }

        let lm_tensor = outputs[0].to_array_view::<f32>().ok()?;
        let lm_flat = lm_tensor.as_slice()?;

        // Expected: 468*3 = 1404 values (x, y, z per landmark)
        let num_landmarks = lm_flat.len() / 3;
        if num_landmarks < 468 {
            log::warn!(
                "Face landmarker returned {} values, expected >= 1404 (468*3)",
                lm_flat.len()
            );
            return None;
        }

        let mut landmarks = Vec::with_capacity(478);
        for i in 0..468 {
            // Landmark coords are in 192x192 crop space
            let lx = lm_flat[i * 3];
            let ly = lm_flat[i * 3 + 1];
            // Map back to original image coordinates (normalised to [0, 1])
            let orig_x = (actual_rect.x + lx / 192.0 * actual_rect.w) / iw;
            let orig_y = (actual_rect.y + ly / 192.0 * actual_rect.h) / ih;
            landmarks.push(Landmark {
                x: orig_x,
                y: orig_y,
            });
        }

        Some(landmarks)
    }

    // ── Stage 3: Iris landmark refinement ────────────────────────

    fn refine_iris(&self, img: &RgbImage, landmarks: &mut Vec<Landmark>) {
        let (iw, ih) = (img.width() as f32, img.height() as f32);

        // Right eye: outer corner 33, inner corner 133
        if let Some(iris_pts) = self.extract_iris(img, landmarks, 33, 133) {
            // Landmarks 468-472: right iris (5 points)
            for pt in &iris_pts {
                if landmarks.len() < 473 {
                    landmarks.push(*pt);
                }
            }
        } else {
            // Pad with eye-center estimate
            let center = estimate_eye_center(landmarks, 33, 133, iw, ih);
            for _ in 0..5 {
                if landmarks.len() < 473 {
                    landmarks.push(center);
                }
            }
        }

        // Left eye: inner corner 362, outer corner 263
        if let Some(iris_pts) = self.extract_iris(img, landmarks, 362, 263) {
            // Landmarks 473-477: left iris (5 points)
            for pt in &iris_pts {
                if landmarks.len() < 478 {
                    landmarks.push(*pt);
                }
            }
        } else {
            let center = estimate_eye_center(landmarks, 362, 263, iw, ih);
            for _ in 0..5 {
                if landmarks.len() < 478 {
                    landmarks.push(center);
                }
            }
        }
    }

    fn extract_iris(
        &self,
        img: &RgbImage,
        landmarks: &[Landmark],
        corner1: usize,
        corner2: usize,
    ) -> Option<[Landmark; 5]> {
        if corner1 >= landmarks.len() || corner2 >= landmarks.len() {
            return None;
        }
        let (iw, ih) = (img.width() as f32, img.height() as f32);

        let c1 = landmarks[corner1];
        let c2 = landmarks[corner2];

        // Compute eye region in pixel coordinates
        let cx_px = (c1.x + c2.x) / 2.0 * iw;
        let cy_px = (c1.y + c2.y) / 2.0 * ih;
        let eye_w_px = ((c2.x - c1.x).abs() * iw).max(10.0);
        let eye_h_px = eye_w_px * 0.75; // approximate eye aspect ratio

        // Add margin (50% for iris model)
        let margin = 0.5;
        let crop_w = eye_w_px * (1.0 + margin);
        let crop_h = eye_h_px * (1.0 + margin);
        let crop_size = crop_w.max(crop_h); // square crop

        let eye_rect = Rect {
            x: cx_px - crop_size / 2.0,
            y: cy_px - crop_size / 2.0,
            w: crop_size,
            h: crop_size,
        };

        let (cropped, actual_rect) = crop_and_resize(img, eye_rect, 64, 64);

        // Build NCHW tensor: 1x3x64x64, normalised to [0, 1]
        let mut input = tract_ndarray::Array4::<f32>::zeros([1, 3, 64, 64]);
        for y in 0..64usize {
            for x in 0..64usize {
                let px = cropped.get_pixel(x as u32, y as u32);
                input[[0, 0, y, x]] = px[0] as f32 / 255.0;
                input[[0, 1, y, x]] = px[1] as f32 / 255.0;
                input[[0, 2, y, x]] = px[2] as f32 / 255.0;
            }
        }

        let input_tv: TValue = input.into_tensor().into();
        let outputs = self.iris_landmarker.run(tvec![input_tv]).ok()?;

        if outputs.is_empty() {
            return None;
        }

        let iris_tensor = outputs[0].to_array_view::<f32>().ok()?;
        let iris_flat = iris_tensor.as_slice()?;

        // Expected: 5 * 3 = 15 values (x, y, z per iris point)
        if iris_flat.len() < 15 {
            log::warn!("Iris landmarker returned {} values, expected >= 15", iris_flat.len());
            return None;
        }

        let mut pts = [Landmark { x: 0.0, y: 0.0 }; 5];
        for i in 0..5 {
            let lx = iris_flat[i * 3];
            let ly = iris_flat[i * 3 + 1];
            // Map from 64x64 crop space back to normalised image coords
            pts[i] = Landmark {
                x: (actual_rect.x + lx / 64.0 * actual_rect.w) / iw,
                y: (actual_rect.y + ly / 64.0 * actual_rect.h) / ih,
            };
        }

        Some(pts)
    }
}

/// Fallback when face_landmark.tflite cannot load: produce a 468-length
/// landmark vector with approximate positions derived from the BlazeFace
/// bounding box. Only the MediaPipe indices that downstream code actually
/// reads (1, 10, 33, 133, 152, 234, 263, 362, 454) are placed meaningfully;
/// the rest are zero-filled.
fn synthesize_landmarks_from_bbox(bbox: &Rect, img_w: f32, img_h: f32) -> Vec<Landmark> {
    let mut lm = vec![Landmark { x: 0.0, y: 0.0 }; 468];

    // Normalised bbox center + spans
    let cx = (bbox.x + bbox.w / 2.0) / img_w;
    let cy = (bbox.y + bbox.h / 2.0) / img_h;
    let fw = bbox.w / img_w;
    let fh = bbox.h / img_h;

    // Rough anthropometric ratios within a face bbox (MediaPipe-ish)
    let eye_y = cy - 0.10 * fh;            // eyes sit ~10% above bbox center
    let eye_half_span = 0.20 * fw;         // each eye ~20% of face width from center
    let eye_radius = 0.08 * fw;            // inner→outer corner half-width

    let right_eye_cx = cx - eye_half_span; // anatomical right = image left
    let left_eye_cx  = cx + eye_half_span;

    // Eye corners (anatomical convention matching gaze_math.rs)
    lm[33]  = Landmark { x: right_eye_cx - eye_radius, y: eye_y }; // right outer
    lm[133] = Landmark { x: right_eye_cx + eye_radius, y: eye_y }; // right inner
    lm[362] = Landmark { x: left_eye_cx  - eye_radius, y: eye_y }; // left inner
    lm[263] = Landmark { x: left_eye_cx  + eye_radius, y: eye_y }; // left outer

    // Face structural points used for head pose estimation
    lm[1]   = Landmark { x: cx,                          y: cy + 0.05 * fh }; // nose tip
    lm[10]  = Landmark { x: cx,                          y: cy - 0.45 * fh }; // forehead
    lm[152] = Landmark { x: cx,                          y: cy + 0.45 * fh }; // chin
    lm[234] = Landmark { x: cx - 0.45 * fw,              y: cy };             // left cheek (image)
    lm[454] = Landmark { x: cx + 0.45 * fw,              y: cy };             // right cheek (image)

    lm
}

/// Estimate the centre of an eye from two corner landmarks.
fn estimate_eye_center(
    landmarks: &[Landmark],
    c1: usize,
    c2: usize,
    _iw: f32,
    _ih: f32,
) -> Landmark {
    if c1 < landmarks.len() && c2 < landmarks.len() {
        Landmark {
            x: (landmarks[c1].x + landmarks[c2].x) / 2.0,
            y: (landmarks[c1].y + landmarks[c2].y) / 2.0,
        }
    } else {
        Landmark { x: 0.0, y: 0.0 }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_anchor_generation() {
        let anchors = generate_blazeface_anchors();
        // stride=8: (128/8)^2 * 2 = 16*16*2 = 512
        // stride=16: (128/16)^2 * 2 = 8*8*2 = 128
        // Total: 640
        assert_eq!(anchors.len(), 640);

        // All anchors should be in [0, 1]
        for a in &anchors {
            assert!(a[0] >= 0.0 && a[0] <= 1.0, "cx out of range: {}", a[0]);
            assert!(a[1] >= 0.0 && a[1] <= 1.0, "cy out of range: {}", a[1]);
        }

        // First anchor should be near (0.5/16, 0.5/16) = (0.03125, 0.03125)
        assert!((anchors[0][0] - 0.03125).abs() < 0.01);
        assert!((anchors[0][1] - 0.03125).abs() < 0.01);
    }
}

//! Camera capture using nokhwa, running in a dedicated background thread.
//!
//! The latest frame is shared via `Arc<Mutex<Option<CameraFrame>>>` so that
//! the gaze pipeline can grab it at its own cadence.
//!
//! Because nokhwa's `Camera` is not `Send`, it must be created and used
//! entirely within the capture thread. A one-shot channel reports init
//! success/failure back to the caller.

use std::sync::Arc;
use std::thread;
use std::time::Instant;

use anyhow::{Context, Result};
use nokhwa::pixel_format::RgbFormat;
use nokhwa::utils::{CameraIndex, FrameFormat, RequestedFormat, RequestedFormatType};
use nokhwa::Camera;
use parking_lot::Mutex;

// ═══════════════════════════════════════════════════════════════════
// CameraFrame — a single captured image
// ═══════════════════════════════════════════════════════════════════

#[derive(Clone)]
pub struct CameraFrame {
    /// RGB pixels, row-major (width * height * 3 bytes).
    pub rgb: Vec<u8>,
    pub width: u32,
    pub height: u32,
    /// Seconds since the capture session started.
    pub timestamp: f64,
}

// ═══════════════════════════════════════════════════════════════════
// CameraCapture — background-thread camera reader
// ═══════════════════════════════════════════════════════════════════

pub struct CameraCapture {
    latest: Arc<Mutex<Option<CameraFrame>>>,
    stop_flag: Arc<Mutex<bool>>,
    handle: Option<thread::JoinHandle<()>>,
}

impl CameraCapture {
    /// Open the default camera at 640x480 @ 30 fps and start capturing
    /// frames in a background thread.
    ///
    /// The latest frame is accessible via [`latest_frame()`].
    pub fn start() -> Result<Self> {
        Self::start_with(0, 640, 480, 30)
    }

    /// Open a specific camera with custom resolution and frame rate.
    ///
    /// The camera is opened entirely within the background thread (nokhwa's
    /// Camera is not Send). A one-shot channel reports whether init succeeded.
    pub fn start_with(camera_index: u32, width: u32, height: u32, fps: u32) -> Result<Self> {
        let latest: Arc<Mutex<Option<CameraFrame>>> = Arc::new(Mutex::new(None));
        let stop_flag = Arc::new(Mutex::new(false));

        let latest_clone = Arc::clone(&latest);
        let stop_clone = Arc::clone(&stop_flag);

        // Channel to report init result from the capture thread
        let (init_tx, init_rx) = std::sync::mpsc::sync_channel::<Result<String>>(1);

        let handle = thread::Builder::new()
            .name("camera-capture".into())
            .spawn(move || {
                // Open camera inside the thread (Camera is !Send)
                let index = CameraIndex::Index(camera_index);
                let requested =
                    RequestedFormat::new::<RgbFormat>(RequestedFormatType::Closest(
                        nokhwa::utils::CameraFormat::new_from(
                            width,
                            height,
                            FrameFormat::MJPEG,
                            fps,
                        ),
                    ));

                let mut cam = match Camera::new(index, requested) {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = init_tx.send(Err(anyhow::anyhow!(
                            "Failed to open camera index {}: {}",
                            camera_index,
                            e
                        )));
                        return;
                    }
                };

                if let Err(e) = cam.open_stream() {
                    let _ = init_tx.send(Err(anyhow::anyhow!(
                        "Failed to open camera stream: {}",
                        e
                    )));
                    return;
                }

                let actual_fmt = cam.camera_format();
                let info_msg = format!(
                    "{}x{} @ {} fps ({:?})",
                    actual_fmt.resolution().width_x,
                    actual_fmt.resolution().height_y,
                    actual_fmt.frame_rate(),
                    actual_fmt.format()
                );
                let _ = init_tx.send(Ok(info_msg));

                // Enter capture loop
                Self::capture_loop(cam, latest_clone, stop_clone);
            })
            .context("Spawning camera thread")?;

        // Wait for init result from the thread
        let init_result = init_rx
            .recv()
            .map_err(|_| anyhow::anyhow!("Camera thread exited before reporting init status"))?;

        match init_result {
            Ok(info) => {
                log::info!("Camera opened: {}", info);
                Ok(Self {
                    latest,
                    stop_flag,
                    handle: Some(handle),
                })
            }
            Err(e) => {
                // Wait for thread to finish, then propagate the error
                let _ = handle.join();
                Err(e)
            }
        }
    }

    fn capture_loop(
        mut cam: Camera,
        latest: Arc<Mutex<Option<CameraFrame>>>,
        stop_flag: Arc<Mutex<bool>>,
    ) {
        let t0 = Instant::now();
        let mut frame_count: u64 = 0;

        loop {
            // Check stop flag
            if *stop_flag.lock() {
                log::info!(
                    "Camera capture stopping (captured {} frames)",
                    frame_count
                );
                break;
            }

            match cam.frame() {
                Ok(buffer) => {
                    let res = buffer.resolution();
                    let w = res.width_x;
                    let h = res.height_y;

                    // Decode to RGB
                    match buffer.decode_image::<RgbFormat>() {
                        Ok(rgb_image) => {
                            let rgb_bytes = rgb_image.into_raw();
                            let ts = t0.elapsed().as_secs_f64();

                            *latest.lock() = Some(CameraFrame {
                                rgb: rgb_bytes,
                                width: w,
                                height: h,
                                timestamp: ts,
                            });

                            frame_count += 1;
                            if frame_count % 300 == 0 {
                                let fps = frame_count as f64 / ts;
                                log::debug!(
                                    "Camera: {} frames, {:.1} fps, {}x{}",
                                    frame_count, fps, w, h
                                );
                            }
                        }
                        Err(e) => {
                            log::warn!("Failed to decode camera frame: {}", e);
                        }
                    }
                }
                Err(e) => {
                    log::warn!("Failed to capture frame: {}", e);
                    // Brief pause before retry to avoid busy-spinning on error
                    thread::sleep(std::time::Duration::from_millis(10));
                }
            }
        }

        // Clean up
        if let Err(e) = cam.stop_stream() {
            log::warn!("Error stopping camera stream: {}", e);
        }
    }

    /// Get the most recently captured frame, or `None` if no frame is
    /// available yet.
    pub fn latest_frame(&self) -> Option<CameraFrame> {
        self.latest.lock().clone()
    }

    /// Get a shared reference to the latest-frame slot (for direct
    /// locking in tight loops).
    pub fn latest_frame_arc(&self) -> Arc<Mutex<Option<CameraFrame>>> {
        Arc::clone(&self.latest)
    }

    /// Signal the capture thread to stop and wait for it to finish.
    pub fn stop(&mut self) {
        *self.stop_flag.lock() = true;
        if let Some(handle) = self.handle.take() {
            if let Err(e) = handle.join() {
                log::warn!("Camera thread panicked: {:?}", e);
            }
        }
    }

    /// Check whether the capture thread is still running.
    pub fn is_running(&self) -> bool {
        self.handle.as_ref().map_or(false, |h| !h.is_finished())
    }
}

impl Drop for CameraCapture {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    // Camera tests require hardware and permission — run manually.
    // cargo test --lib camera -- --ignored

    #[test]
    #[ignore]
    fn test_camera_open() {
        let cap = super::CameraCapture::start();
        match cap {
            Ok(mut c) => {
                std::thread::sleep(std::time::Duration::from_secs(1));
                let frame = c.latest_frame();
                assert!(frame.is_some(), "Should have captured at least one frame");
                let f = frame.unwrap();
                assert!(f.width > 0);
                assert!(f.height > 0);
                assert_eq!(f.rgb.len() as u32, f.width * f.height * 3);
                c.stop();
            }
            Err(e) => {
                eprintln!("Camera not available (expected in CI): {}", e);
            }
        }
    }
}

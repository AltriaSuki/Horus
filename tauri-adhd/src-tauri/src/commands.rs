//! Tauri commands — the IPC interface between Svelte frontend and Rust backend.
//!
//! Each `#[tauri::command]` function is callable from JS via `invoke('name', {...})`.

use crate::{camera, gaze_math, inference, pipeline, storage};
use parking_lot::Mutex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager};

// ═══════════════════════════════════════════════════════════════════
// Global session state — shared between commands
// ═══════════════════════════════════════════════════════════════════

struct SessionState {
    /// Calibration samples: (8D features, screen_x, screen_y)
    calibration_samples: Vec<([f64; 8], [f64; 2])>,
    /// Accumulated trial results for the current session
    trial_results: Vec<inference::TrialResult>,
    /// The gaze pipeline (camera + face mesh + AFFNet + Ridge)
    pipeline: Option<pipeline::GazePipeline>,
    /// Camera capture thread handle
    camera: Option<camera::CameraCapture>,
    /// Whether the gaze streaming loop is running
    streaming: bool,
    /// Models directory path
    models_dir: PathBuf,
}

static SESSION: once_cell::sync::Lazy<Mutex<SessionState>> =
    once_cell::sync::Lazy::new(|| {
        Mutex::new(SessionState {
            calibration_samples: Vec::new(),
            trial_results: Vec::new(),
            pipeline: None,
            camera: None,
            streaming: false,
            models_dir: PathBuf::from("../models"),
        })
    });

// ═══════════════════════════════════════════════════════════════════
// Commands
// ═══════════════════════════════════════════════════════════════════

#[tauri::command]
pub fn get_health() -> HashMap<String, String> {
    let mut h = HashMap::new();
    h.insert("service".into(), "adhd-engine-rust".into());
    h.insert("version".into(), "0.2.0".into());
    h
}

#[tauri::command]
pub fn create_subject(id: String, display_name: String, sex: Option<String>) -> Result<storage::Subject, String> {
    storage::create_subject(&id, &display_name, sex.as_deref())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn list_subjects() -> Result<Vec<storage::Subject>, String> {
    storage::list_subjects().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn start_screening(subject_id: String, app: AppHandle) -> Result<storage::SessionRow, String> {
    let session_id = uuid::Uuid::new_v4().to_string();
    let session = storage::create_session(&session_id, &subject_id, "screening", "sternberg")
        .map_err(|e| e.to_string())?;

    // Reset session state
    {
        let mut state = SESSION.lock();
        state.calibration_samples.clear();
        state.trial_results.clear();
        state.streaming = false;

        // Resolve models directory relative to the executable
        // In dev mode: src-tauri/ is cwd, ../models/ works
        // In release: use the resource dir or exe dir
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));

        // Try multiple candidate paths
        let candidates = [
            exe_dir.join("models"),
            exe_dir.join("../models"),
            exe_dir.join("../../models"),
            exe_dir.join("../Resources/models"),  // macOS .app bundle
            PathBuf::from("../models"),            // dev mode
            PathBuf::from("models"),
        ];
        for c in &candidates {
            if c.join("rf_model.json").exists() {
                state.models_dir = c.clone();
                log::info!("Models dir: {:?}", c);
                break;
            }
        }

        // Initialize the gaze pipeline
        match pipeline::GazePipeline::new(&state.models_dir, 1920, 1080) {
            Ok(p) => {
                state.pipeline = Some(p);
                log::info!("Gaze pipeline initialized");
            }
            Err(e) => {
                log::warn!("Gaze pipeline init failed (will run without live gaze): {}", e);
                // Continue without pipeline — calibration and task can still work
                // with the frontend collecting data through the Canvas
            }
        }

        // Start camera capture
        match camera::CameraCapture::start_with(0, 640, 480, 30) {
            Ok(cam) => {
                state.camera = Some(cam);
                log::info!("Camera started");
            }
            Err(e) => {
                log::warn!("Camera init failed: {}", e);
            }
        }
    }

    // Start gaze streaming in a background thread
    let app_clone = app.clone();
    std::thread::Builder::new()
        .name("gaze-stream".into())
        .spawn(move || {
            stream_gaze_loop(app_clone);
        })
        .map_err(|e| format!("Failed to start gaze stream: {}", e))?;

    Ok(session)
}

/// Background thread: reads camera frames, runs pipeline, emits gaze events.
fn stream_gaze_loop(app: AppHandle) {
    {
        let mut state = SESSION.lock();
        state.streaming = true;
    }

    let start = std::time::Instant::now();
    log::info!("Gaze streaming started");

    loop {
        // Check if we should stop
        {
            let state = SESSION.lock();
            if !state.streaming {
                break;
            }
        }

        // Try to get a camera frame and process it
        // Get the latest camera frame, then process it through the pipeline.
        // Two separate lock scopes to avoid borrow conflict (camera is &,
        // pipeline is &mut, both behind the same Mutex).
        let frame_data = {
            let state = SESSION.lock();
            state.camera.as_ref().and_then(|cam| cam.latest_frame())
        };

        let frame_result = if let Some(cf) = frame_data {
            let mut state = SESSION.lock();
            if let Some(pipe) = &mut state.pipeline {
                let t = start.elapsed().as_secs_f64();
                Some(pipe.process_frame(&cf.rgb, cf.width, cf.height, t))
            } else {
                None
            }
        } else {
            None
        };

        if let Some(gf) = frame_result {
            // Emit gaze_frame event to frontend
            let _ = app.emit("gaze_frame", &gf);
        }

        // ~30 fps pacing (don't spin faster than the camera)
        std::thread::sleep(std::time::Duration::from_millis(16));
    }

    log::info!("Gaze streaming stopped");
}

#[tauri::command]
pub fn get_session(_session_id: String) -> Result<Option<storage::SessionRow>, String> {
    Ok(None) // TODO: implement session lookup
}

#[tauri::command]
pub fn get_report(session_id: String) -> Result<Option<inference::AdhdReport>, String> {
    storage::get_report(&session_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn list_subject_sessions(subject_id: String) -> Result<Vec<storage::SessionRow>, String> {
    storage::list_subject_sessions(&subject_id).map_err(|e| e.to_string())
}

/// Called by the Svelte frontend once per calibration point click.
#[tauri::command]
pub fn submit_calibration_sample(
    features: Vec<f64>,
    screen_x: f64,
    screen_y: f64,
) -> Result<(), String> {
    let mut state = SESSION.lock();

    // Convert features vec to [f64; 8]
    let mut feat = [0.0f64; 8];
    for (i, &v) in features.iter().take(8).enumerate() {
        feat[i] = v;
    }

    state.calibration_samples.push((feat, [screen_x, screen_y]));
    log::info!(
        "Calibration sample {}: screen=({:.0}, {:.0})",
        state.calibration_samples.len(), screen_x, screen_y
    );
    Ok(())
}

/// Called after all 13 calibration points. Fits the Ridge regressor.
#[tauri::command]
pub fn finish_calibration() -> Result<f64, String> {
    let mut state = SESSION.lock();
    let n = state.calibration_samples.len();

    if n < 5 {
        return Err(format!("Not enough calibration points: {} (need >= 5)", n));
    }

    // Collect data first, then borrow pipeline mutably (avoids Rust borrow conflict)
    let x_rows: Vec<[f64; 8]> = state.calibration_samples.iter().map(|(f, _)| *f).collect();
    let y_rows: Vec<[f64; 2]> = state.calibration_samples.iter().map(|(_, s)| *s).collect();

    if let Some(pipe) = &mut state.pipeline {
        pipe.calibrate(&x_rows, &y_rows, 1.0);
        log::info!("Ridge calibration fitted with {} points", n);
    }

    // Return placeholder mean error (real error would require prediction + comparison)
    Ok(12.0)
}

/// Called by the Svelte frontend after each Sternberg trial completes.
#[tauri::command]
pub fn submit_trial_result(trial: inference::TrialResult) -> Result<(), String> {
    let mut state = SESSION.lock();
    log::info!(
        "Trial {} complete: correct={}, rt={:.3}",
        trial.trial_num, trial.correct, trial.reaction_time
    );
    state.trial_results.push(trial);
    Ok(())
}

/// Called when all 160 trials are done. Runs feature extraction + RF prediction.
#[tauri::command]
pub fn finish_screening(
    session_id: String,
    trials: Vec<inference::TrialResult>,
) -> Result<inference::AdhdReport, String> {
    // Stop the gaze streaming
    {
        let mut state = SESSION.lock();
        state.streaming = false;
        // Drop camera + pipeline to free resources
        state.camera = None;
        state.pipeline = None;
    }

    let fps = 30.0;

    // Use the trials passed from frontend (they have the gaze/pupil data)
    let features = inference::extract_features(&trials, fps);
    log::info!("Extracted {} features", features.len());

    // Load the RF model
    let models_dir = SESSION.lock().models_dir.clone();
    let model_path = models_dir.join("rf_model.json");
    let model = inference::RfModelBundle::load(&model_path)
        .map_err(|e| format!("Failed to load RF model from {:?}: {}", model_path, e))?;

    let mut report = model.predict(&features);

    // Add the 6-dimension attention profile
    report.attention_profile = inference::compute_attention_profile(&features);
    log::info!(
        "Attention profile: sustained={:.0}, stability={:.0}, gaze={:.0}",
        report.attention_profile.sustained_attention,
        report.attention_profile.response_stability,
        report.attention_profile.gaze_control,
    );

    // Add per-block stats for the fatigue timeline
    report.block_stats = inference::compute_block_stats(&trials);
    log::info!("Block stats: {} blocks", report.block_stats.len());

    log::info!(
        "Prediction: {} (prob={:.2}, risk={})",
        report.prediction, report.adhd_probability, report.risk_level
    );

    // Persist to SQLite
    storage::save_report(&session_id, &report)
        .map_err(|e| format!("Failed to save report: {}", e))?;

    Ok(report)
}

/// Launch the Unity game as a subprocess.
#[tauri::command]
pub fn launch_game(game_path: String) -> Result<u32, String> {
    use crate::game::GameManager;
    match GameManager::launch(&game_path) {
        Ok(gm) => {
            log::info!("Game launched");
            Ok(0u32) // TODO: store GameManager, return real pid
        }
        Err(e) => Err(format!("Failed to launch game: {}", e)),
    }
}

#[tauri::command]
pub fn stop_game() -> Result<(), String> {
    // TODO: stop the game subprocess
    Ok(())
}

/// Run inference on pre-collected trial data (for testing/replay).
#[tauri::command]
pub fn run_inference(
    trials: Vec<inference::TrialResult>,
    fps: f64,
) -> Result<inference::AdhdReport, String> {
    let features = inference::extract_features(&trials, fps);
    let models_dir = SESSION.lock().models_dir.clone();
    let model_path = models_dir.join("rf_model.json");
    let model = inference::RfModelBundle::load(&model_path)
        .map_err(|e| format!("Failed to load RF model: {}", e))?;
    let mut report = model.predict(&features);
    report.attention_profile = inference::compute_attention_profile(&features);
    report.block_stats = inference::compute_block_stats(&trials);
    Ok(report)
}

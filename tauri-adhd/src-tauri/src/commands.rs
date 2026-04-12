//! Tauri commands — the IPC interface between Svelte frontend and Rust backend.
//!
//! Each `#[tauri::command]` function is callable from JS via `invoke('name', {...})`.
//! Port of `engine/adhd_engine/api/*.py`.

use crate::{inference, storage};
use std::collections::HashMap;

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
pub fn start_screening(subject_id: String) -> Result<storage::SessionRow, String> {
    let session_id = uuid::Uuid::new_v4().to_string();
    storage::create_session(&session_id, &subject_id, "screening", "sternberg")
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_session(session_id: String) -> Result<Option<storage::SessionRow>, String> {
    // Simple lookup — in the full version this would check active state too
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
/// The frontend sends the 8D feature vector + the screen coordinate.
#[tauri::command]
pub fn submit_calibration_sample(
    features: Vec<f64>,
    screen_x: f64,
    screen_y: f64,
) -> Result<(), String> {
    // TODO: accumulate samples in a calibration state manager,
    // then call RidgeRegressor::fit when all 13 points are done.
    log::info!("calibration sample: features={:?}, screen=({}, {})",
               &features[..2], screen_x, screen_y);
    Ok(())
}

#[tauri::command]
pub fn finish_calibration() -> Result<f64, String> {
    // TODO: fit Ridge regressor, return mean error in pixels
    Ok(15.0) // placeholder
}

/// Called by the Svelte frontend after each Sternberg trial completes.
#[tauri::command]
pub fn submit_trial_result(trial: inference::TrialResult) -> Result<(), String> {
    // TODO: accumulate trial results in a session state manager
    log::info!("trial {} complete, correct={}", trial.trial_num, trial.correct);
    Ok(())
}

/// Called when all 160 trials are done. Runs feature extraction + RF prediction.
#[tauri::command]
pub fn finish_screening(session_id: String, trials: Vec<inference::TrialResult>) -> Result<inference::AdhdReport, String> {
    let fps = 30.0;
    let features = inference::extract_features(&trials, fps);
    log::info!("extracted {} features", features.len());

    // Load the RF model
    let model_path = std::path::Path::new("../models/rf_model.json");
    let model = inference::RfModelBundle::load(model_path)
        .map_err(|e| format!("failed to load RF model: {}", e))?;

    let report = model.predict(&features);
    log::info!("prediction: {} (prob={:.2})", report.prediction, report.adhd_probability);

    // Persist
    storage::save_report(&session_id, &report)
        .map_err(|e| format!("failed to save report: {}", e))?;

    Ok(report)
}

/// Launch the Unity game as a subprocess.
#[tauri::command]
pub fn launch_game(game_path: String) -> Result<u32, String> {
    // TODO: spawn Unity .exe, set up stdin/stdout gaze streaming
    log::info!("launch_game: {}", game_path);
    Err("game launch not yet implemented".into())
}

#[tauri::command]
pub fn stop_game() -> Result<(), String> {
    // TODO: kill the Unity subprocess
    Ok(())
}

/// Run inference on pre-collected trial data (for testing/replay).
#[tauri::command]
pub fn run_inference(trials: Vec<inference::TrialResult>, fps: f64) -> Result<inference::AdhdReport, String> {
    let features = inference::extract_features(&trials, fps);
    let model_path = std::path::Path::new("../models/rf_model.json");
    let model = inference::RfModelBundle::load(model_path)
        .map_err(|e| format!("failed to load RF model: {}", e))?;
    Ok(model.predict(&features))
}

//! ADHD screening system — Rust backend.
//!
//! Faithful port of the Python engine (`engine/adhd_engine/`), preserving:
//!   - Rojas-Líbano et al. 2019 Sternberg paradigm timing
//!   - 27-feature extraction pipeline (with 30 Hz slope unit fix)
//!   - Random Forest prediction logic (300 trees from JSON)
//!   - 30 Hz webcam adaptations (pupil smoothing, baseline frames, etc.)

pub mod gaze_math;
pub mod inference;
pub mod storage;
pub mod commands;
pub mod face_mesh;
pub mod pipeline;
pub mod camera;
pub mod game;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::init();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let app_dir = app
                .path()
                .app_data_dir()
                .expect("failed to resolve app data dir");
            std::fs::create_dir_all(&app_dir).ok();
            let db_path = app_dir.join("adhd.db");
            storage::init_db(&db_path)
                .expect("failed to init database");
            log::info!("DB at {:?}", db_path);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_health,
            commands::check_camera_permission,
            commands::create_subject,
            commands::list_subjects,
            commands::start_screening,
            commands::get_session,
            commands::get_report,
            commands::list_subject_sessions,
            commands::submit_calibration_sample,
            commands::finish_calibration,
            commands::submit_trial_result,
            commands::finish_screening,
            commands::cancel_screening,
            commands::launch_game,
            commands::stop_game,
            commands::run_inference,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

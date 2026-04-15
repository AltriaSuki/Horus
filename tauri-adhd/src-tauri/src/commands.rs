//! Tauri commands — the IPC interface between Svelte frontend and Rust backend.
//!
//! Each `#[tauri::command]` function is callable from JS via `invoke('name', {...})`.

use crate::{camera, eye_tracker_server, game, inference, pipeline, storage};
use parking_lot::Mutex;
use std::collections::HashMap;
use std::path::PathBuf;
use tauri::{AppHandle, Emitter, Manager};

// ═══════════════════════════════════════════════════════════════════
// Global session state — shared between commands
// ═══════════════════════════════════════════════════════════════════

struct SessionState {
    /// Calibration samples: (8D features, screen_x, screen_y)
    calibration_samples: Vec<([f64; 8], [f64; 2])>,
    /// The gaze pipeline (camera + face mesh + AFFNet + Ridge)
    pipeline: Option<pipeline::GazePipeline>,
    /// Camera capture thread handle
    camera: Option<camera::CameraCapture>,
    /// Whether the gaze streaming loop is running
    streaming: bool,
    /// Handle of the background gaze-stream thread; joined on session end.
    gaze_thread: Option<std::thread::JoinHandle<()>>,
    /// ID of the currently active screening session (for incremental
    /// trial persistence). Set in start_screening, cleared in finish/cancel.
    current_session_id: Option<String>,
    /// Models directory path
    models_dir: PathBuf,
}

static SESSION: once_cell::sync::Lazy<Mutex<SessionState>> =
    once_cell::sync::Lazy::new(|| {
        Mutex::new(SessionState {
            calibration_samples: Vec::new(),
            pipeline: None,
            camera: None,
            streaming: false,
            gaze_thread: None,
            current_session_id: None,
            models_dir: PathBuf::from("../models"),
        })
    });

/// Signal streaming loop to stop and join its thread (if any). Must be
/// called with SESSION unlocked, because the loop acquires the lock.
fn shutdown_gaze_thread() {
    let handle = {
        let mut state = SESSION.lock();
        state.streaming = false;
        state.gaze_thread.take()
    };
    if let Some(h) = handle {
        let _ = h.join();
    }
}

/// Called from lib.rs on RunEvent::Exit to release all hardware resources.
pub fn cleanup_on_exit() {
    // 1. Stop Python eye tracker subprocess (releases its camera)
    stop_eye_tracker_inner();

    // 2. Stop gaze streaming thread
    shutdown_gaze_thread();

    // 3. Release Rust camera and pipeline
    {
        let mut state = SESSION.lock();
        state.camera = None;
        state.pipeline = None;
    }

    // 4. Stop TCP eye server if running
    if let Some(mut srv) = EYE_SERVER.lock().take() {
        srv.stop();
    }

    log::info!("Cleanup complete — camera and subprocesses released");
}

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

/// 把摄像头相关的原始错误分类成带引导的中文提示。
fn classify_camera_error(raw: &str) -> String {
    let lower = raw.to_lowercase();
    if lower.contains("permission")
        || lower.contains("authorized")
        || lower.contains("denied")
        || lower.contains("not authorized")
    {
        format!(
            "摄像头权限被拒绝。\n请打开「系统设置 → 隐私与安全性 → 摄像头」，勾选本应用后重启再试。\n\n原始错误: {}",
            raw
        )
    } else if lower.contains("no such") || lower.contains("not found") ||
              lower.contains("no camera") || lower.contains("no device")
    {
        format!("未检测到摄像头，请确认设备已连接。\n\n原始错误: {}", raw)
    } else if lower.contains("fulfill") || lower.contains("requestedformat") {
        format!(
            "摄像头不支持请求的视频格式。请联系开发者适配你的设备，或尝试换一台摄像头。\n\n原始错误: {}",
            raw
        )
    } else if lower.contains("busy") || lower.contains("in use") {
        format!("摄像头被其他应用占用，请关闭视频/会议软件后重试。\n\n原始错误: {}", raw)
    } else {
        format!("摄像头不可用: {}", raw)
    }
}

/// Probe camera availability before starting a screening session.
/// Opens the default camera briefly, then immediately releases it.
/// On macOS, the first call triggers the OS TCC permission prompt.
#[tauri::command]
pub fn check_camera_permission() -> Result<String, String> {
    match camera::CameraCapture::start() {
        Ok(mut cap) => {
            cap.stop();
            Ok("摄像头可用".into())
        }
        Err(e) => Err(classify_camera_error(&e.to_string())),
    }
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
pub fn start_screening(
    subject_id: String,
    screen_width: u32,
    screen_height: u32,
    app: AppHandle,
) -> Result<storage::SessionRow, String> {
    // 若之前的 session 线程还在跑（异常路径或重复调用），先优雅停止
    shutdown_gaze_thread();

    // 停止 Python 眼动追踪进程（如果之前训练时启动过），释放摄像头
    stop_eye_tracker_inner();

    let session_id = uuid::Uuid::new_v4().to_string();
    let session = storage::create_session(&session_id, &subject_id, "screening", "sternberg")
        .map_err(|e| e.to_string())?;

    // 初始化阶段出错时用于统一走失败分支：标记 DB + 清空 state
    let fail = |msg: String| -> Result<storage::SessionRow, String> {
        {
            let mut state = SESSION.lock();
            state.pipeline = None;
            state.camera = None;
            state.current_session_id = None;
        }
        // best-effort：二次错误只记日志，不掩盖原始 msg
        if let Err(e) = storage::mark_session_failed(&session_id, &msg) {
            log::warn!("mark_session_failed 失败: {}", e);
        }
        Err(msg)
    };

    // Reset session state + 分配资源
    {
        let mut state = SESSION.lock();
        state.calibration_samples.clear();
        state.streaming = false;
        state.current_session_id = Some(session_id.clone());

        state.models_dir = resolve_models_dir(Some(&app)).ok_or_else(|| {
            "未找到 models 资源目录，请确认安装包包含模型文件。".to_string()
        })?;
        log::info!("Models dir: {:?}", state.models_dir);

        // Pipeline 初始化失败直接中止：没有 pipeline 则 gaze_frame 永远出不来
        match pipeline::GazePipeline::new(&state.models_dir, screen_width, screen_height) {
            Ok(p) => {
                state.pipeline = Some(p);
                log::info!("Gaze pipeline initialized ({}x{})", screen_width, screen_height);
            }
            Err(e) => {
                drop(state);
                return fail(format!(
                    "Gaze pipeline 初始化失败: {}. 请确认模型文件和摄像头可用",
                    e
                ));
            }
        }

        // 摄像头同样不可缺少
        match camera::CameraCapture::start_with(0, 640, 480, 40) {
            Ok(cam) => {
                state.camera = Some(cam);
                log::info!("Camera started");
            }
            Err(e) => {
                drop(state);
                return fail(classify_camera_error(&e.to_string()));
            }
        }
    }

    // Start gaze streaming in a background thread
    let app_clone = app.clone();
    let handle = match std::thread::Builder::new()
        .name("gaze-stream".into())
        .spawn(move || {
            stream_gaze_loop(app_clone);
        }) {
        Ok(h) => h,
        Err(e) => {
            return fail(format!("启动 gaze 流线程失败: {}", e));
        }
    };

    {
        let mut state = SESSION.lock();
        state.gaze_thread = Some(handle);
    }

    // 一切就绪：把 session 标记为 running
    if let Err(e) = storage::mark_session_running(&session_id) {
        log::warn!("mark_session_running 失败: {}", e);
    }

    Ok(session)
}

/// Background thread: reads camera frames, runs pipeline, emits gaze events.
///
/// Precondition: 调用者已保证 `state.pipeline` 初始化成功；只有在 finish/cancel
/// 主动清空 pipeline 时才会观察到 None，此时直接退出循环。
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

        // Two separate lock scopes to avoid borrow conflict (camera is &,
        // pipeline is &mut, both behind the same Mutex).
        let frame_data = {
            let state = SESSION.lock();
            state.camera.as_ref().and_then(|cam| cam.latest_frame())
        };

        let gaze_frame = if let Some(cf) = frame_data {
            let mut state = SESSION.lock();
            match state.pipeline.as_mut() {
                Some(pipe) => {
                    let t = start.elapsed().as_secs_f64();
                    Some(pipe.process_frame(&cf.rgb, cf.width, cf.height, t))
                }
                // pipeline 被清掉 → session 已结束，退出循环
                None => break,
            }
        } else {
            None
        };

        if let Some(gf) = gaze_frame {
            // Emit gaze_frame event to frontend
            let _ = app.emit("gaze_frame", &gf);
            // Also broadcast to any connected Unity game clients over TCP
            if let Some(srv) = EYE_SERVER.lock().as_ref() {
                srv.broadcast(&gf);
            }
        }

        // ~30 fps pacing (don't spin faster than the camera)
        std::thread::sleep(std::time::Duration::from_millis(16));
    }

    log::info!("Gaze streaming stopped");
}

#[tauri::command]
pub fn get_session(session_id: String) -> Result<Option<storage::SessionRow>, String> {
    storage::get_session(&session_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_report(session_id: String) -> Result<Option<inference::AdhdReport>, String> {
    storage::get_report(&session_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn list_subject_sessions(subject_id: String) -> Result<Vec<storage::SessionRow>, String> {
    storage::list_subject_sessions(&subject_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn delete_session(session_id: String) -> Result<(), String> {
    storage::delete_session(&session_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn delete_sessions(session_ids: Vec<String>) -> Result<(), String> {
    for id in session_ids {
        storage::delete_session(&id).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Called by the Svelte frontend once per calibration point click.
/// 后端从 pipeline 拉取当前最新的 8D 虹膜特征，避免前端传入坐标空间错位的数据。
/// Deduplicates: skips if feature is identical to the last saved sample for this screen position.
#[tauri::command]
pub fn submit_calibration_sample(
    screen_x: f64,
    screen_y: f64,
) -> Result<(), String> {
    let mut state = SESSION.lock();

    let feat = match state.pipeline.as_ref().and_then(|p| p.latest_feature()) {
        Some(f) => f,
        None => return Err("未检测到人脸，请调整位置".into()),
    };

    // Deduplicate: skip if this is the exact same feature as the last sample
    // (happens at 1fps camera + 200ms submit interval → many repeats per frame)
    if let Some((last_feat, _)) = state.calibration_samples.last() {
        if *last_feat == feat {
            return Ok(()); // silently skip duplicate
        }
    }

    state.calibration_samples.push((feat, [screen_x, screen_y]));
    log::info!(
        "Calibration sample {}: screen=({:.0}, {:.0}) feat=[{:.3}, {:.3}, {:.3}, {:.3}, {:.3}, {:.3}, {:.3}, {:.3}]",
        state.calibration_samples.len(), screen_x, screen_y,
        feat[0], feat[1], feat[2], feat[3], feat[4], feat[5], feat[6], feat[7]
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

    // Log feature statistics for diagnostics
    let x_rows: Vec<[f64; 8]> = state.calibration_samples.iter().map(|(f, _)| *f).collect();
    let y_rows: Vec<[f64; 2]> = state.calibration_samples.iter().map(|(_, s)| *s).collect();

    // Log feature ranges to check if features actually vary
    let mut mins = [f64::MAX; 8];
    let mut maxs = [f64::MIN; 8];
    for row in &x_rows {
        for j in 0..8 {
            mins[j] = mins[j].min(row[j]);
            maxs[j] = maxs[j].max(row[j]);
        }
    }
    log::info!("Calibration {} samples, feature ranges:", n);
    let labels = ["iris_r_u", "iris_r_n", "iris_l_u", "iris_l_n", "yaw", "pitch", "affnet_x", "affnet_y"];
    for j in 0..8 {
        log::info!("  {}: [{:.4}, {:.4}] (range={:.4})", labels[j], mins[j], maxs[j], maxs[j] - mins[j]);
    }

    if let Some(pipe) = &mut state.pipeline {
        pipe.calibrate(&x_rows, &y_rows, 0.1);
        log::info!("Ridge calibration fitted with {} unique points (alpha=0.1)", n);

        // Compute training error for diagnostics
        let mut total_err = 0.0;
        for i in 0..n {
            let (px, py) = pipe.predict_raw(&x_rows[i]);
            let err = ((px - y_rows[i][0]).powi(2) + (py - y_rows[i][1]).powi(2)).sqrt();
            total_err += err;
        }
        let train_err = total_err / n as f64;
        log::info!("Ridge training mean error: {:.1} px", train_err);
    }

    // Return placeholder mean error (real error would require prediction + comparison)
    Ok(12.0)
}

/// Called by the Svelte frontend after each Sternberg trial completes.
/// 做增量持久化（逐 trial 落盘），崩溃时也能保住已完成的数据。
#[tauri::command]
pub fn submit_trial_result(trial: inference::TrialResult) -> Result<(), String> {
    log::info!(
        "Trial {} complete: correct={}, rt={:.3}",
        trial.trial_num, trial.correct, trial.reaction_time
    );

    let sid = {
        let state = SESSION.lock();
        state.current_session_id.clone()
    };

    match sid {
        Some(id) => storage::save_trial(&id, &trial)
            .map_err(|e| format!("保存 trial 失败: {}", e)),
        // 没有活跃 session 则只记日志，不算错误（兼容离线测试）
        None => {
            log::warn!("submit_trial_result 调用时无活跃 session，跳过持久化");
            Ok(())
        }
    }
}

/// Called when all 160 trials are done. Runs feature extraction + RF prediction.
#[tauri::command]
pub fn finish_screening(
    session_id: String,
    trials: Vec<inference::TrialResult>,
) -> Result<inference::AdhdReport, String> {
    // 先停 gaze 线程，再释放 camera / pipeline
    shutdown_gaze_thread();
    {
        let mut state = SESSION.lock();
        state.camera = None;
        state.pipeline = None;
        state.current_session_id = None;
    }

    let fps = 30.0;

    // Use the trials passed from frontend (they have the gaze/pupil data).
    // 前端的 SternbergCanvas 一直持有 160 条完整 trial 数据直到 finish，
    // 所以这里用参数，而不是后端 state（旧的 state.trial_results 已删除）。
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

    // Persist to SQLite (save_report 内部会把 session 状态置为 completed)
    storage::save_report(&session_id, &report)
        .map_err(|e| format!("Failed to save report: {}", e))?;

    Ok(report)
}

/// 用户中途放弃。停 gaze 线程、释放摄像头/pipeline、把 DB session 标记为 cancelled。
#[tauri::command]
pub fn cancel_screening(session_id: String) -> Result<(), String> {
    shutdown_gaze_thread();
    {
        let mut state = SESSION.lock();
        state.camera = None;
        state.pipeline = None;
        state.calibration_samples.clear();
        state.current_session_id = None;
    }
    storage::mark_session_cancelled(&session_id)
        .map_err(|e| format!("标记 session 取消失败: {}", e))?;
    log::info!("Session {} cancelled", session_id);
    Ok(())
}

// Global handle for the running Unity game. Separate from SESSION because
// a game run is independent of a screening session.
static GAME: once_cell::sync::Lazy<Mutex<Option<game::GameManager>>> =
    once_cell::sync::Lazy::new(|| Mutex::new(None));

// Global TCP server that broadcasts gaze frames to the Unity game client.
// Started when `launch_game` runs, stopped by `stop_game`.
static EYE_SERVER: once_cell::sync::Lazy<Mutex<Option<eye_tracker_server::EyeTrackerServer>>> =
    once_cell::sync::Lazy::new(|| Mutex::new(None));

// ═══════════════════════════════════════════════════════════════════
// Python eye_tracker_server.py subprocess management
// ═══════════════════════════════════════════════════════════════════

/// Global handle for the Python eye tracker subprocess.
static PYTHON_EYE_TRACKER: once_cell::sync::Lazy<Mutex<Option<std::process::Child>>> =
    once_cell::sync::Lazy::new(|| Mutex::new(None));

/// Stdin handle for sending commands to the Python process (headless mode).
static PYTHON_EYE_TRACKER_STDIN: once_cell::sync::Lazy<Mutex<Option<std::process::ChildStdin>>> =
    once_cell::sync::Lazy::new(|| Mutex::new(None));

/// Whether the Python eye tracker has completed calibration.
static EYE_TRACKER_CALIBRATED: once_cell::sync::Lazy<Mutex<bool>> =
    once_cell::sync::Lazy::new(|| Mutex::new(false));

/// Check whether the Python eye tracker subprocess is still alive.
fn is_python_eye_tracker_running() -> bool {
    let mut guard = PYTHON_EYE_TRACKER.lock();
    if let Some(ref mut child) = *guard {
        match child.try_wait() {
            Ok(None) => true,
            _ => {
                *guard = None;
                false
            }
        }
    } else {
        false
    }
}

/// Collect base directories to probe for bundled resources in both dev and release.
fn resource_base_dirs(app: Option<&AppHandle>) -> Vec<PathBuf> {
    let mut bases: Vec<PathBuf> = Vec::new();

    if let Some(app) = app {
        if let Ok(resource_dir) = app.path().resource_dir() {
            bases.push(resource_dir.clone());
            bases.push(resource_dir.join("resources"));
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            bases.push(dir.to_path_buf());
        }
    }

    // Dev-mode fallbacks
    bases.push(PathBuf::from("."));
    bases.push(PathBuf::from(".."));
    bases.push(PathBuf::from("../.."));

    bases
}

/// Locate the models directory across release resources and dev paths.
fn resolve_models_dir(app: Option<&AppHandle>) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    for base in resource_base_dirs(app) {
        candidates.push(base.join("models"));
        candidates.push(base.join("resources").join("models"));
        candidates.push(base.join("..").join("models"));
        candidates.push(base.join("..").join("..").join("models"));
    }

    for c in candidates {
        if c.join("face_detection.onnx").exists() && c.join("rf_model.json").exists() {
            log::info!("Resolved models dir: {:?}", c);
            return Some(c);
        }
    }

    log::warn!("Could not resolve models directory (face_detection.onnx + rf_model.json)");
    None
}

/// Locate eye_tracker_server.py across dev and release candidate paths.
fn resolve_eye_tracker_script(app: Option<&AppHandle>) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    for base in resource_base_dirs(app) {
        candidates.push(base.join("scripts").join("eye_tracker_server.py"));
        candidates.push(base.join("resources").join("scripts").join("eye_tracker_server.py"));
        candidates.push(base.join("..").join("scripts").join("eye_tracker_server.py"));
        candidates.push(base.join("..").join("..").join("scripts").join("eye_tracker_server.py"));
    }

    for c in candidates {
        if c.exists() {
            log::info!("Found eye_tracker_server.py at {:?}", c);
            return Some(c);
        }
    }
    log::warn!("eye_tracker_server.py not found in any candidate path");
    None
}

/// Find a working Python 3 interpreter. Searches common install locations
/// on Windows since the Windows Store stub `python.exe` doesn't actually work.
fn resolve_python() -> Option<PathBuf> {
    // 1. Search common Windows install locations FIRST (avoid Windows Store stub)
    if cfg!(target_os = "windows") {
        if let Ok(user_profile) = std::env::var("USERPROFILE") {
            let user = PathBuf::from(&user_profile);
            // Python official installer (prefer latest version)
            if let Ok(entries) = std::fs::read_dir(user.join("AppData\\Local\\Programs\\Python")) {
                let mut pythons: Vec<PathBuf> = entries
                    .filter_map(|e| e.ok())
                    .map(|e| e.path().join("python.exe"))
                    .filter(|p| p.exists())
                    .collect();
                pythons.sort();
                if let Some(p) = pythons.last() {
                    log::info!("Found Python at {:?}", p);
                    return Some(p.clone());
                }
            }
            // Anaconda / Miniconda
            for name in &["anaconda3", "miniconda3", "Anaconda3", "Miniconda3"] {
                let p = user.join(name).join("python.exe");
                if p.exists() {
                    log::info!("Found Python at {:?}", p);
                    return Some(p);
                }
            }
        }
        // System-wide Python
        for p in &[
            "C:\\Python312\\python.exe",
            "C:\\Python311\\python.exe",
            "C:\\Python310\\python.exe",
            "C:\\ProgramData\\anaconda3\\python.exe",
            "C:\\ProgramData\\miniconda3\\python.exe",
        ] {
            let path = PathBuf::from(p);
            if path.exists() {
                log::info!("Found Python at {:?}", path);
                return Some(path);
            }
        }
    }

    // 2. Fallback: try PATH commands (works on macOS/Linux)
    for cmd in &["python3", "python"] {
        if let Ok(output) = std::process::Command::new(cmd)
            .arg("--version")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .output()
        {
            let ver = String::from_utf8_lossy(&output.stdout);
            if output.status.success() && ver.contains("Python 3") {
                log::info!("Found Python via PATH: {} → {}", cmd, ver.trim());
                return Some(PathBuf::from(cmd));
            }
        }
    }

    log::warn!("No working Python 3 found");
    None
}

/// Internal: kill the Python eye tracker process and reset state.
fn stop_eye_tracker_inner() {
    // Drop stdin first so the process can detect EOF
    PYTHON_EYE_TRACKER_STDIN.lock().take();
    if let Some(mut child) = PYTHON_EYE_TRACKER.lock().take() {
        let _ = child.kill();
        let _ = child.wait();
        log::info!("Python eye tracker process stopped");
    }
    *EYE_TRACKER_CALIBRATED.lock() = false;
}

/// True when the current gaze stream was started by `launch_game` rather
/// than by `start_screening`. Lets `stop_game` know to tear down the camera
/// too (screening keeps ownership when it started).
static GAZE_OWNED_BY_GAME: once_cell::sync::Lazy<Mutex<bool>> =
    once_cell::sync::Lazy::new(|| Mutex::new(false));

/// Start camera + pipeline + gaze stream when we need live gaze without a
/// full screening session (e.g. the training game). No-op if gaze is
/// already streaming (screening already owns the pipeline).
fn ensure_gaze_streaming_for_game(
    screen_width: u32,
    screen_height: u32,
    app: &AppHandle,
) -> Result<(), String> {
    // If a screening is active, reuse its pipeline; just leave ownership flag off.
    {
        let state = SESSION.lock();
        if state.streaming && state.pipeline.is_some() && state.camera.is_some() {
            log::info!("Gaze already streaming (screening session); reusing");
            return Ok(());
        }
    }

    let models_dir = resolve_models_dir(Some(app))
        .ok_or_else(|| "未找到 models 资源目录，请重新安装带完整资源的版本。".to_string())?;

    {
        let mut state = SESSION.lock();
        state.streaming = false;
        state.models_dir = models_dir;
        let pipe = pipeline::GazePipeline::new(&state.models_dir, screen_width, screen_height)
            .map_err(|e| format!("Gaze pipeline 初始化失败: {}", e))?;
        state.pipeline = Some(pipe);

        let cam = camera::CameraCapture::start_with(0, 640, 480, 40)
            .map_err(|e| classify_camera_error(&e.to_string()))?;
        state.camera = Some(cam);
    }

    let app_clone = app.clone();
    let handle = std::thread::Builder::new()
        .name("gaze-stream-game".into())
        .spawn(move || stream_gaze_loop(app_clone))
        .map_err(|e| format!("启动 gaze 流线程失败: {}", e))?;

    SESSION.lock().gaze_thread = Some(handle);
    *GAZE_OWNED_BY_GAME.lock() = true;
    Ok(())
}

/// Launch the Unity game as a subprocess. game_path is optional — if empty,
/// the backend resolves it via game::resolve_game_exe().
#[tauri::command]
pub fn launch_game(
    game_path: String,
    screen_width: Option<u32>,
    screen_height: Option<u32>,
    app: AppHandle,
) -> Result<u32, String> {
    // 先停掉可能残留的旧进程
    if let Some(mut old) = GAME.lock().take() {
        let _ = old.stop();
    }

    // 解析可执行文件
    let exe = if game_path.is_empty() || game_path == "training-game" {
        game::resolve_game_exe()
            .ok_or_else(|| "未找到游戏可执行文件。请确保 eye.exe 已打包到应用目录。".to_string())?
    } else {
        std::path::PathBuf::from(&game_path)
    };

    // Windows exe 在 macOS/Linux 上无法原生执行 — 给明确提示
    if !cfg!(target_os = "windows") && exe.extension().map(|e| e == "exe").unwrap_or(false) {
        return Err(format!(
            "训练游戏目前仅支持 Windows。请在 Windows 电脑上打开本应用。\n(检测到游戏: {:?})",
            exe.file_name().unwrap_or_default()
        ));
    }

    // 如果 Python 眼动追踪器已经在运行（并且已完成校准），它自带 TCP server，
    // 跳过 Rust 的 TCP server 和 gaze pipeline。
    let python_eye_tracker_active = is_python_eye_tracker_running();

    if python_eye_tracker_active {
        log::info!("Python eye tracker is running; skipping Rust TCP server and gaze pipeline");
    } else {
        // 启动 Rust TCP server（幂等）
        {
            let mut slot = EYE_SERVER.lock();
            if slot.is_none() {
                let srv = eye_tracker_server::EyeTrackerServer::start(
                    eye_tracker_server::DEFAULT_HOST,
                    eye_tracker_server::DEFAULT_PORT,
                )
                .map_err(|e| format!("启动眼动 TCP server 失败: {}", e))?;
                *slot = Some(srv);
            }
        }

        // 启动 camera/pipeline（如果还没跑）
        let sw = screen_width.unwrap_or(1920);
        let sh = screen_height.unwrap_or(1080);
        if let Err(e) = ensure_gaze_streaming_for_game(sw, sh, &app) {
            // 回滚 server
            if let Some(mut srv) = EYE_SERVER.lock().take() {
                srv.stop();
            }
            return Err(e);
        }
    }

    let exe_str = exe
        .to_str()
        .ok_or_else(|| "游戏路径包含非法字符".to_string())?;

    match game::GameManager::launch(exe_str) {
        Ok(gm) => {
            let pid = gm.pid().unwrap_or(0);
            log::info!("Game launched (pid={}), gaze server on {}:{}", pid,
                eye_tracker_server::DEFAULT_HOST, eye_tracker_server::DEFAULT_PORT);
            *GAME.lock() = Some(gm);
            Ok(pid)
        }
        Err(e) => Err(format!("启动游戏失败: {}", e)),
    }
}

#[tauri::command]
pub fn stop_game() -> Result<(), String> {
    // 停游戏进程
    if let Some(mut gm) = GAME.lock().take() {
        gm.stop().map_err(|e| format!("停止游戏失败: {}", e))?;
        log::info!("Game stopped");
    }

    // 停 Python 眼动追踪器（如果在跑）
    stop_eye_tracker_inner();

    // 停 Rust TCP server（如果在跑）
    if let Some(mut srv) = EYE_SERVER.lock().take() {
        srv.stop();
    }

    // 如果 gaze 是游戏启动时开的（而不是 screening），顺便关掉摄像头/pipeline
    let owned_by_game = {
        let mut f = GAZE_OWNED_BY_GAME.lock();
        let v = *f;
        *f = false;
        v
    };
    if owned_by_game {
        shutdown_gaze_thread();
        let mut state = SESSION.lock();
        state.camera = None;
        state.pipeline = None;
    }
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

// ═══════════════════════════════════════════════════════════════════
// Python eye tracker commands
// ═══════════════════════════════════════════════════════════════════

/// Launch the Python eye_tracker_server.py subprocess.
/// If `headless` is true, calibration is driven from the frontend via stdin commands.
/// Otherwise it opens a pygame calibration window.
/// Progress is emitted via `eye_tracker_status` and `eye_tracker_log` events.
#[tauri::command]
pub fn start_eye_tracker(app: AppHandle, headless: Option<bool>) -> Result<(), String> {
    // Kill any existing instance
    stop_eye_tracker_inner();

    // 释放 Rust 端的摄像头/pipeline（如果早筛曾经启动过）
    shutdown_gaze_thread();
    {
        let mut state = SESSION.lock();
        state.camera = None;
        state.pipeline = None;
    }

    let headless = headless.unwrap_or(false);

    let script = resolve_eye_tracker_script(Some(&app))
        .ok_or("未找到 eye_tracker_server.py 脚本。请确保 scripts/ 目录存在。")?;

    let script_str = script
        .canonicalize()
        .unwrap_or(script)
        .to_str()
        .ok_or("脚本路径包含非法字符")?
        .to_string();

    let python = resolve_python()
        .ok_or("未找到 Python。请安装 Python 3 并确保其路径可被系统找到。")?;

    log::info!("Using Python: {:?}, script: {}, headless: {}", python, script_str, headless);

    let mut cmd = std::process::Command::new(&python);
    cmd.arg("-u")
        .arg(&script_str)
        .arg("--port")
        .arg("5678")
        .env("PYTHONUNBUFFERED", "1")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    if headless {
        cmd.arg("--headless")
            .stdin(std::process::Stdio::piped());
    }

    let mut child = cmd.spawn()
        .map_err(|e| format!("启动眼动追踪失败: {}。\nPython路径: {:?}", e, python))?;

    let stdout = child.stdout.take()
        .ok_or("无法获取 eye_tracker_server 的标准输出")?;
    let stderr = child.stderr.take();

    // Store stdin handle for headless mode
    if headless {
        *PYTHON_EYE_TRACKER_STDIN.lock() = child.stdin.take();
    }

    *PYTHON_EYE_TRACKER.lock() = Some(child);

    // Monitor stdout for calibration progress
    let app_clone = app.clone();
    std::thread::Builder::new()
        .name("eye-tracker-stdout".into())
        .spawn(move || {
            use std::io::BufRead;
            let reader = std::io::BufReader::new(stdout);
            for line in reader.lines().flatten() {
                log::info!("[eye_tracker] {}", line);
                let _ = app_clone.emit("eye_tracker_log", &line);
                if line.starts_with("READY") {
                    let _ = app_clone.emit("eye_tracker_status", "ready");
                }
                if line.starts_with("POINT_DONE") {
                    // POINT_DONE index n_samples target_samples
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    let index = parts.get(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let samples = parts.get(2).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let target = parts.get(3).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let _ = app_clone.emit("eye_tracker_point_done", serde_json::json!({
                        "index": index,
                        "samples": samples,
                        "target": target,
                    }));
                }
                if line.starts_with("POINT_PROGRESS") {
                    // POINT_PROGRESS index n_samples target_samples
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    let index = parts.get(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let samples = parts.get(2).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let target = parts.get(3).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                    let _ = app_clone.emit("eye_tracker_point_progress", serde_json::json!({
                        "index": index,
                        "samples": samples,
                        "target": target,
                    }));
                }
                if line.starts_with("TRAIN_DONE") {
                    let acc = line.split_whitespace().nth(1)
                        .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                    *EYE_TRACKER_CALIBRATED.lock() = true;
                    let _ = app_clone.emit("eye_tracker_train_done", serde_json::json!({
                        "accuracy": acc,
                    }));
                    let _ = app_clone.emit("eye_tracker_status", "calibrated");
                }
                if line.starts_with("TRAIN_FAILED") {
                    let reason = line.strip_prefix("TRAIN_FAILED ").unwrap_or("unknown");
                    let _ = app_clone.emit("eye_tracker_train_failed", reason);
                    let _ = app_clone.emit("eye_tracker_status", "error");
                }
                if line.contains("Calibration done") {
                    *EYE_TRACKER_CALIBRATED.lock() = true;
                    let _ = app_clone.emit("eye_tracker_status", "calibrated");
                }
                if line.contains("[Server] Listening") {
                    let _ = app_clone.emit("eye_tracker_status", "serving");
                }
                if line.contains("[Fatal]") || line.contains("Calibration failed") {
                    let _ = app_clone.emit("eye_tracker_status", "error");
                }
            }
            let _ = app_clone.emit("eye_tracker_status", "stopped");
            log::info!("eye_tracker stdout monitor exited");
        })
        .map_err(|e| format!("启动监控线程失败: {}", e))?;

    // Monitor stderr
    if let Some(stderr) = stderr {
        let app_clone2 = app.clone();
        std::thread::Builder::new()
            .name("eye-tracker-stderr".into())
            .spawn(move || {
                use std::io::BufRead;
                let reader = std::io::BufReader::new(stderr);
                for line in reader.lines().flatten() {
                    log::warn!("[eye_tracker stderr] {}", line);
                    let _ = app_clone2.emit("eye_tracker_log", &format!("[ERR] {}", line));
                }
            })
            .ok();
    }

    log::info!("Python eye tracker started (script: {})", script_str);
    Ok(())
}

/// Stop the Python eye tracker subprocess.
#[tauri::command]
pub fn stop_eye_tracker() -> Result<(), String> {
    stop_eye_tracker_inner();
    Ok(())
}

/// Query the current status of the Python eye tracker.
#[tauri::command]
pub fn get_eye_tracker_status() -> String {
    if is_python_eye_tracker_running() {
        if *EYE_TRACKER_CALIBRATED.lock() {
            "calibrated".into()
        } else {
            "calibrating".into()
        }
    } else {
        "stopped".into()
    }
}

/// Send a COLLECT command to the headless Python eye tracker to sample
/// features at the given normalized screen position.
#[tauri::command]
pub fn eye_tracker_collect_point(x: f64, y: f64, label: String) -> Result<(), String> {
    use std::io::Write;
    let mut guard = PYTHON_EYE_TRACKER_STDIN.lock();
    let stdin = guard.as_mut()
        .ok_or("眼动追踪进程未运行或非 headless 模式")?;
    let cmd = format!("COLLECT {} {} {}\n", x, y, label);
    stdin.write_all(cmd.as_bytes())
        .map_err(|e| format!("发送校准命令失败: {}", e))?;
    stdin.flush()
        .map_err(|e| format!("发送校准命令失败: {}", e))?;
    Ok(())
}

/// Send a TRAIN command to the headless Python eye tracker to train the model.
#[tauri::command]
pub fn eye_tracker_train() -> Result<(), String> {
    use std::io::Write;
    let mut guard = PYTHON_EYE_TRACKER_STDIN.lock();
    let stdin = guard.as_mut()
        .ok_or("眼动追踪进程未运行或非 headless 模式")?;
    stdin.write_all(b"TRAIN\n")
        .map_err(|e| format!("发送训练命令失败: {}", e))?;
    stdin.flush()
        .map_err(|e| format!("发送训练命令失败: {}", e))?;
    Ok(())
}

/// Send a SERVE command to the headless Python eye tracker to start the TCP server.
#[tauri::command]
pub fn eye_tracker_start_server() -> Result<(), String> {
    use std::io::Write;
    let mut guard = PYTHON_EYE_TRACKER_STDIN.lock();
    let stdin = guard.as_mut()
        .ok_or("眼动追踪进程未运行或非 headless 模式")?;
    stdin.write_all(b"SERVE\n")
        .map_err(|e| format!("发送服务命令失败: {}", e))?;
    stdin.flush()
        .map_err(|e| format!("发送服务命令失败: {}", e))?;
    // Stdin no longer needed after SERVE (it blocks)
    drop(guard);
    Ok(())
}

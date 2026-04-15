//! Tauri commands — the IPC interface between Svelte frontend and Rust backend.
//!
//! Each `#[tauri::command]` function is callable from JS via `invoke('name', {...})`.

use crate::{camera, game, inference, pipeline, storage};
use parking_lot::Mutex;
use std::collections::HashMap;
use std::path::PathBuf;
use tauri::{AppHandle, Emitter};

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
        match camera::CameraCapture::start_with(0, 640, 480, 30) {
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

/// Called by the Svelte frontend once per calibration point click.
/// 后端从 pipeline 拉取当前最新的 8D 虹膜特征，避免前端传入坐标空间错位的数据。
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

    state.calibration_samples.push((feat, [screen_x, screen_y]));
    log::info!(
        "Calibration sample {}: screen=({:.0}, {:.0}) feat=[{:.3}, {:.3}, ...]",
        state.calibration_samples.len(), screen_x, screen_y, feat[0], feat[1]
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

/// Launch the Unity game as a subprocess. game_path is optional — if empty,
/// the backend resolves it via game::resolve_game_exe().
#[tauri::command]
pub fn launch_game(game_path: String) -> Result<u32, String> {
    // 先停掉可能残留的旧进程
    if let Some(mut old) = GAME.lock().take() {
        let _ = old.stop();
    }

    // 解析可执行文件：前端传空字符串或占位符 → 后端自动搜索
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

    let exe_str = exe
        .to_str()
        .ok_or_else(|| "游戏路径包含非法字符".to_string())?;

    match game::GameManager::launch(exe_str) {
        Ok(gm) => {
            let pid = gm.pid().unwrap_or(0);
            log::info!("Game launched (pid={})", pid);
            *GAME.lock() = Some(gm);
            Ok(pid)
        }
        Err(e) => Err(format!("启动游戏失败: {}", e)),
    }
}

#[tauri::command]
pub fn stop_game() -> Result<(), String> {
    let mut slot = GAME.lock();
    if let Some(mut gm) = slot.take() {
        gm.stop().map_err(|e| format!("停止游戏失败: {}", e))?;
        log::info!("Game stopped");
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

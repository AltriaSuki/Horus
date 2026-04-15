//! SQLite persistence — subjects, sessions, reports, trials, calibrations.
//!
//! Port of `engine/adhd_engine/storage/models.py` + `repo.py`.
//!
//! Schema is versioned via `PRAGMA user_version`:
//!   v1 — original (subjects, sessions, adhd_reports base columns)
//!   v2 — adds attention_profile_json, block_stats_json, prob_std/ci95_low/high
//!        on adhd_reports; adds error_message on sessions; adds trials and
//!        calibrations tables.

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;
use once_cell::sync::OnceCell;

static DB: OnceCell<Mutex<Connection>> = OnceCell::new();

const SCHEMA_VERSION: u32 = 2;

pub fn init_db(path: &Path) -> anyhow::Result<()> {
    let conn = Connection::open(path)?;
    // v1 baseline — kept IF NOT EXISTS so fresh DBs work too.
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS subjects (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            sex TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            kind TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT DEFAULT (datetime('now')),
            ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS adhd_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE REFERENCES sessions(id),
            prediction TEXT,
            adhd_probability REAL,
            risk_level TEXT,
            feature_values_json TEXT,
            feature_importance_json TEXT,
            model_info TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ")?;

    migrate(&conn)?;

    DB.set(Mutex::new(conn)).map_err(|_| anyhow::anyhow!("DB already initialized"))?;
    Ok(())
}

/// Incremental, idempotent schema upgrades. Keyed by `PRAGMA user_version`.
fn migrate(conn: &Connection) -> anyhow::Result<()> {
    let ver: u32 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;

    if ver < 2 {
        // ALTER TABLE ADD COLUMN is idempotent across fresh + existing DBs because
        // v1 baseline above only creates the minimal columns.
        conn.execute_batch("
            ALTER TABLE adhd_reports ADD COLUMN attention_profile_json TEXT;
            ALTER TABLE adhd_reports ADD COLUMN block_stats_json TEXT;
            ALTER TABLE adhd_reports ADD COLUMN prob_std REAL;
            ALTER TABLE adhd_reports ADD COLUMN ci95_low REAL;
            ALTER TABLE adhd_reports ADD COLUMN ci95_high REAL;
            ALTER TABLE sessions ADD COLUMN error_message TEXT;
            CREATE TABLE IF NOT EXISTS trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                trial_num INTEGER NOT NULL,
                block_num INTEGER NOT NULL,
                load INTEGER NOT NULL,
                distractor_type INTEGER NOT NULL,
                is_target INTEGER,
                response TEXT,
                reaction_time_ms REAL,
                correct INTEGER,
                pupil_series_json TEXT,
                gaze_x_series_json TEXT,
                gaze_y_series_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_trials_session ON trials(session_id);
            CREATE TABLE IF NOT EXISTS calibrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                n_points INTEGER NOT NULL,
                mean_error_px REAL,
                validation_error_px REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        ")?;
        conn.pragma_update(None, "user_version", SCHEMA_VERSION)?;
    }

    Ok(())
}

fn db() -> &'static Mutex<Connection> {
    DB.get().expect("DB not initialized — call init_db first")
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Subject {
    pub id: String,
    pub display_name: String,
    pub sex: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SessionRow {
    pub id: String,
    pub subject_id: String,
    pub kind: String,
    pub mode: String,
    pub status: String,
    pub started_at: Option<String>,
    pub ended_at: Option<String>,
}

pub fn create_subject(id: &str, name: &str, sex: Option<&str>) -> anyhow::Result<Subject> {
    let conn = db().lock().unwrap();
    conn.execute(
        "INSERT OR IGNORE INTO subjects (id, display_name, sex) VALUES (?1, ?2, ?3)",
        params![id, name, sex],
    )?;
    Ok(Subject { id: id.to_string(), display_name: name.to_string(), sex: sex.map(String::from) })
}

pub fn list_subjects() -> anyhow::Result<Vec<Subject>> {
    let conn = db().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, display_name, sex FROM subjects ORDER BY created_at DESC")?;
    let rows = stmt.query_map([], |row| {
        Ok(Subject {
            id: row.get(0)?,
            display_name: row.get(1)?,
            sex: row.get(2)?,
        })
    })?.collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub fn create_session(id: &str, subject_id: &str, kind: &str, mode: &str) -> anyhow::Result<SessionRow> {
    let conn = db().lock().unwrap();
    conn.execute(
        "INSERT INTO sessions (id, subject_id, kind, mode, status) VALUES (?1, ?2, ?3, ?4, 'pending')",
        params![id, subject_id, kind, mode],
    )?;
    Ok(SessionRow {
        id: id.to_string(), subject_id: subject_id.to_string(),
        kind: kind.to_string(), mode: mode.to_string(),
        status: "pending".to_string(), started_at: None, ended_at: None,
    })
}

// ─── Session status transitions ─────────────────────────────────────

pub fn mark_session_running(id: &str) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "UPDATE sessions SET status = 'running' WHERE id = ?1",
        params![id],
    )?;
    Ok(())
}

pub fn mark_session_failed(id: &str, error: &str) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "UPDATE sessions SET status = 'failed', ended_at = datetime('now'), error_message = ?2 WHERE id = ?1",
        params![id, error],
    )?;
    Ok(())
}

pub fn mark_session_cancelled(id: &str) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "UPDATE sessions SET status = 'cancelled', ended_at = datetime('now') WHERE id = ?1",
        params![id],
    )?;
    Ok(())
}

pub fn save_report(
    session_id: &str,
    report: &crate::inference::AdhdReport,
) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "INSERT OR REPLACE INTO adhd_reports
         (session_id, prediction, adhd_probability, risk_level,
          feature_values_json, feature_importance_json, model_info,
          attention_profile_json, block_stats_json,
          prob_std, ci95_low, ci95_high)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
        params![
            session_id,
            report.prediction,
            report.adhd_probability,
            report.risk_level,
            serde_json::to_string(&report.feature_values)?,
            serde_json::to_string(&report.feature_importance)?,
            report.model_info,
            serde_json::to_string(&report.attention_profile)?,
            serde_json::to_string(&report.block_stats)?,
            report.adhd_probability_std,
            report.adhd_probability_ci95_low,
            report.adhd_probability_ci95_high,
        ],
    )?;
    // Mark session completed
    conn.execute(
        "UPDATE sessions SET status = 'completed', ended_at = datetime('now') WHERE id = ?1",
        params![session_id],
    )?;
    Ok(())
}

pub fn get_report(session_id: &str) -> anyhow::Result<Option<crate::inference::AdhdReport>> {
    let conn = db().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT prediction, adhd_probability, risk_level,
                feature_values_json, feature_importance_json, model_info,
                attention_profile_json, block_stats_json,
                prob_std, ci95_low, ci95_high
         FROM adhd_reports WHERE session_id = ?1")?;
    let mut rows = stmt.query_map(params![session_id], |row| {
        let fv: String = row.get(3)?;
        let fi: String = row.get(4)?;
        let ap_json: Option<String> = row.get(6)?;
        let bs_json: Option<String> = row.get(7)?;
        let prob_std: Option<f64> = row.get(8)?;
        let ci_low: Option<f64> = row.get(9)?;
        let ci_high: Option<f64> = row.get(10)?;
        // NULL columns (pre-v2 rows) degrade to safe defaults.
        let attention_profile = ap_json
            .as_deref()
            .and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or_default();
        let block_stats = bs_json
            .as_deref()
            .and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or_else(Vec::new);
        Ok(crate::inference::AdhdReport {
            prediction: row.get(0)?,
            adhd_probability: row.get(1)?,
            control_probability: 1.0 - row.get::<_, f64>(1)?,
            risk_level: row.get(2)?,
            feature_values: serde_json::from_str(&fv).unwrap_or_default(),
            feature_importance: serde_json::from_str(&fi).unwrap_or_default(),
            model_info: row.get(5)?,
            attention_profile,
            block_stats,
            adhd_probability_std: prob_std.unwrap_or(0.0),
            adhd_probability_ci95_low: ci_low.unwrap_or(0.0),
            adhd_probability_ci95_high: ci_high.unwrap_or(0.0),
        })
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        _ => Ok(None),
    }
}

pub fn get_session(session_id: &str) -> anyhow::Result<Option<SessionRow>> {
    let conn = db().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, subject_id, kind, mode, status, started_at, ended_at
         FROM sessions WHERE id = ?1")?;
    let mut rows = stmt.query_map(params![session_id], |row| {
        Ok(SessionRow {
            id: row.get(0)?,
            subject_id: row.get(1)?,
            kind: row.get(2)?,
            mode: row.get(3)?,
            status: row.get(4)?,
            started_at: row.get(5)?,
            ended_at: row.get(6)?,
        })
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        Some(Err(e)) => Err(e.into()),
        None => Ok(None),
    }
}

pub fn list_subject_sessions(subject_id: &str) -> anyhow::Result<Vec<SessionRow>> {
    let conn = db().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, subject_id, kind, mode, status, started_at, ended_at
         FROM sessions WHERE subject_id = ?1 ORDER BY started_at DESC")?;
    let rows = stmt.query_map(params![subject_id], |row| {
        Ok(SessionRow {
            id: row.get(0)?,
            subject_id: row.get(1)?,
            kind: row.get(2)?,
            mode: row.get(3)?,
            status: row.get(4)?,
            started_at: row.get(5)?,
            ended_at: row.get(6)?,
        })
    })?.collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

/// Delete one screening session and all report/trial/calibration rows linked to it.
pub fn delete_session(session_id: &str) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    let tx = conn.unchecked_transaction()?;

    tx.execute("DELETE FROM adhd_reports WHERE session_id = ?1", params![session_id])?;
    tx.execute("DELETE FROM trials WHERE session_id = ?1", params![session_id])?;
    tx.execute("DELETE FROM calibrations WHERE session_id = ?1", params![session_id])?;
    tx.execute("DELETE FROM sessions WHERE id = ?1", params![session_id])?;

    tx.commit()?;
    Ok(())
}

// ─── Trial-level persistence ────────────────────────────────────────

/// Persist a single trial's metadata + raw pupil/gaze series (JSON blobs)
/// so later replay/QC tooling can reconstruct the session.
pub fn save_trial(session_id: &str, trial: &crate::inference::TrialResult) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    // reaction_time is seconds (NaN on omission); convert → ms or NULL.
    let rt_ms: Option<f64> = if trial.reaction_time.is_nan() {
        None
    } else {
        Some(trial.reaction_time * 1000.0)
    };
    conn.execute(
        "INSERT INTO trials
         (session_id, trial_num, block_num, load, distractor_type,
          is_target, response, reaction_time_ms, correct,
          pupil_series_json, gaze_x_series_json, gaze_y_series_json)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
        params![
            session_id,
            trial.trial_num,
            trial.block_num,
            trial.load,
            trial.distractor_type,
            // TrialResult has no is_target field on the Rust side yet —
            // store NULL; commands.rs can switch to a richer payload later.
            Option::<i64>::None,
            trial.response,
            rt_ms,
            trial.correct as i32,
            serde_json::to_string(&trial.pupil_series)?,
            serde_json::to_string(&trial.gaze_x_series)?,
            serde_json::to_string(&trial.gaze_y_series)?,
        ],
    )?;
    Ok(())
}

// ─── Calibration QC persistence ─────────────────────────────────────

pub fn save_calibration(
    session_id: &str,
    n_points: u32,
    mean_error_px: Option<f64>,
    validation_error_px: Option<f64>,
) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "INSERT INTO calibrations
         (session_id, n_points, mean_error_px, validation_error_px)
         VALUES (?1, ?2, ?3, ?4)",
        params![session_id, n_points, mean_error_px, validation_error_px],
    )?;
    Ok(())
}

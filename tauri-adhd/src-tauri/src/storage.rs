//! SQLite persistence — subjects, sessions, reports.
//!
//! Port of `engine/adhd_engine/storage/models.py` + `repo.py`.

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;
use once_cell::sync::OnceCell;

static DB: OnceCell<Mutex<Connection>> = OnceCell::new();

pub fn init_db(path: &Path) -> anyhow::Result<()> {
    let conn = Connection::open(path)?;
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
    DB.set(Mutex::new(conn)).map_err(|_| anyhow::anyhow!("DB already initialized"))?;
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

pub fn save_report(
    session_id: &str,
    report: &crate::inference::AdhdReport,
) -> anyhow::Result<()> {
    let conn = db().lock().unwrap();
    conn.execute(
        "INSERT OR REPLACE INTO adhd_reports
         (session_id, prediction, adhd_probability, risk_level,
          feature_values_json, feature_importance_json, model_info)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            session_id,
            report.prediction,
            report.adhd_probability,
            report.risk_level,
            serde_json::to_string(&report.feature_values)?,
            serde_json::to_string(&report.feature_importance)?,
            report.model_info,
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
                feature_values_json, feature_importance_json, model_info
         FROM adhd_reports WHERE session_id = ?1")?;
    let mut rows = stmt.query_map(params![session_id], |row| {
        let fv: String = row.get(3)?;
        let fi: String = row.get(4)?;
        Ok(crate::inference::AdhdReport {
            prediction: row.get(0)?,
            adhd_probability: row.get(1)?,
            control_probability: 1.0 - row.get::<_, f64>(1)?,
            risk_level: row.get(2)?,
            feature_values: serde_json::from_str(&fv).unwrap_or_default(),
            feature_importance: serde_json::from_str(&fi).unwrap_or_default(),
            model_info: row.get(5)?,
            attention_profile: crate::inference::AttentionProfile::default(),
            block_stats: Vec::new(),
        })
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        _ => Ok(None),
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

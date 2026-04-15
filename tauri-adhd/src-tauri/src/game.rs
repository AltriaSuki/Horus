//! Unity game subprocess management.
//!
//! Launches the Unity game as a child process and streams gaze data
//! to it via JSON lines written to stdin.

use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

use anyhow::{Context, Result};

use crate::gaze_math::GazeFrame;

/// Locate the Unity game executable across dev and release environments.
/// Returns the first path that exists, or None if the game is not bundled.
pub fn resolve_game_exe() -> Option<PathBuf> {
    let exe_name = if cfg!(target_os = "windows") { "eye.exe" } else { "eye.exe" };

    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    let candidates = [
        // Bundled alongside the app binary (release)
        exe_dir.join("games").join("eyetrack").join(exe_name),
        exe_dir.join("..").join("Resources").join("games").join("eyetrack").join(exe_name),
        // Relative to src-tauri (dev mode)
        PathBuf::from("../games/eyetrack").join(exe_name),
        PathBuf::from("../../eyetrack package").join(exe_name),
        // Absolute fallback for the current workstation (dev)
        PathBuf::from("/Users/feilun/coding/login/eyetrack package").join(exe_name),
    ];

    for c in &candidates {
        if c.exists() {
            log::info!("Found game exe at {:?}", c);
            return Some(c.clone());
        }
    }
    log::warn!("Game exe '{}' not found in any candidate path", exe_name);
    None
}

// ═══════════════════════════════════════════════════════════════════
// GameManager
// ═══════════════════════════════════════════════════════════════════

pub struct GameManager {
    child: Option<Child>,
}

impl GameManager {
    /// Launch the Unity game executable.
    ///
    /// The game receives gaze data via JSON lines on stdin.
    /// Its stdout and stderr are inherited (visible in the terminal).
    pub fn launch(exe_path: &str) -> Result<Self> {
        log::info!("Launching game: {}", exe_path);

        let child = Command::new(exe_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .with_context(|| format!("Failed to launch game at '{}'", exe_path))?;

        log::info!("Game launched with PID {}", child.id());

        Ok(Self {
            child: Some(child),
        })
    }

    /// Send a gaze frame to the running game as a JSON line on stdin.
    ///
    /// Each line is a complete JSON object followed by `\n`:
    /// ```json
    /// {"t":1.23,"x":960.0,"y":540.0,"pupil":0.35,"valid":true,"fps":30}
    /// ```
    pub fn send_gaze(&mut self, frame: &GazeFrame) -> Result<()> {
        let child = self
            .child
            .as_mut()
            .context("Game process is not running")?;

        let stdin = child
            .stdin
            .as_mut()
            .context("Game process stdin not available")?;

        let json = serde_json::to_string(frame).context("Serializing GazeFrame")?;
        writeln!(stdin, "{}", json).context("Writing to game stdin")?;
        stdin.flush().context("Flushing game stdin")?;

        Ok(())
    }

    /// Check if the game process is still running.
    pub fn is_running(&mut self) -> bool {
        match &mut self.child {
            Some(child) => match child.try_wait() {
                Ok(None) => true,   // still running
                Ok(Some(_)) => {
                    log::info!("Game process exited");
                    false
                }
                Err(e) => {
                    log::warn!("Error checking game status: {}", e);
                    false
                }
            },
            None => false,
        }
    }

    /// Stop the game process (sends kill signal).
    pub fn stop(&mut self) -> Result<()> {
        if let Some(mut child) = self.child.take() {
            log::info!("Stopping game process (PID {})", child.id());

            // Try graceful shutdown first by closing stdin
            drop(child.stdin.take());

            // Give it a moment, then kill if still running
            match child.try_wait()? {
                Some(status) => {
                    log::info!("Game already exited with status: {}", status);
                }
                None => {
                    log::info!("Game still running, sending kill signal");
                    child.kill().context("Killing game process")?;
                    let status = child.wait().context("Waiting for game process to exit")?;
                    log::info!("Game exited with status: {}", status);
                }
            }
        }
        Ok(())
    }

    /// Get the PID of the running game, if any.
    pub fn pid(&self) -> Option<u32> {
        self.child.as_ref().map(|c| c.id())
    }
}

impl Drop for GameManager {
    fn drop(&mut self) {
        if let Err(e) = self.stop() {
            log::warn!("Error stopping game on drop: {}", e);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_launch_nonexistent() {
        let result = GameManager::launch("/nonexistent/game.exe");
        assert!(result.is_err());
    }

    #[test]
    #[cfg(unix)]
    fn test_launch_and_send() {
        // Use `cat` as a simple stdin-reading process on Unix
        let mut gm = GameManager::launch("cat").unwrap();
        assert!(gm.is_running());

        let frame = GazeFrame {
            t: 1.0,
            x: 100.0,
            y: 200.0,
            pupil: 0.3,
            valid: true,
            fps: 30,
        };
        gm.send_gaze(&frame).unwrap();
        gm.stop().unwrap();
    }
}

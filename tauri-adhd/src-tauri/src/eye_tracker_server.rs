//! TCP server that broadcasts live gaze frames to subscribed clients.
//!
//! Protocol: newline-delimited JSON. Each message is one object per line:
//! ```json
//! {"x": 960.0, "y": 540.0, "ts": 1234.567, "valid": true}
//! ```
//!
//! The Unity game (EyeTrackerClient) connects as a TCP client to
//! 127.0.0.1:5678 and uses `StreamReader.ReadLine()` + `ExtractDouble()`
//! to parse `x` and `y` out of each line.

use std::io::Write;
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use parking_lot::Mutex;

use crate::gaze_math::GazeFrame;

pub const DEFAULT_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 5678;

pub struct EyeTrackerServer {
    clients: Arc<Mutex<Vec<TcpStream>>>,
    stop_flag: Arc<Mutex<bool>>,
    accept_handle: Option<thread::JoinHandle<()>>,
    host: String,
    port: u16,
}

impl EyeTrackerServer {
    /// Bind to `host:port` and start accepting client connections in a
    /// background thread. Returns immediately after the listener binds.
    pub fn start(host: &str, port: u16) -> Result<Self> {
        let addr = format!("{}:{}", host, port);
        let listener = TcpListener::bind(&addr)
            .with_context(|| format!("Binding eye-tracker server to {}", addr))?;
        listener
            .set_nonblocking(true)
            .context("set_nonblocking on TcpListener")?;

        let clients: Arc<Mutex<Vec<TcpStream>>> = Arc::new(Mutex::new(Vec::new()));
        let stop_flag = Arc::new(Mutex::new(false));

        let clients_c = Arc::clone(&clients);
        let stop_c = Arc::clone(&stop_flag);

        let accept_handle = thread::Builder::new()
            .name("eye-tracker-accept".into())
            .spawn(move || {
                log::info!("Eye-tracker server listening on {}", addr);
                loop {
                    if *stop_c.lock() {
                        break;
                    }
                    match listener.accept() {
                        Ok((stream, peer)) => {
                            log::info!("Game client connected from {}", peer);
                            // Game uses NoDelay, so disable Nagle on our side too.
                            let _ = stream.set_nodelay(true);
                            // Writes must not block if the game is slow to read;
                            // short timeout lets us drop slow/dead peers quickly.
                            let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
                            clients_c.lock().push(stream);
                        }
                        Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(100));
                        }
                        Err(e) => {
                            log::warn!("accept error: {}", e);
                            thread::sleep(Duration::from_millis(200));
                        }
                    }
                }
                log::info!("Eye-tracker accept loop exited");
            })
            .context("spawning accept thread")?;

        Ok(Self {
            clients,
            stop_flag,
            accept_handle: Some(accept_handle),
            host: host.to_string(),
            port,
        })
    }

    /// Serialise `frame` to one JSON line and write it to every connected
    /// client. Dead / unreachable clients are removed.
    pub fn broadcast(&self, frame: &GazeFrame) {
        // Custom compact format; matches what ExtractDouble/ExtractNumber in
        // the Unity client expect (they search for `"field":` substrings).
        let line = format!(
            "{{\"x\":{:.2},\"y\":{:.2},\"ts\":{:.3},\"pupil\":{:.4},\"valid\":{}}}\n",
            frame.x, frame.y, frame.t, frame.pupil, frame.valid
        );
        let bytes = line.as_bytes();

        let mut guard = self.clients.lock();
        guard.retain_mut(|stream| match stream.write_all(bytes) {
            Ok(()) => true,
            Err(e) => {
                log::debug!("Dropping disconnected game client: {}", e);
                false
            }
        });
    }

    pub fn client_count(&self) -> usize {
        self.clients.lock().len()
    }

    pub fn address(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }

    /// Signal the accept thread to stop and close all client connections.
    pub fn stop(&mut self) {
        *self.stop_flag.lock() = true;
        if let Some(h) = self.accept_handle.take() {
            let _ = h.join();
        }
        let mut clients = self.clients.lock();
        for c in clients.iter_mut() {
            let _ = c.shutdown(std::net::Shutdown::Both);
        }
        clients.clear();
        log::info!("Eye-tracker server stopped ({})", self.address());
    }
}

impl Drop for EyeTrackerServer {
    fn drop(&mut self) {
        self.stop();
    }
}

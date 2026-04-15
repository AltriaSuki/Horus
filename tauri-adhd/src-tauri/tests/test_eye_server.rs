use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::thread;
use std::time::Duration;

use tauri_adhd_lib::eye_tracker_server::{EyeTrackerServer, DEFAULT_HOST};
use tauri_adhd_lib::gaze_math::GazeFrame;

#[test]
fn test_server_broadcasts_to_client() {
    // Use a non-standard port to avoid conflict with a real game instance
    let port = 57895u16;
    let srv = EyeTrackerServer::start(DEFAULT_HOST, port).expect("bind server");

    // Let the accept thread enter its loop
    thread::sleep(Duration::from_millis(200));

    let stream = TcpStream::connect((DEFAULT_HOST, port)).expect("client connect");
    // Give server time to register the new client
    thread::sleep(Duration::from_millis(300));
    assert_eq!(srv.client_count(), 1);

    srv.broadcast(&GazeFrame {
        t: 1.5,
        x: 960.25,
        y: 540.75,
        pupil: 0.1234,
        valid: true,
        fps: 30,
    });

    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).expect("read line");
    println!("client received: {}", line.trim());

    assert!(line.contains("\"x\":960.25"));
    assert!(line.contains("\"y\":540.75"));
    assert!(line.contains("\"valid\":true"));
    assert!(line.ends_with('\n'));
}

# WebSocket Protocol — ADHD Engine

The engine exposes two WebSocket endpoints. Both Flutter front-end and Unity
training game subscribe to the same `/gaze` channel; the front-end also
subscribes to `/events` for task progress.

Default base URL: `ws://127.0.0.1:8765`

There is **no authentication** in Phase 1 — the engine binds to localhost
only. Phase 5 (cloud) will add JWT.

## `WS /gaze`

Subscribe to receive a stream of gaze samples, ~30 messages/sec while a
session is active. The server sends; the client should keep the connection
open and need not send anything.

Each message is a JSON object:

```json
{
  "type": "gaze",
  "session_id": "8f3a2b9c-1d4e-...",
  "t": 12.345,
  "x": 960.0,
  "y": 540.0,
  "pupil": 0.142,
  "valid": true,
  "fps": 30
}
```

| Field        | Type      | Meaning                                                |
|--------------|-----------|--------------------------------------------------------|
| `type`       | string    | Always `"gaze"`                                        |
| `session_id` | string    | UUID of the session producing the frame                |
| `t`          | number    | Monotonic seconds since the worker subprocess started  |
| `x`          | number    | Screen pixel X (`NaN` if invalid; serialised as `null`)|
| `y`          | number    | Screen pixel Y (same)                                  |
| `pupil`      | number    | 5-frame smoothed pupil proxy (relative units)          |
| `valid`      | boolean   | `false` for blinks / detection failures                |
| `fps`        | integer   | Camera capture fps                                     |

When no session is running, no messages are sent (the channel stays open).

### Unity client snippet

```csharp
using NativeWebSocket;
using System.Text.Json;

WebSocket ws = new WebSocket("ws://127.0.0.1:8765/gaze");

ws.OnMessage += (bytes) => {
    var json = System.Text.Encoding.UTF8.GetString(bytes);
    var frame = JsonSerializer.Deserialize<GazeFrame>(json);
    if (frame.valid) {
        // Map (x, y) to a virtual direction key — see plan §1.4 (game integration)
        UpdateGameDirection(frame.x, frame.y);
    }
};

await ws.Connect();
```

## `WS /events`

Subscribe to receive high-level task events. Lower volume than gaze
(~1 message every few seconds). Messages share a common envelope:

```json
{ "type": "<event_name>", "session_id": "8f3a2b9c-...", ... }
```

The set of event types and their payloads:

### Calibration phase

```json
{"type": "calibration_start", "session_id": "..."}
{"type": "calibration_done",  "session_id": "..."}
```

### Sternberg task phase

```json
{"type": "task_start",  "session_id": "...", "total_trials": 160, "n_blocks": 8}
{"type": "block_start", "session_id": "...", "block": 1}
{"type": "trial_start", "session_id": "...", "trial_num": 1, "block": 1, "load": 1}
{"type": "trial_end",   "session_id": "...", "trial_num": 1, "correct": 1, "rt": 0.73}
{"type": "block_end",   "session_id": "...", "block": 1}
{"type": "task_done",   "session_id": "...", "total_trials": 160}
```

### Lifecycle

```json
{"type": "session_running", "session_id": "...", "pid": 12345}
{"type": "report_ready",    "session_id": "..."}
```

### Errors

```json
{
  "type": "error",
  "session_id": "...",
  "code": "camera_denied" | "init_failed" | "run_failed" | "import_failed" | "no_report",
  "message": "human-readable error text"
}
```

When you receive `report_ready`, fetch the full prediction with
`GET /sessions/{session_id}/report`.

## REST companion endpoints

The WebSocket channels are read-only. To control the engine you use REST:

| Method | Path                                | Purpose                              |
|--------|-------------------------------------|--------------------------------------|
| POST   | `/subjects`                         | Create a participant record          |
| GET    | `/subjects` / `/subjects/{id}`      | List / read                          |
| POST   | `/sessions`                         | Spawn a worker subprocess            |
| GET    | `/sessions/{id}`                    | Status (pending/running/completed/…) |
| POST   | `/sessions/{id}/cancel`             | Terminate the worker                 |
| GET    | `/sessions/{id}/report`             | Fetch the final RF prediction        |
| POST   | `/games/runs`                       | Begin a game run (placeholder)       |
| PATCH  | `/games/runs/{id}`                  | Finish a game run with score         |

## Lifecycle (typical)

```
Front-end                      Engine                       Worker
   │                              │                            │
   │ POST /subjects               │                            │
   │─────────────────────────────►│                            │
   │ POST /sessions {kind:        │                            │
   │       'screening',           │                            │
   │       mode:'sternberg'}      │                            │
   │─────────────────────────────►│   spawn subprocess         │
   │                              │───────────────────────────►│
   │                              │                            │ pygame.init
   │                              │                            │ open camera
   │                              │ ◄─── worker_ready ─────────│
   │ ◄── 200 SessionOut           │                            │
   │                              │                            │
   │ WS /gaze (subscribe)         │                            │
   │═════════════════════════════►│                            │
   │ WS /events (subscribe)       │                            │
   │═════════════════════════════►│                            │
   │                              │                            │ 13-pt calibration
   │                              │ ◄─── calibration_start ────│
   │                              │ ◄─── gaze frames @30Hz ────│
   │ ◄══ events / gaze ═══════════│                            │
   │                              │ ◄─── calibration_done ─────│
   │                              │                            │ Sternberg 160 trials
   │                              │ ◄─── trial_start/end... ───│
   │                              │ ◄─── task_done ────────────│
   │                              │ ◄─── report ───────────────│
   │                              │  save to SQLite            │
   │ ◄══ report_ready ════════════│                            │
   │                              │                            │ exit
   │ GET /sessions/{id}/report    │                            │
   │─────────────────────────────►│                            │
   │ ◄── 200 prediction JSON      │                            │
```

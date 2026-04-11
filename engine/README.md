# adhd-engine

FastAPI + WebSocket service that wraps the ADHD early-screening pipeline:

- Gaze tracking (MediaPipe + AFFNet, with graceful fallback to geometry-only)
- 13-point click calibration
- Sternberg visuospatial working memory paradigm (Rojas-Líbano et al. 2019)
- 27-feature extraction + Random Forest prediction
- SQLite persistence (subjects, sessions, trials, reports)
- Live gaze + event streaming for the Flutter front-end and Unity game

The trained model weights live in `../model/` and are NOT modified —
this package treats `model/` as vendored read-only assets.

## Installation

The repo's existing `.venv` already has the runtime deps. Install dev deps:

```bash
uv pip install -p .venv -e engine/[dev]
```

## Running the engine

```bash
PYTHONPATH=engine .venv/bin/python -m adhd_engine.server
# Listens on http://127.0.0.1:8765
```

Configure with environment variables:

| Variable               | Default                                          | Purpose                          |
|------------------------|--------------------------------------------------|----------------------------------|
| `ADHD_ENGINE_HOST`     | `127.0.0.1`                                      | Bind address                     |
| `ADHD_ENGINE_PORT`     | `8765`                                           | Bind port                        |
| `ADHD_APP_DATA_DIR`    | `~/Library/Application Support/adhd_app` (macOS) | Per-user data directory          |

## Tests

```bash
PYTHONPATH=engine .venv/bin/python -m pytest engine/tests/ -v
```

10 tests across 3 files:

- `test_inference_unit.py` — verifies the **`pupil_slope` unit fix** (the
  inference output is sample-rate-invariant after multiplying by `fps/1000`).
- `test_ipc_bridge.py` — 1000 gaze frames cross the parent ↔ child queue with
  no loss; both call shapes (`(t,x,y,pupil,valid)` and `(event_dict)`) work.
- `test_end_to_end.py` — drives the full FastAPI app with a fake worker
  subprocess that simulates the calibration → trials → report lifecycle.

## API surface

See `docs/ws_protocol.md` for the WebSocket message reference (used by both
Flutter front-end and Unity game).

REST routes:

```
POST /subjects                     create participant
GET  /subjects                     list participants
GET  /subjects/{id}                detail
POST /sessions                     spawn worker subprocess
GET  /sessions/{id}                status
POST /sessions/{id}/cancel         terminate
GET  /sessions/{id}/report         RF prediction JSON
POST /games/runs                   begin a game run (placeholder)
PATCH /games/runs/{id}             finish a game run (placeholder)

WS   /gaze                         live gaze frame stream (~30 Hz)
WS   /events                       task events (calibration / trial / done)
```

## Architecture

```
┌──────────────────────────────────────────────────┐
│ FastAPI parent process                            │
│  - REST routes, SQLite, WebSocket subscribers      │
│  - One IpcChannel per active session               │
│  - Drain thread bridges mp.Queue → asyncio handler │
└──────┬─────────────────────────────────────────────┘
       │ multiprocessing.Queue
       ▼
┌──────────────────────────────────────────────────┐
│ Worker subprocess (spawned per session)            │
│  - pygame fullscreen calibration + Sternberg       │
│  - MediaPipe + AFFNet inside GazeSystem            │
│  - Pushes gaze frames + trial events back upstream │
│  - Sends final RF report when done                 │
└──────────────────────────────────────────────────┘
```

The worker is a subprocess (not a thread) because:

1. macOS forces UI to live on the main thread of its process.
2. pygame fullscreen + asyncio in the same event loop is fragile.
3. A pygame crash should not bring down the API service.

## Known limitations (Phase 1)

These are documented in the plan file
(`~/.claude/plans/scalable-sprouting-rain.md`) under "Known Risks":

1. **AFFNet class definition is not vendored.** The trained
   `model/eye_tracker/model_pth/affnet.pth.tar` is just a state_dict; it
   needs the AFFNet class from the GazeTrack training repo. Without that,
   `ModelAdapter` falls back to geometry-only mode (6D iris+head pose, no
   2D model gaze). The Ridge calibration regressor still works in this mode
   but tracking accuracy is reduced. Drop the GazeTrack repo at
   `<repo>/itraker/GazeTrack/` to enable AFFNet.

2. **Real Sternberg session takes ~25 minutes** and needs a webcam + a human.
   The end-to-end test uses a mocked worker that simulates the lifecycle in
   <1 sec. To do a true smoke test, run `python -m adhd_engine.worker.runner
   --session-id manual --subject ME --mode sternberg` directly.

3. **gaze_frames table is not yet populated by the worker** — only the
   IPC stream uses the per-frame data. Phase 2 will add bulk insertion.

4. **Image library content imbalance** — `model/eye_tracker/stimuli/emotional/`
   is mostly positive-valence (cats, laughs, pandas). Original Rojas-Líbano
   paradigm used IAPS with mixed valence. This is a data problem, not a
   code problem.

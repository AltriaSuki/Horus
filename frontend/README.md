# adhd_shell — Flutter front-end

Cross-platform shell that talks to the `engine/` FastAPI service.

## Targets

| Platform | Status (Phase 2)              |
|----------|-------------------------------|
| macOS    | Primary target — verified      |
| Windows  | Should work (no Mac-only code) |
| Linux    | Should work                    |
| Android  | Untested — Phase 4 deliverable |
| Web      | Untested — Phase 4 deliverable |

The same code compiles for every target via Flutter — no per-platform forks.

## Prerequisites

- Flutter SDK 3.22+ (install via `brew install --cask flutter`)
- The engine running on `http://127.0.0.1:8765` (see `engine/README.md`)

## Quick start

```bash
# Terminal 1 — start the engine
cd /Users/feilun/coding/login
PYTHONPATH=engine .venv/bin/python -m adhd_engine.server

# Terminal 2 — launch the Flutter app
cd /Users/feilun/coding/login/frontend
flutter pub get
flutter run -d macos
```

## Pages

| Tab        | File                                                | Purpose                                                                  |
|------------|-----------------------------------------------------|--------------------------------------------------------------------------|
| 被试        | `lib/pages/subjects/subjects_list_page.dart`        | Create + list participants                                               |
| 早筛        | `lib/pages/screening/start_page.dart`               | Pick a subject, launch a Sternberg session, watch progress, see report   |
| 训练        | `lib/pages/training/training_page.dart`             | Unity placeholder — registers a `kind=game` session, shows live gaze     |
| 报告        | `lib/pages/reports/history_page.dart`               | Past sessions per subject, jump into any saved report                    |

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ Flutter app                                           │
│   main.dart → app.dart → home_page.dart (4 tabs)      │
│   ├── lib/services/engine_client.dart                 │
│   │     REST methods + Stream<GazeFrame> + Stream<Event>
│   ├── lib/models/{subject,session,report,gaze_frame, │
│   │                task_event}.dart                   │
│   └── lib/pages/{subjects,screening,training,reports} │
└────────────┬─────────────────────────────────────────┘
             │ HTTP REST + WebSocket
             ▼
       http://127.0.0.1:8765 (engine)
```

## Notes

- The engine accepts only one screening session at a time. If a session is
  already running, the second `POST /sessions` returns 409.
- The Sternberg task runs in a **subprocess** (full-screen pygame); the
  Flutter window stays visible and updates progress via `WS /events`.
- The Unity training game is a Phase 2/3 deliverable — for now the training
  tab just registers a session and verifies the gaze stream works.
- The gaze visualisation in the training tab assumes a 1920×1080 calibration
  screen; remap if your display differs.

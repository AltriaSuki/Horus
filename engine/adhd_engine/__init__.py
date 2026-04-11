"""ADHD early-screening engine.

Top-level package. Submodules:
- adhd_engine.server     : FastAPI app entrypoint
- adhd_engine.config     : paths, constants
- adhd_engine.ipc        : parent <-> worker process bridge
- adhd_engine.worker     : pygame fullscreen session worker (calibration + Sternberg + inference)
- adhd_engine.storage    : SQLite ORM
- adhd_engine.api        : REST + WebSocket route modules
"""

__version__ = "0.1.0"

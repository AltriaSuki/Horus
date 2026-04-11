"""One-click launcher for the ADHD screening + training system.

Cross-platform — works on Windows / macOS / Linux without shell-specific
syntax. Run as either:

    python start.py                    # engine + frontend (if built)
    python start.py --engine-only      # only the FastAPI engine
    python start.py --no-browser       # don't auto-open the browser
    python start.py --build-frontend   # rebuild Flutter web first

The wrapper scripts ``start.bat`` (Windows) and ``start.sh`` (macOS/Linux)
just call this with the right venv interpreter so the user can double-click
them.

Architecture: this script is the parent process. It spawns:

    1. The FastAPI engine     (`python -m adhd_engine.server`)
    2. A static file server   (if frontend/build/web exists, on port 8080)
    3. (optionally) opens     http://127.0.0.1:8080 in the user's browser

On Ctrl+C / SIGTERM both children are cleanly terminated.
"""

from __future__ import annotations

import argparse
import http.server
import os
import platform
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Repository layout — everything resolves from the script's own directory
# so cwd doesn't matter.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
ENGINE_DIR = REPO_ROOT / "engine"
ENGINE_PACKAGE = ENGINE_DIR / "adhd_engine"
FRONTEND_BUILD_WEB = REPO_ROOT / "frontend" / "build" / "web"
VENV_DIR = REPO_ROOT / ".venv"

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8765
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080

IS_WIN = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Pretty printing — minimal, no external deps
# ---------------------------------------------------------------------------


class _Color:
    BOLD = "" if IS_WIN else "\033[1m"
    DIM = "" if IS_WIN else "\033[2m"
    GREEN = "" if IS_WIN else "\033[32m"
    YELLOW = "" if IS_WIN else "\033[33m"
    RED = "" if IS_WIN else "\033[31m"
    BLUE = "" if IS_WIN else "\033[34m"
    RESET = "" if IS_WIN else "\033[0m"


def _info(msg: str) -> None:
    print(f"{_Color.BLUE}[start]{_Color.RESET} {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"{_Color.GREEN}[start]{_Color.RESET} {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"{_Color.YELLOW}[start]{_Color.RESET} {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"{_Color.RED}[start]{_Color.RESET} {msg}", flush=True)


# ---------------------------------------------------------------------------
# Environment discovery
# ---------------------------------------------------------------------------


def find_venv_python() -> Path | None:
    """Locate the venv interpreter, returning ``None`` if no venv exists."""
    if not VENV_DIR.is_dir():
        return None
    if IS_WIN:
        candidates = [
            VENV_DIR / "Scripts" / "python.exe",
            VENV_DIR / "Scripts" / "python3.exe",
        ]
    else:
        candidates = [
            VENV_DIR / "bin" / "python",
            VENV_DIR / "bin" / "python3",
        ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def check_environment() -> Path:
    """Sanity-check the workspace and return the Python interpreter to use."""
    if not ENGINE_PACKAGE.is_dir():
        _err(f"engine/ package not found at {ENGINE_PACKAGE}")
        _err("Are you running this script from the right directory?")
        sys.exit(1)

    venv_python = find_venv_python()
    if venv_python is None:
        _warn(f"no .venv found at {VENV_DIR}")
        _warn(f"falling back to system Python: {sys.executable}")
        _warn("if you hit ImportError, create the venv with:")
        if IS_WIN:
            _warn("    py -3.12 -m venv .venv")
            _warn("    .venv\\Scripts\\pip install -r requirements.txt")
        else:
            _warn("    python3.12 -m venv .venv")
            _warn("    .venv/bin/pip install -r requirements.txt")
        return Path(sys.executable)
    return venv_python


# ---------------------------------------------------------------------------
# Engine subprocess
# ---------------------------------------------------------------------------


def spawn_engine(python_exe: Path) -> subprocess.Popen:
    """Start the FastAPI engine in a child process."""
    env = os.environ.copy()
    # PYTHONPATH so `import adhd_engine` resolves
    existing_pp = env.get("PYTHONPATH", "")
    sep = ";" if IS_WIN else ":"
    env["PYTHONPATH"] = (
        f"{ENGINE_DIR}{sep}{existing_pp}" if existing_pp else str(ENGINE_DIR)
    )
    env["ADHD_ENGINE_HOST"] = ENGINE_HOST
    env["ADHD_ENGINE_PORT"] = str(ENGINE_PORT)
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [str(python_exe), "-m", "adhd_engine.server"]
    _info(f"launching engine: {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )


def stream_subprocess(proc: subprocess.Popen, label: str) -> threading.Thread:
    """Pipe a child's stdout into our own with a prefix label."""
    def _pump():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"{_Color.DIM}[{label}]{_Color.RESET} {line.rstrip()}",
                      flush=True)
        except Exception:
            pass

    t = threading.Thread(target=_pump, name=f"pump-{label}", daemon=True)
    t.start()
    return t


def wait_for_engine(timeout: float = 30.0) -> bool:
    """Block until the engine answers GET / or until ``timeout``."""
    url = f"http://{ENGINE_HOST}:{ENGINE_PORT}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return True
        except (URLError, ConnectionResetError, OSError):
            pass
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# Frontend (Flutter web) subprocess
# ---------------------------------------------------------------------------


def find_flutter() -> str | None:
    """Locate the flutter binary on PATH or in /opt/homebrew/share/flutter."""
    candidates: list[str] = []
    if IS_WIN:
        candidates = ["flutter.bat", "flutter"]
    else:
        candidates = [
            "flutter",
            "/opt/homebrew/share/flutter/bin/flutter",
            "/usr/local/share/flutter/bin/flutter",
        ]
    for c in candidates:
        try:
            r = subprocess.run(
                [c, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            if r.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def build_frontend_if_needed(force: bool = False) -> bool:
    """Build the Flutter web bundle if it's missing or ``force=True``."""
    index = FRONTEND_BUILD_WEB / "index.html"
    if index.is_file() and not force:
        _info(f"Flutter web bundle already built at {FRONTEND_BUILD_WEB}")
        return True

    flutter = find_flutter()
    if flutter is None:
        _warn("Flutter SDK not found — skipping frontend build")
        _warn("install from https://docs.flutter.dev/get-started/install")
        _warn("or run `brew install --cask flutter` on macOS")
        return False

    frontend_dir = REPO_ROOT / "frontend"
    if not frontend_dir.is_dir():
        _warn(f"no frontend directory at {frontend_dir}")
        return False

    _info(f"running `flutter pub get` in {frontend_dir}")
    subprocess.run([flutter, "pub", "get"], cwd=str(frontend_dir), check=True)

    _info("running `flutter build web`")
    subprocess.run(
        [flutter, "build", "web", "--no-tree-shake-icons"],
        cwd=str(frontend_dir), check=True,
    )
    return index.is_file()


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - suppress access log
        pass


def spawn_static_server() -> tuple[socketserver.ThreadingTCPServer | None,
                                   threading.Thread | None]:
    """Serve frontend/build/web on WEB_PORT in a background thread."""
    if not (FRONTEND_BUILD_WEB / "index.html").is_file():
        return None, None

    handler = type(
        "Handler",
        (_SilentHandler,),
        {"directory": str(FRONTEND_BUILD_WEB)},
    )

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    try:
        srv = socketserver.ThreadingTCPServer(
            (WEB_HOST, WEB_PORT), handler
        )
    except OSError as exc:
        _err(f"could not bind {WEB_HOST}:{WEB_PORT} for the frontend: {exc}")
        return None, None

    t = threading.Thread(
        target=srv.serve_forever, name="frontend-http", daemon=True,
    )
    t.start()
    return srv, t


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-click launcher for the ADHD screening system",
    )
    parser.add_argument("--engine-only", action="store_true",
                        help="Only run the engine, skip the frontend")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the browser")
    parser.add_argument("--build-frontend", action="store_true",
                        help="Force a fresh `flutter build web`")
    args = parser.parse_args(argv)

    print(f"{_Color.BOLD}=== ADHD 系统启动器 ==={_Color.RESET}")
    print(f"  Repo root: {REPO_ROOT}")
    print()

    python_exe = check_environment()

    # Frontend build (best effort, non-fatal)
    frontend_ready = False
    if not args.engine_only:
        try:
            frontend_ready = build_frontend_if_needed(force=args.build_frontend)
        except subprocess.CalledProcessError as exc:
            _warn(f"frontend build failed: {exc}")
            frontend_ready = False

    # Spawn engine
    engine_proc = spawn_engine(python_exe)
    stream_subprocess(engine_proc, "engine")

    if not wait_for_engine(timeout=30.0):
        _err("engine did not become healthy within 30s — see logs above")
        try:
            engine_proc.terminate()
        finally:
            engine_proc.wait(timeout=5)
        return 1
    _ok(f"engine ready at http://{ENGINE_HOST}:{ENGINE_PORT}")

    # Spawn frontend static server (optional)
    static_server = None
    if not args.engine_only and frontend_ready:
        static_server, _ = spawn_static_server()
        if static_server is not None:
            _ok(f"frontend served at http://{WEB_HOST}:{WEB_PORT}")
            if not args.no_browser:
                # Small delay so the server is ready before the browser hits it
                threading.Timer(
                    0.6,
                    lambda: webbrowser.open(f"http://{WEB_HOST}:{WEB_PORT}"),
                ).start()

    # Print a useful summary box
    print()
    print(f"{_Color.BOLD}── ready ──{_Color.RESET}")
    print(f"  Engine API : http://{ENGINE_HOST}:{ENGINE_PORT}")
    print(f"  Engine docs: http://{ENGINE_HOST}:{ENGINE_PORT}/docs")
    if static_server is not None:
        print(f"  Front-end  : http://{WEB_HOST}:{WEB_PORT}")
    elif args.engine_only:
        print("  Front-end  : skipped (--engine-only)")
    elif not frontend_ready:
        print("  Front-end  : not built — run `python start.py "
              "--build-frontend` after installing Flutter")
    print(f"{_Color.DIM}  Press Ctrl+C to stop everything.{_Color.RESET}")
    print()

    # Wait for either Ctrl+C or the engine to exit
    exit_code = 0
    try:
        while True:
            rc = engine_proc.poll()
            if rc is not None:
                _err(f"engine exited with code {rc}")
                exit_code = rc
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        _info("Ctrl+C received, shutting down…")
    finally:
        # Stop frontend
        if static_server is not None:
            try:
                static_server.shutdown()
                static_server.server_close()
            except Exception:
                pass
        # Stop engine
        if engine_proc.poll() is None:
            try:
                if IS_WIN:
                    engine_proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    engine_proc.terminate()
                engine_proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                engine_proc.kill()
        _ok("done.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

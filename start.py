#!/usr/bin/env python3
"""ADHD 系统统一启动器 — 一个文件，零环境前置。

任何平台 (Windows / macOS / Linux) 上的唯一启动命令：

    python start.py

它会自动：
  1. 检测 .venv 是否存在；不存在就用 python -m venv 创建一个
  2. 把所有依赖装进去（基于 engine/pyproject.toml）
  3. 用 .venv 的 Python 重启自己
  4. 启动 FastAPI engine 子进程
  5. (可选) serve 前端 web build + 自动打开浏览器
  6. Ctrl+C 优雅关停

可选参数：

    python start.py --engine-only      # 只跑 engine，不动 frontend
    python start.py --no-browser       # 不自动打开浏览器
    python start.py --reinstall        # 删 .venv 重新装
    python start.py --build-frontend   # 强制 flutter build web
    python start.py --port 9000        # 改 engine 端口

不需要 PYTHONPATH，不需要 source activate，不需要 .bat / .sh wrapper。
唯一前置条件：系统上有 Python 3.10-3.13 任意一个版本。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import platform
import shutil
import signal
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
# Repository layout — everything resolves from this script's directory
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
ENGINE_DIR = REPO_ROOT / "engine"
ENGINE_PACKAGE = ENGINE_DIR / "adhd_engine"
ENGINE_PYPROJECT = ENGINE_DIR / "pyproject.toml"
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_BUILD_WEB = FRONTEND_DIR / "build" / "web"
VENV_DIR = REPO_ROOT / ".venv"

DEFAULT_ENGINE_HOST = "127.0.0.1"
DEFAULT_ENGINE_PORT = 8765
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080

IS_WIN = platform.system() == "Windows"

# Python version range we accept (mediapipe pin requires <3.13;
# we technically support 3.10+ but recommend 3.12 because that's what
# we tested against in CI / on this machine).
PY_MIN = (3, 10)
PY_MAX_EXCLUSIVE = (3, 13)
PY_RECOMMENDED = (3, 12)


# ---------------------------------------------------------------------------
# Pretty printing — minimal, no external deps. ANSI colors auto-disable
# on Windows cmd.exe (which doesn't render them by default).
# ---------------------------------------------------------------------------


_USE_COLOR = (not IS_WIN) or os.environ.get("WT_SESSION") is not None


def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m" if _USE_COLOR else msg


def info(msg: str) -> None:
    print(f"{_c('34', '[start]')} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{_c('32', '[start]')} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{_c('33', '[start]')} {msg}", flush=True)


def err(msg: str) -> None:
    print(f"{_c('31', '[start]')} {msg}", flush=True)


def banner(title: str) -> None:
    print()
    print(_c("1", f"=== {title} ==="))


# ---------------------------------------------------------------------------
# Self-bootstrap: detect / create the venv, install deps, re-exec
# ---------------------------------------------------------------------------


def venv_python_path() -> Path:
    """Return the path of the Python interpreter inside our managed venv."""
    if IS_WIN:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def running_inside_managed_venv() -> bool:
    """True iff the *current* interpreter is the one we manage in `.venv/`."""
    if not VENV_DIR.is_dir():
        return False
    try:
        expected = venv_python_path().resolve()
        actual = Path(sys.executable).resolve()
        return expected == actual
    except OSError:
        return False


def find_compatible_python() -> tuple[list[str], tuple[int, int]] | None:
    """Find a system Python in [PY_MIN, PY_MAX_EXCLUSIVE).

    Returns ``(invocation_command, version)`` where invocation_command is a
    list suitable for passing to ``subprocess.run`` (e.g. ``["py", "-3.12"]``
    on Windows or ``["python3.12"]`` on POSIX).
    """
    # Check the current interpreter first — if it fits, use it.
    cur = sys.version_info
    if (cur.major, cur.minor) >= PY_MIN and (cur.major, cur.minor) < PY_MAX_EXCLUSIVE:
        return [sys.executable], (cur.major, cur.minor)

    candidates: list[list[str]] = []
    if IS_WIN:
        # Windows Python launcher (py.exe) is the most reliable way
        for minor in (12, 11, 10):
            candidates.append(["py", f"-3.{minor}"])
        for name in ("python3.12.exe", "python3.11.exe", "python3.10.exe",
                     "python3.exe", "python.exe"):
            candidates.append([name])
    else:
        for minor in (12, 11, 10):
            candidates.append([f"python3.{minor}"])
        candidates.extend([["python3"], ["python"]])
        # Common Mac/Linux install paths
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            for minor in (12, 11, 10):
                p = Path(prefix) / f"python3.{minor}"
                if p.is_file():
                    candidates.append([str(p)])

    for cmd in candidates:
        try:
            r = subprocess.run(
                cmd + ["-c", "import sys; print(sys.version_info[:2])"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                continue
            # Parse "(3, 12)" out of stdout
            line = r.stdout.strip()
            if not (line.startswith("(") and line.endswith(")")):
                continue
            parts = [p.strip() for p in line[1:-1].split(",")]
            if len(parts) < 2:
                continue
            maj, min_ = int(parts[0]), int(parts[1])
            if (maj, min_) >= PY_MIN and (maj, min_) < PY_MAX_EXCLUSIVE:
                return cmd, (maj, min_)
        except (FileNotFoundError, subprocess.TimeoutExpired,
                ValueError, OSError):
            continue
    return None


def bootstrap_venv() -> None:
    """Create `.venv/` and install all engine dependencies."""
    sanity_check_repo()

    found = find_compatible_python()
    if found is None:
        err(f"no compatible Python found "
            f"(need {PY_MIN[0]}.{PY_MIN[1]} ≤ version < "
            f"{PY_MAX_EXCLUSIVE[0]}.{PY_MAX_EXCLUSIVE[1]})")
        err(f"please install Python {PY_RECOMMENDED[0]}.{PY_RECOMMENDED[1]} "
            f"from https://www.python.org/downloads/")
        sys.exit(1)
    py_cmd, py_ver = found
    info(f"using Python {py_ver[0]}.{py_ver[1]} from "
         f"{' '.join(py_cmd)} for venv creation")

    # 1. Create the venv
    info(f"creating venv at {VENV_DIR}")
    try:
        subprocess.run(
            py_cmd + ["-m", "venv", str(VENV_DIR)],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err(f"venv creation failed: {exc}")
        sys.exit(1)

    if not venv_python_path().is_file():
        err(f"venv created but {venv_python_path()} is missing")
        sys.exit(1)

    # 2. Upgrade pip first (avoids weird wheel resolution issues)
    info("upgrading pip inside the venv")
    subprocess.run(
        [str(venv_python_path()), "-m", "pip", "install",
         "--upgrade", "pip", "wheel", "setuptools"],
        check=True,
    )

    # 3. Install the engine package + all its deps from pyproject.toml.
    #    This installs torch, mediapipe, fastapi, sqlmodel, and the engine
    #    itself in editable mode in a single shot.
    info("installing engine + all dependencies "
         "(this may take 5-10 minutes on first run)")
    try:
        subprocess.run(
            [str(venv_python_path()), "-m", "pip", "install", "-e",
             str(ENGINE_DIR)],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err(f"dependency install failed: {exc}")
        err("inspect the output above for the failing package")
        sys.exit(1)

    ok(f"venv ready at {VENV_DIR}")


def relaunch_in_venv(extra_args: list[str]) -> int:
    """Run ``start.py`` again with the venv interpreter and wait for it.

    We use ``subprocess`` (not ``os.execv``) so the parent process keeps its
    identity — important for clean Ctrl+C delivery on Windows where execv
    has odd behavior with the cmd.exe parent.
    """
    vp = venv_python_path()
    if not vp.is_file():
        err(f"venv python missing at {vp}")
        return 1

    cmd = [str(vp), str(Path(__file__).resolve())] + extra_args
    info(f"re-launching with venv python")

    creationflags = 0
    preexec_fn = None
    if IS_WIN:
        # Put the child in its own process group so we can deliver
        # CTRL_BREAK_EVENT for clean shutdown.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        preexec_fn = os.setsid

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        creationflags=creationflags,
        preexec_fn=preexec_fn,
    )

    try:
        return proc.wait()
    except KeyboardInterrupt:
        info("Ctrl+C received, forwarding to child…")
        try:
            if IS_WIN:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            return proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            return 130


# ---------------------------------------------------------------------------
# Repo / version sanity
# ---------------------------------------------------------------------------


def sanity_check_repo() -> None:
    if not ENGINE_PACKAGE.is_dir():
        err(f"engine package not found at {ENGINE_PACKAGE}")
        err("are you running start.py from the repo root?")
        sys.exit(1)
    if not ENGINE_PYPROJECT.is_file():
        err(f"engine/pyproject.toml not found at {ENGINE_PYPROJECT}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Engine subprocess
# ---------------------------------------------------------------------------


def spawn_engine(host: str, port: int) -> subprocess.Popen:
    """Start uvicorn + the FastAPI app as a child process."""
    env = os.environ.copy()
    sep = ";" if IS_WIN else ":"
    env["PYTHONPATH"] = (
        f"{ENGINE_DIR}{sep}{env.get('PYTHONPATH', '')}".rstrip(sep)
    )
    env["ADHD_ENGINE_HOST"] = host
    env["ADHD_ENGINE_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, "-m", "adhd_engine.server"]
    info(f"launching engine: {' '.join(cmd)}")

    creationflags = 0
    if IS_WIN:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    return subprocess.Popen(
        cmd,
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        creationflags=creationflags,
    )


def stream_subprocess_output(proc: subprocess.Popen, label: str) -> threading.Thread:
    def _pump():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"{_c('2', f'[{label}]')} {line.rstrip()}", flush=True)
        except Exception:
            pass

    t = threading.Thread(
        target=_pump, name=f"pump-{label}", daemon=True,
    )
    t.start()
    return t


def wait_for_engine(host: str, port: int, timeout: float = 30.0) -> bool:
    url = f"http://{host}:{port}/"
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
# Frontend (Flutter web) — optional
# ---------------------------------------------------------------------------


def find_flutter() -> str | None:
    candidates = (["flutter.bat", "flutter"] if IS_WIN
                  else ["flutter",
                        "/opt/homebrew/share/flutter/bin/flutter",
                        "/usr/local/share/flutter/bin/flutter"])
    for c in candidates:
        try:
            r = subprocess.run([c, "--version"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=8)
            if r.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def build_frontend_if_needed(force: bool = False) -> bool:
    index = FRONTEND_BUILD_WEB / "index.html"
    if index.is_file() and not force:
        info(f"Flutter web bundle already built at {FRONTEND_BUILD_WEB}")
        return True
    if not FRONTEND_DIR.is_dir():
        warn(f"no frontend directory at {FRONTEND_DIR}")
        return False
    flutter = find_flutter()
    if flutter is None:
        warn("Flutter SDK not found — skipping frontend build")
        warn("install Flutter from https://docs.flutter.dev/get-started/install")
        warn("(or run with --engine-only to suppress this warning)")
        return False
    try:
        info(f"running `flutter pub get` in {FRONTEND_DIR}")
        subprocess.run([flutter, "pub", "get"], cwd=str(FRONTEND_DIR),
                       check=True)
        info("running `flutter build web`")
        subprocess.run([flutter, "build", "web", "--no-tree-shake-icons"],
                       cwd=str(FRONTEND_DIR), check=True)
    except subprocess.CalledProcessError as exc:
        warn(f"flutter build failed: {exc}")
        return False
    return index.is_file()


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with the access log silenced.

    The ``directory`` argument MUST be passed at construction time via
    functools.partial — setting it as a class attribute does NOT work
    because ``SimpleHTTPRequestHandler.__init__`` reads it from a kwarg
    and ignores any same-named class attribute.
    """

    def log_message(self, format, *args):  # noqa: A002
        pass


def spawn_static_server() -> socketserver.ThreadingTCPServer | None:
    index = FRONTEND_BUILD_WEB / "index.html"
    if not index.is_file():
        warn(f"no index.html at {index} — frontend not built yet?")
        return None

    # Bind the served directory at construction time. Without functools.partial
    # the handler defaults to os.getcwd(), which is why the user previously
    # saw a directory listing of the repo root instead of the Flutter app.
    handler_factory = functools.partial(
        _SilentHandler,
        directory=str(FRONTEND_BUILD_WEB),
    )

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    try:
        srv = socketserver.ThreadingTCPServer(
            (WEB_HOST, WEB_PORT), handler_factory,
        )
    except OSError as exc:
        err(f"could not bind {WEB_HOST}:{WEB_PORT} for the frontend: {exc}")
        return None
    threading.Thread(
        target=srv.serve_forever, name="frontend-http", daemon=True,
    ).start()
    return srv


# ---------------------------------------------------------------------------
# Run mode (only invoked when running inside the managed venv)
# ---------------------------------------------------------------------------


def run_mode(args: argparse.Namespace) -> int:
    sanity_check_repo()

    banner("ADHD 系统启动器")
    print(f"  Repo : {REPO_ROOT}")
    print(f"  Venv : {VENV_DIR}  {_c('32', '(active)')}")
    print(f"  Python: {sys.executable}")
    print()

    # Frontend build (best effort)
    frontend_ready = False
    if not args.engine_only:
        try:
            frontend_ready = build_frontend_if_needed(force=args.build_frontend)
        except Exception as exc:
            warn(f"frontend prep failed: {exc}")

    # Spawn engine
    engine_proc = spawn_engine(args.host, args.port)
    stream_subprocess_output(engine_proc, "engine")

    if not wait_for_engine(args.host, args.port, timeout=30.0):
        err("engine did not become healthy within 30 seconds")
        try:
            engine_proc.terminate()
            engine_proc.wait(timeout=5)
        except Exception:
            pass
        return 1
    ok(f"engine ready at http://{args.host}:{args.port}")

    # Spawn static frontend
    static_server = None
    if not args.engine_only and frontend_ready:
        static_server = spawn_static_server()
        if static_server is not None:
            ok(f"frontend served at http://{WEB_HOST}:{WEB_PORT}")
            if not args.no_browser:
                threading.Timer(
                    0.6,
                    lambda: webbrowser.open(f"http://{WEB_HOST}:{WEB_PORT}"),
                ).start()

    # Print summary box
    print()
    print(_c("1", "── ready ──"))
    print(f"  Engine API : http://{args.host}:{args.port}")
    print(f"  Engine docs: http://{args.host}:{args.port}/docs")
    if static_server is not None:
        print(f"  Front-end  : http://{WEB_HOST}:{WEB_PORT}")
    elif args.engine_only:
        print("  Front-end  : skipped (--engine-only)")
    elif not frontend_ready:
        print("  Front-end  : not built — install Flutter and re-run "
              "with --build-frontend")
    print(_c("2", "  Press Ctrl+C to stop everything."))
    print()

    # Wait loop
    exit_code = 0
    try:
        while True:
            rc = engine_proc.poll()
            if rc is not None:
                err(f"engine exited unexpectedly (code {rc})")
                exit_code = rc if rc != 0 else 1
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        info("Ctrl+C received, shutting down…")
    finally:
        if static_server is not None:
            try:
                static_server.shutdown()
                static_server.server_close()
            except Exception:
                pass
        if engine_proc.poll() is None:
            try:
                if IS_WIN:
                    engine_proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    engine_proc.terminate()
                engine_proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                engine_proc.kill()
        ok("done.")

    return exit_code


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="start.py",
        description="ADHD 系统统一启动器 — 自动建 venv + 装依赖 + 启动",
    )
    parser.add_argument("--engine-only", action="store_true",
                        help="只跑 engine, 不动 frontend")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器")
    parser.add_argument("--reinstall", action="store_true",
                        help="删 .venv 重新装")
    parser.add_argument("--build-frontend", action="store_true",
                        help="强制重新 flutter build web")
    parser.add_argument("--host", default=DEFAULT_ENGINE_HOST,
                        help=f"engine bind 地址 (默认 {DEFAULT_ENGINE_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_ENGINE_PORT,
                        help=f"engine 端口 (默认 {DEFAULT_ENGINE_PORT})")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sanity_check_repo()

    # If we're already inside the managed venv, skip all bootstrap and run.
    if running_inside_managed_venv():
        return run_mode(args)

    # Otherwise: bootstrap if needed, then re-launch via the venv python.
    if args.reinstall and VENV_DIR.exists():
        info(f"--reinstall: removing existing venv at {VENV_DIR}")
        shutil.rmtree(VENV_DIR)

    if not VENV_DIR.exists():
        bootstrap_venv()

    # The venv now exists — re-launch this script with the venv interpreter
    # so the run_mode branch executes inside it.
    extra_args = [a for a in sys.argv[1:] if a != "--reinstall"]
    return relaunch_in_venv(extra_args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        err(f"unhandled error: {exc}")
        # On Windows double-click the cmd window will close immediately;
        # pause so the user can read the error.
        if IS_WIN and not sys.stdin.isatty():
            try:
                input("\nPress Enter to exit...")
            except EOFError:
                pass
        raise

"""Worker subprocess entrypoint.

The FastAPI parent spawns this module via :func:`multiprocessing.Process` and
hands it a session id + an :class:`mp.Queue` for IPC. The worker:

1. Builds a :class:`adhd_engine.worker.gaze_system.GazeSystem` (which opens
   the camera, loads the deep model, and creates the fullscreen pygame
   window). On macOS this MUST happen on the main thread of the worker
   process — that's why the worker is a separate process from the FastAPI
   server.
2. Runs the 13-point click calibration.
3. Dispatches into either ``run_sternberg_screening()`` or ``track()``
   depending on the session mode.
4. Forwards every captured gaze frame and every task event back to the
   parent through the :class:`WorkerPublisher`.
5. Sends the final report (or an error) and exits.

This module is **not** importable for use inside the FastAPI process — it
must be invoked as a subprocess. The parent uses :func:`spawn_worker`
(see ``adhd_engine.api.sessions``) which calls into here.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import traceback
from typing import Optional

from adhd_engine.ipc.bridge import WorkerPublisher


def _run(session_id: str, mode: str, subject_id: str,
         queue_handle: mp.Queue) -> None:
    """The body of the worker subprocess. Runs on the child's main thread."""
    publisher = WorkerPublisher(queue_handle, session_id)
    publisher.publish_ready(pid=os.getpid())

    # Lazy imports — pygame / torch / mediapipe are heavy and we don't want
    # the parent process to load them just because it imported the runner.
    try:
        from adhd_engine.worker.gaze_system import GazeSystem
    except Exception as exc:
        publisher.publish_error("import_failed",
                                f"{exc.__class__.__name__}: {exc}")
        return

    try:
        publisher.publish_event({"type": "calibration_start"})
        system = GazeSystem(
            subject_id=subject_id,
            ipc_publisher=publisher,
            session_id=session_id,
        )
    except Exception as exc:
        publisher.publish_error(
            "init_failed", f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        )
        return

    try:
        system.calibrate()
        publisher.publish_event({"type": "calibration_done"})
        system._validate_calibration()

        if mode == "sternberg":
            result = system.run_sternberg_screening()
            if not result:
                publisher.publish_error(
                    "no_report", "Sternberg screening produced no report")
            else:
                publisher.publish_report(result)
        elif mode == "track":
            system.track()
            try:
                system.save_heatmap()
                system.save_ecml_data()
            except Exception as save_exc:  # pragma: no cover
                publisher.publish_event({
                    "type": "warning",
                    "msg": f"track save failed: {save_exc}",
                })
            publisher.publish_event({"type": "task_done"})
        else:
            publisher.publish_error("bad_mode", f"unknown mode: {mode}")

    except KeyboardInterrupt:
        publisher.publish_event({"type": "cancelled"})
    except Exception as exc:
        publisher.publish_error(
            "run_failed",
            f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}",
        )
    finally:
        try:
            system.close()
        except Exception:  # pragma: no cover
            pass


def spawn_worker(session_id: str, mode: str, subject_id: str,
                 queue_handle: mp.Queue) -> mp.Process:
    """Start a worker subprocess and return the :class:`mp.Process` handle.

    On macOS we use the ``spawn`` start method by default. The parent should
    keep a reference to the returned ``Process`` so it can ``join`` or
    ``terminate`` later.
    """
    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_run,
        args=(session_id, mode, subject_id, queue_handle),
        name=f"adhd-worker-{session_id[:8]}",
        daemon=False,
    )
    proc.start()
    return proc


# ---------------------------------------------------------------------------
# CLI fallback (for manual testing without FastAPI in the loop)
# ---------------------------------------------------------------------------


def _cli_main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ADHD Engine worker subprocess (manual mode)")
    parser.add_argument("--session-id", default="cli-session")
    parser.add_argument("--subject", default="CLI_SUBJECT")
    parser.add_argument("--mode", choices=["sternberg", "track"],
                        default="sternberg")
    args = parser.parse_args(argv)

    # In CLI mode there's no parent — drain the queue ourselves and just
    # print everything.
    q: mp.Queue = mp.Queue(maxsize=4096)

    import threading
    import queue as _q

    stop = threading.Event()

    def drain():
        while not stop.is_set():
            try:
                msg = q.get(timeout=0.2)
            except _q.Empty:
                continue
            print(f"[ipc] {type(msg).__name__}: {msg.to_dict()}")

    drain_thread = threading.Thread(target=drain, daemon=True)
    drain_thread.start()

    try:
        _run(args.session_id, args.mode, args.subject, q)
    finally:
        stop.set()
        drain_thread.join(timeout=1.0)

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli_main())

"""Parent <-> worker IPC bridge.

The worker subprocess and the FastAPI parent communicate over a single
``multiprocessing.Queue``. The worker pushes :class:`WorkerMessage` instances;
the parent has a background asyncio task that drains the queue and forwards
each message either to WebSocket subscribers or into the SQLite store.

The queue is created in the parent and inherited by the child via the
standard ``multiprocessing.Process`` fork/spawn mechanics. There is no need
for sockets, ports, or shared memory — everything goes through Python's
built-in queue, which is plenty fast for 30 Hz gaze frames + occasional
events (~30-50 messages/sec at peak).
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .messages import (
    EventMsg,
    GazeFrameMsg,
    ReportReadyMsg,
    WorkerErrorMsg,
    WorkerMessage,
    WorkerReadyMsg,
)


# ---------------------------------------------------------------------------
# Worker side: a thin wrapper that converts callable arguments into messages
# ---------------------------------------------------------------------------


class WorkerPublisher:
    """Worker-side helper that turns method calls into queue pushes.

    The worker code (``GazeSystem``, ``SternbergTask``) accepts a generic
    ``ipc_publisher`` callable. We pass an instance of this class as that
    callable so it can be invoked in two shapes:

    * ``publisher(t, x, y, pupil, valid)`` — gaze frame
    * ``publisher({"type": "trial_end", ...})`` — event dict

    Both forms get serialised to dataclasses and pushed to the queue.
    """

    def __init__(self, queue_handle: mp.Queue, session_id: str):
        self._q = queue_handle
        self._session_id = session_id

    # ------- gaze frame call shape -----------------------------------------

    def __call__(self, *args, **kwargs):  # type: ignore[override]
        if len(args) >= 5:
            t, x, y, pupil, valid = args[:5]
            self.publish_gaze(float(t), float(x), float(y), float(pupil), bool(valid))
            return
        if len(args) == 1 and isinstance(args[0], dict):
            self.publish_event(args[0])
            return
        raise TypeError(
            "WorkerPublisher expected (t,x,y,pupil,valid) or (event_dict)"
        )

    def publish_gaze(self, t: float, x: float, y: float, pupil: float,
                     valid: bool, fps: int = 30) -> None:
        try:
            self._q.put_nowait(GazeFrameMsg(
                session_id=self._session_id,
                t=t, x=x, y=y, pupil=pupil, valid=valid, fps=fps,
            ))
        except queue.Full:
            # Queue saturated — drop the frame rather than block the capture
            # loop. This is acceptable: 30 Hz gaze is best-effort streaming.
            pass

    def publish_event(self, payload: dict) -> None:
        msg_type = payload.pop("type", "event")
        payload.pop("session_id", None)
        self._q.put(EventMsg(
            session_id=self._session_id, type=msg_type, payload=payload,
        ))

    def publish_ready(self, pid: int) -> None:
        self._q.put(WorkerReadyMsg(session_id=self._session_id, pid=pid))

    def publish_report(self, report: dict) -> None:
        self._q.put(ReportReadyMsg(session_id=self._session_id, report=report))

    def publish_error(self, code: str, message: str) -> None:
        self._q.put(WorkerErrorMsg(
            session_id=self._session_id, code=code, message=message,
        ))


# ---------------------------------------------------------------------------
# Parent side: drain the queue from a background thread + asyncio bridge
# ---------------------------------------------------------------------------


@dataclass
class IpcChannel:
    """A queue + background drain thread, owned by the FastAPI parent.

    One channel per active worker session. The channel exposes:

    * :attr:`queue` — the ``mp.Queue`` to pass into the child process
    * :meth:`start_drain` — kick off a background thread that polls the queue
      and forwards each message to a user-supplied async handler running on
      the FastAPI event loop
    * :meth:`stop` — join the drain thread and discard remaining messages

    We can't ``await`` directly on a ``mp.Queue.get`` from asyncio (it's a
    blocking call), so the drain runs in a thread and uses
    ``asyncio.run_coroutine_threadsafe`` to dispatch handlers back to the
    event loop. This is the standard pattern for bridging multiprocessing
    queues into asyncio.
    """

    session_id: str
    queue: mp.Queue
    _stop: threading.Event = None  # type: ignore[assignment]
    _thread: Optional[threading.Thread] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _handler: Optional[Callable[[WorkerMessage], Awaitable[None]]] = None

    def __post_init__(self):
        if self._stop is None:
            self._stop = threading.Event()

    def start_drain(
        self,
        loop: asyncio.AbstractEventLoop,
        handler: Callable[[WorkerMessage], Awaitable[None]],
    ) -> None:
        if self._thread is not None:
            return
        self._loop = loop
        self._handler = handler
        self._thread = threading.Thread(
            target=self._drain_loop,
            name=f"ipc-drain-{self.session_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _drain_loop(self) -> None:
        assert self._handler is not None and self._loop is not None
        while not self._stop.is_set():
            try:
                msg = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                # Worker process gone — drop out
                break
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._handler(msg), self._loop
                )
                fut.result(timeout=5.0)
            except Exception as exc:  # pragma: no cover
                print(f"[ipc] handler raised: {exc}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # Drain any remaining messages so the queue can be GC'd cleanly
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass


def make_channel(session_id: str, maxsize: int = 4096) -> IpcChannel:
    """Factory: create a new ``IpcChannel`` with a fresh queue."""
    q: mp.Queue = mp.Queue(maxsize=maxsize)
    return IpcChannel(session_id=session_id, queue=q)

"""Smoke tests for the parent <-> worker IPC bridge.

We don't spawn a real subprocess here (that would pull in pygame, MediaPipe,
etc.). Instead we exercise the queue + drain plumbing on its own — a
``WorkerPublisher`` running in a thread plays the role of the worker, and the
parent ``IpcChannel`` drains its messages back to an asyncio handler.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time

import pytest

from adhd_engine.ipc.bridge import IpcChannel, WorkerPublisher, make_channel
from adhd_engine.ipc.messages import (
    EventMsg,
    GazeFrameMsg,
    ReportReadyMsg,
    WorkerErrorMsg,
    WorkerReadyMsg,
)


@pytest.mark.asyncio
async def test_publisher_pushes_gaze_frames_through_channel():
    """1000 gaze frames cross the queue without loss or reordering."""
    channel = make_channel(session_id="sess-test")
    received: list = []

    async def handler(msg):
        received.append(msg)

    channel.start_drain(asyncio.get_running_loop(), handler)

    publisher = WorkerPublisher(channel.queue, "sess-test")

    # Simulate the worker pushing 1000 gaze frames as fast as it can
    def push_loop():
        for i in range(1000):
            publisher.publish_gaze(
                t=i * (1.0 / 30.0),
                x=float(i % 1920),
                y=float(i % 1080),
                pupil=0.10 + 0.001 * (i % 50),
                valid=True,
            )

    push_thread = threading.Thread(target=push_loop)
    push_thread.start()
    push_thread.join(timeout=5.0)

    # Wait for the drain thread to deliver everything
    deadline = time.monotonic() + 3.0
    while len(received) < 1000 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)

    channel.stop()

    assert len(received) == 1000, f"expected 1000 frames, got {len(received)}"
    # All gaze frames
    assert all(isinstance(m, GazeFrameMsg) for m in received)
    # Order preserved (queue is FIFO)
    assert received[0].t == 0.0
    assert received[-1].t == pytest.approx(999 / 30.0)


@pytest.mark.asyncio
async def test_publisher_handles_event_dict_form():
    """The (event_dict) call shape goes to ``EventMsg``."""
    channel = make_channel(session_id="sess-evt")
    received: list = []

    async def handler(msg):
        received.append(msg)

    channel.start_drain(asyncio.get_running_loop(), handler)

    publisher = WorkerPublisher(channel.queue, "sess-evt")
    publisher.publish_event({"type": "trial_end", "trial_num": 42, "correct": 1})
    publisher.publish_event({"type": "task_done", "total_trials": 160})

    deadline = time.monotonic() + 2.0
    while len(received) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)

    channel.stop()

    assert len(received) == 2
    assert all(isinstance(m, EventMsg) for m in received)
    assert received[0].type == "trial_end"
    assert received[0].payload == {"trial_num": 42, "correct": 1}
    assert received[1].type == "task_done"


@pytest.mark.asyncio
async def test_publisher_callable_dispatch():
    """The plain ``__call__`` shape supports both gaze and event dispatch."""
    channel = make_channel(session_id="sess-call")
    received: list = []

    async def handler(msg):
        received.append(msg)

    channel.start_drain(asyncio.get_running_loop(), handler)

    publisher = WorkerPublisher(channel.queue, "sess-call")

    # Gaze frame call shape
    publisher(0.0, 100.0, 200.0, 0.15, True)
    # Event dict call shape
    publisher({"type": "trial_start", "trial_num": 1, "block": 1, "load": 2})

    deadline = time.monotonic() + 2.0
    while len(received) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)

    channel.stop()

    assert len(received) == 2
    assert isinstance(received[0], GazeFrameMsg)
    assert received[0].x == 100.0
    assert isinstance(received[1], EventMsg)
    assert received[1].type == "trial_start"


def test_message_to_dict_is_json_safe():
    """Each message must serialise to a JSON-friendly dict for the WS layer."""
    import json

    msgs = [
        GazeFrameMsg(session_id="s", t=0.5, x=100.0, y=200.0,
                     pupil=0.15, valid=True),
        EventMsg(session_id="s", type="trial_end",
                 payload={"trial_num": 5, "correct": 1, "rt": 0.73}),
        WorkerReadyMsg(session_id="s", pid=12345),
        ReportReadyMsg(session_id="s",
                       report={"prediction": "ADHD", "adhd_probability": 0.72}),
        WorkerErrorMsg(session_id="s", code="camera_denied",
                       message="permission denied"),
    ]
    for m in msgs:
        d = m.to_dict()
        json.dumps(d)  # raises if not JSON-friendly

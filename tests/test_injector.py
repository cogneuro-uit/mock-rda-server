"""Control-socket and command-parsing injection paths."""

from __future__ import annotations

import json
import socket
import time

from mock_rda.injector import ControlSocketServer, marker_from_command
from mock_rda.markers import AT_NEXT, InjectionQueue


def test_marker_from_command_defaults_and_explicit():
    m = marker_from_command({})
    assert m.sample == AT_NEXT and m.type == "Stimulus" and m.description == "S  1"
    assert m.points == 1 and m.channel == -1

    m2 = marker_from_command(
        {"type": "Response", "description": "R 15", "points": 3, "channel": 7, "at": 12345}
    )
    assert (m2.sample, m2.type, m2.description, m2.points, m2.channel) == (
        12345, "Response", "R 15", 3, 7,
    )


def test_control_socket_injects_into_queue():
    q = InjectionQueue()
    server = ControlSocketServer(q, host="127.0.0.1", port=0)
    port = server.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(json.dumps({"type": "Stimulus", "at": 9000}).encode() + b"\n")
            reply = sock.recv(256)
            assert b'"ok"' in reply
            sock.sendall(json.dumps({"description": "S  2", "at": "next"}).encode() + b"\n")
            sock.recv(256)
        # Give the handler a moment to enqueue.
        deadline = time.monotonic() + 2
        while len(q) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        server.stop()

    drained = q.drain_for_block(8900, 200)  # block [8900, 9100) contains sample 9000
    samples = sorted(m.sample for m in drained)
    assert 9000 in samples  # absolute injection landed in the right block
    assert 8900 in samples  # the "next" injection stamped at block start


def test_control_socket_burst_command():
    q = InjectionQueue()
    server = ControlSocketServer(q, host="127.0.0.1", port=0, sample_rate=5000.0)
    port = server.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(json.dumps({"count": 3, "interval_ms": 20, "at": 1000}).encode() + b"\n")
            reply = sock.recv(256)
            assert b'"ok"' in reply
        deadline = time.monotonic() + 2
        while len(q) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        server.stop()

    # 20 ms at 5 kHz = 100 samples between pulses.
    drained = q.drain_for_block(0, 2000)
    assert [m.sample for m in drained] == [1000, 1100, 1200]


def test_control_socket_burst_needs_sample_rate():
    q = InjectionQueue()
    server = ControlSocketServer(q, host="127.0.0.1", port=0)  # no sample_rate
    port = server.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(json.dumps({"count": 2}).encode() + b"\n")
            reply = sock.recv(256)
            assert b'"error"' in reply
    finally:
        server.stop()
    assert len(q) == 0


def test_control_socket_bad_json_reports_error():
    q = InjectionQueue()
    server = ControlSocketServer(q, host="127.0.0.1", port=0)
    port = server.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"not-json\n")
            reply = sock.recv(256)
            assert b'"error"' in reply
    finally:
        server.stop()
    assert len(q) == 0

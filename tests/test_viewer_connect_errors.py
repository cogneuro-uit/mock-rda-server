"""End-to-end and unit tests for friendly RDA viewer connection errors."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading

import pytest

from rda_viewer.errors import (
    EXIT_CANNOT_CONNECT,
    EXIT_NO_DATA,
    RDAConnectionError,
    RDATimeoutError,
    connect_client,
    friendly_connect_message,
    friendly_no_start_message,
)
from rda_viewer.minimal_client import RDAClient

VIEWER_MODULES = [
    "rda_viewer.dump_markers",
    "rda_viewer.plot_client",
    "rda_viewer.tep_client",
    "rda_viewer.gui_client",
    "rda_viewer.itep_client",
]


def _python() -> str:
    return sys.executable


def _run_cli(module: str, port: int, timeout: float | None = None) -> subprocess.CompletedProcess:
    cmd = [_python(), "-m", module, "--port", str(port)]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def test_rda_client_refuses_unreachable_port(free_port):
    with pytest.raises(RDAConnectionError):
        RDAClient("127.0.0.1", free_port)


def test_connect_client_refuses_unreachable_port(free_port):
    with pytest.raises(RDAConnectionError):
        connect_client("127.0.0.1", free_port, timeout=1.0)


def test_friendly_connect_message_mentions_host_and_port():
    msg = friendly_connect_message("127.0.0.1", 51244)
    assert "127.0.0.1:51244" in msg
    assert "mock-rda synth --port 51244" in msg
    assert "mock-rda file RECORDING.vhdr --loop --port 51244" in msg
    assert "re-run this viewer" in msg


def test_friendly_no_start_message_mentions_host_and_port():
    msg = friendly_no_start_message("127.0.0.1", 51244)
    assert "127.0.0.1:51244" in msg
    assert "Monitor" in msg
    assert "impedance" in msg
    assert "mock-rda synth --port 51244" in msg


@pytest.mark.parametrize("module", VIEWER_MODULES)
def test_viewer_cli_exits_friendly_on_unreachable_port(module, free_port):
    proc = _run_cli(module, free_port)
    assert proc.returncode == EXIT_CANNOT_CONNECT
    assert "Could not connect to the RDA stream" in proc.stderr
    assert "mock-rda synth --port" in proc.stderr


def test_minimal_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.minimal_client", free_port, timeout=0.5)
    assert proc.returncode == EXIT_CANNOT_CONNECT
    assert "Could not connect to the RDA stream" in proc.stderr


def test_messages_raises_timeout_when_nothing_is_sent(free_port):
    """An open socket that stays silent should surface RDATimeoutError."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", free_port))
    listener.listen(1)

    accepted_event = threading.Event()

    def accept_and_wait():
        conn, _ = listener.accept()
        accepted_event.set()
        # Accept but never send; client should hit its 0.5 s timeout.
        try:
            conn.recv(1)
        finally:
            conn.close()

    thread = threading.Thread(target=accept_and_wait, daemon=True)
    thread.start()

    client = RDAClient("127.0.0.1", free_port, timeout=0.5)
    try:
        with pytest.raises(RDATimeoutError):
            next(client.messages())
    finally:
        accepted_event.wait(timeout=2)
        client.close()
        listener.close()
        thread.join(timeout=2)


@pytest.mark.parametrize("module", ["rda_viewer.dump_markers"])
def test_cli_exits_friendly_on_silent_stream(module, free_port):
    """Regression: a silent listener must print the friendly no-data message and exit 3."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", free_port))
    listener.listen(1)

    accepted_event = threading.Event()

    def accept_and_wait():
        conn, _ = listener.accept()
        accepted_event.set()
        try:
            conn.recv(1)
        finally:
            conn.close()

    thread = threading.Thread(target=accept_and_wait, daemon=True)
    thread.start()

    try:
        proc = _run_cli(module, free_port, timeout=0.5)
    finally:
        accepted_event.wait(timeout=2)
        listener.close()
        thread.join(timeout=2)

    assert proc.returncode == EXIT_NO_DATA
    assert "Connected to 127.0.0.1" in proc.stderr
    assert "no START/data" in proc.stderr

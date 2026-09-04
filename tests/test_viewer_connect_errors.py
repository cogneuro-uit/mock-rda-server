"""End-to-end and unit tests for friendly RDA viewer connection errors."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading

import pytest

from rda_viewer.errors import (
    RDAConnectionError,
    RDATimeoutError,
    connect_client,
    friendly_connect_message,
    friendly_no_start_message,
)
from rda_viewer.minimal_client import RDAClient


def _python() -> str:
    return sys.executable


@pytest.fixture
def free_port() -> int:
    """Return an unused localhost TCP port (matches conftest.py)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    assert "Monitor" in msg or "impedance" in msg.lower()
    assert "mock-rda synth --port 51244" in msg


def test_dump_markers_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.dump_markers", free_port)
    assert proc.returncode == 2
    assert "Could not connect to the RDA stream" in proc.stderr
    assert "mock-rda synth --port" in proc.stderr


def test_minimal_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.minimal_client", free_port, timeout=0.5)
    assert proc.returncode == 2
    assert "Could not connect to the RDA stream" in proc.stderr


def test_plot_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.plot_client", free_port)
    assert proc.returncode == 2
    assert "Could not connect to the RDA stream" in proc.stderr


def test_tep_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.tep_client", free_port)
    assert proc.returncode == 2
    assert "Could not connect to the RDA stream" in proc.stderr


def test_gui_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.gui_client", free_port)
    assert proc.returncode == 2
    assert "Could not connect to the RDA stream" in proc.stderr


def test_itep_client_cli_exits_friendly_on_unreachable_port(free_port):
    proc = _run_cli("rda_viewer.itep_client", free_port)
    assert proc.returncode == 2
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

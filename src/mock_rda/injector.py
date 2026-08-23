"""Manual trigger injection paths feeding a shared :class:`InjectionQueue`.

Three ways to inject, all writing to the same queue (drained once per block by
the server):

1. **Python API** — :meth:`Server.inject` / :meth:`Server.inject_burst`, or
   direct ``queue.inject(marker)``. The Tk control panel in :mod:`mock_rda.gui`
   injects this way.
2. **Control socket** — :class:`ControlSocketServer` accepts one-line JSON
   commands over TCP (default ``localhost:51299``).
3. **Keypress** — :func:`keypress_loop`, used by the CLI.

Control command schema (one JSON object per line)::

    {"type": "Stimulus", "description": "S  1", "points": 1,
     "channel": -1, "at": "next" | <absolute_sample_int>,
     "count": 1, "interval_ms": 20.0}

All fields are optional except that ``at`` defaults to ``"next"``. A ``count``
greater than 1 injects a burst: the marker plus ``count - 1`` copies spaced
``interval_ms`` apart (the server must have been given its sample rate).
"""

from __future__ import annotations

import json
import socket
import sys
import threading

from .markers import AT_NEXT, InjectionQueue, Marker


def marker_from_command(cmd: dict) -> Marker:
    """Build a :class:`Marker` from a parsed control command dict."""
    at = cmd.get("at", "next")
    sample = AT_NEXT if at in ("next", None) else int(at)
    return Marker(
        sample=sample,
        type=str(cmd.get("type", "Stimulus")),
        description=str(cmd.get("description", "S  1")),
        points=int(cmd.get("points", 1)),
        channel=int(cmd.get("channel", -1)),
    )


class ControlSocketServer:
    """Background TCP server that turns one-line JSON into queued markers."""

    def __init__(
        self,
        queue: InjectionQueue,
        host: str = "127.0.0.1",
        port: int = 51299,
        on_inject=None,
        sample_rate: float | None = None,
    ) -> None:
        self.queue = queue
        self.host = host
        self.port = port
        self.on_inject = on_inject
        self.sample_rate = sample_rate  # needed to convert burst interval_ms to samples
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> int:
        """Bind, listen, and serve in a daemon thread. Returns the bound port."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="control", daemon=True)
        self._thread.start()
        return self.port

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(0.5)
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    self._dispatch(line, conn)

    def _dispatch(self, line: bytes, conn: socket.socket) -> None:
        line = line.strip()
        if not line:
            return
        try:
            cmd = json.loads(line.decode("utf-8"))
            marker = marker_from_command(cmd)
            count = int(cmd.get("count", 1))
            interval_ms = float(cmd.get("interval_ms", 20.0))
            if count > 1 and (not self.sample_rate or interval_ms <= 0):
                raise ValueError("burst needs a positive interval_ms and a "
                                 "server-side sample rate")
        except (ValueError, TypeError) as exc:
            try:
                conn.sendall(f'{{"error": "{exc}"}}\n'.encode())
            except OSError:
                pass
            return
        if count > 1:
            isi = max(1, round(interval_ms * self.sample_rate / 1000.0))
            self.queue.inject_burst(marker, [k * isi for k in range(1, count)])
        else:
            self.queue.inject(marker)
        if self.on_inject:
            self.on_inject(marker)
        try:
            conn.sendall(b'{"ok": true}\n')
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


def keypress_loop(queue: InjectionQueue, stop_event: threading.Event, on_inject=None) -> None:
    """Inject a default Stimulus marker whenever a line is entered on stdin.

    Runs in the calling thread (typically a daemon thread). Pressing Enter (or
    any line) injects ``Stimulus / "S  1"`` at the next block. Degrades to a
    no-op if stdin is not a TTY.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return
    while not stop_event.is_set():
        line = sys.stdin.readline()
        if not line:
            break
        marker = Marker(sample=AT_NEXT, type="Stimulus", description="S  1")
        queue.inject(marker)
        if on_inject:
            on_inject(marker)

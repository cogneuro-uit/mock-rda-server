"""TCP RDA server: accept clients, send START, stream DATA32, drain injections.

Listens on port **51244** (the 32-bit float port; 51234 is the legacy 16-bit
port). Each client gets its own transmit thread fed by a bounded queue; on
overrun the oldest data is dropped with a warning (like the reference server).
The single acquisition thread paces the source under the scheduler, stamps
injected markers into the block whose sample range contains them, and emits
DATA32 to every connected client.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading

from .markers import InjectionQueue, Marker
from .protocol import encode_data32, encode_keepalive, encode_start, encode_stop
from .scheduler import BlockScheduler
from .sources.base import SourceBase

RDA_FLOAT_PORT = 51244


def _log(msg: str) -> None:
    print(f"[mock-rda] {msg}", file=sys.stderr, flush=True)


class _Client:
    def __init__(self, conn: socket.socket, addr, max_queue: int) -> None:
        self.conn = conn
        self.addr = addr
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._run, name=f"tx-{addr}", daemon=True)
        self.alive = True
        self.dropped = 0

    def start(self) -> None:
        self.thread.start()

    def send(self, data: bytes) -> None:
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            # Drop the oldest queued message to make room (bounded latency).
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(data)
            except queue.Empty:
                pass
            self.dropped += 1
            if self.dropped % 100 == 1:
                _log(f"client {self.addr}: tx queue overrun, dropped {self.dropped}")

    def _run(self) -> None:
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                self.conn.sendall(item)
        except OSError:
            pass
        finally:
            self.alive = False
            try:
                self.conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self.alive = False
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass


class Server:
    """Mock RDA server streaming a :class:`SourceBase` to TCP clients."""

    def __init__(
        self,
        source: SourceBase,
        host: str = "0.0.0.0",
        port: int = RDA_FLOAT_PORT,
        max_queue: int = 128,
        keepalive_interval: float = 1.0,
        name_encoding: str = "cp1252",
        start_on_connect: bool = False,
    ) -> None:
        self.source = source
        self.host = host
        self.port = port
        self.max_queue = max_queue
        self.keepalive_interval = keepalive_interval
        self.name_encoding = name_encoding
        self.start_on_connect = start_on_connect

        self.injector = InjectionQueue()
        self.scheduler = BlockScheduler(source.block_points / source.sample_rate)

        self._sock: socket.socket | None = None
        self._clients: list[_Client] = []
        self._clients_lock = threading.Lock()
        self._stop = threading.Event()
        self._first_client = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._acq_thread: threading.Thread | None = None
        self._start_msg = b""

    # -- public API -------------------------------------------------------- #
    def inject(self, marker: Marker) -> None:
        """Queue a marker for injection (in-process API)."""
        self.injector.inject(marker)

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return sum(1 for c in self._clients if c.alive)

    def start(self) -> int:
        """Bind, listen, and begin streaming. Returns the bound port."""
        self._start_msg = encode_start(
            self.source.channel_names,
            self.source.sample_rate,
            self.source.resolutions,
            name_encoding=self.name_encoding,
        )
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, name="accept", daemon=True)
        self._acq_thread = threading.Thread(target=self._acquire_loop, name="acquire", daemon=True)
        self._accept_thread.start()
        self._acq_thread.start()
        _log(f"listening on {self.host}:{self.port} "
             f"({self.source.n_channels} ch @ {self.source.sample_rate:g} Hz, "
             f"block {self.source.block_points} pts)")
        return self.port

    def stop(self) -> None:
        """Send STOP to clients and shut everything down."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._broadcast(encode_stop())
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = list(self._clients)
        for c in clients:
            c.close()

    def wait(self) -> None:
        """Block until the acquisition thread finishes (e.g. file EOF)."""
        if self._acq_thread is not None:
            self._acq_thread.join()

    # -- internals --------------------------------------------------------- #
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client = _Client(conn, addr, self.max_queue)
            client.send(self._start_msg)  # START first, before any DATA32
            client.start()
            with self._clients_lock:
                self._clients.append(client)
            self._first_client.set()
            _log(f"client connected: {addr} (now {self.client_count})")

    def _broadcast(self, msg: bytes) -> None:
        with self._clients_lock:
            clients = list(self._clients)
            self._clients = [c for c in self._clients if c.alive]
        for c in clients:
            if c.alive:
                c.send(msg)

    def _acquire_loop(self) -> None:
        if self.start_on_connect:
            while not self._first_client.wait(0.1):
                if self._stop.is_set():
                    return
        self.scheduler.reset()
        sample_counter = 0
        block_idx = 0
        for data, src_markers in self.source.blocks():
            if self._stop.is_set():
                break
            n = data.shape[1]
            injected = self.injector.drain_for_block(sample_counter, n)
            if injected:
                self.source.on_injected_markers(injected)
            markers = list(src_markers) + injected
            msg = encode_data32(block_idx, data, markers, sample_counter)
            self._broadcast(msg)
            sample_counter += n
            block_idx += 1
            if self._stop.is_set():
                break
            self.scheduler.wait_next()
        if not self._stop.is_set():
            # Source exhausted (e.g. file without --loop): STOP then keep-alive.
            self._broadcast(encode_stop())
            _log(f"source exhausted after {block_idx} blocks; sending keep-alives")
            self._keepalive_until_stop()

    def _keepalive_until_stop(self) -> None:
        ka = encode_keepalive()
        while not self._stop.wait(self.keepalive_interval):
            if self.client_count == 0:
                continue
            self._broadcast(ka)

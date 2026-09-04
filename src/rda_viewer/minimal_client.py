#!/usr/bin/env python3
"""Minimal raw-socket RDA client — connect, parse, print blocks and markers.

Stdlib + the ``mock_rda`` package only. It reuses the shared ``RDAFramer`` /
``parse_message`` so the protocol is parsed exactly once, in one place. Run it
against a live mock server::

    python -m rda_viewer.minimal_client --host 127.0.0.1 --port 51244

The :class:`RDAClient` class is also imported by the test suite as the in-repo
reference client (see ``tests/test_file_source_exact.py``).
"""

from __future__ import annotations

import argparse
import socket

from mock_rda.protocol import MsgType, RDAFramer, parse_message


class RDAClient:
    """Tiny blocking RDA client yielding parsed ``(msg_type, fields)`` messages."""

    def __init__(self, host: str = "127.0.0.1", port: int = 51244, timeout: float = 5.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._framer = RDAFramer()
        self.n_channels: int | None = None
        self.sample_rate: float | None = None
        self.channel_names: list[str] | None = None
        self.resolutions = None

    def messages(self):
        """Yield ``(msg_type, fields)`` for every message until the peer closes."""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                # timeout, or the socket was closed (e.g. on shutdown) -> stop.
                return
            if not chunk:
                return
            for raw in self._framer.feed(chunk):
                mtype, fields = parse_message(raw, self.n_channels)
                if mtype == MsgType.START:
                    self.n_channels = fields["n_channels"]
                    self.sample_rate = fields["sample_rate"]
                    self.channel_names = fields["channel_names"]
                    self.resolutions = fields["resolutions"]
                yield mtype, fields

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--max-blocks", type=int, default=0, help="stop after N data blocks (0 = run)")
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="socket timeout in seconds; raise it against a real Recorder that "
        "may sit idle between connect and Monitor mode",
    )
    args = ap.parse_args()

    client = RDAClient(args.host, args.port, timeout=args.timeout)
    n_blocks = 0
    try:
        for mtype, fields in client.messages():
            if mtype == MsgType.START:
                print(
                    f"START: {fields['n_channels']} channels @ {fields['sample_rate']:g} Hz\n"
                    f"  names: {fields['channel_names']}"
                )
            elif mtype == MsgType.DATA32:
                n_blocks += 1
                line = (
                    f"DATA32 block={fields['n_block']} nPoints={fields['n_points']}"
                    f" markers={len(fields['markers'])}"
                )
                for m in fields["markers"]:
                    line += (
                        f"\n  marker @rel={m['n_position']} "
                        f"type={m['type']!r} desc={m['description']!r} "
                        f"points={m['n_points']} chan={m['n_channel']}"
                    )
                print(line)
                if args.max_blocks and n_blocks >= args.max_blocks:
                    break
            elif mtype == MsgType.STOP:
                print("STOP")
            elif mtype == MsgType.KEEP_ALIVE:
                print("KEEP_ALIVE")
            else:
                # Real Recorder also emits NEWSTATE (5) and INFO (9), which the mock
                # server does not. Dump them so a live capture can be inspected.
                payload = fields["payload"]
                name = MsgType(mtype).name if mtype in set(MsgType) else "UNKNOWN"
                print(f"{name}({mtype}): {len(payload)} bytes payload\n  {payload[:64].hex(' ')}")
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    main()

"""User-facing connection errors for the rda_viewer command-line tools.

All viewers share the same friendly messages so a missing RDA stream gives the
same actionable hint regardless of which entry point the user ran.
"""

from __future__ import annotations

import socket
import sys

# Exit codes for the CLI viewers: 2 = cannot connect, 3 = connected but no data.
EXIT_CANNOT_CONNECT = 2
EXIT_NO_DATA = 3


class RDAConnectionError(ConnectionError):
    """The TCP connection to the RDA port could not be opened."""


class RDATimeoutError(TimeoutError):
    """The TCP connection opened, but no RDA START/data arrived in time."""


def friendly_connect_message(host: str, port: int) -> str:
    """Actionable message for a refused or unreachable RDA port."""
    return (
        f"Could not connect to the RDA stream at {host}:{port}.\n"
        "\n"
        "No BrainVision Recorder appears to be streaming there. If the amplifier\n"
        "is not connected, you can develop against the bundled mock stream:\n"
        "\n"
        f"    mock-rda synth --port {port}            # synthetic stream (32 ch, 5 kHz)\n"
        f"    mock-rda file RECORDING.vhdr --loop --port {port}   # replay a recording\n"
        "\n"
        "Then re-run this viewer. (See also: rda-viewer --help)"
    )


def friendly_no_start_message(host: str, port: int) -> str:
    """Actionable message for an open TCP socket that never sends RDA data."""
    return (
        f"Connected to {host}:{port}, but the RDA stream sent no START/data within the timeout.\n"
        "\n"
        "If you are using a real BrainVision Recorder, make sure it is in Monitor\n"
        "or impedance mode with RDA streaming enabled. The bundled mock starts\n"
        "streaming immediately:\n"
        "\n"
        f"    mock-rda synth --port {port}\n"
        f"    mock-rda file RECORDING.vhdr --loop --port {port}\n"
        "\n"
        "Re-run this viewer, or raise --timeout if the Recorder needs more time."
    )


def connect_client(host: str, port: int, timeout: float) -> socket.socket:
    """Open a blocking TCP connection, re-raising OSErrors as RDAConnectionError."""
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise RDAConnectionError(friendly_connect_message(host, port)) from exc


def open_client_or_exit(host: str, port: int, timeout: float):
    """Open an :class:`RDAClient` for the CLI, exiting cleanly on connect failure.

    The :class:`RDAClient` class is imported lazily to avoid an import cycle
    (``errors`` is imported by ``minimal_client``, which defines ``RDAClient``).
    """
    from .minimal_client import RDAClient

    try:
        return RDAClient(host, port, timeout=timeout)
    except RDAConnectionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_CONNECT) from exc

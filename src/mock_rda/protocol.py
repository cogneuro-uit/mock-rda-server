"""Pure RDA serialization — no sockets, no threads, fully unit-testable.

Clean-room re-implementation of the Brain Products Remote Data Access wire
format from the published field layout. All integers are little-endian.

Generic message header (``<16sLL``):

* ``GUID``  : 16 raw bytes
* ``nSize`` : uint32, total message size in bytes including the header
* ``nType`` : uint32, message type (see :class:`MsgType`)
"""

from __future__ import annotations

import struct
from enum import IntEnum

import numpy as np

from .markers import Marker

#: Canonical RDA protocol GUID (16 raw bytes), identical for every message.
GUID = bytes.fromhex("8E45584396C9864CAF4A98BBF6C91450")

# Generic header: GUID (16 bytes), nSize (uint32), nType (uint32).
_HEADER = struct.Struct("<16sII")
HEADER_SIZE = _HEADER.size  # 24

# START fixed prefix: nChannels (uint32), dSamplingInterval (double, µs/sample).
_START_PREFIX = struct.Struct("<Id")

# DATA32 fixed prefix: nBlock, nPoints, nMarkers (all uint32).
_DATA_PREFIX = struct.Struct("<III")

# Marker fixed prefix: nSize (uint32), nPosition (int32), nPoints (uint32), nChannel (int32).
_MARKER_PREFIX = struct.Struct("<IiIi")

#: Default text encoding for START channel names (Recorder uses cp1252 here).
START_NAME_ENCODING = "cp1252"


class MsgType(IntEnum):
    """RDA message type codes (the ``nType`` header field)."""

    START = 1
    DATA16 = 2
    STOP = 3
    DATA32 = 4
    NEWSTATE = 5
    INFO = 9
    KEEP_ALIVE = 10000


class RDAError(ValueError):
    """Raised when a buffer cannot be parsed as a valid RDA message."""


# --------------------------------------------------------------------------- #
# Header helpers
# --------------------------------------------------------------------------- #
def _pack_header(n_type: int, payload_len: int) -> bytes:
    return _HEADER.pack(GUID, HEADER_SIZE + payload_len, int(n_type))


def _message(n_type: int, payload: bytes = b"") -> bytes:
    return _pack_header(n_type, len(payload)) + payload


# --------------------------------------------------------------------------- #
# Channel-name codec (tested + swappable, per the START quirk in the spec)
# --------------------------------------------------------------------------- #
def encode_channel_names(names: list[str], encoding: str = START_NAME_ENCODING) -> bytes:
    """Encode channel names as ``name0\\0name1\\0...nameN\\0`` (trailing NUL)."""
    return b"".join(name.encode(encoding) + b"\0" for name in names)


def decode_channel_names(blob: bytes, encoding: str = START_NAME_ENCODING) -> list[str]:
    """Inverse of :func:`encode_channel_names` (ignores a final empty token)."""
    parts = blob.split(b"\0")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    return [p.decode(encoding) for p in parts]


# --------------------------------------------------------------------------- #
# Encoders
# --------------------------------------------------------------------------- #
def encode_start(
    channel_names: list[str],
    sample_rate: float,
    resolutions,
    name_encoding: str = START_NAME_ENCODING,
) -> bytes:
    """Serialize a START (type 1) message.

    Parameters
    ----------
    channel_names:
        One name per channel.
    sample_rate:
        Samples per second; stored as ``dSamplingInterval = 1e6 / sample_rate`` µs.
    resolutions:
        Per-channel µV-per-LSB scaling (length == ``len(channel_names)``).
    name_encoding:
        Text codec for the names array (default cp1252, matching Recorder).
    """
    n_channels = len(channel_names)
    resolutions = np.asarray(resolutions, dtype="<f8")
    if resolutions.shape != (n_channels,):
        raise ValueError(
            f"resolutions length {resolutions.size} != n_channels {n_channels}"
        )
    sampling_interval = 1e6 / sample_rate
    payload = _START_PREFIX.pack(n_channels, sampling_interval)
    payload += resolutions.tobytes()
    payload += encode_channel_names(channel_names, name_encoding)
    return _message(MsgType.START, payload)


def _encode_marker(marker: Marker, block_start_sample: int) -> bytes:
    type_b = marker.type.encode("utf-8") + b"\0"
    desc_b = marker.description.encode("utf-8") + b"\0"
    n_position = marker.sample - block_start_sample
    total = _MARKER_PREFIX.size + len(type_b) + len(desc_b)
    return (
        _MARKER_PREFIX.pack(total, n_position, marker.points, marker.channel)
        + type_b
        + desc_b
    )


def encode_data32(
    block_idx: int,
    data_f32_ch_major,
    markers: list[Marker] | None,
    block_start_sample: int,
) -> bytes:
    """Serialize a DATA32 (type 4) message.

    Parameters
    ----------
    block_idx:
        Monotonically increasing block counter (``nBlock``).
    data_f32_ch_major:
        ``float32`` array shaped ``[n_channels, n_points]`` (the caller's/source's
        convention). Sent over the wire **multiplexed by sample** (all channels
        for point 0, then all channels for point 1, ...), matching real
        Recorder's RDA stream and the ``.eeg`` file's own MULTIPLEXED layout.
    markers:
        Markers carrying **absolute** sample positions; converted to
        block-relative ``nPosition = marker.sample - block_start_sample`` here.
    block_start_sample:
        Absolute sample index of this block's first sample.
    """
    markers = markers or []
    data = np.asarray(data_f32_ch_major, dtype="<f4")
    if data.ndim != 2:
        raise ValueError(f"data must be 2-D [channels, points], got shape {data.shape}")
    n_points = data.shape[1]

    payload = bytearray()
    payload += _DATA_PREFIX.pack(block_idx, n_points, len(markers))
    payload += np.ascontiguousarray(data.T).tobytes(order="C")  # multiplexed by sample
    for m in markers:
        payload += _encode_marker(m, block_start_sample)
    return _message(MsgType.DATA32, bytes(payload))


def encode_stop() -> bytes:
    """Serialize a STOP (type 3) message (header only)."""
    return _message(MsgType.STOP)


def encode_keepalive() -> bytes:
    """Serialize a KEEP_ALIVE (type 10000) message (header only)."""
    return _message(MsgType.KEEP_ALIVE)


# --------------------------------------------------------------------------- #
# Decoders
# --------------------------------------------------------------------------- #
def decode_start(buf: bytes) -> dict:
    """Decode a START message buffer into a dict of fields."""
    n_type, payload = _split_payload(buf, MsgType.START)
    n_channels, sampling_interval = _START_PREFIX.unpack_from(payload, 0)
    off = _START_PREFIX.size
    res = np.frombuffer(payload, dtype="<f8", count=n_channels, offset=off)
    off += n_channels * 8
    names = decode_channel_names(payload[off:])
    return {
        "type": MsgType.START,
        "n_channels": n_channels,
        "sampling_interval": sampling_interval,
        "sample_rate": 1e6 / sampling_interval,
        "resolutions": res.copy(),
        "channel_names": names,
    }


def _decode_markers(payload: bytes, offset: int, n_markers: int) -> list[dict]:
    markers: list[dict] = []
    pos = offset
    for _ in range(n_markers):
        total, n_position, n_points, n_channel = _MARKER_PREFIX.unpack_from(payload, pos)
        body = payload[pos + _MARKER_PREFIX.size : pos + total]
        type_b, _, rest = body.partition(b"\0")
        desc_b, _, _ = rest.partition(b"\0")
        markers.append(
            {
                "n_position": n_position,
                "n_points": n_points,
                "n_channel": n_channel,
                "type": type_b.decode("utf-8"),
                "description": desc_b.decode("utf-8"),
            }
        )
        pos += total
    return markers


def decode_data32(buf: bytes, n_channels: int) -> dict:
    """Decode a DATA32 message buffer.

    ``n_channels`` must be supplied (the block itself only stores ``nPoints``);
    it comes from the preceding START message. The wire payload is multiplexed
    by sample (see :func:`encode_data32`); this reshapes back to ``[n_channels,
    n_points]`` for callers.
    """
    n_type, payload = _split_payload(buf, MsgType.DATA32)
    n_block, n_points, n_markers = _DATA_PREFIX.unpack_from(payload, 0)
    off = _DATA_PREFIX.size
    count = n_channels * n_points
    data = np.frombuffer(payload, dtype="<f4", count=count, offset=off).reshape(
        n_points, n_channels
    ).T
    off += count * 4
    markers = _decode_markers(payload, off, n_markers)
    return {
        "type": MsgType.DATA32,
        "n_block": n_block,
        "n_points": n_points,
        "data": data.copy(),
        "markers": markers,
    }


def _split_payload(buf: bytes, expected: MsgType | None = None) -> tuple[int, bytes]:
    if len(buf) < HEADER_SIZE:
        raise RDAError(f"buffer too short for header: {len(buf)} < {HEADER_SIZE}")
    guid, n_size, n_type = _HEADER.unpack_from(buf, 0)
    if guid != GUID:
        raise RDAError("bad GUID")
    if n_size != len(buf):
        raise RDAError(f"nSize {n_size} != buffer length {len(buf)}")
    if expected is not None and n_type != expected:
        raise RDAError(f"expected message type {expected}, got {n_type}")
    return n_type, buf[HEADER_SIZE:]


def parse_message(buf: bytes, n_channels: int | None = None) -> tuple[int, dict]:
    """Parse a complete message buffer into ``(msg_type, fields)``.

    DATA32 requires ``n_channels`` (from START). For other types it is ignored.
    """
    guid, n_size, n_type = _HEADER.unpack_from(buf, 0)
    if guid != GUID:
        raise RDAError("bad GUID")
    if n_type == MsgType.START:
        return n_type, decode_start(buf)
    if n_type == MsgType.DATA32:
        if n_channels is None:
            raise RDAError("decoding DATA32 requires n_channels from START")
        return n_type, decode_data32(buf, n_channels)
    if n_type in (MsgType.STOP, MsgType.KEEP_ALIVE):
        return n_type, {"type": MsgType(n_type)}
    return n_type, {"type": n_type, "payload": buf[HEADER_SIZE:]}


# --------------------------------------------------------------------------- #
# Streaming framer (handles TCP fragmentation)
# --------------------------------------------------------------------------- #
class RDAFramer:
    """Reassemble length-prefixed RDA messages from a TCP byte stream.

    Feed arbitrary chunks via :meth:`feed`; it yields complete message buffers,
    each ready for :func:`parse_message`.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes):
        """Append ``chunk`` and yield every complete message now available."""
        self._buf += chunk
        while True:
            if len(self._buf) < HEADER_SIZE:
                return
            guid, n_size, n_type = _HEADER.unpack_from(self._buf, 0)
            if guid != GUID:
                raise RDAError("bad GUID in stream")
            if n_size < HEADER_SIZE:
                raise RDAError(f"implausible nSize {n_size}")
            if len(self._buf) < n_size:
                return
            msg = bytes(self._buf[:n_size])
            del self._buf[:n_size]
            yield msg

    @property
    def pending_bytes(self) -> int:
        """Number of buffered bytes not yet forming a complete message."""
        return len(self._buf)

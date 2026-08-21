"""Independent cross-test: a from-scratch RDA parser that shares **no code** with
``mock_rda.protocol`` decodes the live stream, proving our emitted bytes parse
under an implementation written only from the documented field layout.

This is the runnable stand-in for the §6b "third-party parser" cross-test: the
package named there (``mne-lsl``) is an LSL library and ships no RDA client, so
this in-test independent parser provides the equivalent byte-layout conformance
check in CI. Ground truth for the samples/markers is the raw fixture files, not
our own reader. (Per §6b: no timing assertions live here.)
"""

from __future__ import annotations

import socket
import struct

import numpy as np

from mock_rda.server import Server
from mock_rda.sources import FileSource

_GUID = bytes.fromhex("8E45584396C9864CAF4A98BBF6C91450")
_HDR = struct.Struct("<16sII")


class IndependentRDAParser:
    """A minimal RDA parser built only from the published layout (no shared code)."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.n_channels = None

    def feed(self, chunk: bytes):
        self.buf += chunk
        while len(self.buf) >= _HDR.size:
            guid, n_size, n_type = _HDR.unpack_from(self.buf, 0)
            assert guid == _GUID, "GUID mismatch"
            if len(self.buf) < n_size:
                return
            payload = bytes(self.buf[_HDR.size:n_size])
            del self.buf[:n_size]
            yield n_type, self._parse(n_type, payload)

    def _parse(self, n_type: int, payload: bytes):
        if n_type == 1:  # START
            n_ch, interval = struct.unpack_from("<Id", payload, 0)
            off = 12
            res = struct.unpack_from(f"<{n_ch}d", payload, off)
            off += 8 * n_ch
            names = payload[off:].split(b"\x00")
            names = [n.decode("cp1252") for n in names if n != b""]
            self.n_channels = n_ch
            return {"n_channels": n_ch, "sample_rate": 1e6 / interval,
                    "resolutions": res, "names": names}
        if n_type == 4:  # DATA32
            n_block, n_points, n_markers = struct.unpack_from("<III", payload, 0)
            off = 12
            count = self.n_channels * n_points
            # multiplexed by sample: pt0[ch0..chN], pt1[ch0..chN], ...
            data = np.frombuffer(payload, "<f4", count, off).reshape(n_points, self.n_channels).T
            off += 4 * count
            markers = []
            for _ in range(n_markers):
                size, pos, mpts, chan = struct.unpack_from("<IiIi", payload, off)
                body = payload[off + 16:off + size]
                t, _, rest = body.partition(b"\x00")
                d, _, _ = rest.partition(b"\x00")
                markers.append((t.decode("utf-8"), d.decode("utf-8"), pos, mpts, chan))
                off += size
            return {"n_block": n_block, "n_points": n_points,
                    "data": data.copy(), "markers": markers}
        return {"raw": payload}


def test_independent_parser_matches_raw_fixture(fixture_vhdr):
    src = FileSource(fixture_vhdr, block_points=500)
    srv = Server(src, host="127.0.0.1", port=0, start_on_connect=True)
    port = srv.start()

    sock = socket.create_connection(("127.0.0.1", port), timeout=15)
    parser = IndependentRDAParser()

    start = None
    blocks = []
    markers = []
    block_start = 0
    done = False
    try:
        while not done:
            chunk = sock.recv(65536)
            if not chunk:
                break
            for n_type, msg in parser.feed(chunk):
                if n_type == 1:
                    start = msg
                elif n_type == 4:
                    blocks.append(msg["data"])
                    for t, d, pos, _pts, _chan in msg["markers"]:
                        markers.append((t, d, block_start + pos))
                    block_start += msg["n_points"]
                elif n_type == 3:  # STOP
                    done = True
                    break
    finally:
        sock.close()
        srv.stop()

    # Channel config from START vs the .vhdr (via the file source meta).
    assert start["names"] == src.channel_names
    assert start["n_channels"] == src.n_channels
    assert np.isclose(start["sample_rate"], src.sample_rate)
    assert np.allclose(start["resolutions"], src.resolutions)

    # Samples vs an independent raw read of the .eeg.
    received = np.concatenate(blocks, axis=1)
    raw = np.fromfile(fixture_vhdr.with_suffix(".eeg"), dtype="<f4").reshape(-1, src.n_channels).T
    assert np.array_equal(received, raw)

    # Markers vs the parsed .vmrk.
    expected = [(m.type, m.description, m.sample) for m in src.markers]
    assert sorted(markers) == sorted(expected)

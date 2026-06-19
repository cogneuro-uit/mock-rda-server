"""§6a.3 file exactness: stream the fixture triplet, capture with the in-repo
reference client, and assert received samples == file and markers == .vmrk."""

from __future__ import annotations

import numpy as np
from minimal_client import RDAClient

from mock_rda.protocol import MsgType
from mock_rda.server import Server
from mock_rda.sources import FileSource

BLOCK_POINTS = 500  # divides the 20000-sample fixture evenly


def _stream_fixture(vhdr):
    src = FileSource(vhdr, block_points=BLOCK_POINTS)
    srv = Server(src, host="127.0.0.1", port=0, start_on_connect=True)
    port = srv.start()
    cli = RDAClient("127.0.0.1", port, timeout=15)

    blocks: list[np.ndarray] = []
    markers: list[tuple[str, str, int]] = []
    start_fields = None
    block_start = 0
    try:
        for mtype, f in cli.messages():
            if mtype == MsgType.START:
                start_fields = f
            elif mtype == MsgType.DATA32:
                blocks.append(f["data"])
                for m in f["markers"]:
                    markers.append((m["type"], m["description"], block_start + m["n_position"]))
                block_start += f["n_points"]
            elif mtype == MsgType.STOP:
                break
    finally:
        cli.close()
        srv.stop()
    return src, start_fields, blocks, markers


def test_file_source_exact(fixture_vhdr):
    src, start_fields, blocks, markers = _stream_fixture(fixture_vhdr)

    # START config matches the header.
    assert start_fields["channel_names"] == src.channel_names
    assert start_fields["n_channels"] == src.n_channels
    assert np.isclose(start_fields["sample_rate"], src.sample_rate)
    assert np.allclose(start_fields["resolutions"], src.resolutions)

    # Samples: reconstruct and compare against an independent raw read of the .eeg.
    received = np.concatenate(blocks, axis=1)
    eeg = fixture_vhdr.with_suffix(".eeg")
    raw = np.fromfile(eeg, dtype="<f4").reshape(-1, src.n_channels).T  # MULTIPLEXED
    assert received.shape == raw.shape
    assert np.array_equal(received, raw)

    # Markers: positions/types/descriptions match the parsed .vmrk exactly.
    expected = [(m.type, m.description, m.sample) for m in src.markers]
    assert sorted(markers) == sorted(expected)

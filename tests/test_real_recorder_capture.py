"""Lab conformance regression: decode a golden byte capture from a **live**
BrainVision Recorder (RDA2/float, 32 ch @ 50 kHz) and assert the result is
physiologically sane.

Captured 2026-08-21 against real hardware. This is the runnable guard for the
README's "Lab conformance" checklist: it caught a real bug where DATA32 was
assumed channel-major but Recorder actually sends it multiplexed by sample
(matching the .eeg file's own MULTIPLEXED layout) -- decoding real bytes with
the wrong order produced ~1e7-magnitude garbage instead of a plausible EEG
signal. Only START + one DATA32 message are captured (no patient data of
concern -- amplitude/statistics only, not identifiable).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mock_rda import protocol as p

FIXTURE = Path(__file__).parent / "data" / "real_recorder_capture.rda"


def _messages(buf: bytes):
    framer = p.RDAFramer()
    n_channels = None
    for raw in framer.feed(buf):
        mtype, fields = p.parse_message(raw, n_channels)
        if mtype == p.MsgType.START:
            n_channels = fields["n_channels"]
        yield mtype, fields


def test_real_capture_start_is_sane():
    buf = FIXTURE.read_bytes()
    mtype, start = next(_messages(buf))
    assert mtype == p.MsgType.START
    assert start["n_channels"] == 32
    assert "C3" in start["channel_names"]
    assert "EMG" in start["channel_names"]
    assert np.isclose(start["sample_rate"], 50000.0)
    # BrainVision resolutions are a small per-channel µV/LSB scale, not huge or zero.
    assert np.all((start["resolutions"] > 0) & (start["resolutions"] < 10.0))


def test_real_capture_multiplexed_decoding_is_far_smoother_than_channel_major():
    """The regression this guards against: decoding multiplexed-by-sample bytes
    as if they were channel-major scrambles unrelated channels together. Some
    electrodes in this particular capture were evidently disconnected/floating
    (several sit at near-identical multi-million-count railed baselines), so we
    can't assert an absolute amplitude bound session-independently -- but *any*
    real signal, railed or not, is smooth sample-to-sample within its own
    channel. Scrambling channels together via the wrong reshape destroys that
    smoothness regardless of which channels happen to be floating, so comparing
    the two hypotheses on these exact bytes is a robust, session-independent check.
    """
    buf = FIXTURE.read_bytes()
    framer = p.RDAFramer()
    n_channels = None
    for raw in framer.feed(buf):
        mtype, fields = p.parse_message(raw, n_channels)
        if mtype == p.MsgType.START:
            n_channels = fields["n_channels"]
        elif mtype == p.MsgType.DATA32:
            payload = raw[p.HEADER_SIZE:]
            n_points = int(np.frombuffer(payload[4:8], dtype="<u4")[0])
            flat = np.frombuffer(payload, dtype="<f4", count=n_channels * n_points, offset=12)

    channel_major = flat.reshape(n_channels, n_points)
    sample_major = flat.reshape(n_points, n_channels).T

    def smoothness(arr):
        return np.median(np.abs(np.diff(arr, axis=1)))

    # Correctly decoded (multiplexed/sample-major) data is orders of magnitude
    # smoother than the same bytes misread as channel-major.
    assert smoothness(sample_major) * 10 < smoothness(channel_major)
    assert np.array_equal(sample_major, p.decode_data32(raw, n_channels)["data"])

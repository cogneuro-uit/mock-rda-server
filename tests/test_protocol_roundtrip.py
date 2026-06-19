"""§2 acceptance: encode_* -> decode_* is identity over randomized inputs."""

from __future__ import annotations

import numpy as np
import pytest

from mock_rda import protocol as p
from mock_rda.markers import Marker

UNICODE_DESCS = ["S  1", "R 15", "naïve μV", "日本語", "", "comma,inside", "tab\tend"]


@pytest.mark.parametrize("seed", range(20))
def test_start_roundtrip(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 40))
    names = [f"Ch{i}-{rng.integers(0, 99)}" for i in range(n)]
    sample_rate = float(rng.choice([250, 500, 1000, 5000, 25000]))
    res = rng.uniform(0.001, 2.0, size=n)
    msg = p.encode_start(names, sample_rate, res)
    out = p.decode_start(msg)
    assert out["channel_names"] == names
    assert out["n_channels"] == n
    assert np.isclose(out["sample_rate"], sample_rate)
    assert np.allclose(out["resolutions"], res)


@pytest.mark.parametrize("seed", range(30))
def test_data32_roundtrip(seed):
    rng = np.random.default_rng(seed + 100)
    n_ch = int(rng.integers(1, 16))
    n_pts = int(rng.integers(1, 64))
    block_start = int(rng.integers(0, 1_000_000))
    block_idx = int(rng.integers(0, 2**31))
    data = rng.standard_normal((n_ch, n_pts)).astype(np.float32)

    n_mk = int(rng.integers(0, 5))
    markers = []
    for _ in range(n_mk):
        markers.append(
            Marker(
                sample=block_start + int(rng.integers(0, max(1, n_pts))),
                type=str(rng.choice(["Stimulus", "Response", "New Segment"])),
                description=str(rng.choice(UNICODE_DESCS)),
                points=int(rng.integers(1, 10)),
                channel=int(rng.choice([-1, 0, 1, 5])),
            )
        )

    msg = p.encode_data32(block_idx, data, markers, block_start)
    out = p.decode_data32(msg, n_channels=n_ch)
    assert out["n_block"] == block_idx
    assert out["n_points"] == n_pts
    assert np.array_equal(out["data"], data)
    assert len(out["markers"]) == len(markers)
    for src, dec in zip(markers, out["markers"], strict=True):
        assert dec["n_position"] == src.sample - block_start
        assert dec["n_points"] == src.points
        assert dec["n_channel"] == src.channel
        assert dec["type"] == src.type
        assert dec["description"] == src.description


def test_empty_marker_list():
    data = np.zeros((3, 5), dtype=np.float32)
    out = p.decode_data32(p.encode_data32(0, data, [], 0), n_channels=3)
    assert out["markers"] == []


def test_stop_and_keepalive_headers():
    for msg, mtype in [(p.encode_stop(), p.MsgType.STOP),
                       (p.encode_keepalive(), p.MsgType.KEEP_ALIVE)]:
        t, fields = p.parse_message(msg)
        assert t == mtype
        assert len(msg) == p.HEADER_SIZE


def test_framer_reassembles_across_arbitrary_fragmentation():
    data = np.arange(2 * 3, dtype=np.float32).reshape(2, 3)
    blob = (
        p.encode_start(["a", "b"], 1000.0, [1.0, 1.0])
        + p.encode_data32(0, data, [Marker(1, "Stimulus", "S  1")], 0)
        + p.encode_stop()
        + p.encode_keepalive()
    )
    for step in (1, 3, 7, 13, 50, len(blob)):
        framer = p.RDAFramer()
        msgs = []
        for i in range(0, len(blob), step):
            msgs.extend(framer.feed(blob[i : i + step]))
        assert framer.pending_bytes == 0
        types = [p.parse_message(m, n_channels=2)[0] for m in msgs]
        assert types == [p.MsgType.START, p.MsgType.DATA32,
                         p.MsgType.STOP, p.MsgType.KEEP_ALIVE]

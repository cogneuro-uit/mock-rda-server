"""Server stream counters and the GUI's pure formatting/stat helpers (no Tk)."""

from __future__ import annotations

import time

from mock_rda.gui import (
    describe_source,
    format_bytes,
    format_count,
    format_duration,
    live_stats,
    stream_settings,
)
from mock_rda.markers import AT_NEXT, Marker
from mock_rda.protocol import MsgType
from mock_rda.server import Server
from mock_rda.sources import SyntheticSource
from rda_viewer.minimal_client import RDAClient


def test_format_helpers():
    assert format_duration(0) == "00:00"
    assert format_duration(93.4) == "01:33"
    assert format_duration(3723) == "1:02:03"
    assert format_duration(-5) == "00:00"

    assert format_bytes(512) == "512 B"
    assert format_bytes(2048) == "2.0 KiB"
    assert format_bytes(5 * 1024 ** 2) == "5.0 MiB"

    assert format_count(415000) == "415 000"


def test_stream_settings_rows():
    src = SyntheticSource(n_channels=32, sample_rate=5000.0, block_points=20)
    server = Server(src, host="127.0.0.1", port=51244)
    rows = dict(stream_settings(server, control_port=51299))
    assert rows["source"] == "synthetic"
    assert rows["channels"] == "32"
    assert rows["sample rate"] == "5000 Hz"
    assert rows["block"] == "20 pts / 4 ms"
    assert rows["RDA port"] == "51244"
    assert rows["control port"] == "51299"
    # control port is optional
    assert "control port" not in dict(stream_settings(server))


def test_describe_source_file_vs_synth(tmp_path):
    class FakeFile:
        vhdr_path = tmp_path / "rec.vhdr"
        loop = False

    assert describe_source(FakeFile()) == "rec.vhdr"
    FakeFile.loop = True
    assert describe_source(FakeFile()) == "rec.vhdr (loop)"
    assert describe_source(SyntheticSource(n_channels=2, sample_rate=100.0,
                                           block_points=10)) == "synthetic"


def test_live_stats_before_streaming_are_zeroed():
    src = SyntheticSource(n_channels=8, sample_rate=1000.0, block_points=10)
    server = Server(src, host="127.0.0.1", port=0)
    rows = dict(live_stats(server))
    assert rows["elapsed"] == "00:00"
    assert rows["blocks"] == "0"
    assert rows["markers"] == "0"
    assert rows["data"] == "0 B"


def test_server_counters_advance_while_streaming():
    bp = 100
    src = SyntheticSource(n_channels=4, sample_rate=1000.0, block_points=bp)
    server = Server(src, host="127.0.0.1", port=0)
    port = server.start()
    try:
        client = RDAClient("127.0.0.1", port)
        msgs = client.messages()
        seen = 0
        for mtype, _f in msgs:
            if mtype == MsgType.DATA32:
                seen += 1
                if seen >= 5:
                    break
        client.close()
        deadline = time.monotonic() + 2
        while server.blocks_streamed < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        server.stop()

    assert server.blocks_streamed >= 5
    assert server.samples_streamed == server.blocks_streamed * bp
    assert server.bytes_streamed == server.samples_streamed * 4 * 4  # 4 ch × float32
    assert server.stream_started_at is not None
    assert server.stream_seconds > 0

    rows = dict(live_stats(server))
    assert rows["data"] != "0 B"
    # Stream time tracks the block schedule, so drift stays small.
    drift_ms = float(rows["drift"].split()[0])
    assert abs(drift_ms) < 500, rows["drift"]


def test_injected_markers_counted():
    src = SyntheticSource(n_channels=2, sample_rate=1000.0, block_points=50)
    server = Server(src, host="127.0.0.1", port=0)
    port = server.start()
    try:
        client = RDAClient("127.0.0.1", port)
        msgs = client.messages()

        def wait_blocks(n):
            got = 0
            for mtype, _f in msgs:
                if mtype == MsgType.DATA32:
                    got += 1
                    if got >= n:
                        return

        wait_blocks(2)
        before = server.markers_streamed
        server.inject_burst(Marker(sample=AT_NEXT), 5, 20.0)
        wait_blocks(12)  # burst spans 80 ms = 2 blocks at 50 ms/block
        client.close()
    finally:
        server.stop()
    assert server.markers_streamed - before >= 5

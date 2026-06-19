"""§6a.5 timing: inter-block interval ≈ nominal, injected-trigger latency ≤ one
block + tolerance. These are the authoritative timing assertions (the mne-lsl
cross-test must NOT carry timing assertions — see §6b caveat)."""

from __future__ import annotations

import time

from minimal_client import RDAClient

from mock_rda.markers import AT_NEXT, Marker
from mock_rda.protocol import MsgType
from mock_rda.server import Server
from mock_rda.sources import SyntheticSource


def _make_server(rate=2000.0, block_points=100):
    src = SyntheticSource(n_channels=4, sample_rate=rate, block_points=block_points,
                          response_amp=0.0, seed=7)
    srv = Server(src, host="127.0.0.1", port=0, start_on_connect=True)
    return srv, block_points / rate


def test_inter_block_interval_matches_nominal():
    srv, nominal = _make_server()
    port = srv.start()
    cli = RDAClient("127.0.0.1", port, timeout=10)
    arrivals: list[float] = []
    try:
        for mtype, _f in cli.messages():
            if mtype == MsgType.DATA32:
                arrivals.append(time.monotonic())
                if len(arrivals) >= 40:
                    break
    finally:
        cli.close()
        srv.stop()

    diffs = [b - a for a, b in zip(arrivals[5:], arrivals[6:], strict=False)]
    mean = sum(diffs) / len(diffs)
    # Generous bound for noisy CI, but still pins the pacing to the nominal rate.
    assert abs(mean - nominal) < 0.4 * nominal, (
        f"mean inter-block {mean*1e3:.2f} ms vs nominal {nominal*1e3:.2f} ms"
    )


def test_injected_trigger_latency_within_one_block():
    rate, block_points = 2000.0, 100
    srv, nominal = _make_server(rate, block_points)
    port = srv.start()
    cli = RDAClient("127.0.0.1", port, timeout=10)

    t_inject = None
    latency = None
    n_data = 0
    try:
        for mtype, f in cli.messages():
            if mtype != MsgType.DATA32:
                continue
            n_data += 1
            if n_data == 10:
                t_inject = time.monotonic()
                srv.inject(Marker(sample=AT_NEXT, type="Stimulus", description="S  1"))
            if t_inject is not None:
                for m in f["markers"]:
                    if m["type"] == "Stimulus":
                        latency = time.monotonic() - t_inject
                        break
            if latency is not None:
                break
    finally:
        cli.close()
        srv.stop()

    assert latency is not None, "injected marker never arrived"
    # Worst-case quantization is one block; allow one block of transport slack.
    assert latency <= 2 * nominal + 0.02, f"latency {latency*1e3:.2f} ms > budget"


def test_scheduler_reports_low_jitter():
    srv, nominal = _make_server()
    port = srv.start()
    cli = RDAClient("127.0.0.1", port, timeout=10)
    n = 0
    try:
        for mtype, _f in cli.messages():
            if mtype == MsgType.DATA32:
                n += 1
                if n >= 30:
                    break
    finally:
        cli.close()
        srv.stop()
    # The absolute-deadline scheduler should keep mean jitter well under a block.
    assert srv.scheduler.mean_abs_jitter < nominal

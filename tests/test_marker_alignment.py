"""§6a.4 marker alignment: injected markers land in the right block at the
right block-relative offset, including across block boundaries."""

from __future__ import annotations

import threading

from minimal_client import RDAClient

from mock_rda.markers import AT_NEXT, InjectionQueue, Marker
from mock_rda.protocol import MsgType
from mock_rda.server import Server
from mock_rda.sources import SyntheticSource


def test_injection_queue_block_relative_offsets():
    q = InjectionQueue()
    bp = 200
    # Absolute samples chosen to straddle three blocks [0,200) [200,400) [400,600).
    for s in (0, 199, 200, 201, 450):
        q.inject(Marker(sample=s))
    q.inject(Marker(sample=AT_NEXT))  # "now" -> start of the very next drained block

    b0 = q.drain_for_block(0, bp)
    assert sorted((m.sample, m.sample - 0) for m in b0) == [(0, 0), (0, 0), (199, 199)]

    b1 = q.drain_for_block(200, bp)
    assert sorted((m.sample, m.sample - 200) for m in b1) == [(200, 0), (201, 1)]

    b2 = q.drain_for_block(400, bp)
    assert [(m.sample, m.sample - 400) for m in b2] == [(450, 50)]

    assert len(q) == 0


def test_burst_followers_resolved_relative_to_head():
    q = InjectionQueue()
    q.inject_burst(Marker(sample=AT_NEXT), [100, 200, 300, 400])
    # The AT_NEXT head resolves to the first drained block's start; followers
    # keep their exact spacing relative to that resolved sample.
    assert [m.sample for m in q.drain_for_block(1000, 100)] == [1000]
    assert len(q) == 4
    assert [m.sample for m in q.drain_for_block(1100, 100)] == [1100]
    assert [m.sample for m in q.drain_for_block(1200, 300)] == [1200, 1300, 1400]
    assert len(q) == 0


def test_burst_followers_within_same_block_emitted_at_once():
    q = InjectionQueue()
    q.inject_burst(Marker(sample=AT_NEXT), [10, 20])
    assert [m.sample for m in q.drain_for_block(0, 100)] == [0, 10, 20]
    assert len(q) == 0


def test_late_injection_is_not_dropped():
    q = InjectionQueue()
    q.inject(Marker(sample=50))  # already-passed sample
    out = q.drain_for_block(1000, 200)  # block starts well after sample 50
    assert len(out) == 1
    assert out[0].sample == 1000  # clamped to the current block start


def test_injected_markers_arrive_in_correct_block_e2e():
    bp = 100
    src = SyntheticSource(n_channels=2, sample_rate=2000.0, block_points=bp,
                          response_amp=0.0, seed=5)
    srv = Server(src, host="127.0.0.1", port=0)
    port = srv.start()
    cli = RDAClient("127.0.0.1", port, timeout=10)

    inject_samples = [600, 650, 700, 1000, 1001, 1099]
    received: list[tuple[int, int, str]] = []
    injected = threading.Event()

    try:
        for mtype, f in cli.messages():
            if mtype == MsgType.START:
                for s in inject_samples:
                    srv.inject(Marker(sample=s, type="Stimulus", description="S  1"))
                injected.set()
            elif mtype == MsgType.DATA32:
                for m in f["markers"]:
                    received.append((f["n_block"], m["n_position"], m["description"]))
                if f["n_block"] >= 12:
                    break
    finally:
        cli.close()
        srv.stop()

    # For constant-width blocks starting at sample 0: absolute = n_block*bp + nPos.
    got = {n_block * bp + n_pos for (n_block, n_pos, _) in received}
    for s in inject_samples:
        assert s in got, f"injected sample {s} missing from {sorted(got)}"
        # and the block-relative offset must be exact
        assert any(nb == s // bp and npos == s % bp for (nb, npos, _) in received)

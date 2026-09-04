#!/usr/bin/env python3
"""Print every marker the RDA stream delivers, with gaps between consecutive ones.

    python -m rda_viewer.dump_markers --host 127.0.0.1 --port 51244

Each line: absolute sample, time since the previous marker (ms), type, description.
A burst shows up as a cluster; a stimulator that emits up+down ramps shows pairs
separated by a fraction of a millisecond.
"""
import argparse
import sys

from mock_rda.protocol import MsgType

from .minimal_client import RDAClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    a = ap.parse_args()

    client = RDAClient(a.host, a.port)
    msgs = client.messages()
    for mtype, f in msgs:
        if mtype == MsgType.START:
            sfreq = f["sample_rate"]
            print(f"# connected: {len(f['channel_names'])} ch @ {sfreq:g} Hz", file=sys.stderr)
            break
    else:
        raise SystemExit("server closed before START")

    total = 0          # absolute sample of the current block's first sample
    prev = None        # absolute sample of the previous marker
    n = 0
    print(f"{'abs sample':>12} {'t (s)':>10} {'gap (ms)':>10}  type / description")
    try:
        for mtype, f in msgs:
            if mtype != MsgType.DATA32:
                continue
            for m in f["markers"]:
                s = total + m["n_position"]
                gap = "" if prev is None else f"{(s - prev) / sfreq * 1000:10.3f}"
                print(f"{s:12d} {s / sfreq:10.3f} {gap:>10}  "
                      f"{m['type']!r} / {m['description']!r}")
                prev = s
                n += 1
            total += f["n_points"]
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        print(f"# {n} markers seen", file=sys.stderr)


if __name__ == "__main__":
    main()

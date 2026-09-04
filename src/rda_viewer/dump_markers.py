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

from .errors import (
    EXIT_NO_DATA,
    RDATimeoutError,
    friendly_no_start_message,
    open_client_or_exit,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="socket timeout in seconds; raise it against a real Recorder that "
             "may sit idle before streaming",
    )
    args = ap.parse_args()

    client = open_client_or_exit(args.host, args.port, timeout=args.timeout)

    msgs = client.messages()
    try:
        for mtype, f in msgs:
            if mtype == MsgType.START:
                sfreq = f["sample_rate"]
                print(f"# connected: {len(f['channel_names'])} ch @ {sfreq:g} Hz", file=sys.stderr)
                break
        else:
            print("server closed before START", file=sys.stderr)
            raise SystemExit(EXIT_NO_DATA)
    except RDATimeoutError as exc:
        print(friendly_no_start_message(args.host, args.port), file=sys.stderr)
        raise SystemExit(EXIT_NO_DATA) from exc

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
    except RDATimeoutError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_NO_DATA) from exc
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        print(f"# {n} markers seen", file=sys.stderr)


if __name__ == "__main__":
    main()

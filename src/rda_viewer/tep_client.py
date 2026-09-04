#!/usr/bin/env python3
"""TMS-locked epoch viewer — redraw only on a TMS marker, show the post-TMS window.

Unlike ``rda_viewer.plot_client`` (a free-running rolling scope), this client stays idle
until a TMS/stimulus marker arrives, then captures the epoch around it and draws
a butterfly plot (all channels overlaid). The y-scale is set from the **bulk** of
the signal — a percentile range computed *after* a short blanking window — so the
large TMS artifact at onset is clipped out of frame instead of squashing the
evoked response you actually want to see.

Headless (recommended in a container with no usable display): write a PNG that is
rewritten on every TMS, and open/refresh it in your editor::

    # terminal A — a stimulus every 0.5 s with an evoked response
    mock-rda synth --channels 32 --rate 5000 --block-ms 4 --stim-period 0.5

    # terminal B
    python -m rda_viewer.tep_client --save /tmp/tep.png

Live window (needs a working X display)::

    python -m rda_viewer.tep_client --host 127.0.0.1 --port 51244

This is a debugging/eyeballing aid, not part of the test suite.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

from mock_rda.protocol import MsgType

from .minimal_client import RDAClient


def _is_tms(marker: dict, args) -> bool:
    """Decide whether a received marker should trigger an epoch."""
    if args.any_marker:
        return marker["type"] != "New Segment"
    if marker["type"] != args.marker_type:
        return False
    return args.marker_desc is None or args.marker_desc in marker["description"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--post-ms", type=float, default=50.0, help="window shown after TMS (ms)")
    ap.add_argument("--pre-ms", type=float, default=5.0,
                    help="baseline shown before TMS (ms); used for baseline correction")
    ap.add_argument("--blank-ms", type=float, default=5.0,
                    help="ignore this window after onset when computing the y-scale (the "
                         "TMS-artifact region)")
    ap.add_argument("--pct", type=float, default=99.0,
                    help="y-scale to the [100-pct, pct] percentiles of the post-blank signal")
    ap.add_argument("--marker-type", default="Stimulus", help="marker type that triggers an epoch")
    ap.add_argument("--marker-desc", default=None,
                    help="only trigger if this substring is in the marker description")
    ap.add_argument("--any-marker", action="store_true",
                    help="trigger on any marker except 'New Segment'")
    ap.add_argument("--max-channels", type=int, default=0, help="plot first N channels (0 = all)")
    ap.add_argument("--save", metavar="PATH", default=None,
                    help="headless: rewrite this PNG on every TMS instead of opening a window")
    ap.add_argument("--max-epochs", type=int, default=0, help="stop after N epochs (0 = run)")
    args = ap.parse_args()

    headless = bool(args.save) or not os.environ.get("DISPLAY")
    import matplotlib
    if headless:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    client = RDAClient(args.host, args.port)
    msgs = client.messages()
    for mtype, fields in msgs:  # consume START for the stream configuration
        if mtype == MsgType.START:
            sfreq = fields["sample_rate"]
            n_channels = fields["n_channels"]
            res = np.asarray(fields["resolutions"], dtype=np.float64)
            break
    else:
        return

    ms = sfreq / 1000.0
    pre = int(round(args.pre_ms * ms))
    post = int(round(args.post_ms * ms))
    blank = int(round(args.blank_ms * ms))
    picks = list(range(n_channels if not args.max_channels
                       else min(n_channels, args.max_channels)))
    time_ms = (np.arange(pre + post) - pre) / ms  # x-axis: ms relative to TMS onset

    fig, ax = plt.subplots(figsize=(10, 6))
    if not headless:
        plt.ion()
    if args.save:
        print(f"waiting for {'any marker' if args.any_marker else args.marker_type!r}; "
              f"writing {args.save} on each TMS (Ctrl-C to stop)")

    history = np.zeros((n_channels, max(pre, 1)), dtype=np.float32)  # rolling pre-stim buffer
    seen_unmatched: set[tuple[str, str]] = set()  # markers we saw but didn't trigger on
    trigger_desc = "any marker (except 'New Segment')" if args.any_marker else \
        f"type=={args.marker_type!r}" + (f", desc contains {args.marker_desc!r}"
                                         if args.marker_desc else "")
    print(f"trigger: {trigger_desc}", file=sys.stderr)
    capturing = False
    pre_seg = None
    collected: list[np.ndarray] = []
    have = 0
    marker_label = ("", "")
    count = 0
    prev_epoch = None

    def render(epoch: np.ndarray) -> None:
        nonlocal count, prev_epoch
        if prev_epoch is not None and np.array_equal(epoch, prev_epoch):
            print("  ! this epoch is byte-identical to the previous one — the source is "
                  "replaying the same samples (are you streaming a looped short file?).",
                  file=sys.stderr)
        prev_epoch = epoch
        sig = epoch[picks] * res[picks, None]  # raw LSB -> physical units (µV)
        if pre > 0:
            sig = sig - sig[:, :pre].mean(axis=1, keepdims=True)  # baseline correction
        scale_region = sig[:, pre + blank:]
        if scale_region.size == 0:
            scale_region = sig
        lo, hi = np.percentile(scale_region, [100 - args.pct, args.pct])
        pad = 0.15 * (hi - lo) if hi > lo else 1.0

        ax.clear()
        for row in sig:
            ax.plot(time_ms, row, lw=0.6, alpha=0.5)
        ax.axvline(0.0, color="k", lw=1.0)                       # TMS onset
        if blank > 0:
            ax.axvspan(0.0, args.blank_ms, color="red", alpha=0.08)  # blanked (artifact) window
        ax.set_xlim(time_ms[0], time_ms[-1])
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlabel("time from TMS (ms)")
        ax.set_ylabel("amplitude (µV)")
        ax.set_title(
            f"TMS epoch #{count + 1}  —  {marker_label[0]}/{marker_label[1]!r}  "
            f"(y scaled to {100 - args.pct:g}–{args.pct:g}th pct after {args.blank_ms:g} ms)"
        )
        count += 1
        if args.save:
            fig.savefig(args.save, dpi=100)
            print(f"  epoch #{count}: wrote {args.save}")
        else:
            fig.canvas.draw_idle()
            plt.pause(0.001)

    try:
        for mtype, fields in msgs:
            if mtype != MsgType.DATA32:
                continue
            data = fields["data"]
            n = data.shape[1]

            if not capturing:
                for m in fields["markers"]:
                    if _is_tms(m, args):
                        p = m["n_position"]
                        if pre > 0:
                            pre_seg = np.concatenate([history, data[:, :p]], axis=1)[:, -pre:]
                        else:
                            pre_seg = np.empty((n_channels, 0), dtype=np.float32)
                        collected = [data[:, p:p + post]]
                        have = collected[0].shape[1]
                        marker_label = (m["type"], m["description"])
                        capturing = True
                        break
                    elif m["type"] != "New Segment":
                        key = (m["type"], m["description"])
                        if key not in seen_unmatched:
                            seen_unmatched.add(key)
                            print(f"  ignoring marker {m['type']}/{m['description']!r} "
                                  f"(does not match trigger). Use --marker-type {m['type']!r} "
                                  f"or --any-marker.", file=sys.stderr)
            elif have < post:
                take = min(post - have, n)
                collected.append(data[:, :take])
                have += take

            # maintain the rolling pre-stim buffer for the next epoch
            if pre > 0:
                if n >= pre:
                    history = data[:, -pre:].copy()
                else:
                    history = np.concatenate([history[:, n:], data], axis=1)

            if capturing and have >= post:
                post_arr = np.concatenate(collected, axis=1)[:, :post]
                render(np.concatenate([pre_seg, post_arr], axis=1))
                capturing = False
                if args.max_epochs and count >= args.max_epochs:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Optional plotting client for eyeballing the stream (needs matplotlib).

Live window (needs a working display):

    pip install matplotlib
    python -m rda_viewer.plot_client --host 127.0.0.1 --port 51244 --seconds 5

Headless — render to an image file instead (works in a container with no X
display; open/refresh the PNG in your editor):

    python -m rda_viewer.plot_client --save /tmp/rda.png --refresh-blocks 20

Plots a rolling window of the first few channels and draws a vertical line at
each received marker. This is a debugging aid, not part of the test suite.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

from mock_rda.protocol import MsgType

from .errors import EXIT_NO_DATA, RDATimeoutError, open_client_or_exit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--channels", type=int, default=4, help="number of channels to plot")
    ap.add_argument("--seconds", type=float, default=5.0, help="rolling window length")
    ap.add_argument("--save", metavar="PATH", default=None,
                    help="headless: write a PNG here every --refresh-blocks instead of "
                         "opening a window (no display needed)")
    ap.add_argument("--refresh-blocks", type=int, default=20,
                    help="in --save mode, re-render the PNG every N data blocks")
    ap.add_argument("--max-blocks", type=int, default=0,
                    help="stop after N data blocks (0 = run until interrupted)")
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="socket timeout in seconds; raise it against a real Recorder that "
             "may sit idle before streaming",
    )
    args = ap.parse_args()

    # Choose a backend before importing pyplot. Headless when saving, or on Linux
    # with no X display (DISPLAY is an X11-only concept — Windows/macOS have a
    # native display without it, so only gate on DISPLAY there).
    no_linux_display = sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
    headless = bool(args.save) or no_linux_display
    import matplotlib
    if headless:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    client = open_client_or_exit(args.host, args.port, timeout=args.timeout)
    msgs = client.messages()
    try:
        for mtype, fields in msgs:  # pull START to learn the configuration
            if mtype == MsgType.START:
                sfreq = fields["sample_rate"]
                n_plot = min(args.channels, fields["n_channels"])
                break
        else:
            print("server closed before START", file=sys.stderr)
            raise SystemExit(EXIT_NO_DATA)
    except RDATimeoutError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_NO_DATA) from exc

    win = int(args.seconds * sfreq)
    buf = np.zeros((n_plot, win), dtype=np.float32)
    if not headless:
        plt.ion()
    fig, ax = plt.subplots(figsize=(10, 6))
    line_objs = [ax.plot(np.zeros(win))[0] for _ in range(n_plot)]
    ax.set_title(f"rda stream ({n_plot} ch @ {sfreq:g} Hz)")
    ax.set_xlabel("samples (rolling window)")

    if args.save:
        print(f"headless mode: writing {args.save} every {args.refresh_blocks} blocks "
              f"(Ctrl-C to stop)")

    n_blocks = 0
    try:
        for mtype, fields in msgs:
            if mtype != MsgType.DATA32:
                continue
            n_blocks += 1
            data = fields["data"][:n_plot]
            k = data.shape[1]
            buf = np.roll(buf, -k, axis=1)
            buf[:, -k:] = data
            for i, ln in enumerate(line_objs):
                ln.set_data(np.arange(win), buf[i] + i * 50.0)
            ax.relim()
            ax.autoscale_view()
            for m in fields["markers"]:
                ax.axvline(win - k + m["n_position"], color="r", alpha=0.4)

            if headless:
                if args.save and n_blocks % args.refresh_blocks == 0:
                    fig.savefig(args.save, dpi=90)
                    print(f"  wrote {args.save} (block {fields['n_block']})")
            else:
                fig.canvas.draw_idle()
                plt.pause(0.001)

            if args.max_blocks and n_blocks >= args.max_blocks:
                break
    except RDATimeoutError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_NO_DATA) from exc
    except KeyboardInterrupt:
        pass
    finally:
        if args.save:
            fig.savefig(args.save, dpi=90)
            print(f"final frame written to {args.save}")
        client.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Optional live-plot client for eyeballing the stream (needs matplotlib).

    pip install matplotlib
    python examples/plot_client.py --host 127.0.0.1 --port 51244 --seconds 5

Plots a rolling window of the first few channels and draws a vertical line at
each received marker. This is a debugging aid, not part of the test suite.
"""

from __future__ import annotations

import argparse

import numpy as np
from minimal_client import RDAClient

from mock_rda.protocol import MsgType


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--channels", type=int, default=4, help="number of channels to plot")
    ap.add_argument("--seconds", type=float, default=5.0, help="rolling window length")
    args = ap.parse_args()

    import matplotlib.pyplot as plt

    client = RDAClient(args.host, args.port)
    # Pull START to learn the configuration.
    msgs = client.messages()
    for mtype, fields in msgs:
        if mtype == MsgType.START:
            sfreq = fields["sample_rate"]
            n_plot = min(args.channels, fields["n_channels"])
            break
    else:
        return

    win = int(args.seconds * sfreq)
    buf = np.zeros((n_plot, win), dtype=np.float32)
    plt.ion()
    fig, ax = plt.subplots()
    line_objs = [ax.plot(np.zeros(win))[0] for _ in range(n_plot)]
    ax.set_title(f"mock-rda live ({n_plot} ch @ {sfreq:g} Hz)")

    try:
        for mtype, fields in msgs:
            if mtype == MsgType.DATA32:
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
                fig.canvas.draw_idle()
                plt.pause(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    main()

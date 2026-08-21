#!/usr/bin/env python3
"""Tkinter TMS/EMG epoch viewer — motor-cortex TEP + MEP monitoring.

Waits for any marker (except ``New Segment``), then shows the pulse-locked
window in five linked panels:

* **EMG** — a single channel (default ``EMG``) from -10 to 50 ms, for
  eyeballing the MEP.
* **Selected electrodes (early)** — overlay of the selected EEG electrodes
  from -2 to 10 ms.
* **Two topomaps** — scalp distribution at two fixed post-pulse latencies
  (default 3 and 4 ms). **Click a sensor to toggle it in/out of the TEP
  panels.**
* **TEP (long timescale)** — the same selected electrodes from -10 to 150 ms.

A single y-scale (shared by every line panel and both topomap color scales)
is either set manually or computed automatically as the signal's min/max,
excluding a small window around the pulse (default +/-2 ms, where the TMS
artifact lives).

Toolkits: Tkinter (Python stdlib) + matplotlib + MNE for the topomaps — same
dependencies as ``gui_client.py``, which this module reuses for the montage
lookup and the data-flow indicator.

    python examples/itep_client.py --emg-electrode EMG --electrode C3
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time

import numpy as np
from gui_client import build_montage, flow_status, nearest_channel, send_inject
from minimal_client import RDAClient

from mock_rda.protocol import MsgType


# --------------------------------------------------------------------------- #
# Pure helpers (no Tk) — unit-testable
# --------------------------------------------------------------------------- #
def parse_ms_range(s: str) -> tuple[float, float]:
    """Parse ``"-10,50"`` into ``(-10.0, 50.0)``."""
    lo, hi = (float(x) for x in s.split(","))
    return lo, hi


def parse_float_list(s: str) -> list[float]:
    """Parse ``"3,4"`` into ``[3.0, 4.0]``."""
    return [float(x) for x in s.split(",")]


def capture_bounds(emg_window, short_window, long_window, topo_latencies):
    """The ``(pre_ms, post_ms)`` epoch span needed to cover every sub-panel."""
    los = [emg_window[0], short_window[0], long_window[0], 0.0]
    his = [emg_window[1], short_window[1], long_window[1], 0.0, *topo_latencies]
    return max(0.0, -min(los)), max(0.0, max(his))


def baseline_correct(epoch_uv, times_ms):
    """Subtract each channel's own pre-trigger (``times_ms < 0``) mean.

    Raw RDA data carries each electrode's DC offset (electrode-skin half-cell
    potential), which can be enormous on a poor-contact channel and dwarfs the
    actual response -- Recorder's own viewer filters this out before display.
    Baseline-correcting per channel to its own pre-trigger window (standard
    ERP/TEP practice) removes it here too, so the shared y-scale reflects the
    response amplitude, not each channel's idiosyncratic offset.
    """
    mask = times_ms < 0
    if not mask.any():
        return epoch_uv
    return epoch_uv - epoch_uv[:, mask].mean(axis=1, keepdims=True)


def auto_range_minmax(epoch_uv, times_ms, channel_idxs, exclude_ms=2.0):
    """Min/max over the given channels, ignoring the +/-``exclude_ms`` artifact window."""
    if not channel_idxs:
        return (-1.0, 1.0)
    mask = np.abs(times_ms) > exclude_ms
    if not mask.any():
        mask = np.ones_like(times_ms, dtype=bool)
    vals = epoch_uv[np.asarray(channel_idxs)][:, mask]
    if vals.size == 0:
        return (-1.0, 1.0)
    lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        hi = lo + 1.0
    pad = 0.1 * (hi - lo)
    return (lo - pad, hi + pad)


def _slice_range(times_ms, t0, t1):
    """Index bounds ``[i0, i1)`` covering ``[t0, t1]`` ms in a sorted time axis."""
    i0 = int(np.searchsorted(times_ms, t0, side="left"))
    i1 = int(np.searchsorted(times_ms, t1, side="right"))
    return max(0, i0), min(len(times_ms), i1)


def epoch_stream_pre_post(msgs, pre_samples, post_samples, trigger_pred,
                          on_unmatched=None, on_data=None):
    """Yield ``(epoch[n_ch, pre+post], marker)`` spanning ``pre_samples`` before to
    ``post_samples`` after each triggering marker's absolute sample position.

    A rolling ``pre_samples``-long history buffer supplies the pre-trigger part
    even though DATA32 blocks only carry markers with a block-relative offset;
    the buffer is maintained every block regardless of capture state. Markers
    arriving during an active capture are ignored (pulses are far apart
    relative to the window).
    """
    history = None
    capturing = False
    collected: list[np.ndarray] = []
    have = 0
    pre_seg = None
    marker = None
    for mtype, f in msgs:
        if mtype != MsgType.DATA32:
            continue
        if on_data is not None:
            on_data(f["n_points"])
        data = f["data"]
        n_ch, n = data.shape
        if history is None:
            history = np.zeros((n_ch, pre_samples), dtype=data.dtype)
        if not capturing:
            for m in f["markers"]:
                if trigger_pred(m):
                    p = m["n_position"]
                    if pre_samples > 0:
                        pre_seg = np.concatenate([history, data[:, :p]], axis=1)[:, -pre_samples:]
                    else:
                        pre_seg = np.empty((n_ch, 0), dtype=data.dtype)
                    collected = [data[:, p:p + post_samples]]
                    have = collected[0].shape[1]
                    marker = m
                    capturing = True
                    break
                elif on_unmatched is not None and m["type"] != "New Segment":
                    on_unmatched(m)
        elif have < post_samples:
            take = min(post_samples - have, n)
            collected.append(data[:, :take])
            have += take
        if pre_samples > 0:
            if n >= pre_samples:
                history = data[:, -pre_samples:].copy()
            else:
                history = np.concatenate([history[:, n:], data], axis=1)
        if capturing and have >= post_samples:
            post_arr = np.concatenate(collected, axis=1)[:, :post_samples]
            yield np.concatenate([pre_seg, post_arr], axis=1), marker
            capturing = False


# --------------------------------------------------------------------------- #
# Rendering (matplotlib only — works headless with Agg, testable)
# --------------------------------------------------------------------------- #
class ItepViewer:
    """Owns the 5-panel figure and renders an epoch given explicit y-limits."""

    def __init__(self, fig, eeg_names, pos, full_idx):
        self.fig = fig
        self.eeg_names = eeg_names
        self.pos = pos
        self.full_idx = full_idx
        self.ax_emg = fig.add_subplot(2, 3, 1)
        self.ax_topo1 = fig.add_subplot(2, 3, 2)
        self.ax_topo2 = fig.add_subplot(2, 3, 3)
        self.ax_short = fig.add_subplot(2, 3, 4)
        self.ax_long = fig.add_subplot(2, 3, 5)

    def render(self, epoch_uv, times_ms, emg_idx, emg_name, selected,
              topo_latencies, emg_window, short_window, long_window, ylim):
        # --- EMG ---
        ax = self.ax_emg
        ax.clear()
        i0, i1 = _slice_range(times_ms, *emg_window)
        ax.plot(times_ms[i0:i1], epoch_uv[emg_idx, i0:i1], lw=1.0, color="C1")
        ax.axvline(0.0, color="k", lw=0.8, ls="--")
        ax.set_xlim(*emg_window)
        ax.set_ylim(*ylim)
        ax.set_xlabel("ms from pulse")
        ax.set_ylabel("µV")
        ax.set_title(f"EMG ({emg_name})")

        # --- topomaps at fixed latencies ---
        eeg = epoch_uv[self.full_idx] if self.full_idx else np.empty((0, epoch_uv.shape[1]))
        mask = np.array([n in selected for n in self.eeg_names])
        for ax_t, lat in ((self.ax_topo1, topo_latencies[0]), (self.ax_topo2, topo_latencies[1])):
            ax_t.clear()
            if self.eeg_names:
                import mne
                t_idx = int(np.argmin(np.abs(times_ms - lat)))
                mne.viz.plot_topomap(
                    eeg[:, t_idx], self.pos, axes=ax_t, show=False, vlim=ylim,
                    mask=mask, mask_params=dict(markersize=8, markerfacecolor="none",
                                                markeredgecolor="k", markeredgewidth=1.5),
                    sensors=True, contours=4,
                )
            ax_t.set_title(f"topomap @ {lat:g} ms\n(click to toggle)")

        # --- selected electrodes, early window ---
        ax = self.ax_short
        ax.clear()
        i0, i1 = _slice_range(times_ms, *short_window)
        for name in sorted(selected):
            if name in self.eeg_names:
                idx = self.full_idx[self.eeg_names.index(name)]
                ax.plot(times_ms[i0:i1], epoch_uv[idx, i0:i1], lw=1.4, label=name)
        ax.axvline(0.0, color="k", lw=0.8, ls="--")
        for lat in topo_latencies:
            ax.axvline(lat, color="0.6", lw=0.8, ls=":")
        ax.set_xlim(*short_window)
        ax.set_ylim(*ylim)
        ax.set_xlabel("ms from pulse")
        ax.set_ylabel("µV")
        ax.set_title("selected electrodes (early)")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        # --- selected electrodes, long timescale ---
        ax = self.ax_long
        ax.clear()
        i0, i1 = _slice_range(times_ms, *long_window)
        for name in sorted(selected):
            if name in self.eeg_names:
                idx = self.full_idx[self.eeg_names.index(name)]
                ax.plot(times_ms[i0:i1], epoch_uv[idx, i0:i1], lw=1.2, label=name)
        ax.axvline(0.0, color="k", lw=0.8, ls="--")
        for lat in topo_latencies:
            ax.axvline(lat, color="0.6", lw=0.8, ls=":")
        ax.set_xlim(*long_window)
        ax.set_ylim(*ylim)
        ax.set_xlabel("ms from pulse")
        ax.set_ylabel("µV")
        ax.set_title("TEP (long timescale)")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        self.fig.tight_layout()


# --------------------------------------------------------------------------- #
# Tk application
# --------------------------------------------------------------------------- #
def run_gui(args):
    import matplotlib
    matplotlib.use("TkAgg")
    import tkinter as tk
    from tkinter import ttk

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    emg_window = parse_ms_range(args.emg_window)
    short_window = parse_ms_range(args.short_window)
    long_window = parse_ms_range(args.long_window)
    topo_latencies = parse_float_list(args.topo_latencies)
    if len(topo_latencies) != 2:
        raise SystemExit("--topo-latencies needs exactly two comma-separated values")
    pre_ms, post_ms = capture_bounds(emg_window, short_window, long_window, topo_latencies)

    client = RDAClient(args.host, args.port)
    msgs = client.messages()
    for mtype, f in msgs:  # consume START
        if mtype == MsgType.START:
            sfreq = f["sample_rate"]
            channel_names = f["channel_names"]
            res = np.asarray(f["resolutions"], dtype=np.float64)
            break
    else:
        print("server closed before START", file=sys.stderr)
        return

    eeg_names, pos, full_idx = build_montage(channel_names, sfreq)
    default_electrode = args.electrode if args.electrode in eeg_names else (
        eeg_names[0] if eeg_names else channel_names[0])
    if args.emg_electrode in channel_names:
        emg_idx = channel_names.index(args.emg_electrode)
        emg_name = args.emg_electrode
    else:
        print(f"warning: EMG channel {args.emg_electrode!r} not found; "
              f"using {channel_names[0]!r} instead", file=sys.stderr)
        emg_idx = 0
        emg_name = channel_names[0]

    pre_samples = int(round(pre_ms * sfreq / 1000.0))
    post_samples = max(1, int(round(post_ms * sfreq / 1000.0)))

    root = tk.Tk()
    root.title("mock-rda iTEP viewer — TMS/EMG epoch monitor")
    status_label = tk.Label(root, text="RDA: waiting for data…", anchor="w",
                            fg="white", bg="#e53935", font=("TkDefaultFont", 11, "bold"))
    status_label.pack(side=tk.TOP, fill=tk.X)

    fig = Figure(figsize=(13, 8))
    viewer = ItepViewer(fig, eeg_names, pos, full_idx)
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # --- control panel ---
    panel = ttk.Frame(root)
    panel.pack(side=tk.BOTTOM, fill=tk.X)

    selected: set[str] = {default_electrode}
    sel_var = tk.StringVar(value=f"selected: {default_electrode}")

    info = ttk.Frame(panel)
    info.pack(side=tk.LEFT, padx=6, pady=4)
    ttk.Label(info, text=f"EMG channel: {emg_name}").grid(row=0, column=0, sticky="w")
    ttk.Label(info, textvariable=sel_var).grid(row=1, column=0, sticky="w")

    scale = ttk.LabelFrame(panel, text="y-scale (all panels)")
    scale.pack(side=tk.LEFT, padx=6, pady=4)
    auto_var = tk.BooleanVar(value=True)
    min_var = tk.StringVar(value="-50")
    max_var = tk.StringVar(value="50")
    ttk.Checkbutton(scale, text="auto (min/max, excl. artifact)", variable=auto_var,
                    command=lambda: redraw()).grid(row=0, column=0, columnspan=2)
    ttk.Label(scale, text="min").grid(row=1, column=0)
    ttk.Entry(scale, textvariable=min_var, width=7).grid(row=1, column=1)
    ttk.Label(scale, text="max").grid(row=2, column=0)
    ttk.Entry(scale, textvariable=max_var, width=7).grid(row=2, column=1)

    ctrl = ttk.Frame(panel)
    ctrl.pack(side=tk.LEFT, padx=6, pady=4)
    status_var = tk.StringVar(value="")

    def inject_trigger():
        status_var.set("injecting…")

        def worker():
            result = send_inject(args.host, args.control_port, "Stimulus", "S  1")
            root.after(0, lambda: status_var.set(result))

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(ctrl, text="Inject trigger", command=inject_trigger).grid(
        row=0, column=0, sticky="we")
    ttk.Label(ctrl, textvariable=status_var, foreground="#357").grid(
        row=1, column=0, sticky="w")

    state = {"epoch": None}
    flow = {"last": None, "blocks": 0, "interval": 0.02, "ended": False}
    new_epoch = queue.Queue(maxsize=1)
    redrawing = {"busy": False}

    def compute_ylim(epoch_uv, times_ms):
        idxs = [emg_idx] + [full_idx[eeg_names.index(n)] for n in selected if n in eeg_names]
        if auto_var.get():
            lo, hi = auto_range_minmax(epoch_uv, times_ms, idxs, exclude_ms=args.exclude_ms)
            min_var.set(f"{lo:.1f}")
            max_var.set(f"{hi:.1f}")
            return lo, hi
        try:
            return float(min_var.get()), float(max_var.get())
        except ValueError:
            return auto_range_minmax(epoch_uv, times_ms, idxs, exclude_ms=args.exclude_ms)

    def redraw():
        # Setting min/max StringVars below fires their write-traces, which call
        # redraw again; guard against that reentrancy (see gui_client.py).
        if redrawing["busy"]:
            return
        epoch = state["epoch"]
        if epoch is None:
            return
        redrawing["busy"] = True
        try:
            _redraw_impl(epoch)
        finally:
            redrawing["busy"] = False

    def _redraw_impl(epoch):
        epoch_uv = epoch * res[:, None]
        n = epoch_uv.shape[1]
        times_ms = (np.arange(n) - pre_samples) / sfreq * 1000.0
        epoch_uv = baseline_correct(epoch_uv, times_ms)
        sel_var.set("selected: " + (", ".join(sorted(selected)) if selected else "(none)"))
        ylim = compute_ylim(epoch_uv, times_ms)
        viewer.render(epoch_uv, times_ms, emg_idx, emg_name, selected, topo_latencies,
                     emg_window, short_window, long_window, ylim)
        canvas.draw_idle()

    def on_click(event):
        for ax_t in (viewer.ax_topo1, viewer.ax_topo2):
            if event.inaxes is ax_t and eeg_names and event.xdata is not None:
                name = eeg_names[nearest_channel(pos, event.xdata, event.ydata)]
                if name in selected:
                    selected.discard(name)
                else:
                    selected.add(name)
                redraw()
                return

    canvas.mpl_connect("button_press_event", on_click)
    for v in (min_var, max_var):
        v.trace_add("write", lambda *a: redraw())

    # --- network thread: capture epochs, hand to the GUI thread ---
    def on_data(n_points):
        flow["last"] = time.monotonic()
        flow["blocks"] += 1
        flow["interval"] = n_points / sfreq

    def net_loop():
        trigger_pred = lambda m: m["type"] != "New Segment"  # react to all triggers
        for epoch, _marker in epoch_stream_pre_post(msgs, pre_samples, post_samples,
                                                     trigger_pred, on_data=on_data):
            state["epoch"] = epoch
            if new_epoch.full():
                try:
                    new_epoch.get_nowait()
                except queue.Empty:
                    pass
            new_epoch.put(epoch)
        flow["ended"] = True

    threading.Thread(target=net_loop, name="net", daemon=True).start()

    stop_requested = threading.Event()

    def on_close():
        client.close()
        root.destroy()

    def poll():
        if stop_requested.is_set():
            on_close()
            return
        try:
            new_epoch.get_nowait()
            redraw()
        except queue.Empty:
            pass
        except Exception as exc:
            print(f"redraw error: {exc}", file=sys.stderr)
        root.after(50, poll)

    root.after(50, poll)

    colors = {"green": "#43a047", "orange": "#fb8c00", "red": "#e53935"}

    def update_status():
        if stop_requested.is_set():
            return
        text, color = flow_status(flow, time.monotonic())
        status_label.config(text=text, bg=colors[color])
        root.after(200, update_status)

    root.after(200, update_status)

    signal.signal(signal.SIGINT, lambda *_: stop_requested.set())
    root.protocol("WM_DELETE_WINDOW", on_close)
    print(f"iTEP viewer started; waiting for any trigger; epoch window "
          f"-{pre_ms:g}..{post_ms:g} ms", file=sys.stderr)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--control-port", type=int, default=51299,
                    help="server control socket for the 'Inject trigger' button")
    ap.add_argument("--emg-electrode", default="EMG", help="channel name for the EMG panel")
    ap.add_argument("--electrode", default="C3",
                    help="initially selected electrode for the TEP panels")
    ap.add_argument("--topo-latencies", default="3,4",
                    help="two comma-separated post-pulse latencies (ms) for the topomaps")
    ap.add_argument("--emg-window", default="-10,50", help="ms range shown in the EMG panel")
    ap.add_argument("--short-window", default="-2,10",
                    help="ms range shown in the early TEP panel")
    ap.add_argument("--long-window", default="-10,150",
                    help="ms range shown in the long-timescale TEP panel")
    ap.add_argument("--exclude-ms", type=float, default=2.0,
                    help="auto y-scale ignores +/- this many ms around the pulse (the artifact)")
    args = ap.parse_args(argv)
    run_gui(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tkinter GUI epoch viewer for the mock RDA stream.

Waits for a configurable trigger marker, then shows the post-trigger window in
three linked panels:

* **Butterfly** — all EEG channels overlaid.
* **Topomap** (MNE) — scalp distribution at the window's peak global-field-power
  latency. **Click it to select an electrode.**
* **Selected electrode** — single-channel trace (default ``C3``).

Each panel has its own y-limits (or color limits, for the topomap) with an
"auto" checkbox; a global "sync" checkbox ties all three to one range. The shown
window length is configurable (default 10 ms).

Toolkits: Tkinter (Python stdlib) + matplotlib (already a dependency) + MNE for
the topomap — no extra installs. Needs a working display; in a headless
container see the note at the bottom of ``README``/the plotting discussion.

    python examples/gui_client.py --trigger Stimulus --window-ms 10 --electrode C3
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import socket
import sys
import threading
import time

import numpy as np
from minimal_client import RDAClient

from mock_rda.protocol import MsgType


def send_inject(host: str, control_port: int, mtype: str, desc: str, timeout: float = 2.0) -> str:
    """Send a one-line JSON inject command to the server control socket.

    Returns a short human-readable status string (used by the GUI button).
    """
    cmd = json.dumps({"type": mtype, "description": desc, "at": "next"}) + "\n"
    try:
        with socket.create_connection((host, control_port), timeout=timeout) as s:
            s.sendall(cmd.encode("utf-8"))
            s.recv(64)
        return f"injected {mtype}/{desc!r}"
    except OSError as exc:
        return f"inject failed ({exc}); is --control-port right?"


# --------------------------------------------------------------------------- #
# Pure helpers (no Tk) — unit-testable
# --------------------------------------------------------------------------- #
def auto_range(arr, symmetric: bool = False, pct: float = 99.0):
    """A robust [lo, hi] from percentiles, ignoring extreme outliers."""
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return (-1.0, 1.0)
    if symmetric:
        m = float(np.percentile(np.abs(a), pct))
        m = m if m > 0 else 1.0
        return (-m, m)
    lo, hi = np.percentile(a, [100 - pct, pct])
    if hi <= lo:
        hi = lo + 1.0
    pad = 0.1 * (hi - lo)
    return (float(lo - pad), float(hi + pad))


def nearest_channel(pos: np.ndarray, x: float, y: float) -> int:
    """Index of the sensor position closest to (x, y)."""
    d = (pos[:, 0] - x) ** 2 + (pos[:, 1] - y) ** 2
    return int(np.argmin(d))


def flow_status(flow: dict, now: float):
    """Map the data-flow monitor to ``(text, "green"|"orange"|"red")``.

    Thresholds scale with the block interval so slow and fast streams both work:
    green while blocks keep arriving, orange if they pause, red if none have
    arrived, the gap grows large, or the stream has closed.
    """
    iv = flow["interval"] or 0.02
    warn_after = max(0.4, 6 * iv)
    dead_after = max(1.5, 25 * iv)
    if flow["last"] is None:
        return "RDA: waiting for data…", "red"
    dt = now - flow["last"]
    if flow["ended"] and dt >= warn_after:
        return f"RDA: disconnected — no data for {dt:.1f} s", "red"
    if dt < warn_after:
        rate = 1.0 / iv if iv else 0.0
        return f"RDA: receiving  ●  {flow['blocks']} blocks  (~{rate:.0f}/s)", "green"
    if dt < dead_after:
        return f"RDA: data stale  ⚠  {dt:.1f} s since last block", "orange"
    return f"RDA: NO SIGNAL  ✖  {dt:.1f} s since last block", "red"


def build_montage(channel_names, sfreq):
    """Return (eeg_names, pos[N,2], full_indices) for channels in the 10-20 montage."""
    import mne

    montage = mne.channels.make_standard_montage("standard_1020")
    in_montage = set(montage.ch_names)
    eeg_names = [n for n in channel_names if n in in_montage]
    if not eeg_names:
        return [], np.empty((0, 2)), []
    info = mne.create_info(eeg_names, sfreq, ch_types="eeg")
    info.set_montage(montage)
    from mne.channels.layout import _find_topomap_coords

    pos = np.asarray(_find_topomap_coords(info, picks=list(range(len(eeg_names)))))
    full_idx = [channel_names.index(n) for n in eeg_names]
    return eeg_names, pos, full_idx


def epoch_stream(msgs, get_window_samples, trigger_pred, on_unmatched=None, on_data=None):
    """Yield ``(epoch[n_ch, window], marker)`` for each triggering marker.

    Post-trigger only: capture starts at the marker and spans following blocks
    until ``get_window_samples()`` samples are collected. Markers arriving during
    a capture are ignored (TMS pulses are far apart relative to the window).
    ``on_data(n_points)`` is called for every DATA32 block (for the flow monitor).
    """
    capturing = False
    collected: list[np.ndarray] = []
    have = 0
    need = 0
    marker = None
    for mtype, f in msgs:
        if mtype != MsgType.DATA32:
            continue
        if on_data is not None:
            on_data(f["n_points"])
        data = f["data"]
        n = data.shape[1]
        if not capturing:
            for m in f["markers"]:
                if trigger_pred(m):
                    need = get_window_samples()
                    p = m["n_position"]
                    collected = [data[:, p:p + need]]
                    have = collected[0].shape[1]
                    marker = m
                    capturing = True
                    break
                elif on_unmatched is not None and m["type"] != "New Segment":
                    on_unmatched(m)
        elif have < need:
            take = min(need - have, n)
            collected.append(data[:, :take])
            have += take
        if capturing and have >= need:
            yield np.concatenate(collected, axis=1)[:, :need], marker
            capturing = False


# --------------------------------------------------------------------------- #
# Rendering (matplotlib only — works headless with Agg, testable)
# --------------------------------------------------------------------------- #
class EpochViewer:
    """Owns the 3-panel figure and renders an epoch given explicit limits."""

    def __init__(self, fig, eeg_names, pos, full_idx):
        self.fig = fig
        self.eeg_names = eeg_names
        self.pos = pos
        self.full_idx = full_idx  # eeg channel -> index in the full data array
        self.ax_bf = fig.add_subplot(1, 3, 1)
        self.ax_topo = fig.add_subplot(1, 3, 2)
        self.ax_el = fig.add_subplot(1, 3, 3)

    def render(self, epoch_uv, times_ms, selected_name, bf_ylim, el_ylim, topo_clim):
        eeg = epoch_uv[self.full_idx] if self.full_idx else epoch_uv
        gfp = eeg.std(axis=0) if eeg.shape[0] else np.zeros(epoch_uv.shape[1])
        t_idx = int(np.argmax(gfp)) if gfp.size else 0
        sel_full = (self.full_idx[self.eeg_names.index(selected_name)]
                    if selected_name in self.eeg_names else 0)

        # --- butterfly ---
        ax = self.ax_bf
        ax.clear()
        for row in eeg:
            ax.plot(times_ms, row, lw=0.5, alpha=0.4, color="0.5")
        ax.plot(times_ms, epoch_uv[sel_full], lw=1.6, color="C3", label=selected_name)
        ax.axvline(times_ms[t_idx], color="k", lw=0.8, ls="--")
        ax.set_ylim(*bf_ylim)
        ax.set_xlim(times_ms[0], times_ms[-1])
        ax.set_xlabel("time from trigger (ms)")
        ax.set_ylabel("µV")
        ax.set_title("butterfly")
        ax.legend(loc="upper right", fontsize=8)

        # --- topomap at peak-GFP latency ---
        ax = self.ax_topo
        ax.clear()
        if self.eeg_names:
            import mne
            vals = eeg[:, t_idx]
            mask = np.array([n == selected_name for n in self.eeg_names])
            mne.viz.plot_topomap(
                vals, self.pos, axes=ax, show=False, vlim=topo_clim,
                mask=mask, mask_params=dict(markersize=10, markerfacecolor="none",
                                            markeredgecolor="k", markeredgewidth=1.5),
                sensors=True, contours=4,
            )
        ax.set_title(f"topomap @ {times_ms[t_idx]:.1f} ms\n(click to select)")

        # --- selected electrode ---
        ax = self.ax_el
        ax.clear()
        ax.plot(times_ms, epoch_uv[sel_full], lw=1.2, color="C0")
        ax.axvline(times_ms[t_idx], color="k", lw=0.8, ls="--")
        ax.set_ylim(*el_ylim)
        ax.set_xlim(times_ms[0], times_ms[-1])
        ax.set_xlabel("time from trigger (ms)")
        ax.set_ylabel("µV")
        ax.set_title(f"electrode {selected_name}")

        self.fig.tight_layout()
        return t_idx


# --------------------------------------------------------------------------- #
# Tk application
# --------------------------------------------------------------------------- #
def _make_trigger_pred(args):
    if args.any_marker:
        return lambda m: m["type"] != "New Segment"
    return lambda m: (m["type"] == args.trigger
                      and (args.trigger_desc is None or args.trigger_desc in m["description"]))


def run_gui(args):
    import matplotlib
    matplotlib.use("TkAgg")
    import tkinter as tk
    from tkinter import ttk

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

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
    default_el = args.electrode if args.electrode in eeg_names else (
        eeg_names[0] if eeg_names else channel_names[0])

    root = tk.Tk()
    root.title(f"mock-rda epoch viewer — trigger: "
               f"{'any' if args.any_marker else args.trigger}")
    status_label = tk.Label(root, text="RDA: waiting for data…", anchor="w",
                            fg="white", bg="#e53935", font=("TkDefaultFont", 11, "bold"))
    status_label.pack(side=tk.TOP, fill=tk.X)

    fig = Figure(figsize=(13, 5))
    viewer = EpochViewer(fig, eeg_names, pos, full_idx)
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # --- control panel ---
    panel = ttk.Frame(root)
    panel.pack(side=tk.BOTTOM, fill=tk.X)

    win_var = tk.StringVar(value=str(args.window_ms))
    sel_var = tk.StringVar(value=default_el)
    sync_var = tk.BooleanVar(value=False)

    def limit_controls(parent, label, default_auto=True):
        frame = ttk.LabelFrame(parent, text=label)
        auto = tk.BooleanVar(value=default_auto)
        vmin = tk.StringVar(value="-50")
        vmax = tk.StringVar(value="50")
        ttk.Checkbutton(frame, text="auto", variable=auto,
                        command=lambda: redraw()).grid(row=0, column=0, columnspan=2)
        ttk.Label(frame, text="min").grid(row=1, column=0)
        ttk.Entry(frame, textvariable=vmin, width=7).grid(row=1, column=1)
        ttk.Label(frame, text="max").grid(row=2, column=0)
        ttk.Entry(frame, textvariable=vmax, width=7).grid(row=2, column=1)
        return frame, auto, vmin, vmax

    ctrl = ttk.Frame(panel)
    ctrl.pack(side=tk.LEFT, padx=6, pady=4)
    ttk.Label(ctrl, text="window (ms)").grid(row=0, column=0, sticky="e")
    ttk.Entry(ctrl, textvariable=win_var, width=7).grid(row=0, column=1)
    ttk.Label(ctrl, text="electrode").grid(row=1, column=0, sticky="e")
    el_combo = ttk.Combobox(ctrl, textvariable=sel_var, values=eeg_names, width=7,
                            state="readonly")
    el_combo.grid(row=1, column=1)
    ttk.Checkbutton(ctrl, text="sync all y-limits", variable=sync_var,
                    command=lambda: redraw()).grid(row=2, column=0, columnspan=2, sticky="w")

    status_var = tk.StringVar(value="")

    def inject_trigger():
        mtype = "Stimulus" if args.any_marker else args.trigger
        desc = args.trigger_desc or "S  1"
        status_var.set("injecting…")

        def worker():
            result = send_inject(args.host, args.control_port, mtype, desc)
            root.after(0, lambda: status_var.set(result))

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(ctrl, text="Inject trigger", command=inject_trigger).grid(
        row=3, column=0, columnspan=2, sticky="we", pady=(4, 0))
    ttk.Label(ctrl, textvariable=status_var, foreground="#357").grid(
        row=4, column=0, columnspan=2, sticky="w")

    bf_frame, bf_auto, bf_min, bf_max = limit_controls(panel, "butterfly y")
    el_frame, el_auto, el_min, el_max = limit_controls(panel, "electrode y")
    topo_frame, topo_auto, topo_min, topo_max = limit_controls(panel, "topomap color")
    for fr in (bf_frame, el_frame, topo_frame):
        fr.pack(side=tk.LEFT, padx=6, pady=4)

    state = {"epoch": None}  # latest raw epoch (n_ch, window), set by net thread
    flow = {"last": None, "blocks": 0, "interval": 0.02, "ended": False}  # data-flow monitor
    new_epoch = queue.Queue(maxsize=1)

    def window_samples():
        try:
            return max(1, int(round(float(win_var.get()) * sfreq / 1000.0)))
        except ValueError:
            return max(1, int(round(args.window_ms * sfreq / 1000.0)))

    def _read_limits(auto, vmin, vmax, data, symmetric):
        if auto.get():
            lo, hi = auto_range(data, symmetric=symmetric)
            vmin.set(f"{lo:.1f}")
            vmax.set(f"{hi:.1f}")
            return lo, hi
        try:
            return float(vmin.get()), float(vmax.get())
        except ValueError:
            return auto_range(data, symmetric=symmetric)

    redrawing = {"busy": False}

    def redraw():
        # Setting the limit StringVars below fires their write-traces, which call
        # redraw again; guard against that reentrancy (it would otherwise spin the
        # main thread and, among other things, block Ctrl-C).
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
        times_ms = np.arange(n) / sfreq * 1000.0
        sel = sel_var.get()
        eeg = epoch_uv[full_idx] if full_idx else epoch_uv
        sel_full = full_idx[eeg_names.index(sel)] if sel in eeg_names else 0
        # peak-GFP latency for topomap values
        t_idx = int(np.argmax(eeg.std(axis=0))) if eeg.shape[0] else 0

        if sync_var.get():
            lo, hi = _read_limits(bf_auto, bf_min, bf_max, eeg, symmetric=False)
            for vmn, vmx in ((el_min, el_max), (topo_min, topo_max)):
                vmn.set(f"{lo:.1f}")
                vmx.set(f"{hi:.1f}")
            bf_ylim = el_ylim = (lo, hi)
            topo_clim = (lo, hi)
        else:
            bf_ylim = _read_limits(bf_auto, bf_min, bf_max, eeg, symmetric=False)
            el_ylim = _read_limits(el_auto, el_min, el_max, epoch_uv[sel_full], symmetric=False)
            topo_clim = _read_limits(topo_auto, topo_min, topo_max,
                                     eeg[:, t_idx] if eeg.shape[0] else [0], symmetric=True)

        viewer.render(epoch_uv, times_ms, sel, bf_ylim, el_ylim, topo_clim)
        canvas.draw_idle()

    def on_click(event):
        if event.inaxes is viewer.ax_topo and len(eeg_names) and event.xdata is not None:
            i = nearest_channel(pos, event.xdata, event.ydata)
            sel_var.set(eeg_names[i])
            redraw()

    canvas.mpl_connect("button_press_event", on_click)
    # window length is read fresh for each new epoch (see window_samples)
    el_combo.bind("<<ComboboxSelected>>", lambda e: redraw())
    for v in (bf_min, bf_max, el_min, el_max, topo_min, topo_max):
        v.trace_add("write", lambda *a: redraw())

    # --- network thread: capture epochs, hand to the GUI thread ---
    def on_data(n_points):
        flow["last"] = time.monotonic()
        flow["blocks"] += 1
        flow["interval"] = n_points / sfreq

    def net_loop():
        def on_unmatched(m):
            print(f"  ignoring {m['type']}/{m['description']!r} (not the trigger)",
                  file=sys.stderr)
        for epoch, _marker in epoch_stream(msgs, window_samples,
                                            _make_trigger_pred(args), on_unmatched, on_data):
            state["epoch"] = epoch
            if new_epoch.full():
                try:
                    new_epoch.get_nowait()
                except queue.Empty:
                    pass
            new_epoch.put(epoch)
        flow["ended"] = True  # stream closed / server gone

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
        except Exception as exc:  # never let a bad frame kill the poll loop
            print(f"redraw error: {exc}", file=sys.stderr)
        root.after(50, poll)

    root.after(50, poll)

    # data-flow indicator: green=arriving, orange=stale, red=no signal/disconnected
    colors = {"green": "#43a047", "orange": "#fb8c00", "red": "#e53935"}

    def update_status():
        if stop_requested.is_set():
            return
        text, color = flow_status(flow, time.monotonic())
        status_label.config(text=text, bg=colors[color])
        root.after(200, update_status)

    root.after(200, update_status)

    # Tk's C event loop blocks Python from handling SIGINT, so the handler only
    # sets a flag; the periodic poll (running in the main thread) does the actual
    # teardown. This makes Ctrl-C in the launching terminal close the window.
    signal.signal(signal.SIGINT, lambda *_: stop_requested.set())

    root.protocol("WM_DELETE_WINDOW", on_close)
    print(f"GUI started; waiting for trigger "
          f"{'any marker' if args.any_marker else repr(args.trigger)}", file=sys.stderr)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=51244)
    ap.add_argument("--control-port", type=int, default=51299,
                    help="server control socket for the 'Inject trigger' button")
    ap.add_argument("--trigger", default="Stimulus",
                    help="marker type to wait for (default: Stimulus)")
    ap.add_argument("--trigger-desc", default=None,
                    help="only trigger if this substring is in the description")
    ap.add_argument("--any-marker", action="store_true",
                    help="trigger on any marker except 'New Segment'")
    ap.add_argument("--window-ms", type=float, default=10.0,
                    help="length of the post-trigger window shown (default: 10 ms)")
    ap.add_argument("--electrode", default="C3", help="initially selected electrode")
    args = ap.parse_args(argv)
    run_gui(args)


if __name__ == "__main__":
    main()

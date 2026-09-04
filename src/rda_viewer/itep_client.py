#!/usr/bin/env python3
"""Tkinter TMS/EMG epoch viewer — motor-cortex TEP + MEP monitoring.

Waits for any marker (except ``New Segment``), then shows the pulse-locked
window. Two display modes, switched with a button:

**Single pulse mode** (default) — six linked panels:

* **Three topomaps** — scalp distribution at fixed post-pulse latencies
  (default 2.3, 3.5 and 4.8 ms), sharing one colorbar. **Click a sensor to
  toggle it in/out of the TEP panels.**
* **EMG** — every EMG channel (default: any named ``EMG*``, so a second
  electrode ``EMG2`` appears automatically) from -10 to 50 ms, for eyeballing
  the MEP; one colour per channel with a legend, plus reference lines at
  +/-25 µV (dotted) and +/-50 µV (dashed).
* **iTEP** — overlay of the selected EEG electrodes from -2 to 10 ms.
* **GMFP** — global mean field power (spatial SD across all montage
  electrodes), independent of which electrodes are selected.
* **TEP** — the same selected electrodes from -10 to 150 ms.

Signals are shown unfiltered apart from per-channel baseline correction: a
band-pass worth having needs seconds of data either side of the epoch, which
would cost the same in display latency. Filter post-hoc in analysis instead.

**Burst mode** — the stimulator fires a burst (default 5 pulses at 50 Hz,
i.e. 20 ms apart) on each trigger. The first trigger locks the epoch; any
further triggers inside the capture window never start a new epoch, but their
actual arrival times are recorded. Expected pulse times are drawn as grey
dotted lines, actually received triggers as red lines. Panels:

* **EMG** — every EMG channel, from the usual pre-window until 50 ms after
  the last *expected* pulse.
* **TEP (burst)** — selected electrodes from -2 ms (first pulse) until 20 ms
  after the last pulse.
* **Per-pulse butterfly** — each pulse's -2..10 ms segment overlaid, aligned
  per pulse (actual trigger time when it arrived, expected time otherwise);
  electrode = color, pulse order = shade, thick line = across-pulse average.
* **One topomap per pulse** at a fixed post-pulse latency (default 3 ms).

A single y-scale (shared by every line panel and all topomap color scales)
is either set manually or computed automatically as the signal's min/max,
excluding a small window around each pulse (default +/-2 ms, where the TMS
artifact lives).

Toolkits: Tkinter (Python stdlib) + matplotlib + MNE for the topomaps — same
dependencies as ``rda_viewer.gui_client``, which this module reuses for the montage
lookup and the data-flow indicator.

    python -m rda_viewer.itep_client --emg-electrodes 'EMG*' --electrode C3
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time

import numpy as np

from mock_rda.protocol import MsgType

from .gui_client import build_montage, flow_status, nearest_channel
from .minimal_client import RDAClient


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


def resolve_emg_channels(channel_names, spec="EMG*"):
    """``[(index, name), ...]`` for every channel matching ``spec``.

    ``spec`` is a comma-separated list of channel names or glob patterns. The
    default ``EMG*`` picks up ``EMG``, ``EMG2``, ``EMG3``... so adding a second
    electrode to the montage needs no command-line change, while a single-EMG
    setup keeps working unchanged.

    Matching is case-insensitive and platform-independent (``fnmatchcase`` on
    pre-lowered strings -- plain ``fnmatch`` would fold case on Windows only).
    Results stay in acquisition order; a channel matched by several patterns
    appears once.
    """
    import fnmatch

    pats = [p.strip().lower() for p in spec.split(",") if p.strip()]
    return [(i, name) for i, name in enumerate(channel_names)
            if any(fnmatch.fnmatchcase(name.lower(), p) for p in pats)]


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


def gmfp(eeg_uv):
    """Global mean field power: the spatial standard deviation across electrodes.

    GMFP(t) = sqrt(mean_i (V_i(t) - mean(V(t)))^2) — one non-negative trace
    summarising how much response there is anywhere on the scalp, independent
    of which electrodes happen to be selected.
    """
    if eeg_uv.shape[0] == 0:
        return np.zeros(eeg_uv.shape[1])
    return eeg_uv.std(axis=0)


def match_burst_triggers(actual_ms, expected_ms, tol_ms):
    """Per expected pulse time, the nearest actual trigger within ``tol_ms``
    of it — or the expected time itself when no trigger arrived."""
    align = []
    for t in expected_ms:
        cands = [a for a in actual_ms if abs(a - t) <= tol_ms]
        align.append(min(cands, key=lambda a: abs(a - t)) if cands else t)
    return align


def auto_range_minmax(epoch_uv, times_ms, channel_idxs, exclude_ms=2.0,
                      trigger_times_ms=(0.0,)):
    """Min/max over the given channels, ignoring the +/-``exclude_ms`` artifact
    window around every trigger time."""
    if not channel_idxs:
        return (-1.0, 1.0)
    mask = np.ones_like(times_ms, dtype=bool)
    for t in trigger_times_ms:
        mask &= np.abs(times_ms - t) > exclude_ms
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
    """Yield ``(epoch[n_ch, pre+post], marker, trigger_offsets)`` spanning
    ``pre_samples`` before to ``post_samples`` after each triggering marker's
    absolute sample position.

    A rolling ``pre_samples``-long history buffer supplies the pre-trigger part
    even though DATA32 blocks only carry markers with a block-relative offset;
    the buffer is maintained every block regardless of capture state. Markers
    arriving during an active capture never start a new epoch (pulses/bursts
    are far apart relative to the window), but the sample offsets of those that
    match ``trigger_pred`` are recorded and yielded as ``trigger_offsets``
    (relative to the triggering marker, so the list always starts with 0) —
    burst mode uses them to mark the actually received pulses.
    """
    history = None
    capturing = False
    collected: list[np.ndarray] = []
    have = 0
    pre_seg = None
    marker = None
    offsets: list[int] = []
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
                    offsets = [0]
                    for m2 in f["markers"]:
                        off = m2["n_position"] - p
                        if m2 is not m and trigger_pred(m2) and 0 < off < post_samples:
                            offsets.append(off)
                    capturing = True
                    break
                elif on_unmatched is not None and m["type"] != "New Segment":
                    on_unmatched(m)
        else:
            base = have
            for m in f["markers"]:
                off = base + m["n_position"]
                if trigger_pred(m) and off < post_samples:
                    offsets.append(off)
            if have < post_samples:
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
            yield np.concatenate([pre_seg, post_arr], axis=1), marker, sorted(offsets)
            capturing = False


# --------------------------------------------------------------------------- #
# Rendering (matplotlib only — works headless with Agg, testable)
# --------------------------------------------------------------------------- #
class ItepViewer:
    """Owns the figure and renders an epoch given explicit y-limits.

    Two layouts: ``"single"`` (the original 5-panel view) and ``"burst"``
    (EMG on top, burst TEP + per-pulse butterfly in the middle, one topomap
    per pulse below). Axes are rebuilt whenever the requested mode changes;
    ``topo_axes`` always lists the clickable topomap axes of the current mode.
    """

    def __init__(self, fig, eeg_names, pos, full_idx):
        self.fig = fig
        self.eeg_names = eeg_names
        self.pos = pos
        self.full_idx = full_idx
        self.mode = None
        self.topo_axes: list = []
        self._build_axes("single")

    def _build_axes(self, mode, n_topo=3):
        self.fig.clear()
        self.cax = None
        if mode == "single":
            # Topomap row on top (plus a narrow colorbar column), EMG / iTEP /
            # GMFP in the middle, the long TEP across the bottom. The grid is
            # always >= 3 wide so the middle row's three panels fit regardless
            # of how many topomap latencies were requested.
            cols = max(3, n_topo)
            gs = self.fig.add_gridspec(3, cols + 1, width_ratios=[*([1] * cols), 0.08])
            self.topo_axes = [self.fig.add_subplot(gs[0, i]) for i in range(n_topo)]
            self.cax = self.fig.add_subplot(gs[0, cols])
            self.ax_emg = self.fig.add_subplot(gs[1, 0])
            self.ax_short = self.fig.add_subplot(gs[1, 1])
            self.ax_gmfp = self.fig.add_subplot(gs[1, 2])
            self.ax_long = self.fig.add_subplot(gs[2, :cols])
        else:
            gs = self.fig.add_gridspec(3, n_topo + 1,
                                       width_ratios=[*([1] * n_topo), 0.08])
            self.ax_emg = self.fig.add_subplot(gs[0, :n_topo])
            split = max(1, (3 * n_topo) // 5)
            self.ax_tep = self.fig.add_subplot(gs[1, :split])
            self.ax_fly = self.fig.add_subplot(gs[1, split:n_topo])
            self.topo_axes = [self.fig.add_subplot(gs[2, i]) for i in range(n_topo)]
            self.cax = self.fig.add_subplot(gs[2, n_topo])
        self.mode = mode

    def _draw_topomap(self, ax, eeg_vals, selected, ylim, title):
        """Draw one topomap; returns the image (for a colorbar) or ``None``."""
        ax.clear()
        im = None
        if self.eeg_names:
            import mne
            mask = np.array([n in selected for n in self.eeg_names])
            im, _cn = mne.viz.plot_topomap(
                eeg_vals, self.pos, axes=ax, show=False, vlim=ylim,
                mask=mask, mask_params=dict(markersize=8, markerfacecolor="none",
                                            markeredgecolor="k", markeredgewidth=1.5),
                sensors=True, contours=4,
            )
        ax.set_title(title)
        return im

    def _draw_emg(self, ax, epoch_uv, times_ms, emg_channels, window, ylim,
                  emg_guides, title, mark_lines=None):
        """EMG panel: one coloured trace per EMG channel, with a legend.

        Colours start at C1 so a single-EMG setup keeps the orange trace it
        had before a second electrode was added.
        """
        ax.clear()
        i0, i1 = _slice_range(times_ms, *window)
        for k, (idx, name) in enumerate(emg_channels):
            ax.plot(times_ms[i0:i1], epoch_uv[idx, i0:i1], lw=1.0,
                    color=f"C{(1 + k) % 10}", label=name)
        if mark_lines is not None:
            mark_lines(ax)
        else:
            ax.axvline(0.0, color="k", lw=0.8, ls="--")
        # MEP amplitude references: dotted at the inner pair, dashed at the outer.
        inner, outer = emg_guides
        for v in (-inner, inner):
            ax.axhline(v, color="0.6", lw=0.8, ls=":")
        for v in (-outer, outer):
            ax.axhline(v, color="0.6", lw=0.8, ls="--")
        ax.set_xlim(*window)
        ax.set_ylim(*ylim)
        ax.set_ylabel("µV")
        ax.set_title(title)
        if emg_channels:
            ax.legend(loc="upper right", fontsize=8)

    def render(self, epoch_uv, times_ms, emg_channels, selected,
              topo_latencies, emg_window, short_window, long_window, ylim,
              emg_guides=(25.0, 50.0)):
        if self.mode != "single" or len(self.topo_axes) != len(topo_latencies):
            self._build_axes("single", n_topo=len(topo_latencies))
        # --- EMG ---
        self._draw_emg(self.ax_emg, epoch_uv, times_ms, emg_channels,
                       emg_window, ylim, emg_guides, "EMG")
        self.ax_emg.set_xlabel("ms from pulse")

        # --- topomaps at fixed latencies ---
        eeg = epoch_uv[self.full_idx] if self.full_idx else np.empty((0, epoch_uv.shape[1]))
        im = None
        for k, (ax_t, lat) in enumerate(zip(self.topo_axes, topo_latencies, strict=False)):
            t_idx = int(np.argmin(np.abs(times_ms - lat)))
            # The click hint belongs on the panel group, not on every map.
            title = f"{lat:g} ms" + ("\n(click a sensor to toggle)" if k == 0 else "\n")
            im = self._draw_topomap(ax_t, eeg[:, t_idx], selected, ylim, title) or im
        if self.cax is not None:
            self.cax.clear()
            if im is not None:
                self.fig.colorbar(im, cax=self.cax, label="µV")
            else:
                self.cax.set_axis_off()

        # --- selected electrodes, early window (iTEP) ---
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
        ax.set_title("iTEP")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        # --- GMFP across all EEG electrodes ---
        ax = self.ax_gmfp
        ax.clear()
        i0, i1 = _slice_range(times_ms, *long_window)
        g = gmfp(eeg)
        ax.plot(times_ms[i0:i1], g[i0:i1], lw=1.2, color="C4")
        ax.axvline(0.0, color="k", lw=0.8, ls="--")
        for lat in topo_latencies:
            ax.axvline(lat, color="0.6", lw=0.8, ls=":")
        ax.set_xlim(*long_window)
        # GMFP is non-negative, so the shared +/- y-scale would waste half the
        # axis; scale it to its own peak instead.
        peak = float(g[i0:i1].max()) if i1 > i0 and g.size else 1.0
        ax.set_ylim(0.0, peak * 1.1 if peak > 0 else 1.0)
        ax.set_xlabel("ms from pulse")
        ax.set_ylabel("µV")
        ax.set_title(f"GMFP ({len(self.eeg_names)} ch)")

        # --- selected electrodes, long timescale (TEP) ---
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
        ax.set_title("TEP")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        self.fig.tight_layout()

    def render_burst(self, epoch_uv, times_ms, emg_channels, selected,
                     expected_ms, actual_ms, align_ms, topo_lat,
                     emg_window, tep_window, fly_window, ylim, sfreq,
                     emg_guides=(25.0, 50.0)):
        """Burst layout: times are ms relative to the *first* pulse.

        ``expected_ms`` are the nominal pulse times (grey dotted lines),
        ``actual_ms`` the offsets of triggers that really arrived (red lines),
        ``align_ms`` the per-pulse alignment times (actual when received,
        expected otherwise) used for the butterfly panel and the topomaps.
        """
        if self.mode != "burst" or len(self.topo_axes) != len(expected_ms):
            self._build_axes("burst", n_topo=len(expected_ms))

        def pulse_lines(ax):
            for t in expected_ms:
                ax.axvline(t, color="0.6", lw=0.9, ls=":")
            for t in actual_ms:
                ax.axvline(t, color="red", lw=1.0, alpha=0.8)

        # --- EMG until 50 ms after the last expected pulse ---
        self._draw_emg(self.ax_emg, epoch_uv, times_ms, emg_channels,
                       emg_window, ylim, emg_guides, "EMG — burst",
                       mark_lines=pulse_lines)
        self.ax_emg.set_xlabel("ms from first pulse")

        # --- TEP across the whole burst ---
        ax = self.ax_tep
        ax.clear()
        i0, i1 = _slice_range(times_ms, *tep_window)
        for name in sorted(selected):
            if name in self.eeg_names:
                idx = self.full_idx[self.eeg_names.index(name)]
                ax.plot(times_ms[i0:i1], epoch_uv[idx, i0:i1], lw=1.2, label=name)
        pulse_lines(ax)
        ax.set_xlim(*tep_window)
        ax.set_ylim(*ylim)
        ax.set_xlabel("ms from first pulse")
        ax.set_ylabel("µV")
        ax.set_title("TEP (burst)")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        # --- per-pulse butterfly: segments aligned to each pulse ---
        ax = self.ax_fly
        ax.clear()
        n_seg = max(1, int(round((fly_window[1] - fly_window[0]) * sfreq / 1000.0)))
        rel_ms = fly_window[0] + np.arange(n_seg) / sfreq * 1000.0
        n_pulses = len(align_ms)
        for ci, name in enumerate(sorted(selected)):
            if name not in self.eeg_names:
                continue
            idx = self.full_idx[self.eeg_names.index(name)]
            color = f"C{ci % 10}"
            segs = []
            for k, t_k in enumerate(align_ms):
                j0 = int(np.searchsorted(times_ms, t_k + fly_window[0], side="left"))
                seg = epoch_uv[idx, j0:j0 + n_seg]
                if seg.shape[0] < n_seg:
                    continue
                segs.append(seg)
                alpha = 0.25 + 0.55 * (k / max(1, n_pulses - 1))
                ax.plot(rel_ms, seg, lw=0.9, color=color, alpha=alpha)
            if segs:
                ax.plot(rel_ms, np.mean(segs, axis=0), lw=2.2, color=color, label=name)
        ax.axvline(0.0, color="k", lw=0.8, ls="--")
        ax.set_xlim(*fly_window)
        ax.set_ylim(*ylim)
        ax.set_xlabel("ms from each pulse")
        ax.set_ylabel("µV")
        ax.set_title("per-pulse average (thick; faint = pulses 1..n)")
        if selected:
            ax.legend(loc="upper right", fontsize=8)

        # --- one topomap per pulse ---
        eeg = epoch_uv[self.full_idx] if self.full_idx else np.empty((0, epoch_uv.shape[1]))
        im = None
        for k, (ax_t, t_k) in enumerate(zip(self.topo_axes, align_ms, strict=False)):
            t_idx = int(np.argmin(np.abs(times_ms - (t_k + topo_lat))))
            im = self._draw_topomap(ax_t, eeg[:, t_idx], selected, ylim,
                                    f"pulse {k + 1} +{topo_lat:g} ms") or im
        if self.cax is not None:
            self.cax.clear()
            if im is not None:
                self.fig.colorbar(im, cax=self.cax, label="µV")
            else:
                self.cax.set_axis_off()

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
    if not topo_latencies:
        raise SystemExit("--topo-latencies needs at least one value")
    emg_guides = tuple(sorted(parse_float_list(args.emg_guides)))
    if len(emg_guides) != 2:
        raise SystemExit("--emg-guides needs exactly two comma-separated values")

    # Burst mode: expected pulse times relative to the first trigger, and the
    # display windows derived from them. The capture window is the superset of
    # both modes so a captured epoch can be re-rendered in either mode.
    burst_expected = [k * args.burst_isi for k in range(args.burst_count)]
    burst_last = burst_expected[-1]
    burst_emg_window = (emg_window[0], burst_last + 50.0)
    burst_tep_window = (short_window[0], burst_last + 20.0)
    burst_topo_lat = args.burst_topo_latency

    pre_ms, post_ms = capture_bounds(emg_window, short_window, long_window, topo_latencies)
    post_ms = max(post_ms, burst_emg_window[1], burst_tep_window[1],
                  burst_last + short_window[1], burst_last + burst_topo_lat)

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
    emg_channels = resolve_emg_channels(channel_names, args.emg_electrodes)
    if not emg_channels:
        print(f"warning: no channel matches {args.emg_electrodes!r}; "
              f"using {channel_names[0]!r} instead", file=sys.stderr)
        emg_channels = [(0, channel_names[0])]
    emg_idxs = [i for i, _n in emg_channels]
    print(f"EMG channels: {', '.join(n for _i, n in emg_channels)}", file=sys.stderr)

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
    ttk.Label(info, text="EMG: " + ", ".join(n for _i, n in emg_channels)).grid(
        row=0, column=0, sticky="w")
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

    mode = {"value": "single"}
    burst_label = f"burst ({args.burst_count} × {1000.0 / args.burst_isi:g} Hz)"

    def toggle_mode():
        mode["value"] = "burst" if mode["value"] == "single" else "single"
        mode_btn.config(text="Mode: " + (burst_label if mode["value"] == "burst"
                                         else "single pulse"))
        redraw()

    mode_btn = ttk.Button(ctrl, text="Mode: single pulse", command=toggle_mode)
    mode_btn.grid(row=0, column=0, sticky="we")

    state = {"epoch": None}
    flow = {"last": None, "blocks": 0, "interval": 0.02, "ended": False}
    new_epoch = queue.Queue(maxsize=1)
    redrawing = {"busy": False}

    def compute_ylim(epoch_uv, times_ms, trigger_times_ms):
        idxs = emg_idxs + [full_idx[eeg_names.index(n)] for n in selected if n in eeg_names]
        if auto_var.get():
            lo, hi = auto_range_minmax(epoch_uv, times_ms, idxs, exclude_ms=args.exclude_ms,
                                       trigger_times_ms=trigger_times_ms)
            min_var.set(f"{lo:.1f}")
            max_var.set(f"{hi:.1f}")
            return lo, hi
        try:
            return float(min_var.get()), float(max_var.get())
        except ValueError:
            return auto_range_minmax(epoch_uv, times_ms, idxs, exclude_ms=args.exclude_ms,
                                     trigger_times_ms=trigger_times_ms)

    def redraw():
        # Setting min/max StringVars below fires their write-traces, which call
        # redraw again; guard against that reentrancy (see rda_viewer.gui_client).
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

    def _redraw_impl(item):
        epoch, offsets = item
        epoch_uv = epoch * res[:, None]
        n = epoch_uv.shape[1]
        times_ms = (np.arange(n) - pre_samples) / sfreq * 1000.0
        epoch_uv = baseline_correct(epoch_uv, times_ms)
        sel_var.set("selected: " + (", ".join(sorted(selected)) if selected else "(none)"))
        if mode["value"] == "burst":
            actual_ms = [o / sfreq * 1000.0 for o in offsets]
            align_ms = match_burst_triggers(actual_ms, burst_expected,
                                            tol_ms=args.burst_isi / 2.0)
            ylim = compute_ylim(epoch_uv, times_ms, align_ms)
            viewer.render_burst(epoch_uv, times_ms, emg_channels, selected,
                                burst_expected, actual_ms, align_ms, burst_topo_lat,
                                burst_emg_window, burst_tep_window, short_window,
                                ylim, sfreq, emg_guides=emg_guides)
        else:
            ylim = compute_ylim(epoch_uv, times_ms, [0.0])
            viewer.render(epoch_uv, times_ms, emg_channels, selected, topo_latencies,
                         emg_window, short_window, long_window, ylim,
                         emg_guides=emg_guides)
        canvas.draw_idle()

    def on_click(event):
        for ax_t in viewer.topo_axes:
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
        def trigger_pred(m):
            return m["type"] != "New Segment"  # react to all triggers

        for epoch, _marker, offsets in epoch_stream_pre_post(msgs, pre_samples, post_samples,
                                                             trigger_pred, on_data=on_data):
            state["epoch"] = (epoch, offsets)
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
    ap.add_argument("--emg-electrodes", "--emg-electrode", dest="emg_electrodes",
                    default="EMG*",
                    help="comma-separated channel names or globs for the EMG panel; "
                         "the default 'EMG*' shows EMG, EMG2, ... automatically")
    ap.add_argument("--electrode", default="C3",
                    help="initially selected electrode for the TEP panels")
    ap.add_argument("--topo-latencies", default="2.3,3.5,4.8",
                    help="comma-separated post-pulse latencies (ms) for the "
                         "single-pulse topomaps")
    ap.add_argument("--burst-topo-latency", type=float, default=3.0,
                    help="post-pulse latency (ms) for burst mode's per-pulse topomaps")
    ap.add_argument("--emg-guides", default="25,50",
                    help="two amplitudes (µV) for the EMG reference lines; the "
                         "inner pair is dotted, the outer dashed")
    ap.add_argument("--emg-window", default="-10,50", help="ms range shown in the EMG panel")
    ap.add_argument("--short-window", default="-2,10",
                    help="ms range shown in the early TEP panel")
    ap.add_argument("--long-window", default="-10,150",
                    help="ms range shown in the long-timescale TEP panel")
    ap.add_argument("--exclude-ms", type=float, default=2.0,
                    help="auto y-scale ignores +/- this many ms around each pulse (the artifact)")
    ap.add_argument("--burst-count", type=int, default=5,
                    help="number of pulses per burst in burst mode")
    ap.add_argument("--burst-isi", type=float, default=20.0,
                    help="inter-pulse interval (ms) within a burst (20 ms = 50 Hz)")
    args = ap.parse_args(argv)
    run_gui(args)


if __name__ == "__main__":
    main()

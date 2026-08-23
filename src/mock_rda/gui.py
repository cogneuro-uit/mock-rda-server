"""Tkinter control panel for a running mock server.

Shows the stream configuration (source, channels, sample rate, block size,
ports), live counters (elapsed time, data streamed, blocks, markers, timing
drift), and manual trigger injection — single pulses and configurable bursts.
Injection goes through the same per-block queue as the keypress and
control-socket paths, so the latency contract is identical.

Started by the CLI unless ``--no-gui`` is given; ``tkinter`` is imported lazily
so headless installs only pay for it when the window is actually requested.
"""

from __future__ import annotations

import threading

from .markers import AT_NEXT, Marker


# --------------------------------------------------------------------------- #
# Pure helpers (no Tk) — unit-testable
# --------------------------------------------------------------------------- #
def format_duration(seconds: float) -> str:
    """``93.4`` -> ``"01:33"``; past an hour -> ``"1:02:03"``."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_bytes(n: float) -> str:
    """Human-readable byte count (binary units, 3 significant-ish digits)."""
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024.0 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"  # unreachable; keeps the return type obvious


def format_count(n: int) -> str:
    """Thousands-separated integer, e.g. ``415_000`` -> ``"415 000"``."""
    return f"{int(n):,}".replace(",", " ")


def describe_source(source) -> str:
    """Short label for the source backing the stream."""
    path = getattr(source, "vhdr_path", None)
    if path is not None:
        return f"{path.name}{' (loop)' if getattr(source, 'loop', False) else ''}"
    return "synthetic"


def stream_settings(server, control_port=None):
    """Static ``(label, value)`` rows describing the stream configuration."""
    src = server.source
    block_ms = src.block_points / src.sample_rate * 1000.0
    rows = [
        ("source", describe_source(src)),
        ("channels", str(src.n_channels)),
        ("sample rate", f"{src.sample_rate:g} Hz"),
        ("block", f"{src.block_points} pts / {block_ms:g} ms"),
        ("RDA port", str(server.port)),
    ]
    if control_port is not None:
        rows.append(("control port", str(control_port)))
    return rows


def live_stats(server):
    """Live ``(label, value)`` rows: elapsed, data volume, counters, drift.

    ``drift`` compares wall-clock elapsed time against the stream time actually
    emitted (``samples / sample_rate``). A steadily growing positive value means
    the server is falling behind real time — the number to watch when testing
    closed-loop timing.
    """
    src = server.source
    elapsed = server.stream_seconds
    stream_s = server.samples_streamed / src.sample_rate if src.sample_rate else 0.0
    drift = elapsed - stream_s
    return [
        ("elapsed", format_duration(elapsed)),
        ("streamed", f"{format_duration(stream_s)} "
                     f"({format_count(server.samples_streamed)} samples)"),
        ("data", format_bytes(server.bytes_streamed)),
        ("blocks", format_count(server.blocks_streamed)),
        ("markers", format_count(server.markers_streamed)),
        ("drift", f"{drift * 1e3:+.0f} ms"),
    ]


# --------------------------------------------------------------------------- #
# Tk application
# --------------------------------------------------------------------------- #
def run_control_gui(server, stop_event: threading.Event, *,
                    burst_count: int = 5, burst_isi_ms: float = 20.0,
                    control_port=None, on_inject=None, on_ready=None) -> None:
    """Run the Tk control panel until the window is closed or ``stop_event`` set.

    Must run in the main thread (Tk requirement). Raises ``tkinter.TclError``
    when no display is available — callers should fall back to headless mode;
    ``on_ready`` is only called once the window exists, so the fallback can
    tell whether anything was started. Closing the window sets ``stop_event``
    so the CLI shuts the server down.
    """
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("mock-rda server")
    if on_ready:
        on_ready()

    status_var = tk.StringVar(value="starting…")
    status = tk.Label(root, textvariable=status_var, anchor="w", fg="white",
                      bg="#43a047", font=("TkDefaultFont", 10, "bold"), padx=6)
    status.pack(side=tk.TOP, fill=tk.X)

    frm = ttk.Frame(root, padding=8)
    frm.pack(fill=tk.BOTH, expand=True)

    def value_grid(parent, title, rows, column):
        """A labelled frame of ``label: value`` rows; returns the value StringVars.

        The vars are also stashed on the frame: a StringVar with no live
        reference is garbage-collected and its label silently renders empty.
        """
        box = ttk.LabelFrame(parent, text=title)
        box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        out = []
        for r, (label, value) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="e", padx=(4, 6))
            var = tk.StringVar(value=str(value))
            ttk.Label(box, textvariable=var, font=("TkFixedFont", 9)).grid(
                row=r, column=1, sticky="w", padx=(0, 4))
            out.append(var)
        box._value_vars = out
        return out

    value_grid(frm, "stream", stream_settings(server, control_port), 0)
    stat_vars = value_grid(frm, "live", live_stats(server), 1)
    frm.columnconfigure(0, weight=1)
    frm.columnconfigure(1, weight=1)

    # --- injection controls ---
    inject_row = ttk.Frame(frm)
    inject_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))

    type_var = tk.StringVar(value="Stimulus")
    desc_var = tk.StringVar(value="S  1")
    marker_box = ttk.LabelFrame(inject_row, text="marker")
    marker_box.pack(side=tk.LEFT, fill=tk.Y)
    ttk.Label(marker_box, text="type").grid(row=0, column=0, sticky="e")
    ttk.Entry(marker_box, textvariable=type_var, width=10).grid(row=0, column=1, padx=(4, 4))
    ttk.Label(marker_box, text="descr.").grid(row=1, column=0, sticky="e")
    ttk.Entry(marker_box, textvariable=desc_var, width=10).grid(row=1, column=1, padx=(4, 4))

    count_var = tk.StringVar(value=str(burst_count))
    isi_var = tk.StringVar(value=f"{burst_isi_ms:g}")
    burst_box = ttk.LabelFrame(inject_row, text="burst")
    burst_box.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
    ttk.Label(burst_box, text="pulses").grid(row=0, column=0, sticky="e")
    ttk.Spinbox(burst_box, from_=2, to=100, textvariable=count_var,
                width=5).grid(row=0, column=1, padx=(4, 4))
    ttk.Label(burst_box, text="ISI (ms)").grid(row=1, column=0, sticky="e")
    ttk.Entry(burst_box, textvariable=isi_var, width=5).grid(row=1, column=1, padx=(4, 4))

    last_var = tk.StringVar(value="")

    def _marker() -> Marker:
        return Marker(sample=AT_NEXT, type=type_var.get() or "Stimulus",
                      description=desc_var.get() or "S  1")

    def inject_single(*_event):
        m = _marker()
        server.inject(m)
        last_var.set(f"injected {m.type}/{m.description!r}")
        if on_inject:
            on_inject(m)

    def inject_burst():
        try:
            count = int(count_var.get())
            isi_ms = float(isi_var.get())
            if count < 1 or isi_ms <= 0:
                raise ValueError
        except ValueError:
            last_var.set("burst: pulses must be ≥ 1 and ISI > 0 ms")
            return
        m = _marker()
        server.inject_burst(m, count, isi_ms)
        last_var.set(f"injected burst: {count} × {m.description!r} "
                     f"@ {1000.0 / isi_ms:g} Hz")
        if on_inject:
            on_inject(m)

    btns = ttk.Frame(frm)
    btns.grid(row=2, column=0, columnspan=2, pady=(8, 0), sticky="we")
    ttk.Button(btns, text="Inject trigger", command=inject_single).pack(
        side=tk.LEFT, expand=True, fill=tk.X)
    ttk.Button(btns, text="Inject burst", command=inject_burst).pack(
        side=tk.LEFT, expand=True, fill=tk.X, padx=(6, 0))
    ttk.Label(frm, textvariable=last_var, foreground="#357").grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
    root.bind("<Return>", inject_single)  # parity with the CLI keypress path

    def on_close():
        stop_event.set()
        root.destroy()

    def poll():
        if stop_event.is_set():
            root.destroy()
            return
        for var, (_label, value) in zip(stat_vars, live_stats(server)):
            var.set(value)
        sched = server.scheduler
        streaming = server.blocks_streamed > 0
        status_var.set(f"clients: {server.client_count}   "
                       f"jitter mean {sched.mean_abs_jitter * 1e3:.2f} ms / "
                       f"max {sched.max_abs_jitter * 1e3:.2f} ms")
        status.config(bg="#43a047" if streaming else "#fb8c00")
        root.after(250, poll)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(250, poll)
    root.mainloop()

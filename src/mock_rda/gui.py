"""Tkinter control panel for a running mock server.

Manual trigger injection — single pulses and configurable bursts (pulse count
and inter-stimulus interval) — plus a live status line (connected clients,
scheduler jitter). Injection goes through the same per-block queue as the
keypress and control-socket paths, so the latency contract is identical.

Started by the CLI unless ``--no-gui`` is given; ``tkinter`` is imported lazily
so headless installs only pay for it when the window is actually requested.
"""

from __future__ import annotations

import threading

from .markers import AT_NEXT, Marker


def run_control_gui(server, stop_event: threading.Event, *,
                    burst_count: int = 5, burst_isi_ms: float = 20.0,
                    on_inject=None, on_ready=None) -> None:
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
    tk.Label(root, textvariable=status_var, anchor="w", fg="white", bg="#43a047",
             font=("TkDefaultFont", 10, "bold"), padx=6).pack(side=tk.TOP, fill=tk.X)

    frm = ttk.Frame(root, padding=8)
    frm.pack(fill=tk.BOTH, expand=True)

    type_var = tk.StringVar(value="Stimulus")
    desc_var = tk.StringVar(value="S  1")
    marker_box = ttk.LabelFrame(frm, text="marker")
    marker_box.grid(row=0, column=0, sticky="ns")
    ttk.Label(marker_box, text="type").grid(row=0, column=0, sticky="e")
    ttk.Entry(marker_box, textvariable=type_var, width=10).grid(row=0, column=1, padx=(4, 4))
    ttk.Label(marker_box, text="descr.").grid(row=1, column=0, sticky="e")
    ttk.Entry(marker_box, textvariable=desc_var, width=10).grid(row=1, column=1, padx=(4, 4))

    count_var = tk.StringVar(value=str(burst_count))
    isi_var = tk.StringVar(value=f"{burst_isi_ms:g}")
    burst_box = ttk.LabelFrame(frm, text="burst")
    burst_box.grid(row=0, column=1, padx=(10, 0), sticky="ns")
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
    btns.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="we")
    ttk.Button(btns, text="Inject trigger", command=inject_single).pack(
        side=tk.LEFT, expand=True, fill=tk.X)
    ttk.Button(btns, text="Inject burst", command=inject_burst).pack(
        side=tk.LEFT, expand=True, fill=tk.X, padx=(6, 0))
    ttk.Label(frm, textvariable=last_var, foreground="#357").grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
    root.bind("<Return>", inject_single)  # parity with the CLI keypress path

    def on_close():
        stop_event.set()
        root.destroy()

    def poll():
        if stop_event.is_set():
            root.destroy()
            return
        sched = server.scheduler
        status_var.set(f"clients: {server.client_count}   "
                       f"jitter mean {sched.mean_abs_jitter * 1e3:.2f} ms / "
                       f"max {sched.max_abs_jitter * 1e3:.2f} ms")
        root.after(250, poll)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(250, poll)
    root.mainloop()

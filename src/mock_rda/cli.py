"""``mock-rda`` command-line entry point.

::

    mock-rda file  RECORDING.vhdr [--loop] [--block-ms 4] [--port 51244] [--control-port 51299]
    mock-rda synth --channels 64 --rate 5000 [--block-ms 4] [--tep-template default] [--port ...]

Both modes accept manual injection via keypress (press Enter) and the JSON
control socket. Realized timing jitter and connected-client count are printed
to stderr.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from .injector import ControlSocketServer, keypress_loop
from .markers import Marker
from .server import RDA_FLOAT_PORT, Server
from .sources import FileSource, SyntheticSource, TEPTemplate


def _block_points(sample_rate: float, block_ms: float) -> int:
    return max(1, round(sample_rate * block_ms / 1000.0))


def _build_source(args) -> FileSource | SyntheticSource:
    if args.mode == "file":
        source = FileSource(args.vhdr, loop=args.loop)
        source.block_points = _block_points(source.sample_rate, args.block_ms)
        return source
    tep = TEPTemplate() if args.tep_template != "none" else None
    bp = _block_points(args.rate, args.block_ms)
    return SyntheticSource(
        n_channels=args.channels,
        sample_rate=args.rate,
        block_points=bp,
        seed=args.seed,
        stim_period_s=args.stim_period,
        tep=tep,
        response_amp=args.response_amp,
        jitter_ms=args.jitter_ms,
        max_samples=(round(args.duration * args.rate) if args.duration else None),
    )


def _parse_args(argv):
    ap = argparse.ArgumentParser(prog="mock-rda", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--block-ms", type=float, default=4.0, help="block duration in ms")
    common.add_argument("--port", type=int, default=RDA_FLOAT_PORT, help="RDA TCP port")
    common.add_argument("--control-port", type=int, default=51299, help="JSON control socket port")
    common.add_argument("--host", default="0.0.0.0")
    common.add_argument("--name-encoding", default="cp1252",
                        help="text codec for START channel names")

    pf = sub.add_parser("file", parents=[common], help="stream a .vhdr/.eeg/.vmrk triplet")
    pf.add_argument("vhdr")
    pf.add_argument("--loop", action="store_true", help="repeat the file seamlessly")

    ps = sub.add_parser("synth", parents=[common], help="stream a synthetic source")
    ps.add_argument("--channels", type=int, default=32)
    ps.add_argument("--rate", type=float, default=5000.0)
    ps.add_argument("--seed", type=int, default=0)
    ps.add_argument("--stim-period", type=float, default=None,
                    help="seconds between scheduled stimulus markers (default: none)")
    ps.add_argument("--tep-template", default="default", choices=["default", "none"])
    ps.add_argument("--response-amp", type=float, default=1.0)
    ps.add_argument("--jitter-ms", type=float, default=0.0)
    ps.add_argument("--duration", type=float, default=None, help="stop after N seconds")

    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    source = _build_source(args)

    server = Server(source, host=args.host, port=args.port, name_encoding=args.name_encoding)

    def _announce(marker: Marker) -> None:
        print(f"[mock-rda] injected {marker.type!r}/{marker.description!r} "
              f"(at={'next' if marker.sample < 0 else marker.sample})", file=sys.stderr)

    control = ControlSocketServer(server.injector, port=args.control_port, on_inject=_announce)

    server.start()
    cport = control.start()
    print(f"[mock-rda] control socket on 127.0.0.1:{cport} "
          f"(send one-line JSON, e.g. {{\"type\":\"Stimulus\",\"at\":\"next\"}})", file=sys.stderr)

    stop_event = threading.Event()
    threading.Thread(target=keypress_loop, args=(server.injector, stop_event, _announce),
                     name="keypress", daemon=True).start()

    def _handle_sigint(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        while not stop_event.is_set():
            if server._acq_thread is not None and not server._acq_thread.is_alive():
                # Acquisition finished (e.g. file EOF without --loop): keep running
                # so post-stream keep-alives continue until the user quits.
                pass
            time.sleep(1.0)
            sched = server.scheduler
            print(f"[mock-rda] clients={server.client_count} "
                  f"jitter last={sched.last_jitter * 1e3:+.3f} ms "
                  f"mean={sched.mean_abs_jitter * 1e3:.3f} ms "
                  f"max={sched.max_abs_jitter * 1e3:.3f} ms", file=sys.stderr)
    finally:
        print("[mock-rda] shutting down…", file=sys.stderr)
        server.stop()
        control.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

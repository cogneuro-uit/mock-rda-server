"""Marker dataclass + thread-safe injection queue.

A :class:`Marker` carries an **absolute, 0-based** sample position. The protocol
serializer converts it to a block-relative position when emitting DATA32. The
:class:`InjectionQueue` is the shared sink for the three injection paths
(keypress, control socket, in-process API); the server drains it once per block.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# Sentinel meaning "place this marker at the start of the next emitted block".
AT_NEXT = -1


@dataclass
class Marker:
    """A single RDA marker (trigger).

    Attributes
    ----------
    sample:
        Absolute sample position, 0-based, relative to the start of the stream.
        BrainVision ``.vmrk`` positions are 1-based; subtract 1 when importing.
    type:
        Marker type string, e.g. ``"Stimulus"`` / ``"Response"`` / ``"New Segment"``.
    description:
        Marker description, e.g. ``"S  1"`` / ``"R 15"`` (Recorder pads to width).
    points:
        Duration in samples (1 for an instantaneous pulse).
    channel:
        Channel index, or ``-1`` for all channels.
    """

    sample: int
    type: str = "Stimulus"
    description: str = "S  1"
    points: int = 1
    channel: int = -1


class InjectionQueue:
    """Thread-safe queue of pending markers with absolute target samples.

    Markers requested for "now" carry ``sample == AT_NEXT`` and are resolved to a
    concrete absolute sample (the next block's start) when the server drains the
    queue. Concrete absolute samples are kept as-is so callers can schedule a
    pulse at a precise future sample.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[Marker] = []

    def inject(self, marker: Marker) -> None:
        """Add a marker to the queue (thread-safe)."""
        with self._lock:
            self._pending.append(marker)

    def drain_for_block(self, block_start: int, block_points: int) -> list[Marker]:
        """Return markers belonging to block ``[block_start, block_start+block_points)``.

        ``AT_NEXT`` markers are stamped at ``block_start``. Concrete markers whose
        sample falls before the block (already passed) are also emitted in this
        block, clamped to ``block_start``, so a late injection is never silently
        dropped. Markers targeting a later block remain queued.
        """
        block_end = block_start + block_points
        out: list[Marker] = []
        keep: list[Marker] = []
        with self._lock:
            for m in self._pending:
                if m.sample == AT_NEXT or m.sample < block_end:
                    sample = block_start if m.sample == AT_NEXT else max(m.sample, block_start)
                    out.append(Marker(sample, m.type, m.description, m.points, m.channel))
                else:
                    keep.append(m)
            self._pending = keep
        out.sort(key=lambda m: m.sample)
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

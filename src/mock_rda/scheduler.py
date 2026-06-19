"""Absolute-deadline block pacing with low jitter.

Block ``n`` is due at ``t0 + n * block_duration``. We sleep coarsely until just
before the deadline, then busy-wait the final ~1 ms to trim scheduler jitter.
Deadlines are anchored to a single ``t0`` so pacing never drifts (we never
``sleep(block_duration)`` cumulatively).
"""

from __future__ import annotations

import time


class BlockScheduler:
    """Paces an acquisition loop to real time and tracks realized jitter."""

    def __init__(self, block_duration: float, busy_window: float = 0.0015) -> None:
        self.block_duration = block_duration
        self.busy_window = busy_window
        self._t0 = 0.0
        self._n = 0
        self.max_abs_jitter = 0.0
        self.last_jitter = 0.0
        self._jitter_sum = 0.0
        self._jitter_count = 0

    def reset(self) -> None:
        """Anchor ``t0`` to now and clear jitter stats. Call before block 0."""
        self._t0 = time.monotonic()
        self._n = 0
        self.max_abs_jitter = 0.0
        self.last_jitter = 0.0
        self._jitter_sum = 0.0
        self._jitter_count = 0

    def wait_next(self) -> float:
        """Block until the next block's deadline; return realized jitter (s).

        Jitter is ``actual_wake - deadline`` (positive == late). Call once per
        block, *after* emitting the current block.
        """
        self._n += 1
        deadline = self._t0 + self._n * self.block_duration
        sleep_for = deadline - time.monotonic() - self.busy_window
        if sleep_for > 0:
            time.sleep(sleep_for)
        while time.monotonic() < deadline:
            pass
        jitter = time.monotonic() - deadline
        self.last_jitter = jitter
        self.max_abs_jitter = max(self.max_abs_jitter, abs(jitter))
        self._jitter_sum += abs(jitter)
        self._jitter_count += 1
        return jitter

    @property
    def mean_abs_jitter(self) -> float:
        if not self._jitter_count:
            return 0.0
        return self._jitter_sum / self._jitter_count

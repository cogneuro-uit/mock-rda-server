"""Synthetic source: pink noise + oscillations + parametric evoked response.

The synthetic source is the preferred input for validating per-pulse logic
(staircase, detection) because you control ground truth exactly: stimulus
markers fire on a deterministic schedule (or via injection) and add a
configurable TEP/MEP template at a known latency, amplitude, and jitter.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from ..markers import Marker
from .base import SourceBase

# Standard 10-20 names used to label synthetic channels (cycled/truncated).
_DEFAULT_NAMES = [
    "Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "FC5", "FC1", "FC2", "FC6",
    "T7", "C3", "Cz", "C4", "T8", "CP5", "CP1", "CP2", "CP6", "P7", "P3",
    "Pz", "P4", "P8", "O1", "Oz", "O2", "FT9", "FT10", "TP9", "TP10",
]


@dataclass
class TEPTemplate:
    """Parametric evoked response built from damped sinusoids.

    Each component is ``(latency_ms, freq_hz, decay_ms, amp_uv)`` and contributes
    ``amp * exp(-(t-latency)/decay) * sin(2*pi*freq*(t-latency)/1000)`` for
    ``t >= latency`` (``t`` in ms). The default loosely mimics a TMS-evoked
    potential (early positivity, N40, P60, N100).
    """

    components: list[tuple[float, float, float, float]] = field(
        default_factory=lambda: [
            (20.0, 90.0, 6.0, 6.0),
            (35.0, 40.0, 15.0, -8.0),
            (60.0, 25.0, 25.0, 7.0),
            (100.0, 12.0, 40.0, -5.0),
        ]
    )
    duration_ms: float = 300.0

    def waveform(self, sample_rate: float) -> np.ndarray:
        n = max(1, int(round(self.duration_ms * sample_rate / 1000.0)))
        t = np.arange(n) / sample_rate * 1000.0  # ms
        out = np.zeros(n, dtype=np.float64)
        for latency, freq, decay, amp in self.components:
            mask = t >= latency
            tt = t[mask] - latency
            out[mask] += amp * np.exp(-tt / decay) * np.sin(2 * np.pi * freq * tt / 1000.0)
        return out.astype(np.float32)


def _resolve_names(n_channels: int, names: list[str] | None) -> list[str]:
    if names is not None:
        if len(names) != n_channels:
            raise ValueError(f"got {len(names)} names for {n_channels} channels")
        return list(names)
    out = []
    for i in range(n_channels):
        base = _DEFAULT_NAMES[i] if i < len(_DEFAULT_NAMES) else f"Ch{i + 1}"
        out.append(base)
    return out


def default_channel_names(n_channels: int) -> list[str]:
    """The built-in 10-20 style names used when no explicit list is given."""
    return _resolve_names(n_channels, None)


class SyntheticSource(SourceBase):
    """Continuous synthetic EEG with scheduled and injectable evoked responses."""

    def __init__(
        self,
        n_channels: int = 32,
        sample_rate: float = 5000.0,
        block_points: int | None = None,
        *,
        channel_names: list[str] | None = None,
        resolutions=None,
        seed: int = 0,
        noise_amp: float = 5.0,
        alpha_amp: float = 10.0,
        beta_amp: float = 3.0,
        stim_period_s: float | None = None,
        stim_type: str = "Stimulus",
        stim_description: str = "S  1",
        tep: TEPTemplate | None = None,
        response_amp: float = 1.0,
        jitter_ms: float = 0.0,
        spatial_weights=None,
        max_samples: int | None = None,
    ) -> None:
        self.n_channels = n_channels
        self.sample_rate = float(sample_rate)
        self.block_points = block_points or max(1, int(round(self.sample_rate * 0.004)))
        self.channel_names = _resolve_names(n_channels, channel_names)
        self.resolutions = (
            np.ones(n_channels, dtype="<f8")
            if resolutions is None
            else np.asarray(resolutions, dtype="<f8")
        )
        self._rng = np.random.default_rng(seed)
        self.noise_amp = noise_amp
        self.alpha_amp = alpha_amp
        self.beta_amp = beta_amp
        self.stim_period_samples = (
            int(round(stim_period_s * self.sample_rate)) if stim_period_s else None
        )
        self.stim_type = stim_type
        self.stim_description = stim_description
        self.tep = tep if tep is not None else (TEPTemplate() if response_amp else None)
        self.response_amp = response_amp
        self.jitter_ms = jitter_ms
        if spatial_weights is None:
            self.spatial_weights = np.ones(n_channels, dtype=np.float32)
        else:
            self.spatial_weights = np.asarray(spatial_weights, dtype=np.float32)
        self.max_samples = max_samples

        # Pink-noise (Paul Kellet) filter state, per channel.
        self._pink_state = np.zeros((n_channels, 7), dtype=np.float64)
        # Active evoked responses: list of (start_sample, waveform[n]).
        self._responses: list[tuple[int, np.ndarray]] = []
        self._next_stim = self.stim_period_samples  # absolute sample of next scheduled stim
        self._cursor = 0  # absolute sample of next block start

    # -- pink noise -------------------------------------------------------- #
    _PINK_A = np.array([0.99886, 0.99332, 0.96900, 0.86650, 0.55000, -0.7616, 0.0])
    _PINK_B = np.array([0.0555179, 0.0750759, 0.1538520, 0.3104856, 0.5329522, -0.0168980, 0.0])

    def _pink_block(self, n_points: int) -> np.ndarray:
        white = self._rng.standard_normal((self.n_channels, n_points))
        out = np.empty((self.n_channels, n_points), dtype=np.float64)
        s = self._pink_state
        a, b = self._PINK_A, self._PINK_B
        for i in range(n_points):
            w = white[:, i]
            s[:, :6] = a[:6] * s[:, :6] + np.outer(w, b[:6])
            pink = s[:, :7].sum(axis=1) + w * 0.5362
            s[:, 6] = w * 0.115926
            out[:, i] = pink
        return out * (self.noise_amp / 0.5)

    # -- evoked responses -------------------------------------------------- #
    def _schedule_response(self, sample: int) -> None:
        if self.tep is None or self.response_amp == 0:
            return
        jitter = 0
        if self.jitter_ms:
            jitter = int(round(self._rng.normal(0, self.jitter_ms) * self.sample_rate / 1000.0))
        wf = self.tep.waveform(self.sample_rate) * self.response_amp
        self._responses.append((sample + jitter, wf))

    def on_injected_markers(self, markers: list[Marker]) -> None:
        for m in markers:
            if m.type == self.stim_type or m.type == "Stimulus":
                self._schedule_response(m.sample)

    def _add_responses(self, block: np.ndarray, block_start: int) -> None:
        block_end = block_start + block.shape[1]
        still_active: list[tuple[int, np.ndarray]] = []
        for start, wf in self._responses:
            end = start + wf.shape[0]
            if end <= block_start:
                continue  # fully in the past
            lo = max(start, block_start)
            hi = min(end, block_end)
            if hi > lo:
                seg = wf[lo - start : hi - start]
                block[:, lo - block_start : hi - block_start] += np.outer(
                    self.spatial_weights, seg
                )
            if end > block_end:
                still_active.append((start, wf))
        self._responses = still_active

    # -- main generator ---------------------------------------------------- #
    def blocks(self) -> Iterator[tuple[np.ndarray, list[Marker]]]:
        np_ = self.block_points
        two_pi = 2 * np.pi
        while self.max_samples is None or self._cursor < self.max_samples:
            block_start = self._cursor
            n = np_
            if self.max_samples is not None:
                n = min(n, self.max_samples - block_start)
            idx = block_start + np.arange(n)
            block = self._pink_block(n)
            if self.alpha_amp:
                block += self.alpha_amp * np.sin(two_pi * 10.0 * idx / self.sample_rate)
            if self.beta_amp:
                block += self.beta_amp * np.sin(two_pi * 20.0 * idx / self.sample_rate)

            markers: list[Marker] = []
            # Scheduled stimuli that fall in this block.
            if self.stim_period_samples:
                while self._next_stim is not None and self._next_stim < block_start + n:
                    if self._next_stim >= block_start:
                        markers.append(
                            Marker(
                                self._next_stim,
                                self.stim_type,
                                self.stim_description,
                                1,
                                -1,
                            )
                        )
                        self._schedule_response(self._next_stim)
                    self._next_stim += self.stim_period_samples

            self._add_responses(block, block_start)
            self._cursor += n
            yield block.astype(np.float32), markers

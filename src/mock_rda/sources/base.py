"""Source interface shared by the synthetic and file-backed generators."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from ..markers import Marker


class SourceBase:
    """Base class for block sources.

    A source exposes the static stream configuration (``n_channels``,
    ``channel_names``, ``sample_rate``, ``resolutions``) and a :meth:`blocks`
    generator yielding ``(data, markers)`` where ``data`` is
    ``float32[n_channels, block_points]`` and ``markers`` is a list of
    :class:`~mock_rda.markers.Marker` with **absolute** sample positions.

    Subclasses must set the config attributes and implement :meth:`blocks`.
    """

    n_channels: int
    channel_names: list[str]
    sample_rate: float
    resolutions: np.ndarray
    block_points: int

    def blocks(self) -> Iterator[tuple[np.ndarray, list[Marker]]]:
        raise NotImplementedError

    def on_injected_markers(self, markers: list[Marker]) -> None:
        """Hook called by the server with markers injected for a block.

        The default implementation does nothing. The synthetic source overrides
        it to add an evoked response (TEP/MEP) for each injected stimulus, so a
        manually fired pulse produces a deterministic response in later blocks.
        """
        return None

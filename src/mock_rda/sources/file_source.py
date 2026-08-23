"""File source: stream a BrainVision ``.vhdr`` / ``.eeg`` / ``.vmrk`` triplet.

A dependency-light hand parser is the primary path because the server must
stream the **raw** stored values together with the per-channel resolutions
(exactly what a real Recorder sends — the client multiplies to get µV). MNE,
when installed, scales internally and is used in the test suite as an
independent cross-check rather than for streaming.

Supported: ``DataFormat=BINARY`` with ``BinaryFormat`` IEEE_FLOAT_32 / INT_16 /
IEEE_FLOAT_64, both MULTIPLEXED and VECTORIZED orientations. The ``.eeg`` is
memory-mapped, so multi-gigabyte recordings stream without loading into RAM.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ..markers import Marker
from .base import SourceBase

_BINARY_DTYPE = {
    "IEEE_FLOAT_32": "<f4",
    "INT_16": "<i2",
    "UINT_16": "<u2",
    "IEEE_FLOAT_64": "<f8",
}


def _unescape(text: str) -> str:
    # BrainVision encodes commas inside names/descriptions as "\1".
    return text.replace("\\1", ",")


def _read_ini(path: Path) -> dict[str, dict[str, str]]:
    """Parse a BrainVision INI file into ``{section: {key: value}}``.

    Tolerant of the leading title line, ``;`` comments, blank lines and the
    freeform ``[Comment]`` block (lines without ``=`` are ignored). Section and
    key case are preserved.
    """
    raw = path.read_bytes()
    encoding = "utf-8"
    m = re.search(rb"Codepage=([^\r\n]+)", raw)
    if m:
        cp_name = m.group(1).decode("ascii", "ignore").strip().lower()
        encoding = {"utf-8": "utf-8", "ansi": "cp1252"}.get(cp_name, cp_name)
    try:
        text = raw.decode(encoding, "replace")
    except LookupError:
        text = raw.decode("latin-1", "replace")

    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = sections.setdefault(stripped[1:-1], {})
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    return sections


def _parse_vhdr(vhdr: Path) -> dict:
    cp = _read_ini(vhdr)
    common = cp["Common Infos"]
    binary = cp.get("Binary Infos", {})

    n_channels = int(common["NumberOfChannels"])
    sampling_interval_us = float(common["SamplingInterval"])
    sample_rate = 1e6 / sampling_interval_us
    orientation = common.get("DataOrientation", "MULTIPLEXED").upper()
    data_format = common.get("DataFormat", "BINARY").upper()
    if data_format != "BINARY":
        raise ValueError(f"only DataFormat=BINARY supported, got {data_format}")
    binary_format = binary.get("BinaryFormat", "IEEE_FLOAT_32").upper()
    if binary_format not in _BINARY_DTYPE:
        raise ValueError(f"unsupported BinaryFormat {binary_format}")

    names: list[str] = []
    resolutions: list[float] = []
    ch_section = cp["Channel Infos"]
    for i in range(1, n_channels + 1):
        raw = ch_section[f"Ch{i}"]
        # Ch<n>=Name,Ref,Resolution,Unit  (Name/Unit may contain escaped commas)
        fields = raw.split(",")
        name = _unescape(fields[0])
        res = 1.0
        if len(fields) >= 3 and fields[2].strip():
            res = float(fields[2])
        names.append(name)
        resolutions.append(res)

    return {
        "data_file": common.get("DataFile", vhdr.with_suffix(".eeg").name),
        "marker_file": common.get("MarkerFile", vhdr.with_suffix(".vmrk").name),
        "n_channels": n_channels,
        "sample_rate": sample_rate,
        "orientation": orientation,
        "dtype": _BINARY_DTYPE[binary_format],
        "channel_names": names,
        "resolutions": np.asarray(resolutions, dtype="<f8"),
    }


def _parse_vmrk(vmrk: Path) -> list[Marker]:
    cp = _read_ini(vmrk)
    if "Marker Infos" not in cp:
        return []
    markers: list[Marker] = []
    for key, raw in cp["Marker Infos"].items():
        if not key.lower().startswith("mk"):
            continue
        # Mk<n>=Type,Description,Position,Points,Channel[,Date]
        fields = raw.split(",")
        if len(fields) < 5:
            continue
        mtype = _unescape(fields[0])
        desc = _unescape(fields[1])
        position = int(fields[2])  # 1-based in the file
        points = int(fields[3]) if fields[3].strip() else 1
        channel = int(fields[4]) if fields[4].strip() else 0
        # .vmrk channel: 0 == all channels -> RDA convention is -1.
        rda_channel = -1 if channel == 0 else channel
        markers.append(Marker(position - 1, mtype, desc, points, rda_channel))
    markers.sort(key=lambda m: m.sample)
    return markers


class FileSource(SourceBase):
    """Stream a BrainVision triplet block by block, optionally looping."""

    def __init__(
        self,
        vhdr_path: str | Path,
        block_points: int | None = None,
        *,
        loop: bool = False,
    ) -> None:
        vhdr = Path(vhdr_path)
        self.vhdr_path = vhdr
        meta = _parse_vhdr(vhdr)
        self.n_channels = meta["n_channels"]
        self.sample_rate = meta["sample_rate"]
        self.channel_names = meta["channel_names"]
        self.resolutions = meta["resolutions"]
        self.orientation = meta["orientation"]
        self.dtype = meta["dtype"]
        self.loop = loop
        self.block_points = block_points or max(1, int(round(self.sample_rate * 0.004)))

        eeg = vhdr.parent / meta["data_file"]
        itemsize = np.dtype(self.dtype).itemsize
        total_items = eeg.stat().st_size // itemsize
        self.n_samples = total_items // self.n_channels
        if self.orientation == "MULTIPLEXED":
            shape = (self.n_samples, self.n_channels)
        elif self.orientation == "VECTORIZED":
            shape = (self.n_channels, self.n_samples)
        else:
            raise ValueError(f"unknown DataOrientation {self.orientation}")
        self._mm = np.memmap(eeg, dtype=self.dtype, mode="r", shape=shape)

        vmrk = vhdr.parent / meta["marker_file"]
        self.markers = _parse_vmrk(vmrk) if vmrk.exists() else []

    def _read_window(self, lo: int, hi: int) -> np.ndarray:
        """Return raw values for samples ``[lo, hi)`` as ``float32[n_ch, hi-lo]``."""
        if self.orientation == "MULTIPLEXED":
            seg = np.asarray(self._mm[lo:hi, :], dtype=np.float32).T
        else:
            seg = np.asarray(self._mm[:, lo:hi], dtype=np.float32)
        return np.ascontiguousarray(seg)

    def blocks(self) -> Iterator[tuple[np.ndarray, list[Marker]]]:
        np_ = self.block_points
        loop_count = 0
        while True:
            pos = 0
            while pos < self.n_samples:
                n = min(np_, self.n_samples - pos)
                data = self._read_window(pos, pos + n)
                offset = loop_count * self.n_samples
                block_markers = [
                    Marker(m.sample + offset, m.type, m.description, m.points, m.channel)
                    for m in self.markers
                    if pos <= m.sample < pos + n
                ]
                pos += n
                yield data, block_markers
            if not self.loop:
                return
            loop_count += 1

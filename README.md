# mock-rda — a mock BrainVision RDA server

A standalone, MIT-licensed Python package that emulates the **server** side of
Brain Products' Remote Data Access (RDA) protocol, so a closed-loop / iTEP
client can be developed without a live amplifier. It streams from a recorded
`.eeg`/`.vhdr`/`.vmrk` triplet **or** a synthetic generator, with inline marker
(trigger) streaming and a manual-injection interface for simulating TMS pulses.

The wire format is a **clean-room re-implementation** from the published field
layout (see [`protocol.py`](src/mock_rda/protocol.py)); no GPL code is copied.

## Install

### Portable install (uv, no admin — recommended for lab machines)

One-command bootstrap; it downloads a vendored `uv`, a managed Python 3.12, and
all dependencies into the project folder. Nothing is installed system-wide and
no home-directory state is used.

**Linux / macOS:**

```bash
git clone https://github.com/cogneuro-uit/mock-rda-server.git
cd mock-rda-server
bash scripts/bootstrap.sh
source scripts/env.sh
```

**Windows (cmd.exe):**

```cmd
git clone https://github.com/cogneuro-uit/mock-rda-server.git
cd mock-rda-server
scripts\bootstrap.bat
scripts\env.bat
```

No PowerShell is required: `bootstrap.bat`, `vendor.bat`, and `vendor-verify.bat`
use plain `cmd.exe` plus Python, so they work even when Group Policy blocks
PowerShell script execution.

What lands where:

| Path | Purpose |
| ---- | ------- |
| `.tools/` | vendored `uv`/`uvx` binaries |
| `.uv-python/` | managed Python interpreter |
| `.uv-cache/` | package download cache |
| `.venv/` | project virtual environment |

Daily use after the one-time bootstrap:

```bash
source scripts/env.sh       # or just add .tools to PATH
uv run pytest -q -rs        # run tests
uv run ruff check .         # lint
uv run mock-rda --help      # entry point
```

The `.venv/bin/*` (Linux) or `.venv\Scripts\*` (Windows) binaries work without
sourcing anything, so automated scripts can call `.venv/bin/pytest` directly.

To use a system interpreter instead of the managed build, override
`UV_PYTHON_PREFERENCE` before sourcing (note: `export` on its own line — a
`VAR=x source ...` prefix does not persist after `source` returns):

```bash
export UV_PYTHON_PREFERENCE=system
source scripts/env.sh
uv sync --extra test --group dev
```

To bump the pinned uv version, edit `UV_VERSION` in both
`scripts/bootstrap.sh` and `scripts/bootstrap.bat`.

### Offline / air-gapped install

The repository can carry its own Python interpreter and wheels so it bootstraps
with **no network at all**. The `vendor/` directory contains:

- `vendor/python/` — CPython 3.12 standalone tarballs for Linux x86_64 and Windows x86_64.
- `vendor/wheels/` — all runtime, test, dev, and build dependency wheels for
  CPython 3.12 (Linux and Windows).
- `vendor/reqs-flat.txt` — the pinned dependency list used for offline installs.
- `vendor/MANIFEST.txt` and `vendor/MANIFEST.sha256` — integrity manifests.

**On an internet-connected machine**, keep the vendor tree up to date after any
`pyproject.toml` change:

```bash
bash scripts/vendor.sh          # Linux / macOS
scripts\vendor.bat              # Windows cmd.exe
```

**On an offline/air-gapped machine**, run the same bootstrap command with the
`--offline` flag. `bootstrap --offline` now works from a completely fresh clone
because the repository carries vendored `uv` binaries in `vendor/uv-bin/`:

```bash
# Linux / macOS
bash scripts/bootstrap.sh --offline
source scripts/env.sh

# Windows cmd.exe
scripts\bootstrap.bat --offline
scripts\env.bat
```

The bootstrap and vendor tools use this download-source fallback order when they
do need network access, so networks that block `github.com` can still fall back
to PyPI and Astral's mirror:

- `uv` binary: vendored `vendor/uv-bin/` first, then PyPI wheel, then GitHub release.
- Python standalone tarball: Astral mirror (`releases.astral.sh`) first, then GitHub.
- wheels: pip uses PyPI (and `--find-links` to the vendored wheelhouse when offline).

Verify the vendored files before trusting them in a restricted environment:

```bash
bash scripts/vendor-verify.sh     # Linux / macOS
scripts\vendor-verify.bat         # Windows cmd.exe
```

A corrupted or missing file will cause the verify script to exit non-zero.

**Moving the repo with a USB stick / exFAT drive:** copy only the tracked
repository files (the git checkout — source, scripts, and `vendor/`); do **not**
copy the generated state directories (`.venv/`, `.uv-python/`, `.uv-cache/`,
`.tools/`). The generated dirs contain symlinks and hardlinks that exFAT and
FAT cannot represent ("destination filesystem does not support symlinks"),
and `.venv` also hardcodes absolute paths — they are rebuilt per machine by
`bootstrap --offline` from the vendored tarball and wheels anyway. The
repository itself contains no symlinks and is exFAT-safe.

### Install with pip (any Python ≥ 3.11)

```bash
pip install git+https://github.com/cogneuro-uit/mock-rda-server.git
```

Or from a local checkout, for development:

```bash
git clone https://github.com/cogneuro-uit/mock-rda-server.git
cd mock-rda-server
pip install -e .            # core (numpy only)
pip install -e ".[file]"    # + MNE, for file-source cross-checks
pip install -e ".[test]"    # + pytest, mne, mne-lsl, for the test suite
```

Requires Python ≥ 3.11. A conda environment is pinned in `environment.yml`.

## Usage

The commands below assume the environment is active. With the **portable
install**, prefix them with `uv run` (after `source scripts/env.sh`), or call
the venv binaries directly — `.venv/bin/mock-rda` / `.venv/bin/python`
(`.venv\Scripts\mock-rda.exe` / `.venv\Scripts\python.exe` on Windows) — no
env vars needed.

### Double-click quick start

After cloning (or copying) the repo, no terminal is needed at all:

1. **Double-click `install.bat`** (Windows) or run `./install.sh` — installs
   everything into the project folder, offline from `vendor/` when present.
2. **Double-click `run-server.bat`** — starts streaming a synthetic source
   (32 ch, 5 kHz, stimulus every 2 s) and opens the Tk control window.
   **Drag a `.vhdr` file onto `run-server.bat`** to stream that recording
   instead (it loops automatically). Extra command-line arguments are
   forwarded to `mock-rda` verbatim.

These launchers call `.venv\Scripts\mock-rda.exe` / `.venv/bin/mock-rda`
directly, so they work regardless of the machine's `.py` file associations
(e.g. an editor owning `.py` double-clicks).

```bash
# Stream a recorded triplet (loops seamlessly with --loop)
mock-rda file example_data/thea_session_2.vhdr --loop --block-ms 4

# Stream a synthetic source with a scheduled stimulus every 2 s and a TEP
mock-rda synth --channels 32 --rate 5000 --block-ms 4 \
    --stim-period 2.0 --tep-template default
```

The server listens on TCP **51244** (the 32-bit float port). Connect the bundled
client to watch the stream:

```bash
uv run python examples/minimal_client.py --host 127.0.0.1 --port 51244
# or equivalently: .venv/bin/python examples/minimal_client.py --host 127.0.0.1 --port 51244
```

### Manual trigger injection

Four paths, all feeding the same per-block queue:

1. **Control GUI** — the server opens a small Tk window (disable with
   `--no-gui`) with **Inject trigger** and **Inject burst** buttons. A burst is
   `--burst-count` pulses spaced `--burst-isi` ms apart (default 5 × 20 ms =
   50 Hz); both are editable in the window, as are the marker type and
   description. <kbd>Enter</kbd> injects a single trigger.

   The window also shows the stream configuration (source, channels, sample
   rate, block size, both ports) and live counters — elapsed time, data
   streamed, blocks, markers, and **drift** (wall-clock elapsed minus emitted
   stream time; a steadily growing value means the server is falling behind
   real time). The title bar carries connected-client count and scheduler
   jitter. Running the GUI does not measurably affect block timing: over a
   12 s run, mean jitter was 0.001 ms with the window open vs 0.002 ms
   headless (max ≈ 1 ms both ways).
2. **Keypress** — press <kbd>Enter</kbd> in the running CLI to fire a
   `Stimulus` / `S  1` marker at the next block.
3. **Control socket** — one-line JSON over TCP (default `localhost:51299`):
   ```bash
   echo '{"type":"Stimulus","description":"S  1","at":"next"}' | nc 127.0.0.1 51299
   echo '{"count":5,"interval_ms":20}' | nc 127.0.0.1 51299   # burst
   ```
   `at` is `"next"` or an absolute sample index; a `count` > 1 injects a
   burst spaced `interval_ms` apart.
4. **Python API** — `server.inject(Marker(...))` /
   `server.inject_burst(Marker(...), count, isi_ms)` for in-process tests.

**Latency contract:** a marker requested at wall-clock *T* lands in the first
block whose emission deadline is ≥ *T*, with `nPosition` computed from that
block's start sample. Worst-case quantization is one block duration — make
`--block-ms` small for tight timing.

## Wire format

All integers little-endian. Generic message header = `<16sLL`:

| Field   | Type    | Meaning                                   |
| ------- | ------- | ----------------------------------------- |
| `GUID`  | 16 raw bytes | `8E45584396C9864CAF4A98BBF6C91450`   |
| `nSize` | uint32  | total message size in bytes incl. header  |
| `nType` | uint32  | message type (below)                      |

| `nType` | Name        | Notes                                          |
| ------: | ----------- | ---------------------------------------------- |
| `1`     | START       | setup info (channels, rate, resolutions, names)|
| `2`     | DATA16      | legacy 16-bit block (parse/skip only)          |
| `3`     | STOP        | acquisition stopped (header only)              |
| `4`     | DATA32      | 32-bit float data block (the workhorse)        |
| `5`     | NEWSTATE    | recorder state changed (int payload)           |
| `9`     | INFO        | recorder info header (UTF-16LE names + units)  |
| `10000` | KEEP_ALIVE  | header only, periodic                          |

**START (type 1)** payload: `<Ld` = `nChannels`, `dSamplingInterval`
(µs/sample = `1e6 / sample_rate`); then `nChannels × double` resolutions
(µV per LSB; 1.0 if data already in µV); then channel names, cp1252-encoded,
joined and terminated by `\0`.

**DATA32 (type 4)** payload: `<LLL` = `nBlock` (monotonic counter), `nPoints`
(samples per channel), `nMarkers`; then `nChannels × nPoints` float32 data,
**multiplexed by sample** (`array2d[points, channels].flatten()` — all channels
for point 0, then all channels for point 1, ...); then the markers. Confirmed
against a live BrainVision Recorder capture (2026-08-21); matches the `.eeg`
file's own MULTIPLEXED layout.

**Marker struct:** `<LlLl` = `nSize`, `nPosition` (sample offset relative to the
**start of this block**, 0-based), `nPoints` (duration in samples), `nChannel`
(index, or `-1` for all); then a null-terminated UTF-8 `type` string and a
null-terminated UTF-8 `description` string.

> The streamed values are the **raw** stored samples; the client multiplies by
> the per-channel resolution from START to get µV — exactly what a real Recorder
> does. The START name encoding is a tested, swappable function
> (`encode_channel_names`); see the START quirk note below.

## Validation

Two tiers:

- **Default tier** (pure Python, gates CI): spec byte-vectors, encode/decode
  round-trip, file-source exactness against the fixture, marker alignment across
  block boundaries, and timing/jitter assertions.
- **Opt-in tier** (`@pytest.mark.mne_lsl`): cross-test that our bytes parse
  correctly under the maintained third-party `mne-lsl` RDA client. This is a
  *byte-layout conformance* check only — timing-latency assertions live in the
  default tier against the in-repo client, because routing through LSL
  reintroduces cross-stream jitter.

```bash
pytest                      # default tier (+ mne-lsl cross-test if installed)
pytest -m "not mne_lsl"     # default tier only
```

### Lab conformance (manual, never in CI)

When next at an amplifier: run BrainVision Recorder in test-signal mode, capture
its raw RDA stream with a byte-logging client, and diff the field layout (START
encoding, INFO presence, block sizing, marker encoding) against the mock for a
matching config. Commit the trace as a golden file and add a default-tier test
that diffs against it, so the lab trip is needed only once. Separately, confirm
Brain Products' own clients (**RecView**, the compiled `LSL-BrainVisionRDA`
app) connect and render sane data — these are GUI/compiled binaries and stay
manual.

**Done (2026-08-21):** DATA32 sample ordering, against a live Recorder —
confirmed multiplexed-by-sample, not channel-major as first assumed (see
`tests/test_real_recorder_capture.py` and its golden fixture). Still open:
diffing START's cp1252-vs-INFO's UTF-16LE encoding, INFO (type 9) presence,
and a RecView/LSL-BrainVisionRDA smoke test.

> **START encoding quirk to verify with a real capture:** the reference
> server's START uses cp1252 names while its INFO (type 9) uses UTF-16LE names +
> a units array. We emit START per the field layout above and make the encoding
> swappable (`--name-encoding`); confirm what your target client reads (type 1,
> type 9, or both).

## Repository layout

```
src/mock_rda/
  protocol.py     # GUID, enums, struct formats, encode_*/decode_*, RDAFramer (pure, no I/O)
  markers.py      # Marker dataclass + thread-safe injection queue (single + burst)
  scheduler.py    # absolute-deadline block pacing with jitter tracking
  server.py       # TCP server: START, DATA32 loop, STOP, per-client tx threads
  injector.py     # control socket + keypress injection paths
  gui.py          # Tk control panel: inject single/burst triggers, live status
  cli.py          # `mock-rda` entry point
  sources/        # base, synthetic (pink noise + TEP), file_source (.vhdr/.eeg/.vmrk)
examples/
  minimal_client.py   # raw-socket reference client (also used by the tests)
tests/                # see Validation above
example_data/         # a short BrainVision triplet fixture (32 ch, 50 kHz)
```

## Non-goals

Emulating amplifier-noise / TMS-artifact morphology or hardware trigger latency;
impedance (types 6–8) and DATA16 emission (parse/skip only); the iTEP processing
client itself (separate repo).

## License

MIT — see [`LICENSE`](LICENSE).

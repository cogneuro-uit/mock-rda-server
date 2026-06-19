# mock-rda — a mock BrainVision RDA server

A standalone, MIT-licensed Python package that emulates the **server** side of
Brain Products' Remote Data Access (RDA) protocol, so a closed-loop / iTEP
client can be developed without a live amplifier. It streams from a recorded
`.eeg`/`.vhdr`/`.vmrk` triplet **or** a synthetic generator, with inline marker
(trigger) streaming and a manual-injection interface for simulating TMS pulses.

The wire format is a **clean-room re-implementation** from the published field
layout (see [`protocol.py`](src/mock_rda/protocol.py)); no GPL code is copied.

## Install

```bash
pip install -e .            # core (numpy only)
pip install -e ".[file]"    # + MNE, for file-source cross-checks
pip install -e ".[test]"    # + pytest, mne, mne-lsl, for the test suite
```

In this devcontainer everything lives in the conda env `project` (see
`CLAUDE.md`); `environment.yml` pins it.

## Usage

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
python examples/minimal_client.py --host 127.0.0.1 --port 51244
```

### Manual trigger injection

Three paths, all feeding the same per-block queue:

1. **Keypress** — press <kbd>Enter</kbd> in the running CLI to fire a
   `Stimulus` / `S  1` marker at the next block.
2. **Control socket** — one-line JSON over TCP (default `localhost:51299`):
   ```bash
   echo '{"type":"Stimulus","description":"S  1","at":"next"}' | nc 127.0.0.1 51299
   ```
   `at` is `"next"` or an absolute sample index.
3. **Python API** — `server.inject(Marker(...))` for in-process tests.

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
**channel-major** (`array2d[channels, points].flatten()`); then the markers.

**Marker struct:** `<LlLl` = `nSize`, `nPosition` (sample offset relative to the
**start of this block**, 0-based), `nPoints` (duration in samples), `nChannel`
(index, or `-1` for all); then a null-terminated UTF-8 `type` string and a
null-terminated UTF-8 `description` string.

> The streamed values are the **raw** stored samples; the client multiplies by
> the per-channel resolution from START to get µV — exactly what a real Recorder
> does. The START name encoding is a tested, swappable function
> (`encode_channel_names`); see the START quirk note below.

## Validation

Two tiers (see [`mock-rda-server-PLAN.md`](mock-rda-server-PLAN.md) §6):

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

> **START encoding quirk to verify with a real capture:** the reference
> server's START uses cp1252 names while its INFO (type 9) uses UTF-16LE names +
> a units array. We emit START per the field layout above and make the encoding
> swappable (`--name-encoding`); confirm what your target client reads (type 1,
> type 9, or both).

## Repository layout

```
src/mock_rda/
  protocol.py     # GUID, enums, struct formats, encode_*/decode_*, RDAFramer (pure, no I/O)
  markers.py      # Marker dataclass + thread-safe injection queue
  scheduler.py    # absolute-deadline block pacing with jitter tracking
  server.py       # TCP server: START, DATA32 loop, STOP, per-client tx threads
  injector.py     # control socket + keypress injection paths
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

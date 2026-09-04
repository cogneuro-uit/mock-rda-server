"""§6a.1 spec vectors: pin the wire format independently of our own decoder.

Two independent checks per message:

* **Frozen golden bytes** — hex literals captured once; guard against any silent
  change to struct formats or field order.
* **Hand-derived layout** — the expected bytes rebuilt in-test from primitive
  ``struct`` calls and the literal GUID, re-deriving the §0 field layout by hand
  rather than reusing ``protocol.py``'s internals.
"""

from __future__ import annotations

import struct

import numpy as np

from mock_rda import protocol as p
from mock_rda.markers import Marker

GUID = bytes.fromhex("8E45584396C9864CAF4A98BBF6C91450")

# --- frozen golden vectors (captured from the field layout) ----------------- #
START_HEX = (
    "8e45584396c9864caf4a98bbf6c914503a0000000100000002000000000000000040"
    "8f40000000000000f03f000000000000e03f467a00437a00"
)
DATA_HEX = (
    "8e45584396c9864caf4a98bbf6c9145052000000040000000500000002000000010000"
    "000000803f0000404000000040000080401e0000000100000001000000ffffffff5374"
    "696d756c7573005320203100"
)
STOP_HEX = "8e45584396c9864caf4a98bbf6c914501800000003000000"
KA_HEX = "8e45584396c9864caf4a98bbf6c914501800000010270000"


def test_start_frozen_and_handbuilt():
    names = ["Fz", "Cz"]
    sample_rate = 1000.0
    res = [1.0, 0.5]
    msg = p.encode_start(names, sample_rate, res)

    # frozen
    assert msg == bytes.fromhex(START_HEX)

    # hand-derived
    payload = struct.pack("<Id", 2, 1e6 / sample_rate)
    payload += struct.pack("<2d", 1.0, 0.5)
    payload += b"Fz\0Cz\0"
    expected = GUID + struct.pack("<II", 24 + len(payload), 1) + payload
    assert msg == expected


def test_data32_frozen_and_handbuilt():
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)  # 2 ch x 2 pts
    markers = [Marker(sample=11, type="Stimulus", description="S  1")]
    msg = p.encode_data32(5, data, markers, block_start_sample=10)

    # frozen
    assert msg == bytes.fromhex(DATA_HEX)

    # hand-derived
    body = struct.pack("<III", 5, 2, 1)  # nBlock, nPoints, nMarkers
    body += struct.pack("<4f", 1.0, 3.0, 2.0, 4.0)
    # multiplexed by sample: pt0[ch0,ch1], pt1[ch0,ch1]
    type_b = b"Stimulus\0"
    desc_b = b"S  1\0"
    marker_size = 16 + len(type_b) + len(desc_b)
    body += struct.pack("<IiIi", marker_size, 11 - 10, 1, -1) + type_b + desc_b
    expected = GUID + struct.pack("<II", 24 + len(body), 4) + body
    assert msg == expected


def test_stop_keepalive_frozen():
    assert p.encode_stop() == bytes.fromhex(STOP_HEX)
    assert p.encode_keepalive() == bytes.fromhex(KA_HEX)
    # header-only: GUID + nSize=24 + nType
    assert p.encode_stop() == GUID + struct.pack("<II", 24, 3)
    assert p.encode_keepalive() == GUID + struct.pack("<II", 24, 10000)


def test_sample_major_layout():
    # 3 channels, 2 points: bytes must be multiplexed by sample -- pt0[ch0,ch1,ch2],
    # pt1[ch0,ch1,ch2] -- matching real Recorder's RDA stream and the .eeg file's
    # own MULTIPLEXED layout (confirmed against a live BrainVision Recorder capture).
    data = np.array([[10, 11], [20, 21], [30, 31]], dtype=np.float32)
    msg = p.encode_data32(0, data, [], 0)
    floats = struct.unpack_from("<6f", msg, p.HEADER_SIZE + 12)
    assert floats == (10, 20, 30, 11, 21, 31)

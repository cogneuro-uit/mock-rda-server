"""mock_rda — a clean-room mock BrainVision RDA (Remote Data Access) server.

Emulates the *server* side of Brain Products' RDA protocol so closed-loop /
iTEP clients can be developed without a live amplifier. The wire format is a
clean re-implementation from the published field layout (see ``protocol.py``);
no GPL code is copied.
"""

from .markers import Marker
from .protocol import (
    GUID,
    MsgType,
    RDAFramer,
    decode_data32,
    decode_start,
    encode_data32,
    encode_keepalive,
    encode_start,
    encode_stop,
    parse_message,
)

__version__ = "0.1.0"

__all__ = [
    "GUID",
    "MsgType",
    "Marker",
    "RDAFramer",
    "encode_start",
    "encode_data32",
    "encode_stop",
    "encode_keepalive",
    "decode_start",
    "decode_data32",
    "parse_message",
    "__version__",
]

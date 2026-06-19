"""Shared pytest fixtures: example-client import path, fixture paths, free ports."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

# Make the in-repo reference client importable as ``minimal_client``.
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))


@pytest.fixture(scope="session")
def fixture_vhdr() -> Path:
    """Path to the committed BrainVision triplet fixture."""
    p = ROOT / "example_data" / "thea_session_2.vhdr"
    if not p.exists():
        pytest.skip("example_data fixture not present")
    return p


def free_port() -> int:
    """Return an unused localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

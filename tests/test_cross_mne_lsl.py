"""§6b opt-in cross-test against a third-party RDA client.

The plan names ``mne-lsl`` as the maintained third-party RDA parser. As of
mne-lsl 1.13.x, however, mne-lsl is a *Lab Streaming Layer* library and ships
**no RDA client** (its public API is ``lsl``/``stream``/``player``). This test
therefore looks for an RDA client in mne-lsl and skips gracefully when absent —
exactly the "skip, don't fail, when the prerequisite is missing" contract of
§6b. The equivalent runnable byte-layout conformance check lives in
``test_cross_independent_parser.py`` (an independent from-scratch parser).

If a future mne-lsl (or another pip-installable RDA client) exposes an RDA
client, wire it in below: start the mock with a synthetic source + scripted
marker train, connect the client to ``localhost``, and assert channel config,
samples (float tol), and marker positions/descriptions. Per the §6b caveat, do
**not** add timing-latency assertions here.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.mne_lsl


def _find_rda_client():
    """Return a third-party RDA client class if one is importable, else None."""
    candidates = [
        ("mne_lsl.stream", "RDAClient"),
        ("mne_lsl.rda", "RDAClient"),
        ("mne_lsl", "RDAClient"),
    ]
    for module_name, attr in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        client = getattr(module, attr, None)
        if client is not None:
            return client
    return None


def test_cross_mne_lsl():
    try:
        importlib.import_module("mne_lsl")
    except Exception as exc:  # ImportError, or liblsl RuntimeError on headless CI
        pytest.skip(f"mne-lsl unavailable: {exc}")
    client = _find_rda_client()
    if client is None:
        pytest.skip(
            "mne-lsl provides no RDA client (it is an LSL library); the runnable "
            "independent cross-test is test_cross_independent_parser.py"
        )
    # Placeholder for a real third-party RDA client cross-test (see module docstring).
    raise AssertionError("RDA client found but cross-test not implemented")  # pragma: no cover

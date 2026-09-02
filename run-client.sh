#!/usr/bin/env bash
# Double-clickable GUI client launcher for Linux / macOS.
#
#   ./run-client.sh          -> epoch viewer watching the local stream
#                               (trigger "Stimulus", electrode C3)
#   other arguments         -> forwarded to the client verbatim
#
# Always uses the project's own venv python (with numpy/mne installed).

cd "$(dirname "$0")" || exit 1

if [[ ! -x ".venv/bin/python" ]]; then
    echo "mock-rda is not installed yet. Run ./install.sh first."
    exit 1
fi

if [[ $# -eq 0 ]]; then
    exec .venv/bin/python examples/gui_client.py --trigger Stimulus --window-ms 10 --electrode C3
else
    exec .venv/bin/python examples/gui_client.py "$@"
fi
#!/usr/bin/env bash
# Double-clickable mock-rda launcher for Linux / macOS (run in a terminal).
#
#   ./run-server.sh                    -> synthetic source (32 ch, 5 kHz)
#   ./run-server.sh recording.vhdr     -> stream that recording, looping
#   other arguments                    -> forwarded verbatim to mock-rda
#
# The Tk control window (Inject trigger / Inject burst) opens with the
# server; keep this terminal focused and press Enter to fire a
# Stimulus/S  1 marker at the next block.

cd "$(dirname "$0")" || exit 1

if [[ ! -x ".venv/bin/python" ]]; then
    echo "mock-rda is not installed yet. Run ./install.sh first."
    exit 1
fi

# Call python -m mock_rda.cli instead of the mock-rda shim: the shim is a
# small unsigned launcher that antivirus / lab policies commonly block
# ("permission denied (os error 5)"), while python itself ran fine during
# install.

if [[ "${1:-}" == *.vhdr ]]; then
    exec .venv/bin/python -m mock_rda.cli file "$1" --loop
elif [[ $# -eq 0 ]]; then
    exec .venv/bin/python -m mock_rda.cli synth --channels 32 --rate 5000 --block-ms 4 \
        --stim-period 2.0 --tep-template default
else
    exec .venv/bin/python -m mock_rda.cli "$@"
fi
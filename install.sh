#!/usr/bin/env bash
# Double-clickable installer for Linux / macOS (run in a terminal).
# Installs uv, Python 3.12, and all dependencies fully inside this folder
# (offline from vendor/ when present - no admin, no system installs).
# Safe to run again at any time; it only fills in what is missing.

cd "$(dirname "$0")" || exit 1
echo "Installing mock-rda (project-local, no admin) ..."
bash scripts/bootstrap.sh --offline
echo
echo "Done. Double-click run-server.sh (or ./run-server.sh) to start streaming."
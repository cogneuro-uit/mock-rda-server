#!/usr/bin/env bash
# Vendor wheels and CPython tarballs for fully-offline bootstrap.
#
# Run this ONCE on an internet-connected machine after dependencies change.
# It refreshes vendor/wheels and vendor/python, then regenerates
# vendor/MANIFEST.txt (sha256 of every vendored file). The resulting vendor/
# tree is committed to git so clones bootstrap without any network access.
#
# Windows: use scripts/vendor.bat (it vendors for the current Windows platform;
# Linux wheels are intentionally not fetched on Windows).

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

# shellcheck source=scripts/env.sh
source scripts/env.sh

mkdir -p vendor/wheels vendor/python

OFFLINE_FLAG=""
MODE="refresh"
if [[ "${1:-}" == "--verify" ]]; then
    MODE="verify"
    OFFLINE_FLAG=""
fi

if [[ "$MODE" == "verify" ]]; then
    if [[ ! -f vendor/MANIFEST.txt ]]; then
        echo "ERROR: vendor/MANIFEST.txt not found." >&2
        exit 1
    fi
    echo "==> verifying vendored files against vendor/MANIFEST.txt ..."
    cd vendor
    if [[ ! -f MANIFEST.sha256 ]]; then
        echo "ERROR: vendor/MANIFEST.sha256 not found." >&2
        exit 1
    fi
    # First verify the manifest file itself against its detached hash.
    expected_manifest=$(cat MANIFEST.sha256)
    actual_manifest=$(sha256sum MANIFEST.txt | awk '{print $1}')
    if [[ "$expected_manifest" != "$actual_manifest" ]]; then
        echo "  MISMATCH: MANIFEST.txt (expected $expected_manifest, got $actual_manifest)" >&2
        echo "ERROR: manifest file is corrupt." >&2
        exit 1
    fi
    mismatches=0
    missing=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        [[ "$line" == \#* ]] && continue
        hash="${line%%  *}"
        rel="${line#*  }"
        if [[ ! -f "$rel" ]]; then
            echo "  MISSING: $rel" >&2
            missing=$((missing + 1))
            continue
        fi
        actual=$(sha256sum "$rel" | awk '{print $1}')
        if [[ "$actual" != "$hash" ]]; then
            echo "  MISMATCH: $rel (expected $hash, got $actual)" >&2
            mismatches=$((mismatches + 1))
        fi
    done < MANIFEST.txt
    if [[ $missing -gt 0 || $mismatches -gt 0 ]]; then
        echo "ERROR: $missing missing, $mismatches mismatched." >&2
        exit 1
    fi
    echo "==> all vendored files passed the integrity check"
    exit 0
fi

echo "==> exporting locked requirements to vendor/reqs.txt ..."
uv export --frozen --no-hashes --extra test --group dev --no-editable -o vendor/reqs.txt

# Strip the project line (.) and comment lines so pip download treats this as a
# plain requirements file. Retain environment markers.
awk '/^#|^$|^\./{next} {sub(/ *# .*/,""); print}' vendor/reqs.txt | sed '/^$/d' > vendor/reqs-flat.txt

REQS="$ROOT/vendor/reqs-flat.txt"

# Wipe wheels for a clean set; keep the new requirements files.
rm -f vendor/wheels/*.whl
echo "==> downloading Linux x86_64 wheels ..."
pip3 download -r "$REQS" -d vendor/wheels

echo "==> downloading Windows win_amd64 wheels ..."
pip3 download -r "$REQS" --platform win_amd64 --python-version 3.12 --only-binary=:all: -d vendor/wheels

echo "==> downloading hatchling + transitive build deps ..."
pip3 download hatchling editables -d vendor/wheels

PYTHON_TAG="20260901"
LINUX_PY="cpython-3.12.14+${PYTHON_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
WIN_PY="cpython-3.12.14+${PYTHON_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PY_DIR="vendor/python/${PYTHON_TAG}"
mkdir -p "$PY_DIR"

download_python() {
    local filename="$1"
    local url="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_TAG}/${filename//+/%2B}"
    local dest="$PY_DIR/$filename"
    if [[ -f "$dest" ]]; then
        echo "==> $filename already present, verifying checksum ..."
        # Verify the file is not corrupted by checking it is a valid tar.gz.
        if tar -tzf "$dest" >/dev/null 2>&1; then
            echo "    $filename verified"
            return 0
        fi
        echo "    $filename is corrupt, re-downloading ..."
        rm -f "$dest"
    fi
    echo "==> downloading $filename ..."
    curl -LsSf "$url" -o "$dest"
    if ! tar -tzf "$dest" >/dev/null 2>&1; then
        echo "ERROR: downloaded $filename is not a valid tar.gz (URL: $url)" >&2
        rm -f "$dest"
        exit 1
    fi
    echo "    $filename downloaded and verified"
}

download_python "$LINUX_PY"
download_python "$WIN_PY"

echo "==> generating vendor/MANIFEST.txt ..."
cd vendor
# Build the manifest body excluding the manifest file and its detached hash.
{
    echo "# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# uv $(uv --version)"
    echo "# pip $(pip3 --version | head -1)"
    find . -type f ! -name MANIFEST.txt ! -name MANIFEST.sha256 | sed 's|^\./||' | sort | while IFS= read -r f; do
        sha256sum "$f" | awk '{print $1"  "$2}'
    done
} > MANIFEST.txt
# Detached hash file protects the manifest itself (self-hashing is fragile).
sha256sum MANIFEST.txt | awk '{print $1}' > MANIFEST.sha256

cd "$ROOT"
WHEEL_COUNT=$(find vendor/wheels -maxdepth 1 -name '*.whl' | wc -l)
PY_COUNT=$(find vendor/python -type f -name '*.tar.gz' | wc -l)
TOTAL_SIZE=$(du -sh vendor | cut -f1)
echo ""
echo "Vendor summary:"
echo "  wheels      : $WHEEL_COUNT"
echo "  python tars : $PY_COUNT"
echo "  total size  : $TOTAL_SIZE"
echo ""
echo "Commit vendor/ to git so clones bootstrap fully offline."

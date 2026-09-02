#!/usr/bin/env bash
# Verify the integrity of vendor/ against vendor/MANIFEST.txt (and its detached
# hash in vendor/MANIFEST.sha256). Run this on an offline/air-gapped machine
# after copying the repo to detect USB/bitrot corruption.
#
#   bash scripts/vendor-verify.sh

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if [[ ! -f vendor/MANIFEST.txt ]]; then
    echo "ERROR: vendor/MANIFEST.txt not found." >&2
    exit 1
fi
if [[ ! -f vendor/MANIFEST.sha256 ]]; then
    echo "ERROR: vendor/MANIFEST.sha256 not found." >&2
    exit 1
fi

echo "==> verifying vendor/MANIFEST.txt ..."
expected_manifest=$(cat vendor/MANIFEST.sha256)
actual_manifest=$(sha256sum vendor/MANIFEST.txt | awk '{print $1}')
if [[ "$expected_manifest" != "$actual_manifest" ]]; then
    echo "ERROR: MANIFEST.txt hash mismatch (expected $expected_manifest, got $actual_manifest)" >&2
    exit 1
fi

echo "==> verifying vendored files against vendor/MANIFEST.txt ..."
cd vendor
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

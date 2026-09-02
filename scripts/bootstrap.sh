#!/usr/bin/env bash
set -euo pipefail

# Pin the vendored uv version. Bump this constant to refresh the binary.
# NOTE: astral's release tags have NO leading 'v' (0.12.9, not v0.12.9).
readonly UV_VERSION="0.12.9"

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

# shellcheck source=scripts/env.sh
source scripts/env.sh

UV_BIN="$UV_INSTALL_DIR/uv"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "${OS:-}" == "Windows_NT" ]]; then
    UV_BIN="$UV_INSTALL_DIR/uv.exe"
fi

if [[ ! -x "$UV_BIN" ]]; then
    echo "==> uv $UV_VERSION not found in $UV_INSTALL_DIR; downloading..."
    mkdir -p "$UV_INSTALL_DIR"

    # Download the pinned release directly from GitHub so nothing is written
    # outside the repository (the astral.sh installer also drops a receipt in
    # ~/.config/uv). uv/uvx are self-contained static binaries.
    case "$(uname -sm)" in
        "Linux x86_64")  plat="x86_64-unknown-linux-gnu";  ext="tar.gz" ;;
        "Linux aarch64") plat="aarch64-unknown-linux-gnu"; ext="tar.gz" ;;
        "Darwin x86_64") plat="x86_64-apple-darwin";       ext="tar.gz" ;;
        "Darwin arm64")  plat="aarch64-apple-darwin";      ext="tar.gz" ;;
        *) echo "unsupported platform: $(uname -sm)" >&2; exit 1 ;;
    esac
    base_url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${plat}"
    tmpdir=$(mktemp -d)
    if [[ "$ext" == "tar.gz" ]]; then
        curl -LsSf "$base_url.tar.gz" | tar -xz -C "$tmpdir"
        cp "$tmpdir/uv-${plat}/uv" "$tmpdir/uv-${plat}/uvx" "$UV_INSTALL_DIR/"
    fi
    rm -rf "$tmpdir"
    chmod +x "$UV_BIN" "$UV_INSTALL_DIR/uvx"
    echo "==> uv $UV_VERSION installed from GitHub release"
fi

if [[ ! -f .python-version ]]; then
    echo "3.12" > .python-version
    echo "==> wrote .python-version -> 3.12"
fi

echo "==> syncing dependencies (extra=test, group=dev)..."
uv sync --extra test --group dev

echo "==> sanity check: importing the package..."
uv run python -c "import mock_rda; print(f'mock-rda {mock_rda.__version__} ready')"

cat <<'EOF'

Bootstrap complete. Everything lives inside this repository:
  .tools/      vendored uv binary
  .uv-python/  managed Python interpreter (only-managed default)
  .uv-cache/   package cache
  .venv/       virtual environment

Daily use:
  source scripts/env.sh       # once per shell session (or add .tools to PATH)
  uv run pytest -q -rs        # run the test suite
  uv run ruff check .         # lint

You can also call .venv/bin/* directly without any env vars:
  .venv/bin/pytest -q -rs
  .venv/bin/mock-rda --help

To use a system interpreter instead of the managed build:
  export UV_PYTHON_PREFERENCE=system   # (export first; a VAR=x prefix is lost)
  source scripts/env.sh
  uv sync --extra test --group dev

To bump the pinned uv version, edit the UV_VERSION constant in:
  scripts/bootstrap.sh
  scripts/bootstrap.bat
EOF

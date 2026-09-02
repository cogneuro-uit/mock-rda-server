#!/usr/bin/env bash
set -euo pipefail

# Pin the vendored uv version. Bump this constant to refresh the binary.
# NOTE: astral's release tags have NO leading 'v' (0.12.9, not v0.12.9).
readonly UV_VERSION="0.12.9"

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

# Parse arguments before sourcing env.sh because --system-python controls
# how env.sh configures uv's Python discovery.
OFFLINE=0
SYSTEM_PYTHON=0
for arg in "$@"; do
    if [[ "$arg" == "--offline" ]]; then
        OFFLINE=1
    fi
    if [[ "$arg" == "--system-python" ]]; then
        SYSTEM_PYTHON=1
    fi
done

if [[ "$SYSTEM_PYTHON" == 1 ]]; then
    export MOCK_RDA_SYSTEM_PYTHON=1
fi

# shellcheck source=scripts/env.sh
source scripts/env.sh

UV_BIN="$UV_INSTALL_DIR/uv"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "${OS:-}" == "Windows_NT" ]]; then
    UV_BIN="$UV_INSTALL_DIR/uv.exe"
fi

if [[ ! -x "$UV_BIN" ]]; then
    plat_subdir="linux-x86_64"
    case "$(uname -sm)" in
        "Linux x86_64")  plat="x86_64-unknown-linux-gnu";  ext="tar.gz" ;;
        "Linux aarch64") plat="aarch64-unknown-linux-gnu"; ext="tar.gz"; plat_subdir="linux-aarch64" ;;
        "Darwin x86_64") plat="x86_64-apple-darwin";       ext="tar.gz"; plat_subdir="darwin-x86_64" ;;
        "Darwin arm64")  plat="aarch64-apple-darwin";      ext="tar.gz"; plat_subdir="darwin-arm64" ;;
        *) echo "unsupported platform: $(uname -sm)" >&2; exit 1 ;;
    esac

    # Offline bootstrap now works from a fully fresh clone if the vendored uv
    # binary is present in vendor/uv-bin/<plat>/.
    if [[ -x "$ROOT/vendor/uv-bin/$plat_subdir/uv" ]]; then
        echo "==> uv $UV_VERSION found in vendor/uv-bin/$plat_subdir; copying to $UV_INSTALL_DIR ..."
        mkdir -p "$UV_INSTALL_DIR"
        cp "$ROOT/vendor/uv-bin/$plat_subdir/uv" "$ROOT/vendor/uv-bin/$plat_subdir/uvx" "$UV_INSTALL_DIR/"
        chmod +x "$UV_BIN" "$UV_INSTALL_DIR/uvx"
    else
        if [[ "$OFFLINE" == 1 ]]; then
            echo "ERROR: --offline requires uv to already be present in $UV_INSTALL_DIR or vendor/uv-bin" >&2
            exit 1
        fi
        echo "==> uv $UV_VERSION not found in $UV_INSTALL_DIR; downloading..."
        mkdir -p "$UV_INSTALL_DIR"

        # Try the PyPI wheel first (better reachability than GitHub on some networks),
        # then the GitHub release tarball.
        tmpdir=$(mktemp -d)
        got_uv=0
        if command -v python3 >/dev/null 2>&1; then
            python3 "$ROOT/scripts/pyvendor.py" ensure-uv
            if [[ -x "$UV_BIN" ]]; then
                got_uv=1
            fi
        fi
        if [[ "$got_uv" == 0 ]]; then
            # Download the pinned release directly from GitHub so nothing is written
            # outside the repository (the astral.sh installer also drops a receipt in
            # ~/.config/uv). uv/uvx are self-contained static binaries.
            base_url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${plat}"
            if [[ "$ext" == "tar.gz" ]]; then
                curl -LsSf "$base_url.tar.gz" | tar -xz -C "$tmpdir"
                cp "$tmpdir/uv-${plat}/uv" "$tmpdir/uv-${plat}/uvx" "$UV_INSTALL_DIR/"
            fi
            rm -rf "$tmpdir"
            chmod +x "$UV_BIN" "$UV_INSTALL_DIR/uvx"
            echo "==> uv $UV_VERSION installed from GitHub release"
        fi
    fi
fi

if [[ ! -f .python-version ]]; then
    echo "3.12" > .python-version
    echo "==> wrote .python-version -> 3.12"
fi

VENDOR_DIR="$ROOT/vendor"
if [[ "$OFFLINE" == 1 && -d "$VENDOR_DIR" ]]; then
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        echo "==> offline bootstrap: using vendored wheels (cp312 + cp314) + system Python..."
    else
        echo "==> offline bootstrap: using vendored Python tarball and wheels..."
    fi

    # Keep the bootstrap entirely inside the repo: don't write a persistent
    # uv cache for the offline install path.
    export UV_NO_CACHE=1

    # In system-python mode we do NOT install a managed interpreter.
    if [[ "$SYSTEM_PYTHON" == 0 ]]; then
        # Ensure the managed Python 3.12 is installed from the local mirror.
        # --no-bin: skip uv's default ~/.local/bin/python3.12 symlink so nothing
        # escapes the project.
        uv python install 3.12 --offline --no-bin
    fi

    # Create a fresh venv. For managed mode the .python-version file pins 3.12.
    # For system mode, try 3.12 first (the originally supported version), then 3.14
    # (the lab Windows machines often have only 3.14.7).  The user may override
    # with UV_PYTHON.  Vendored wheels cover CPython 3.12 and 3.14, so one of
    # those versions is required for offline installs; other versions need network.
    rm -rf .venv
    VENV_VERSION=""
    if [[ -n "${UV_PYTHON:-}" ]]; then
        echo "==> using explicit UV_PYTHON=${UV_PYTHON} for venv ..."
        if uv venv --python "$UV_PYTHON"; then
            VENV_VERSION="${UV_PYTHON}"
        fi
    fi
    if [[ -z "$VENV_VERSION" ]]; then
        if uv venv --python 3.12; then
            VENV_VERSION="3.12"
            echo "==> created venv with system Python 3.12"
        elif uv venv --python 3.14; then
            VENV_VERSION="3.14"
            echo "==> created venv with system Python 3.14"
        fi
    fi
    if [[ -z "$VENV_VERSION" ]]; then
        echo
        echo "ERROR: could not create a venv with system Python 3.12 or 3.14." >&2
        echo "Vendored wheels cover CPython 3.12 and 3.14; other versions need network." >&2
        echo "Found system Pythons:" >&2
        uv python find 3.12 2>/dev/null || true
        uv python find 3.14 2>/dev/null || true
        echo >&2
        echo "Run without --system-python to use the managed Python, or set UV_PYTHON" >&2
        echo "to an explicit system Python 3.12 or 3.14 path." >&2
        exit 1
    fi

    # A system interpreter may be EXTERNALLY-MANAGED (Debian/Ubuntu), so uv pip
    # refuses to install until pip is seeded in the venv. Seed pip proactively.
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        .venv/bin/python -m ensurepip --default-pip >/dev/null 2>&1 || true
    fi

    # Install all locked runtime/test/dev dependencies from the flat requirements
    # file using the local wheelhouse only. We use the venv's own pip when the
    # system interpreter is EXTERNALLY-MANAGED, because uv pip refuses to install
    # into such an environment even inside a venv.
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        if ! UV_OFFLINE=1 uv pip install -r "$VENDOR_DIR/reqs-flat.txt" \
                --find-links "$VENDOR_DIR/wheels" --no-index; then
            .venv/bin/python -m pip install --no-index --find-links "$VENDOR_DIR/wheels" \
                -r "$VENDOR_DIR/reqs-flat.txt"
        fi
    else
        UV_OFFLINE=1 uv pip install -r "$VENDOR_DIR/reqs-flat.txt" \
            --find-links "$VENDOR_DIR/wheels" --no-index
    fi

    # Install the project itself. We keep the editable link so src/mock_rda is
    # imported directly. Use the vendored hatchling wheel for the build so the
    # whole operation stays offline.
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        if ! UV_OFFLINE=1 uv pip install -e . --no-deps \
                --find-links "$VENDOR_DIR/wheels" --no-index; then
            .venv/bin/python -m pip install --no-index --find-links "$VENDOR_DIR/wheels" \
                -e . --no-deps
        fi
    else
        UV_OFFLINE=1 uv pip install -e . --no-deps \
            --find-links "$VENDOR_DIR/wheels" --no-index
    fi
elif [[ "$SYSTEM_PYTHON" == 1 ]]; then
    echo "==> syncing dependencies (extra=test, group=dev) against system Python..."
    if ! uv sync --extra test --group dev; then
        echo
        echo "ERROR: system Python 3.12 or 3.14 required (vendored wheels cover both);" >&2
        echo "either install a system 3.12/3.14, set UV_PYTHON to its path, or bootstrap" >&2
        echo "without --system-python." >&2
        exit 1
    fi
else
    echo "==> syncing dependencies (extra=test, group=dev)..."
    uv sync --extra test --group dev
fi

echo "==> sanity check: importing the package..."
uv run python -c "import mock_rda; print(f'mock-rda {mock_rda.__version__} ready')"

if [[ "$OFFLINE" == 1 ]]; then
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        cat <<'EOF'

Bootstrap complete (offline + system Python). Everything lives inside this
repository:
  .tools/      vendored uv binary
  .venv/       virtual environment (installed from vendor/wheels)

Daily use:
  source scripts/env.sh       # once per shell session
  uv run pytest -q -rs        # run the test suite
  uv run ruff check .         # lint

You can also call .venv/bin/* directly without any env vars:
  .venv/bin/pytest -q -rs
  .venv/bin/mock-rda --help

This venv uses the system Python interpreter. Antivirus did not block it
because it is an IT-installed, signed binary. To switch back to uv's managed
Python, re-run bootstrap without the --system-python flag.

To refresh the vendor/ tree after changing dependencies, run on an internet
machine:
  bash scripts/vendor.sh
EOF
    else
        cat <<'EOF'

Bootstrap complete (offline). Everything lives inside this repository:
  .tools/      vendored uv binary
  .uv-python/  managed Python interpreter (installed from vendor/python mirror)
  .venv/       virtual environment (installed from vendor/wheels)

Daily use:
  source scripts/env.sh       # once per shell session
  uv run pytest -q -rs        # run the test suite
  uv run ruff check .         # lint

You can also call .venv/bin/* directly without any env vars:
  .venv/bin/pytest -q -rs
  .venv/bin/mock-rda --help

To refresh the vendor/ tree after changing dependencies, run on an internet
machine:
  bash scripts/vendor.sh
EOF
    fi
else
    if [[ "$SYSTEM_PYTHON" == 1 ]]; then
        cat <<'EOF'

Bootstrap complete (system Python). Everything lives inside this repository:
  .tools/      vendored uv binary
  .uv-cache/   package download cache
  .venv/       virtual environment (linked to the system interpreter)

Daily use:
  source scripts/env.sh       # once per shell session (or add .tools to PATH)
  uv run pytest -q -rs        # run the test suite
  uv run ruff check .         # lint

You can also call .venv/bin/* directly without any env vars:
  .venv/bin/pytest -q -rs
  .venv/bin/mock-rda --help

This venv uses the system Python interpreter. Antivirus did not block it
because it is an IT-installed, signed binary. To switch back to uv's managed
Python, re-run bootstrap without the --system-python flag.

To bump the pinned uv version, edit the UV_VERSION constant in:
  scripts/bootstrap.sh
  scripts/bootstrap.bat

For fully offline/air-gapped installs, see the "Offline / air-gapped install"
section in README.md.
EOF
    else
        cat <<'EOF'

Bootstrap complete. Everything lives inside this repository:
  .tools/      vendored uv binary
  .uv-python/  managed Python interpreter (only-managed default)
  .uv-cache/   package download cache
  .venv/       virtual environment

Daily use:
  source scripts/env.sh       # once per shell session (or add .tools to PATH)
  uv run pytest -q -rs        # run the test suite
  uv run ruff check .         # lint

You can also call .venv/bin/* directly without any env vars:
  .venv/bin/pytest -q -rs
  .venv/bin/mock-rda --help

To use a system interpreter instead of the managed build:
  export MOCK_RDA_SYSTEM_PYTHON=1   # (export first; a VAR=x prefix is lost)
  source scripts/env.sh
  bash scripts/bootstrap.sh --system-python

To bump the pinned uv version, edit the UV_VERSION constant in:
  scripts/bootstrap.sh
  scripts/bootstrap.bat

For fully offline/air-gapped installs, see the "Offline / air-gapped install"
section in README.md.
EOF
    fi
fi

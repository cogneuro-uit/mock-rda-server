# Project-local uv environment settings.
#
# Source this file (do not execute it) to make the project's vendored uv binary
# usable for the current shell session:
#
#   source scripts/env.sh
#
# Everything is kept inside the repository so no admin rights, home-directory
# state, or system Python is required. Override UV_PYTHON_PREFERENCE if you want
# to point uv at a system interpreter instead of the managed standalone build:
#
#   UV_PYTHON_PREFERENCE=system source scripts/env.sh

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

export UV_INSTALL_DIR="$ROOT/.tools"
export UV_CACHE_DIR="$ROOT/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/.uv-python"
export UV_TOOL_DIR="$ROOT/.uv-tools"
export UV_TOOL_BIN_DIR="$ROOT/.uv-tools/bin"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-only-managed}"

# If a vendored python-build-standalone mirror exists, use it so uv never
# needs to hit the CDN for the managed interpreter.
if [ -d "$ROOT/vendor/python" ]; then
    export UV_PYTHON_INSTALL_MIRROR="file://$ROOT/vendor/python"
fi

# Prepend the vendored uv binary directory to PATH only once per session.
case ":$PATH:" in
    *":$UV_INSTALL_DIR:"*) ;;
    *) export PATH="$UV_INSTALL_DIR:$PATH" ;;
esac

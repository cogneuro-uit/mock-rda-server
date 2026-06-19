#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."   # workspace root

# Claude Code permission mode: write USER-scope settings so bypassPermissions
# is honored. (Project-scope .claude/settings.json is deliberately ignored for
# bypass mode, to stop untrusted repos auto-granting themselves access.) The
# user scope is $CLAUDE_CONFIG_DIR == /home/vscode/.claude, a persistent volume.
# Only create the file if absent so we never clobber login/session data or
# hand-edited settings.
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
if [ ! -f "$SETTINGS" ]; then
    echo ">> Writing user-scope Claude settings (bypassPermissions)"
    mkdir -p "$CLAUDE_DIR"
    cat > "$SETTINGS" <<'JSON'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
JSON
fi

# System packages: reinstall anything recorded in apt-packages.txt.
# (Lines starting with # and blank lines are ignored.)
if [ -f apt-packages.txt ]; then
    pkgs=$(grep -vE '^[[:space:]]*(#|$)' apt-packages.txt | tr '\n' ' ')
    if [ -n "${pkgs// /}" ]; then
        echo ">> Reinstalling apt packages from apt-packages.txt"
        sudo apt-get update
        sudo apt-get install -y --no-install-recommends $pkgs
        sudo rm -rf /var/lib/apt/lists/*
    fi
fi

# Python: recreate/refresh the conda env from environment.yml if present.
if [ -f environment.yml ]; then
    echo ">> Restoring conda env from environment.yml"
    source /opt/conda/etc/profile.d/conda.sh
    conda env create -f environment.yml 2>/dev/null \
        || conda env update -f environment.yml --prune
fi

# R: restore packages from the pak lockfile if present.
if [ -f pkg.lock ]; then
    echo ">> Restoring R packages from pkg.lock"
    Rscript -e 'pak::lockfile_install("pkg.lock")'
fi

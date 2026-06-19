# Debian-based devcontainer base: ships a non-root 'vscode' user with
# passwordless sudo. That user (uid 1000) is what lets Claude install software
# on its own AND satisfies --dangerously-skip-permissions, which refuses to run
# as root. RUN steps below execute as root at build time.
FROM mcr.microsoft.com/devcontainers/base:bookworm

# System build tooling (pak compiles some R packages; conda covers the rest)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git less jq ripgrep \
        build-essential gfortran \
        libcurl4-openssl-dev libssl-dev libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# --- Node.js LTS (Claude Code runtime) ------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code

# --- R via rig + pak ------------------------------------------------------
# Install rig (R Installation Manager), then the latest release of R.
RUN curl -Ls https://github.com/r-lib/rig/releases/download/latest/rig-linux-$(arch)-latest.tar.gz \
        | tar xz -C /usr/local \
    && rig add release \
    && rig default release
# Install pak (binary) into the default R.
RUN Rscript -e 'install.packages("pak", repos = sprintf("https://r-lib.github.io/p/pak/stable/%s/%s/%s", .Platform$pkgType, R.Version()$os, R.Version()$arch))'

# --- Python via Miniforge (conda-forge) -----------------------------------
RUN curl -fsSL -o /tmp/miniforge.sh \
        https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm /tmp/miniforge.sh \
    && chown -R vscode:vscode /opt/conda \
    && echo 'source /opt/conda/etc/profile.d/conda.sh' >> /home/vscode/.bashrc

# Persist Claude auth/session in a volume mounted here (see devcontainer.json)
ENV CLAUDE_CONFIG_DIR=/home/vscode/.claude
RUN mkdir -p /home/vscode/.claude && chown -R vscode:vscode /home/vscode/.claude

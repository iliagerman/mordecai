# =============================================================================
# Mordecai - Multi-stage Dockerfile
# =============================================================================
# Base image: Ubuntu 24.04 with Python 3.13, Node.js 22, Rust/Cargo, uv, Himalaya
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Base - Install all runtime dependencies
# -----------------------------------------------------------------------------
FROM ubuntu:24.04 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Set locale
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    ca-certificates \
    gnupg \
    build-essential \
    pkg-config \
    libssl-dev \
    software-properties-common \
    python3-pip \
    ffmpeg \
    poppler-utils \
    file \
    && rm -rf /var/lib/apt/lists/*

# Install global Python CLI tools (yt-dlp for YouTube skill)
RUN pip3 install --break-system-packages yt-dlp

# -----------------------------------------------------------------------------
# Install Python 3.13 via deadsnakes PPA
# -----------------------------------------------------------------------------
RUN add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3.13 \
    python3.13-venv \
    python3.13-dev \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.13 1

# -----------------------------------------------------------------------------
# Install Node.js 22 via NodeSource
# -----------------------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Install Rust/Cargo via rustup
# -----------------------------------------------------------------------------
ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable \
    && chmod -R a+w $RUSTUP_HOME $CARGO_HOME

# -----------------------------------------------------------------------------
# Install uv package manager
# -----------------------------------------------------------------------------
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# -----------------------------------------------------------------------------
# Install 1Password CLI
# -----------------------------------------------------------------------------
RUN curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" | \
    tee /etc/apt/sources.list.d/1password.list \
    && mkdir -p /etc/debsig/policies/AC2D62742012EA22/ \
    && curl -sS https://downloads.1password.com/linux/debian/debsig/1password.pol | \
    tee /etc/debsig/policies/AC2D62742012EA22/1password.pol \
    && mkdir -p /usr/share/debsig/keyrings/AC2D62742012EA22 \
    && curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg \
    && apt-get update && apt-get install -y --no-install-recommends 1password-cli \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Install Himalaya email CLI via Cargo
# -----------------------------------------------------------------------------
RUN cargo install himalaya --locked \
    && ls -la /usr/local/cargo/bin/ \
    && himalaya --version

# -----------------------------------------------------------------------------
# Stage 2: Application - Copy source and install dependencies
# -----------------------------------------------------------------------------
FROM base AS application

# Set working directory
WORKDIR /app

# Create directories for volumes with permissions that allow non-root users to write
# (needed when running container as host UID via docker-compose `user:` directive)
RUN mkdir -p /app/data /app/sessions /app/skills /app/skills/shared /app/tools \
    && chmod 777 /app/data /app/sessions \
    && chmod 775 /app/skills /app/tools

# Copy dependency files first (for better caching)
COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Copy application source
COPY app/ ./app/

# Copy repo default personality templates (required for first-run onboarding and
# for PersonalityService fallback when no Obsidian vault is configured/mounted).
COPY instructions/ ./instructions/

# Install Python dependencies via uv
RUN uv sync --no-dev

# Install nano-pdf in the uv environment for PDF editing skill
RUN uv pip install nano-pdf

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Expose API port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

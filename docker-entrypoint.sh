#!/bin/bash
# =============================================================================
# Mordecai - Docker Entrypoint Script
# =============================================================================
# Handles:
# - Configuration file warnings
# - Himalaya email CLI configuration
# - Database migrations (unless SKIP_MIGRATIONS=true)
# - Application startup
# =============================================================================

set -e

# Ensure Cargo/Rust binaries (including himalaya) are in PATH.
# These are installed in the base stage via cargo to /usr/local/cargo/bin.
export PATH="/usr/local/cargo/bin:$PATH"

# Ensure we use the prebuilt virtual environment created at image build time.
# When running the container as a non-root host UID (via docker-compose `user:`),
# `uv run` may attempt to re-sync or install the project in editable mode,
# which writes to `/app` (e.g., `mordecai.egg-info`) and fails if `/app` is
# owned by root. Using the venv directly avoids that class of permission issues.
VENV_BIN="/app/.venv/bin"

# Ensure we use the uv-managed virtual environment created at image build time.
# This avoids runtime editable builds that try to write `*.egg-info` into /app.
if [ -x "/app/.venv/bin/python" ]; then
    export PATH="/app/.venv/bin:$PATH"
fi

if [ ! -x "$VENV_BIN/python" ]; then
    echo "ERROR: Expected virtualenv python at $VENV_BIN/python but it was not found or not executable"
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=${PYTHONDONTWRITEBYTECODE:-1}

# -----------------------------------------------------------------------------
# Configuration File Checks
# -----------------------------------------------------------------------------

# Warn if secrets.yml is missing (continue startup)
if [ ! -f /app/secrets.yml ]; then
    echo "WARNING: secrets.yml not found, some features may not work"
fi

# Warn if config.json is missing (use defaults)
if [ ! -f /app/config.json ]; then
    echo "WARNING: config.json not found, using default configuration"
fi

# -----------------------------------------------------------------------------
# Database Migrations
# -----------------------------------------------------------------------------

# Run Alembic migrations unless SKIP_MIGRATIONS=true
if [ "$SKIP_MIGRATIONS" != "true" ]; then
    # Helpful hint for the common SQLite case.
    if [ -n "$AGENT_DATABASE_URL" ] && echo "$AGENT_DATABASE_URL" | grep -q "^sqlite"; then
        DB_PATH=$(echo "$AGENT_DATABASE_URL" | sed -E 's#^sqlite\+aiosqlite:////##; s#^sqlite:////##')
        if [ -n "$DB_PATH" ]; then
            echo "SQLite DB path: /${DB_PATH}"
        fi
    fi

    echo "Running database migrations..."

    if ! python -m alembic upgrade head; then
        echo "ERROR: Database migration failed"
        exit 1
    fi
    echo "Database migrations completed successfully"
else
    echo "Skipping database migrations (SKIP_MIGRATIONS=true)"
fi

# -----------------------------------------------------------------------------
# Application Startup
# -----------------------------------------------------------------------------

echo "Starting Mordecai..."
exec python -m app.main

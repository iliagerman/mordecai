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

    if ! uv run alembic upgrade head; then
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
exec uv run python -m app.main

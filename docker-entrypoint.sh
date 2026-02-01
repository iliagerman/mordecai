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
    echo "Running database migrations..."
    set +e
    MIGRATION_OUTPUT=$(uv run alembic upgrade head 2>&1)
    MIGRATION_EXIT=$?
    set -e

    if [ $MIGRATION_EXIT -ne 0 ]; then
        echo "$MIGRATION_OUTPUT"

        # Common recovery case (SQLite): the app previously created tables via
        # Base.metadata.create_all (no alembic_version), so the initial_schema
        # migration fails with "table ... already exists".
        #
        # In that case, stamp the baseline revision and retry upgrading.
        if echo "$MIGRATION_OUTPUT" | grep -q "table .* already exists"; then
            echo "Detected existing schema without Alembic version table; stamping baseline and retrying..."
            uv run alembic stamp d5b7e1de6779
            uv run alembic upgrade head
        else
            echo "ERROR: Database migration failed"
            exit 1
        fi
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

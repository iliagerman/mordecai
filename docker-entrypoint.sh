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
    if [ -n "$AGENT_DATABASE_URL" ]; then
        # Avoid printing non-SQLite URLs (may include credentials)
        if echo "$AGENT_DATABASE_URL" | grep -q "^sqlite"; then
            echo "AGENT_DATABASE_URL is set (sqlite)"
            # Extract path portion for sqlite URLs (best-effort)
            DB_PATH=$(echo "$AGENT_DATABASE_URL" | sed -E 's#^sqlite\+aiosqlite:////##; s#^sqlite:////##')
            if [ -n "$DB_PATH" ]; then
                echo "SQLite DB path: /${DB_PATH}"
                if [ -f "/${DB_PATH}" ]; then
                    ls -la "/${DB_PATH}" || true
                else
                    echo "SQLite DB file does not exist yet (will be created by migrations)"
                fi
            fi
        else
            echo "AGENT_DATABASE_URL is set (non-sqlite; redacted)"
        fi
    else
        echo "AGENT_DATABASE_URL is not set; Alembic will use alembic.ini sqlalchemy.url"
    fi

    echo "Running database migrations..."

    # SQLite recovery: if the DB was previously created via Base.metadata.create_all(),
    # Alembic has no version history and migrations that create tables/indexes will
    # fail with "already exists". In that case we can stamp the revision Alembic was
    # trying to apply (since the objects already exist) and continue upgrading.
    #
    # This is intentionally bounded to avoid infinite loops.
    MAX_RECOVERY_STEPS=${MAX_MIGRATION_RECOVERY_STEPS:-20}
    STEP=0
    while true; do
        set +e
        MIGRATION_OUTPUT=$(uv run alembic upgrade head 2>&1)
        MIGRATION_EXIT=$?
        set -e

        if [ $MIGRATION_EXIT -eq 0 ]; then
            break
        fi

        echo "$MIGRATION_OUTPUT"

        STEP=$((STEP+1))
        if [ $STEP -gt $MAX_RECOVERY_STEPS ]; then
            echo "ERROR: Database migration failed (exceeded MAX_MIGRATION_RECOVERY_STEPS=${MAX_RECOVERY_STEPS})"
            exit 1
        fi

        # Only attempt this recovery for SQLite (dev/container default). For other DBs,
        # failing migrations should be treated as fatal.
        if [ -n "$AGENT_DATABASE_URL" ] && ! echo "$AGENT_DATABASE_URL" | grep -q "^sqlite"; then
            echo "ERROR: Database migration failed (non-SQLite DB; refusing to auto-stamp)"
            exit 1
        fi

        if echo "$MIGRATION_OUTPUT" | grep -Eq "(table|index) .* already exists|duplicate column name"; then
            # Extract the *target* revision from Alembic's log line:
            #   Running upgrade <from> -> <to>, <message>
            TARGET_REV=$(echo "$MIGRATION_OUTPUT" \
                | sed -nE 's/.*Running upgrade[^>]*-> ([0-9a-f]+),.*/\1/p' \
                | tail -n 1)

            if [ -z "$TARGET_REV" ]; then
                # Fall back to stamping the baseline revision (initial_schema).
                TARGET_REV="d5b7e1de6779"
            fi

            echo "Detected existing schema objects; stamping revision ${TARGET_REV} and retrying (step ${STEP}/${MAX_RECOVERY_STEPS})..."
            uv run alembic stamp "$TARGET_REV"
            continue
        fi

        echo "ERROR: Database migration failed"
        exit 1
    done
    echo "Database migrations completed successfully"
else
    echo "Skipping database migrations (SKIP_MIGRATIONS=true)"
fi

# -----------------------------------------------------------------------------
# Application Startup
# -----------------------------------------------------------------------------

echo "Starting Mordecai..."
exec uv run python -m app.main

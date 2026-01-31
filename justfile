# Mordecai - Task Runner
# Run `just --list` to see all available commands

# Default recipe - show available commands
default:
    @just --list

# Run all tests
test:
    uv run pytest

# Run unit tests only
test-unit:
    uv run pytest tests/unit/

# Run integration tests (requires LocalStack)
test-integration:
    uv run pytest tests/integration/

# Run "real" tests (non-mocked; requires real credentials and may incur costs)
# This is intentionally opt-in.
run-real-tests:
    MORDECAI_RUN_REAL_TESTS=1 \
    MORDECAI_RUN_E2E_AWS=1 \
    uv run pytest -ra -m e2e tests/e2e/

# Run tests with coverage
test-coverage:
    uv run pytest --cov=app --cov-report=html

# Generate a new database migration
# Usage: just migrate-generate "description of changes"
migrate-generate message:
    uv run alembic revision --autogenerate -m "{{message}}"

# Run database migrations
migrate:
    uv run alembic upgrade head

# Rollback one migration
migrate-rollback:
    uv run alembic downgrade -1

# Test server configuration
TEST_API_PORT := "8743"
TEST_DB_PATH := "sqlite+aiosqlite:///./test_agent.db"
TEST_SQS_PREFIX := "test-agent-user-"
TEST_SESSION_DIR := "./test_sessions"
TEST_LOCALSTACK := "http://localhost:4566"

# Start test servers (runs on different port with isolated resources)
start-test-servers:
    #!/usr/bin/env bash
    set -euo pipefail
    
    echo "Setting up test environment..."
    
    # Create test SQS queue in LocalStack
    echo "Creating test SQS queue..."
    aws --endpoint-url={{TEST_LOCALSTACK}} sqs create-queue \
        --queue-name {{TEST_SQS_PREFIX}}default \
        --region us-east-1 2>/dev/null || true
    
    # Create test sessions directory
    mkdir -p {{TEST_SESSION_DIR}}
    
    # Run migrations on test database
    echo "Running migrations on test database..."
    AGENT_DATABASE_URL="{{TEST_DB_PATH}}" uv run alembic upgrade head
    
    echo "Starting test server on port {{TEST_API_PORT}}..."
    
    # Start the application with test configuration
    AGENT_API_PORT={{TEST_API_PORT}} \
    AGENT_DATABASE_URL="{{TEST_DB_PATH}}" \
    AGENT_SQS_QUEUE_PREFIX="{{TEST_SQS_PREFIX}}" \
    AGENT_SESSION_STORAGE_DIR="{{TEST_SESSION_DIR}}" \
    AGENT_LOCALSTACK_ENDPOINT="{{TEST_LOCALSTACK}}" \
    nohup uv run python -m app.main > test_server.log 2>&1 &
    
    echo $! > .test_server.pid
    echo "Test server started with PID $(cat .test_server.pid)"
    echo "Logs available at: test_server.log"
    echo "API available at: http://localhost:{{TEST_API_PORT}}"

# Stop test servers
stop-test-servers:
    #!/usr/bin/env bash
    set -euo pipefail
    
    if [ -f .test_server.pid ]; then
        PID=$(cat .test_server.pid)
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping test server (PID: $PID)..."
            kill "$PID"
            rm -f .test_server.pid
            echo "Test server stopped"
        else
            echo "Test server not running (stale PID file)"
            rm -f .test_server.pid
        fi
    else
        echo "No test server PID file found"
        # Try to find and kill any orphaned test server processes
        pkill -f "AGENT_API_PORT={{TEST_API_PORT}}" 2>/dev/null || true
    fi

# Check test server status
test-server-status:
    #!/usr/bin/env bash
    if [ -f .test_server.pid ]; then
        PID=$(cat .test_server.pid)
        if kill -0 "$PID" 2>/dev/null; then
            echo "Test server is running (PID: $PID)"
            echo "API: http://localhost:{{TEST_API_PORT}}"
        else
            echo "Test server not running (stale PID file)"
        fi
    else
        echo "No test server running"
    fi

# View test server logs
test-server-logs:
    @tail -f test_server.log 2>/dev/null || echo "No log file found"

# Clean up test artifacts
clean-test:
    rm -f test_agent.db test_server.log .test_server.pid
    rm -rf {{TEST_SESSION_DIR}}
    echo "Test artifacts cleaned"

# Start LocalStack (required for SQS)
localstack-start:
    docker run -d --name localstack -p 4566:4566 localstack/localstack

# Stop LocalStack
localstack-stop:
    docker stop localstack && docker rm localstack

# Lint code with ruff
lint:
    uv run ruff check app/

# Type check with mypy
typecheck:
    uv run mypy app/

# Format code with ruff
format:
    uv run ruff format app/

# Run all quality checks
check: lint typecheck

# Start the main application
start:
    uv run python -m app.main

# =============================================================================
# Docker Commands
# =============================================================================

# Build Docker image and start Docker Compose
build-docker:
    #!/usr/bin/env bash
    set -euo pipefail
    
    echo "🐳 Building Docker image..."
    docker compose build
    
    if [ $? -eq 0 ]; then
        echo "✅ Docker image built successfully"
        echo ""
        echo "🚀 Starting Docker Compose services..."
        docker compose up -d
        
        if [ $? -eq 0 ]; then
            echo "✅ Services started successfully"
            echo ""
            echo "📊 Service status:"
            docker compose ps
            echo ""
            echo "🔗 API available at: http://localhost:${AGENT_API_PORT:-8000}"
            echo "📝 View logs with: docker compose logs -f"
        else
            echo "❌ Failed to start Docker Compose services"
            exit 1
        fi
    else
        echo "❌ Docker image build failed"
        exit 1
    fi


# Force rebuild Docker image (no cache) and restart services
re-build:
    #!/usr/bin/env bash
    set -euo pipefail
    
    echo "🛑 Stopping existing Docker Compose services..."
    docker compose down 2>/dev/null || true
    
    echo ""
    echo "🔨 Force rebuilding Docker image (no cache)..."
    docker compose build --no-cache
    
    if [ $? -eq 0 ]; then
        echo "✅ Docker image rebuilt successfully"
        echo ""
        echo "🚀 Starting Docker Compose services..."
        docker compose up -d
        
        if [ $? -eq 0 ]; then
            echo "✅ Services restarted successfully"
            echo ""
            echo "📊 Service status:"
            docker compose ps
            echo ""
            echo "🔗 API available at: http://localhost:${AGENT_API_PORT:-8000}"
            echo "📝 View logs with: docker compose logs -f"
        else
            echo "❌ Failed to start Docker Compose services"
            exit 1
        fi
    else
        echo "❌ Docker image rebuild failed"
        exit 1
    fi


# Run Docker-specific tests
test-docker:
    uv run pytest tests/docker/ -v
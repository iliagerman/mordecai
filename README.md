# Mordecai

<!-- (local-dev) README updated to document compose overrides and shared mounts. -->

<!-- LOGO_PLACEHOLDER -->
<img src="docs/images/logo.png" alt="Mordecai Logo" width="200">
<!-- END_LOGO_PLACEHOLDER -->

Mordecai is a multi-user AI agent platform built on the Strands Agents SDK, featuring Telegram bot integration, skills/plugins system, and a kanban-style task dashboard.

## Features

- **Telegram Bot Interface**: Primary user interaction channel
- **File Attachments**: Receive documents and images from users, automatically downloaded to user workspace
- **Skills/Plugins**: Downloadable extensions from URLs
- **MCP Integration**: Connect to external tools via Model Context Protocol
- **Memory System**: AWS Bedrock AgentCore Memory with short-term (session) and long-term (preferences, facts, summaries) persistence
- **Webhooks**: HTTP endpoints for external system integration
- **Kanban Dashboard**: Web-based task visualization (Pending → In Progress → Done)
- **Per-User SQS Queues**: Async message processing with isolation

## Skill Installation

When a user asks the bot to install a skill (via `/install skill <url>` command or through conversation), the skill is **always installed to the user's personal folder** at `skills/<user_id>/`. Skills are never installed to the shared folder when requested by users through the bot interface.

- **User skills**: `skills/<user_id>/` - Each user has their own isolated skills directory
- **Shared skills**: `skills/shared/` - Available to all users (managed by administrators)

This isolation ensures that:
1. Users cannot affect other users' skills
2. Each user can have their own version of a skill
3. User-specific configurations and secrets are properly isolated

## Skill secrets & per-user config templates

Mordecai supports multi-user skills that may require secrets (API keys, passwords) and/or config files.

### Per-user skill secrets

- Global secrets live in `secrets.yml` (git-ignored).
- Per-user skill secrets live in `skills/<USER_ID>/skills_secrets.yml` (git-ignored).
- Example templates (e.g. `secrets.yml_example`) are always committed.

The agent will ask for missing required values (declared in each skill's `SKILL.md`) and persist them automatically.

### *_example template materialization

If a skill directory contains files ending in `*_example` or `*.example`, Mordecai treats them as templates:

- It renders a per-user copy with the suffix removed
   - Example: `himalaya.toml_example` → `himalaya.toml`
- It replaces placeholders of the form `[PLACEHOLDER]` using values stored under `skills.<skill>` in `skills_secrets.yml`
- For the canonical config pattern `{skill}.toml_example`, it also exports `{SKILL}_CONFIG` (e.g. `HIMALAYA_CONFIG`) on every skill invocation

This makes it possible for each user/tenant to use their own configuration without hard-coding paths inside the skill.

## Requirements

- Python 3.13+
- AWS credentials (or LocalStack for local development)
- Telegram Bot Token

## Quick Start

### 1. Clone and Install

```bash
cd mordecai

# Create virtual environment (recommended: uv)
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
uv pip install -r requirements.txt

# Or install in development mode
uv pip install -e ".[dev]"
```

### 2. Configure

Copy the example configuration and update with your settings:

```bash
cp config.example.json config.json
```

Edit `config.json` with your credentials:

```json
{
  "model_provider": "bedrock",
  "bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
  "aws_region": "us-east-1",
  "database_url": "sqlite+aiosqlite:///./agent.db"
}
```

You can also use environment variables with the `AGENT_` prefix:

```bash
export AGENT_TELEGRAM_BOT_TOKEN="your-token"
export AGENT_MODEL_PROVIDER="openai"
export AGENT_OPENAI_API_KEY="your-openai-key"

# Or for Google Gemini:
export AGENT_MODEL_PROVIDER="google"
export AGENT_GOOGLE_API_KEY="your-google-api-key"
```

### 3. Initialize Database

```bash
alembic upgrade head
```

### 4. Run the Application

```bash
uv run python -m app.main
```

Or with uvicorn for the API server:

```bash
uv run uvicorn app.main:get_fastapi_app --host 0.0.0.0 --port 8000 --factory
```

### 5. Start Using the Bot

1. Open Telegram and search for your bot by username
2. Send `/start` to begin a conversation
3. Use `/help` to see available commands

## Deployment

### Notes storage and retention

- `scratchpad/` is **long-lived** and is where the agent stores per-user notes/memory artifacts.
- `workspace/` is **ephemeral** and is only for artifacts (images/scripts/etc.) that are meant to be returned to the user.
  - The backend runs an hourly cleanup job that deletes stale `workspace/<USER_ID>/` directories when they have not changed for 24 hours.

### Docker Deployment

Docker Compose is used for server deployments in this repo.

#### Deploy with Docker Compose

1. Prepare configuration files:

```bash
cp config.example.json config.json
cp secrets.yml_example secrets.yml  # Edit with your secrets
```

2. Start services:

```bash
docker-compose up -d
```

3. View logs:

```bash
docker-compose logs -f mordecai
```

Notes:
- `scratchpad/` is mounted into the container and is the **only** supported long-lived notes/memory store.
- `workspace/` is for ephemeral artifacts and is auto-cleaned by the backend.

##### Sharing Vibe-Kanban's SQLite directory (optional)

If you're running Vibe-Kanban in a separate container and want Mordecai to have **direct read/write file I/O** access to Vibe-Kanban's persisted SQLite DB, this repo's `docker-compose.yml` can bind-mount the Vibe-Kanban data directory into Mordecai at the **same path**:

- Vibe-Kanban data dir: `/home/ilia/.local/share/vibe-kanban/`
- Vibe-Kanban DB file: `/home/ilia/.local/share/vibe-kanban/db.sqlite`

To avoid creating **root-owned** files on the host, Mordecai is configured to run as your host UID/GID via `.env`:

- `HOST_UID` (default `1000`)
- `HOST_GID` (default `1000`)

If you already have permission issues in that directory from prior runs, fix ownership on the host once, then restart the container.

#### Manual Docker Build

```bash
# Build the image
docker build -t mordecai:latest .

# Run a container
docker run -d \
  --name mordecai \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/secrets.yml:/app/secrets.yml:ro \
  -v $(pwd)/skills:/app/skills:rw \
  -p 8000:8000 \
  mordecai:latest
```

### Production Deployment

#### AWS ECS / Fargate

1. Push image to ECR:
```bash
aws ecr create-repository --repository-name mordecai
docker tag mordecai:latest <account-id>.dkr.ecr.<region>.amazonaws.com/mordecai:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/mordecai:latest
```

2. Create ECS task definition with:
   - Container image: ECR URI
   - Environment variables for secrets (use Secrets Manager)
   - IAM role for SQS and Bedrock access
   - Health check: `CMD-SHELL curl -f http://localhost:8000/health || exit 1`

3. Configure Application Load Balancer with:
   - Target group on port 8000
   - Health check path: `/health`

#### Railway / Render / Fly.io

The application can be deployed to any PaaS supporting Docker:

1. Connect GitHub repository
2. Set environment variables in the platform dashboard
3. Deploy - the platform will build from Dockerfile

Required environment variables:
```
AGENT_TELEGRAM_BOT_TOKEN=your_token
AGENT_MODEL_PROVIDER=bedrock
AGENT_AWS_REGION=us-east-1
```

#### Health Check

The application exposes a health endpoint:

```bash
curl http://localhost:8000/health
```

Returns `{"status": "healthy"}` when the application is running.

## Configuration Options

| Option                | Description                                          | Default                                       |
| --------------------- | ---------------------------------------------------- | --------------------------------------------- |
| `model_provider`      | AI model provider (`bedrock`, `openai`, or `google`) | `bedrock`                                     |
| `bedrock_model_id`    | Bedrock model ID                                     | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `openai_model_id`     | OpenAI model ID                                      | `gpt-4`                                       |
| `openai_api_key`      | OpenAI API key                                       | -                                             |
| `google_model_id`     | Google Gemini model ID                               | `gemini-2.5-flash`                            |
| `google_api_key`      | Google AI Studio API key                             | -                                             |
| `telegram_bot_token`  | Telegram Bot API token                               | Required                                      |
| `aws_region`          | AWS region for SQS                                   | `us-east-1`                                   |
| `sqs_queue_prefix`    | Prefix for user queues                               | `agent-user-`                                 |
| `localstack_endpoint` | LocalStack URL for local dev                         | -                                             |
| `database_url`        | SQLite database URL                                  | `sqlite+aiosqlite:///./agent.db`              |
| `session_storage_dir` | Session file storage                                 | `./sessions`                                  |
| `skills_dir`          | Skills/tools directory                               | `./tools`                                     |
| `api_host`            | API server host                                      | `0.0.0.0`                                     |
| `api_port`            | API server port                                      | `8000`                                        |

### File Attachment Configuration

The platform supports receiving file attachments (documents and images) from users via Telegram. Files are downloaded to user-specific workspace directories and their paths are provided to the agent for processing.

| Option                    | Description                             | Default        |
| ------------------------- | --------------------------------------- | -------------- |
| `enable_file_attachments` | Enable/disable file attachment handling | `true`         |
| `max_file_size_mb`        | Maximum file size in MB                 | `20`           |
| `file_retention_hours`    | Hours before files are auto-deleted     | `24`           |
| `allowed_file_extensions` | List of allowed file extensions         | See below      |
| `working_folder_base_dir` | Base directory for user workspaces      | `./workspace`  |
| `vision_model_id`         | Model ID for image analysis (optional)  | -              |

#### Default Allowed Extensions

```
Documents: .txt, .pdf, .csv, .json, .xml, .md, .yaml, .yml
Code: .py, .js, .ts, .html, .css, .sql, .sh
Images: .png, .jpg, .jpeg, .gif, .webp
```

#### How File Attachments Work

1. **User sends file**: User sends a document or photo via Telegram
2. **Validation**: File is validated (size, extension, filename sanitization)
3. **Download**: File is downloaded to user's temp directory
4. **Workspace copy**: File is copied to user's workspace directory
5. **Agent context**: File path and metadata are added to agent's context
6. **Processing**: Agent can read/process the file using its tools
7. **Cleanup**: Files older than `file_retention_hours` are automatically deleted

#### Security Features

- Path traversal prevention in filenames
- File extension allowlist
- Size limits enforced
- User-isolated storage directories
- Automatic file cleanup

### MCP Configuration (Model Context Protocol)

Mordecai supports connecting to external MCP (Model Context Protocol) servers to extend the agent's capabilities with additional tools.

#### MCP Server Configuration

MCP servers are configured in `mcp_servers.json` at the repository root:

```json
{
  "mcp": {
    "homeserver-aws": {
      "type": "remote",
      "url": "http://mcp.homeserver/servers/1a7e56fb077f4f17aab0d9d2cff26b49/sse"
    },
    "homeserver-development": {
      "type": "remote",
      "url": "http://mcp.homeserver/servers/dc673fa141b642308c293d4bf9612023/sse"
    }
  }
}
```

#### Per-User MCP Configuration

Users can add their own MCP servers in `skills/{user_id}/mcp_servers.json`. These configs override global settings for that user:

```json
{
  "mcp": {
    "my-custom-server": {
      "type": "remote",
      "url": "http://localhost:3000/sse"
    }
  }
}
```

#### MCP Tools

The agent has built-in tools to manage MCP servers:

| Tool                | Description                            |
| ------------------- | -------------------------------------- |
| `mcp_add_server`    | Add an MCP server for the current user |
| `mcp_remove_server` | Remove an MCP server configuration     |
| `mcp_list_servers`  | List all configured MCP servers        |

#### Usage Examples

```
User: List my MCP servers
Agent: [shows configured servers with their URLs]

User: Add an MCP server named "test-server" with type "remote" and URL "http://localhost:3000/sse"
Agent: Added MCP server 'test-server'. Start a new conversation to use the new tools.

User: Remove MCP server "test-server"
Agent: Removed MCP server 'test-server'. Start a new conversation to apply changes.
```

#### Transport Types

| Type     | Description                              | Example                     |
| -------- | ---------------------------------------- | --------------------------- |
| `remote` | SSE transport for HTTP-based MCP servers | `http://localhost:3000/sse` |
| `stdio`  | Local process transport (future)         | `python server.py`          |

#### Tool Prefixing

MCP tools from different servers are automatically prefixed with the server name to avoid conflicts. For example, tools from `homeserver-aws` will be prefixed like `homeserver-aws_<tool_name>`.

### Memory Configuration (AgentCore)

The platform uses AWS Bedrock AgentCore Memory for persistent memory across sessions. Memory is managed in two layers:

- **Session memory**: In-memory conversation history using `SlidingWindowConversationManager` (cleared on session end)
- **Long-term memory**: User preferences, facts, and session summaries stored in AgentCore Memory (persists across sessions)

| Option                             | Description                           | Default                                   |
| ---------------------------------- | ------------------------------------- | ----------------------------------------- |
| `memory_enabled`                   | Enable/disable AgentCore memory       | `true`                                    |
| `memory_id`                        | Existing AgentCore memory instance ID | -                                         |
| `memory_name`                      | Name for new memory instance          | `MordecaiMemory`                          |
| `memory_description`               | Description for new memory instance   | `Multi-user agent memory with strategies` |
| `memory_retrieval_top_k`           | Max memories to retrieve              | `10`                                      |
| `memory_retrieval_relevance_score` | Minimum relevance score (0.0-1.0)     | `0.5`                                     |
| `conversation_window_size`         | Messages to keep in session window    | `20`                                      |

#### Memory Architecture

**Session Memory (Short-term)**
- Managed by Strands SDK's `SlidingWindowConversationManager`
- Keeps recent conversation context within a session
- Automatically trimmed to `conversation_window_size` messages
- Cleared when user starts a new session (`new` command)

**Long-term Memory (AgentCore)**
- Facts and preferences extracted from conversations
- Persists across sessions
- Automatically retrieved and injected into agent context
- Agent can search memory on demand using `search_memory` tool

#### Memory Tools

The agent has access to memory-related tools:

1. **`set_agent_name`**: Store the agent's name when user assigns one
2. **`search_memory`**: Search long-term memory for facts and preferences

Example queries that trigger memory search:
- "What do you know about me?"
- "What are my preferences?"
- "Do you remember when I told you...?"

#### Memory Strategies

AgentCore Memory uses three strategies to automatically extract and store information:

1. **Summary Strategy** (`/summaries/{actorId}/{sessionId}`)
   - Automatically summarizes conversations at session end
   - Provides context from previous sessions

2. **User Preference Strategy** (`/preferences/{actorId}`)
   - Learns and stores user preferences from conversations
   - Enables personalized responses across sessions

3. **Semantic Strategy** (`/facts/{actorId}`)
   - Extracts and stores important facts mentioned by users
   - Builds a knowledge base about each user

#### User Isolation

Each user's memories are isolated via `actor_id` (derived from Telegram username/ID). The namespace patterns ensure:
- User A cannot access User B's preferences or facts
- Session summaries are scoped to individual users
- Complete privacy between users sharing the same agent

#### First-Time Setup

On first startup with `memory_enabled=true`:
1. If `memory_id` is not set, a new AgentCore memory instance is created
2. The memory ID should be saved to `config.json` for reuse across restarts
3. Memory strategies are configured automatically

## User Commands

Interact with the agent via Telegram using these commands:

| Command                  | Description                        |
| ------------------------ | ---------------------------------- |
| `new`                    | Start a fresh conversation session |
| `logs`                   | View recent agent activity         |
| `install skill <url>`    | Install a skill from URL           |
| `uninstall skill <name>` | Remove an installed skill          |
| `help`                   | Show available commands            |

## Architecture

<!-- DIAGRAM_PLACEHOLDER -->
<img src="docs/images/architecture-diagram.png" alt="Mordecai Architecture Diagram">
<!-- END_DIAGRAM_PLACEHOLDER -->

```
┌─────────────────────────────────────────────────────────────┐
│                      ROUTERS (API Layer)                     │
│  task_router.py, webhook_router.py                          │
│  - HTTP endpoint definitions only                            │
│  - Request/Response validation                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    SERVICES (Business Logic)                 │
│  task_service.py, logging_service.py, webhook_service.py    │
│  agent_service.py, skill_service.py, command_parser.py      │
│  - All business rules and validation                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    DAOs (Data Access Objects)                │
│  user_dao.py, task_dao.py, log_dao.py, memory_dao.py        │
│  - Database CRUD operations only                             │
│  - Returns Pydantic models (never SQLAlchemy objects)        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATABASE (SQLite Async)                   │
│  SQLAlchemy async engine + aiosqlite + Alembic migrations    │
└─────────────────────────────────────────────────────────────┘
```

## Development

This project uses [just](https://github.com/casey/just) as a command runner. Install it with:

```bash
# macOS
brew install just

# Linux
cargo install just
# or: snap install --edge --classic just

# Windows
choco install just
```

Run `just --list` to see all available commands.

### Running Tests

```bash
# Run all tests
just test

# Run unit tests only
just test-unit

# Run integration tests (requires LocalStack)
just test-integration

# Run with coverage
just test-coverage

# Run deterministic property-style tests
uv run pytest tests/ -k "pbt"
```

### Local Development with LocalStack

For local SQS development without AWS:

```bash
# Start LocalStack
just localstack-start

# Stop LocalStack
just localstack-stop
```

Or manually:

```bash
docker run -d --name localstack -p 4566:4566 localstack/localstack
```

Configure endpoint in config.json:

```json
{
  "localstack_endpoint": "http://localhost:4566"
}
```

### Database Migrations

```bash
# Generate a new migration
just migrate-generate "description of changes"

# Apply migrations
just migrate

# Rollback one migration
just migrate-rollback
```

### Test Servers

Run a parallel test instance with isolated resources (different port, database, SQS queues):

```bash
# Start test servers (requires LocalStack running)
just start-test-servers

# Check test server status
just test-server-status

# View test server logs
just test-server-logs

# Stop test servers
just stop-test-servers

# Clean up test artifacts
just clean-test
```

Test server configuration:
- API Port: `8743` (vs production `8742`)
- Database: `test_agent.db` (isolated from `agent.db`)
- SQS Queue Prefix: `test-agent-user-` (isolated from `agent-user-`)
- Session Directory: `./test_sessions`

### Code Quality

```bash
# Lint with ruff
just lint

# Type check with mypy
just typecheck

# Format code
just format

# Run all quality checks
just check
```

## Project Structure

```
mordecai/
├── app/
│   ├── main.py              # Application bootstrap
│   ├── config.py            # Configuration with pydantic-settings
│   ├── enums.py             # StrEnum definitions
│   ├── database.py          # Async SQLAlchemy setup
│   ├── models/
│   │   ├── base.py          # JsonModel base class
│   │   ├── orm.py           # SQLAlchemy ORM models
│   │   └── domain.py        # Pydantic domain models
│   ├── dao/                 # Data Access Objects
│   ├── services/            # Business Logic
│   ├── routers/             # HTTP Endpoints
│   ├── telegram/            # Telegram bot
│   ├── sqs/                 # SQS queue management
│   └── tools/               # Skills directory
├── alembic/                 # Database migrations
├── tests/
│   ├── unit/
│   └── integration/
├── config.json              # Configuration file
├── alembic.ini
└── pyproject.toml
```

## License

MIT

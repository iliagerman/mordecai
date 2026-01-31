"""Configuration with JSON file, secrets.yml, and env variable support.

This module also provides helpers for skill onboarding to persist and hot-reload
skill-specific environment variables from secrets.yml.
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import ModelProvider


# Container deployments commonly mount a host Obsidian vault at this path.
# If the configured obsidian_vault_root does not exist *in the current runtime*,
# we will prefer this mount when present.
DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT = "/app/obsidian-vaults"


def _create_config_file(path: str, config_data: dict) -> None:
    """Create a config file at the specified path based on file extension.

    Supports:
        - .json: JSON format
        - .yaml, .yml: YAML format
        - .toml: TOML format

    Args:
        path: File path with extension indicating format
        config_data: Configuration data to write (excluding 'path' key)
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    # Create parent directories if they don't exist
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Can't create directory (e.g., /root on macOS), skip file creation
        import logging

        logging.getLogger(__name__).warning(
            f"Cannot create config directory {file_path.parent}: {e}"
        )
        return

    # Remove 'path' key from config data if present
    data_to_write = {k: v for k, v in config_data.items() if k != "path"}

    try:
        if suffix == ".json":
            with open(file_path, "w") as f:
                json.dump(data_to_write, f, indent=2)
        elif suffix in (".yaml", ".yml"):
            with open(file_path, "w") as f:
                yaml.dump(data_to_write, f, default_flow_style=False)
        elif suffix == ".toml":
            # Build TOML content manually for himalaya-style config
            _write_toml_config(file_path, data_to_write)
        else:
            # Unknown format, skip file creation
            return
    except (OSError, PermissionError) as e:
        import logging

        logging.getLogger(__name__).warning(f"Cannot write config file {file_path}: {e}")


def _write_toml_config(file_path: Path, config_data: dict) -> None:
    """Write config data as TOML format.

    Handles nested structures for tools like Himalaya that expect
    specific TOML formats like [accounts.name] sections.

    Args:
        file_path: Path to write the TOML file
        config_data: Configuration data to write
    """
    try:
        import tomli_w

        with open(file_path, "wb") as f:
            tomli_w.dump(config_data, f)
    except ImportError:
        # Fallback: write TOML manually for simple structures
        lines = []

        # Check if this looks like a Himalaya email config
        if "email" in config_data and ("imap" in config_data or "smtp" in config_data):
            # Himalaya-style email account config
            account_name = config_data.get("display_name", "default").lower().replace(" ", "-")
            lines.append(f"[accounts.{account_name}]")
            lines.append(f"default = true")

            if "email" in config_data:
                lines.append(f'email = "{config_data["email"]}"')
            if "display_name" in config_data:
                lines.append(f'display-name = "{config_data["display_name"]}"')

            lines.append("")

            # IMAP backend (for reading emails)
            if "imap" in config_data:
                imap = config_data["imap"]
                lines.append(f'backend = "imap"')
                lines.append("")
                lines.append(f"[accounts.{account_name}.imap]")
                lines.append(f'host = "{imap.get("host", "")}"')
                lines.append(f"port = {imap.get('port', 993)}")
                lines.append(f'encryption = "tls"')
                lines.append(f'login = "{imap.get("login", "")}"')
                lines.append("")
                lines.append(f"[accounts.{account_name}.imap.passwd]")
                lines.append(f'cmd = "echo {imap.get("password", "")}"')
                lines.append("")

            # SMTP backend (for sending emails)
            if "smtp" in config_data:
                smtp = config_data["smtp"]
                lines.append(f'message.send.backend = "smtp"')
                lines.append("")
                lines.append(f"[accounts.{account_name}.smtp]")
                lines.append(f'host = "{smtp.get("host", "")}"')
                lines.append(f"port = {smtp.get('port', 587)}")
                lines.append(f'encryption = "start-tls"')
                lines.append(f'login = "{smtp.get("login", "")}"')
                lines.append("")
                lines.append(f"[accounts.{account_name}.smtp.passwd]")
                lines.append(f'cmd = "echo {smtp.get("password", "")}"')
        else:
            # Generic TOML output
            for key, value in config_data.items():
                if isinstance(value, dict):
                    lines.append(f"[{key}]")
                    for k, v in value.items():
                        if isinstance(v, str):
                            lines.append(f'{k} = "{v}"')
                        else:
                            lines.append(f"{k} = {v}")
                    lines.append("")
                elif isinstance(value, str):
                    lines.append(f'{key} = "{value}"')
                else:
                    lines.append(f"{key} = {value}")

        with open(file_path, "w") as f:
            f.write("\n".join(lines))


def _flatten_secrets_mapping(secrets: dict) -> dict:
    """Flatten secrets mapping into AgentConfig-compatible keys.

    Converts nested YAML structure to flat config keys:
        telegram.bot_token -> telegram_bot_token
        bedrock.api_key -> bedrock_api_key

    Special handling for 'skills' section:
        - Supports both legacy and structured schemas.

        Recommended schema:
            skills:
              <skill_name>:
                env:
                  SOME_KEY: some_value
                # optional per-user overrides
                users:
                  <user_id>:
                    env:
                      SOME_KEY: other_value

        NOTE: Per-user overrides are not applied here because config loading is
        global; apply them at runtime via refresh_runtime_env_from_secrets(...).
    """
    flat = {}
    for section, values in secrets.items():
        if isinstance(values, dict):
            if section == "skills":
                # Process skill secrets
                for key, value in values.items():
                    if isinstance(value, dict):
                        # Structured schema: export env vars (global only)
                        env_block = value.get("env")
                        if isinstance(env_block, dict):
                            for k, v in env_block.items():
                                if v is None:
                                    continue
                                os.environ[str(k)] = str(v)

                        # Nested skill config - check for 'path' key
                        if "path" in value:
                            # Create config file at the specified path
                            _create_config_file(value["path"], value)
                            # Also export individual values as env vars
                            for k, v in value.items():
                                if k != "path" and not isinstance(v, dict):
                                    env_key = f"{key.upper()}_{k.upper()}"
                                    os.environ[env_key] = str(v)
                        else:
                            # No path, export as env vars
                            for k, v in value.items():
                                if k in {"env", "users"}:
                                    continue
                                if not isinstance(v, dict):
                                    env_key = f"{key.upper()}_{k.upper()}"
                                    os.environ[env_key] = str(v)
                    elif value is not None:
                        # Simple key-value, export as env var
                        os.environ[key] = str(value)
            else:
                for key, value in values.items():
                    flat_key = f"{section}_{key}"
                    flat[flat_key] = value
        else:
            flat[section] = values

    return flat


def _load_secrets(secrets_path: Path) -> dict:
    """Load and flatten secrets from YAML file."""
    if not secrets_path.exists():
        return {}

    with open(secrets_path) as f:
        secrets = yaml.safe_load(f) or {}

    if not isinstance(secrets, dict):
        return {}

    return _flatten_secrets_mapping(secrets)


def load_raw_secrets(secrets_path: Path) -> dict[str, Any]:
    """Load raw secrets.yml as a mapping.

    Returns an empty dict if the file does not exist or is invalid.
    """
    if not secrets_path.exists():
        return {}
    try:
        with secrets_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_raw_secrets(secrets_path: Path, secrets: dict[str, Any]) -> None:
    """Persist secrets mapping to secrets.yml."""
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    with secrets_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            secrets,
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )


def upsert_skill_env_vars(
    *,
    secrets_path: Path,
    skill_name: str,
    env_vars: dict[str, str],
    user_id: str | None = None,
) -> dict[str, Any]:
    """Upsert skill env vars into secrets.yml.

    Writes to:
      skills.<skill_name>.env                 (if user_id is None)
      skills.<skill_name>.users.<user_id>.env (if user_id is provided)
    """
    secrets = load_raw_secrets(secrets_path)

    skills = secrets.setdefault("skills", {})
    if not isinstance(skills, dict):
        secrets["skills"] = {}
        skills = secrets["skills"]

    skill_block = skills.setdefault(skill_name, {})
    if not isinstance(skill_block, dict):
        skills[skill_name] = {}
        skill_block = skills[skill_name]

    target_block: dict[str, Any]
    if user_id is None:
        target_block = skill_block
    else:
        users_block = skill_block.setdefault("users", {})
        if not isinstance(users_block, dict):
            skill_block["users"] = {}
            users_block = skill_block["users"]

        user_block = users_block.setdefault(str(user_id), {})
        if not isinstance(user_block, dict):
            users_block[str(user_id)] = {}
            user_block = users_block[str(user_id)]
        target_block = user_block

    env_block = target_block.setdefault("env", {})
    if not isinstance(env_block, dict):
        target_block["env"] = {}
        env_block = target_block["env"]

    for k, v in env_vars.items():
        if v is None:
            continue
        env_block[str(k)] = str(v)

    save_raw_secrets(secrets_path, secrets)
    return secrets


def get_skill_env_vars(
    *,
    secrets: dict[str, Any],
    skill_name: str,
    user_id: str | None = None,
) -> dict[str, str]:
    """Return merged (global + per-user override) env vars for a skill."""
    skills = secrets.get("skills")
    if not isinstance(skills, dict):
        return {}

    skill_block = skills.get(skill_name)
    if not isinstance(skill_block, dict):
        return {}

    merged: dict[str, str] = {}

    base_env = skill_block.get("env")
    if isinstance(base_env, dict):
        for k, v in base_env.items():
            if v is None:
                continue
            merged[str(k)] = str(v)

    if user_id is not None:
        users = skill_block.get("users")
        if isinstance(users, dict):
            user_block = users.get(str(user_id))
            if isinstance(user_block, dict):
                user_env = user_block.get("env")
                if isinstance(user_env, dict):
                    for k, v in user_env.items():
                        if v is None:
                            continue
                        merged[str(k)] = str(v)

    return merged


def refresh_runtime_env_from_secrets(
    *,
    secrets_path: Path,
    user_id: str | None = None,
    skill_names: list[str] | None = None,
) -> dict[str, Any]:
    """Reload secrets.yml and ensure the process env sees latest skill env vars.

    This supports "no restart" skill execution.
    """
    secrets = load_raw_secrets(secrets_path)
    skills = secrets.get("skills")
    if not isinstance(skills, dict):
        # Still attempt legacy env export for non-skill sections.
        try:
            _flatten_secrets_mapping(secrets)
        except Exception:
            pass
        return {"ok": True, "applied": 0, "skills": []}

    names = list(skills.keys()) if skill_names is None else skill_names
    applied = 0
    applied_skills: list[str] = []

    for name in names:
        env_vars = get_skill_env_vars(secrets=secrets, skill_name=name, user_id=user_id)
        if not env_vars:
            continue
        for k, v in env_vars.items():
            os.environ[k] = v
            applied += 1
        applied_skills.append(name)

    # Also export legacy env vars / non-skill sections for compatibility.
    try:
        _flatten_secrets_mapping(secrets)
    except Exception:
        pass

    return {"ok": True, "applied": applied, "skills": applied_skills}


class AgentConfig(BaseSettings):
    """Configuration with JSON file + secrets.yml + env var support.

    Load order (later overrides earlier):
    1. config.json - base configuration
    2. secrets.yml - sensitive values (API keys, tokens, access control)
    3. Environment variables - runtime overrides

    Prefix: AGENT_ (e.g., AGENT_TELEGRAM_BOT_TOKEN)
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model settings
    model_provider: ModelProvider = Field(default=ModelProvider.BEDROCK)
    bedrock_model_id: str = Field(default="anthropic.claude-3-sonnet-20240229-v1:0")
    bedrock_api_key: str | None = Field(default=None)
    openai_model_id: str = Field(default="gpt-4")
    openai_api_key: str | None = Field(default=None)
    google_model_id: str = Field(default="gemini-2.5-flash")
    google_api_key: str | None = Field(default=None)

    # Telegram settings
    telegram_bot_token: str = Field(...)

    # Access control
    allowed_users: list[str] = Field(
        default_factory=list,
        description=(
            "Optional whitelist of allowed user identifiers (e.g., Telegram username). "
            "If non-empty, non-whitelisted users are rejected."
        ),
    )

    # AWS settings
    aws_region: str = Field(default="us-east-1")
    aws_access_key_id: str | None = Field(default=None)
    aws_secret_access_key: str | None = Field(default=None)
    aws_session_token: str | None = Field(
        default=None,
        description=(
            "Optional AWS session token (for temporary credentials such as STS/SSO). "
            "If set, it will be exported as AWS_SESSION_TOKEN for AWS SDK calls."
        ),
    )
    sqs_queue_prefix: str = Field(default="agent-user-")
    localstack_endpoint: str | None = Field(default=None)

    # Database settings
    database_url: str = Field(default="sqlite+aiosqlite:///./agent.db")

    # Session settings
    session_storage_dir: str = Field(default="./sessions")

    # Secrets file location (used for skill env persistence + hot-reload)
    secrets_path: str = Field(default="secrets.yml")

    # Memory settings (AgentCore)
    memory_enabled: bool = Field(default=True)
    memory_id: str | None = Field(default=None)  # Existing AgentCore memory ID
    memory_name: str = Field(default="MordecaiMemory")  # For creating new
    memory_description: str = Field(default="Mordecai multi-user memory with strategies")
    memory_retrieval_top_k: int = Field(default=10)
    memory_retrieval_relevance_score: float = Field(default=0.2)

    # Session memory management
    max_conversation_messages: int = Field(
        default=30, description="Maximum messages before triggering extraction"
    )
    extraction_timeout_seconds: int = Field(
        default=30, description="Timeout for memory extraction operations"
    )
    conversation_window_size: int = Field(
        default=20, description="Number of messages to keep in conversation window"
    )

    # Observability / trace logging
    trace_enabled: bool = Field(
        default=True,
        description=(
            "Emit structured trace logs for agent message processing and tool calls. "
            "These logs are redacted and size-bounded."
        ),
    )
    trace_tool_io_enabled: bool = Field(
        default=True,
        description="Log tool/action call start/end events (inputs/outputs are sanitized).",
    )
    trace_model_io_enabled: bool = Field(
        default=False,
        description=(
            "Log user message and model response previews (sanitized). "
            "Enable with care if conversations may include sensitive data."
        ),
    )
    trace_max_chars: int = Field(
        default=2000,
        description="Maximum characters to log for any single input/output preview.",
    )
    trace_sample_rate: float = Field(
        default=1.0,
        description="Fraction of traces to log (0.0-1.0).",
    )

    # Skills settings (base directory, per-user subdirs created automatically)
    skills_base_dir: str = Field(default="./skills")
    shared_skills_dir: str = Field(default="./skills/shared")

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        """Post-init normalization.

        If a caller overrides skills_base_dir but leaves shared_skills_dir at
        its default, treat shared_skills_dir as a subdirectory of skills_base_dir.

        This keeps test/dev setups isolated (tmp skills dirs won't accidentally
        sync from the repository's ./skills/shared).
        """
        try:
            if self.shared_skills_dir == "./skills/shared" and self.skills_base_dir != "./skills":
                self.shared_skills_dir = str(Path(self.skills_base_dir) / "shared")
        except Exception:
            # Be conservative: never fail config construction due to normalization.
            return

        # Obsidian vault root auto-detection:
        # - Env override (AGENT_OBSIDIAN_VAULT_ROOT) always wins.
        # - If we're running in a container where /app/obsidian-vaults is mounted,
        #   use it when the configured path is missing in this runtime.
        try:
            if os.environ.get("AGENT_OBSIDIAN_VAULT_ROOT"):
                return

            container_root = Path(DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT).expanduser().resolve()
            container_root_ok = container_root.exists() and container_root.is_dir()
            if not container_root_ok:
                return

            raw = (self.obsidian_vault_root or "").strip()
            if not raw:
                self.obsidian_vault_root = str(container_root)
                return

            candidate = Path(raw).expanduser()
            # Only override when the configured path does not exist in this runtime.
            if not candidate.exists():
                self.obsidian_vault_root = str(container_root)
        except Exception:
            # Never fail config construction due to vault-path heuristics.
            return

    # Pending skill onboarding
    pending_skills_preflight_enabled: bool = Field(
        default=True,
        description="If true, scan and preflight skills in pending/ folders on startup",
    )
    pending_skills_preflight_install_deps: bool = Field(
        default=False,
        description="If true, preflight will install per-skill dependencies into per-skill venvs",
    )
    pending_skills_preflight_max_skills: int = Field(
        default=200,
        description="Maximum number of pending skills to preflight on startup",
    )
    pending_skills_generate_requirements: bool = Field(
        default=True,
        description=(
            "If true, generate/refresh requirements.txt for pending skills "
            "by analyzing Python imports in the skill folder"
        ),
    )
    pending_skills_pip_timeout_seconds: int = Field(
        default=180,
        description="Timeout for pip install during onboarding/preflight (per skill)",
    )

    pending_skills_run_scripts_timeout_seconds: int = Field(
        default=20,
        description=(
            "Timeout for running individual skill scripts during onboarding smoke tests (per script)"
        ),
    )
    pending_skills_run_scripts_max_files: int = Field(
        default=5,
        description=(
            "Maximum number of Python scripts to run per pending skill during onboarding smoke tests"
        ),
    )

    # File attachment settings
    enable_file_attachments: bool = Field(default=True)
    max_file_size_mb: int = Field(default=20)
    file_retention_hours: int = Field(default=24)
    allowed_file_extensions: list[str] = Field(
        default_factory=lambda: [
            # Documents
            ".txt",
            ".pdf",
            ".csv",
            ".json",
            ".xml",
            ".md",
            ".yaml",
            ".yml",
            # Code files
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".sql",
            ".sh",
            # Images
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        ]
    )

    # Vision model settings
    vision_model_id: str | None = Field(default=None)

    # Working folder settings
    temp_files_base_dir: str = Field(default="./temp_files")
    working_folder_base_dir: str = Field(default="./workspaces")

    # Obsidian vault (external to repo) + personality files
    obsidian_vault_root: str | None = Field(
        default=None,
        description=(
            "Filesystem path to the Obsidian vault root (outside the repo). "
            "Used for loading per-user personality/identity files under me/<TELEGRAM_ID>/."
        ),
    )

    # Personality injection settings
    personality_enabled: bool = Field(
        default=True,
        description="If true and obsidian_vault_root is configured, load soul/id into the system prompt.",
    )
    personality_max_chars: int = Field(
        default=20_000,
        description="Maximum characters to read from each personality markdown file (soul/id).",
    )

    # Agent commands (loaded from config, shown in system prompt)
    agent_commands: list[dict] = Field(
        default_factory=lambda: [
            {"name": "new", "description": "Start a new conversation session"},
            {"name": "logs", "description": "View recent activity logs"},
            {"name": "install skill <url>", "description": "Install a skill"},
            {"name": "uninstall skill <name>", "description": "Remove a skill"},
            {"name": "help", "description": "Show available commands"},
            {"name": "name <name>", "description": "Set the agent's name"},
        ]
    )

    # API settings
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8742)

    # Timezone settings
    timezone: str = Field(
        default="UTC",
        description="Timezone for displaying current date/time (e.g., 'Asia/Jerusalem', 'America/New_York')",
    )

    @classmethod
    def from_json_file(
        cls,
        config_path: str = "config.json",
        secrets_path: str = "secrets.yml",
    ) -> "AgentConfig":
        """Load config from JSON + secrets.yml with env var overrides.

        Args:
            config_path: Path to JSON config file.
            secrets_path: Path to secrets YAML file.

        Returns:
            Configured AgentConfig instance.
        """
        import os

        config_data = {}

        # Load base config from JSON
        json_path = Path(config_path)
        if json_path.exists():
            with open(json_path) as f:
                config_data = json.load(f)

        # Merge secrets (overrides JSON values)
        secrets = _load_secrets(Path(secrets_path))
        config_data.update(secrets)

        # Remove keys from config_data if corresponding env var is set
        # This allows env vars to properly override JSON config values
        env_prefix = "AGENT_"
        keys_to_remove = []
        for key in config_data:
            env_key = f"{env_prefix}{key.upper()}"
            if env_key in os.environ:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del config_data[key]

        # Env variables override everything (handled by pydantic-settings)
        return cls(**config_data)

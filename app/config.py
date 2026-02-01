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


def _find_repo_root(*, start: Path) -> Path:
    """Best-effort repository root discovery.

    We resolve relative config paths against the repo root so the app can be launched
    from any working directory (macOS dev, container entrypoints, tests, etc.).

    Root detection is heuristic but stable:
    - first directory containing `pyproject.toml`
    - otherwise fall back to the current working directory
    """

    try:
        start = start.resolve()
        for p in [start, *start.parents]:
            if (p / "pyproject.toml").exists():
                return p
    except Exception:
        pass

    return Path.cwd()


def _normalize_user_skills_dir_template(raw: str) -> str:
    """Normalize supported placeholders in a user skills dir template.

    Supported placeholders:
    - {username} / {user_id}
    - [USERNAME] / [USER_ID] (legacy docs-style)
    """

    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("[USERNAME]", "{username}")
    s = s.replace("[USER_ID]", "{user_id}")
    return s


def _validate_user_identifier_for_path(user_id: str) -> str:
    """Validate user_id for use in filesystem paths.

    Telegram usernames are already constrained, but we enforce a minimal safety check
    to prevent path traversal if an upstream caller misbehaves.
    """

    u = (user_id or "").strip()
    if not u:
        raise ValueError("user_id is required")
    if "/" in u or "\\" in u:
        raise ValueError("user_id must not contain path separators")
    if u == "." or u == ".." or ".." in u:
        raise ValueError("user_id must not contain traversal segments")
    return u


def resolve_user_skills_dir(config: Any, user_id: str, *, create: bool = True) -> Path:
    """Resolve the per-user skills directory in a portable way.

    If `config.user_skills_dir_template` is set, it is used as the authoritative
    path pattern (e.g. "/app/skills/{username}"). Otherwise, fall back to
    "{skills_base_dir}/{user_id}".

    Relative paths are resolved against the repository root.
    """

    u = _validate_user_identifier_for_path(user_id)
    raw_template = getattr(config, "user_skills_dir_template", None)
    template = _normalize_user_skills_dir_template(str(raw_template)) if raw_template else ""

    if template:
        try:
            rendered = template.format(username=u, user_id=u)
        except Exception as e:
            raise ValueError(f"Invalid user_skills_dir_template: {template}") from e

        p = Path(rendered).expanduser()
    else:
        base = Path(getattr(config, "skills_base_dir", "./skills")).expanduser()
        p = base / u

    if not p.is_absolute():
        repo_root = _find_repo_root(start=Path(__file__))
        p = repo_root / p

    p = p.resolve()
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_user_pending_skills_dir(config: Any, user_id: str, *, create: bool = True) -> Path:
    """Resolve the per-user pending skills directory."""

    p = resolve_user_skills_dir(config, user_id, create=create)
    d = p / "pending"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_user_skills_secrets_path(config: Any, user_id: str, *, create: bool = True) -> Path:
    """Resolve the per-user skill secrets file path.

    Convention:
      skills/<user_id>/skills_secrets.yml

    This file is intended to store ONLY per-user skill configuration/secrets.
    It should be git-ignored.
    """

    user_dir = resolve_user_skills_dir(config, user_id, create=create)
    p = user_dir / "skills_secrets.yml"
    if create:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return p


# Container deployments commonly mount a host Obsidian vault at a known path.
# If the configured obsidian_vault_root does not exist *in the current runtime*,
# we will prefer this mount when present.
#
# This is intentionally overridable because different deployments may mount the vault
# at different paths.
DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT = os.environ.get(
    "AGENT_CONTAINER_OBSIDIAN_VAULT_ROOT", "/app/obsidian-vaults"
)


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


def _deep_merge_dict(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge src into dst (mutates dst), returning dst."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge_dict(dst[str(k)], v)  # type: ignore[index]
        else:
            dst[str(k)] = v
    return dst


def upsert_skill_config(
    *,
    secrets_path: Path,
    skill_name: str,
    config_data: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    """Upsert structured skill configuration into secrets.yml.

    This is for skills that need a config file materialized on disk (e.g., TOML)
    or non-env structured config.

    Writes to:
      skills.<skill_name>                       (if user_id is None)
      skills.<skill_name>.users.<user_id>       (if user_id is provided)

    Notes:
    - This does NOT write into the 'env' subkey; use upsert_skill_env_vars for that.
    - Reserved keys 'env' and 'users' in config_data are ignored.
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

    clean: dict[str, Any] = {}
    keys_to_delete: set[str] = set()
    for k, v in (config_data or {}).items():
        key = str(k)
        if key in {"env", "users"}:
            continue
        # Support explicit deletions: if a caller passes null for a key in config_json,
        # remove it from the stored skill config instead of persisting a null.
        # This is especially useful for cleaning up stale provider credentials.
        if v is None:
            keys_to_delete.add(key)
            continue
        clean[key] = v

    _deep_merge_dict(target_block, clean)

    # Apply deletions after merge.
    for key in keys_to_delete:
        target_block.pop(key, None)
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


# Runtime tracking for skill-derived env vars applied from secrets.yml.
#
# This prevents cross-user leakage in a long-running process.
# We only manage variables injected from the `skills:` section.
_RUNTIME_SKILL_ENV_CONTEXT: tuple[str, str] | None = None  # (resolved secrets_path, user_id)
_RUNTIME_SKILL_ENV_KEYS_BY_SKILL: dict[str, set[str]] = {}
_RUNTIME_SKILL_ENV_MANAGED_KEYS: set[str] = set()


def refresh_runtime_env_from_secrets(
    *,
    secrets_path: Path,
    user_id: str | None = None,
    skill_names: list[str] | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Reload secrets.yml and ensure the process env sees latest skill env vars.

    This supports "no restart" skill execution.
    """
    # Track which env vars we injected from the `skills:` section so we can
    # safely prevent cross-user leakage in a long-running, multi-tenant process.
    #
    # Important: we do NOT attempt to manage every env var in the process—only
    # the ones we ourselves applied from secrets.yml skill blocks.
    global \
        _RUNTIME_SKILL_ENV_CONTEXT, \
        _RUNTIME_SKILL_ENV_KEYS_BY_SKILL, \
        _RUNTIME_SKILL_ENV_MANAGED_KEYS

    try:
        secrets_key = str(secrets_path.resolve())
    except Exception:
        secrets_key = str(secrets_path)

    context = (secrets_key, str(user_id or ""))
    context_changed = (
        _RUNTIME_SKILL_ENV_CONTEXT is not None and _RUNTIME_SKILL_ENV_CONTEXT != context
    )

    # ------------------------------------------------------------
    # Load + merge secrets sources
    #
    # Precedence (later wins):
    #   config.yml (optional, repo-root)  < secrets.yml < skills/<user>/skills_secrets.yml
    #
    # Only the `skills:` section is considered from per-user skills_secrets.yml.
    # ------------------------------------------------------------
    merged_secrets: dict[str, Any] = {}

    # Optional repo-level config.yml
    try:
        repo_root = _find_repo_root(start=Path(__file__))
        cfg_yml = repo_root / "config.yml"
        if cfg_yml.exists() and cfg_yml.is_file():
            with cfg_yml.open("r", encoding="utf-8") as f:
                cfg_data = yaml.safe_load(f) or {}
            if isinstance(cfg_data, dict):
                _deep_merge_dict(merged_secrets, cfg_data)
    except Exception:
        pass

    # Global secrets.yml
    secrets = load_raw_secrets(secrets_path)
    if isinstance(secrets, dict):
        _deep_merge_dict(merged_secrets, secrets)

    # Per-user skill secrets (skills/<user>/skills_secrets.yml)
    if user_id is not None and config is not None:
        try:
            user_skills_secrets_path = resolve_user_skills_secrets_path(config, user_id)
            user_secrets = load_raw_secrets(user_skills_secrets_path)
            if isinstance(user_secrets, dict):
                user_skills = user_secrets.get("skills")
                if isinstance(user_skills, dict):
                    merged_secrets.setdefault("skills", {})
                    if isinstance(merged_secrets.get("skills"), dict):
                        _deep_merge_dict(merged_secrets["skills"], user_skills)  # type: ignore[index]
        except Exception:
            pass

    skills = merged_secrets.get("skills")
    if not isinstance(skills, dict):
        # Still attempt legacy env export for non-skill sections.
        try:
            _flatten_secrets_mapping(merged_secrets)
        except Exception:
            pass
        return {"ok": True, "applied": 0, "skills": []}

    # If the user context changed, force a full refresh to ensure the environment
    # is correct and we can safely clear keys from the previous user.
    effective_skill_names = None if (skill_names is None or context_changed) else skill_names
    names = list(skills.keys()) if effective_skill_names is None else effective_skill_names

    # Compute desired env for the skills we're refreshing.
    desired_env: dict[str, str] = {}
    new_keys_by_skill: dict[str, set[str]] = {}
    applied_skills: list[str] = []

    for name in names:
        env_vars = get_skill_env_vars(secrets=merged_secrets, skill_name=name, user_id=user_id)
        if not env_vars:
            new_keys_by_skill[name] = set()
            continue
        desired_env.update(env_vars)
        new_keys_by_skill[name] = set(env_vars.keys())
        applied_skills.append(name)

    full_refresh = effective_skill_names is None

    # On full refresh (including any user switch), remove keys we previously
    # injected that are not desired for the current context.
    if full_refresh:
        for k in list(_RUNTIME_SKILL_ENV_MANAGED_KEYS):
            if k not in desired_env:
                os.environ.pop(k, None)

    # ------------------------------------------------------------
    # Materialize per-user example-based config templates.
    #
    # Convention:
    #   If a skill directory contains *_example or *.example files,
    #   Mordecai will render them into the same directory with the suffix removed.
    #
    # Placeholders:
    #   [PLACEHOLDER] will be replaced with values from the merged skill config.
    #
    # Special env export:
    #   If a file named {skill}.toml_example exists, export {SKILL}_CONFIG pointing
    #   to the rendered {skill}.toml (e.g., HIMALAYA_CONFIG).
    # ------------------------------------------------------------
    def _materialize_skill_templates() -> dict[str, str]:
        if user_id is None or config is None:
            return {}

        import re

        extra_env: dict[str, str] = {}

        # Determine the per-user skills directory where shared skills are mirrored.
        try:
            user_dir = resolve_user_skills_dir(config, user_id, create=True)
        except Exception:
            return {}

        skills_block = merged_secrets.get("skills")
        if not isinstance(skills_block, dict):
            return {}

        # A conservative cap to avoid runaway IO if a skill accidentally includes many example files.
        max_templates_total = 50
        rendered_count = 0

        # Track config env vars we set so we can also emit a per-user .env convenience file.
        config_env_lines: dict[str, str] = {}

        for skill in names:
            if rendered_count >= max_templates_total:
                break

            skill_dir = user_dir / str(skill)
            if not (skill_dir.exists() and skill_dir.is_dir()):
                continue

            # Merge config values from the skill block. We accept:
            # - top-level scalar keys (e.g., skills.himalaya.GMAIL)
            # - env vars (skills.himalaya.env)
            # - per-user overrides in the merged mapping (already applied)
            skill_cfg = skills_block.get(skill)
            if not isinstance(skill_cfg, dict):
                skill_cfg = {}

            # Build replacement map.
            replacements: dict[str, str] = {}

            # Include scalar config keys.
            for k, v in skill_cfg.items():
                if k in {"env", "users"}:
                    continue
                if isinstance(v, (dict, list)):
                    continue
                key = str(k).strip()
                if not key:
                    continue
                if v is None:
                    continue
                replacements[key.upper()] = str(v)

            # Include env keys.
            env_block = skill_cfg.get("env")
            if isinstance(env_block, dict):
                for k, v in env_block.items():
                    if v is None:
                        continue
                    replacements[str(k).upper()] = str(v)

            # Apply legacy per-user overrides (if present in secrets.yml) for template rendering.
            if user_id is not None:
                users_block = skill_cfg.get("users")
                if isinstance(users_block, dict):
                    user_block = users_block.get(str(user_id))
                    if isinstance(user_block, dict):
                        # Merge scalar keys and env keys from the user block.
                        for k, v in user_block.items():
                            if k in {"env", "users"}:
                                continue
                            if isinstance(v, (dict, list)):
                                continue
                            key = str(k).strip()
                            if key and v is not None:
                                replacements[key.upper()] = str(v)

                        user_env = user_block.get("env")
                        if isinstance(user_env, dict):
                            for k, v in user_env.items():
                                if v is None:
                                    continue
                                replacements[str(k).upper()] = str(v)

            # Find example templates.
            example_files: list[Path] = []
            try:
                for p in skill_dir.rglob("*_example"):
                    if p.is_file():
                        example_files.append(p)
                for p in skill_dir.rglob("*.example"):
                    if p.is_file():
                        example_files.append(p)
            except Exception:
                continue

            # De-dup paths.
            seen_paths: set[str] = set()
            uniq_example_files: list[Path] = []
            for p in example_files:
                s = str(p)
                if s in seen_paths:
                    continue
                seen_paths.add(s)
                uniq_example_files.append(p)

            for tpl in uniq_example_files:
                if rendered_count >= max_templates_total:
                    break

                try:
                    raw = tpl.read_text(encoding="utf-8")
                except Exception:
                    continue

                # Resolve destination path.
                if tpl.name.endswith("_example"):
                    dest_name = tpl.name[: -len("_example")]
                elif tpl.name.endswith(".example"):
                    dest_name = tpl.name[: -len(".example")]
                else:
                    continue

                # Write rendered outputs next to the per-user skills secrets file
                # (i.e., in the per-user skills directory root).
                #
                # This keeps generated configs isolated per user and makes it
                # easy to point CLIs to a stable per-user config path.
                #
                # Naming convention:
                # - If the rendered filename already starts with the skill name
                #   (e.g., himalaya.toml), keep it.
                # - Otherwise, prefix to avoid collisions across skills.
                #
                # Examples:
                # - skill=himalaya, tpl=himalaya.toml_example -> himalaya.toml
                # - skill=foo, tpl=config.toml_example -> foo__config.toml
                if dest_name.startswith(f"{skill}.") or dest_name == str(skill):
                    out_name = dest_name
                else:
                    out_name = f"{skill}__{dest_name}"
                dest = user_dir / out_name

                def _replace(match: re.Match) -> str:
                    key = (match.group(1) or "").strip().upper()
                    if not key:
                        return match.group(0)
                    if key not in replacements:
                        return match.group(0)
                    return replacements[key]

                rendered = re.sub(r"\[([A-Z0-9_]+)\]", _replace, raw)

                try:
                    # Only write when changed to reduce churn.
                    if dest.exists():
                        try:
                            existing = dest.read_text(encoding="utf-8")
                        except Exception:
                            existing = None
                        if existing == rendered:
                            pass
                        else:
                            dest.write_text(rendered, encoding="utf-8")
                    else:
                        dest.write_text(rendered, encoding="utf-8")
                except Exception:
                    continue

                rendered_count += 1

                # Auto-export <SKILL>_CONFIG for the canonical pattern:
                #   {skill}.toml_example -> {skill}.toml
                # This stays generic and applies to any skill following the convention.
                if dest.name == f"{skill}.toml":
                    env_key = f"{str(skill).upper()}_CONFIG"
                    extra_env[env_key] = str(dest)
                    config_env_lines[env_key] = str(dest)

        # Write a per-user .env convenience file (git-ignored) containing only *_CONFIG vars.
        if config_env_lines:
            try:
                env_path = user_dir / ".env"
                existing_lines: list[str] = []
                if env_path.exists():
                    try:
                        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
                    except Exception:
                        existing_lines = []

                # Parse existing assignments.
                kept: list[str] = []
                for ln in existing_lines:
                    if not ln.strip() or ln.lstrip().startswith("#"):
                        kept.append(ln)
                        continue
                    if "=" not in ln:
                        kept.append(ln)
                        continue
                    k = ln.split("=", 1)[0].strip()
                    if k in config_env_lines:
                        continue
                    kept.append(ln)

                # Append updated lines.
                if kept and kept[-1].strip():
                    kept.append("")
                kept.append("# Auto-generated by Mordecai. Do not commit.")
                for k, v in sorted(config_env_lines.items()):
                    kept.append(f"{k}={v}")

                env_path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
            except Exception:
                pass

        return extra_env

    extra_env = _materialize_skill_templates()
    if extra_env:
        for env_key, env_val in extra_env.items():
            # Associate the exported config key with the corresponding skill so runtime tracking
            # can safely unset it on user switch.
            # Infer skill name from prefix: <SKILL>_CONFIG.
            if not env_key.endswith("_CONFIG"):
                continue
            inferred_skill = env_key[: -len("_CONFIG")].lower()
            if inferred_skill in new_keys_by_skill:
                new_keys_by_skill[inferred_skill].add(env_key)
            desired_env[env_key] = env_val
            applied_skills.append(inferred_skill)

    # Apply desired env vars.
    applied = 0
    for k, v in desired_env.items():
        os.environ[k] = v
        applied += 1

    # Update tracking.
    if full_refresh:
        _RUNTIME_SKILL_ENV_CONTEXT = context
        _RUNTIME_SKILL_ENV_KEYS_BY_SKILL = {k: set(v) for k, v in new_keys_by_skill.items()}
        _RUNTIME_SKILL_ENV_MANAGED_KEYS = set(desired_env.keys())
    else:
        # Partial refresh: only update the touched skills; do not wipe other skills.
        for skill, keys in new_keys_by_skill.items():
            _RUNTIME_SKILL_ENV_KEYS_BY_SKILL[skill] = set(keys)
        _RUNTIME_SKILL_ENV_MANAGED_KEYS.update(desired_env.keys())

    # Best-effort materialization of per-skill config files.
    # Global skill blocks with 'path' are handled by _flatten_secrets_mapping, but
    # per-user blocks are not; handle them here when user_id is provided.
    try:
        for name in names:
            skill_block = skills.get(name)
            if not isinstance(skill_block, dict):
                continue

            # Global config file (legacy / existing behavior)
            if isinstance(skill_block.get("path"), str) and str(skill_block.get("path")).strip():
                _create_config_file(str(skill_block["path"]), skill_block)
    except Exception:
        # Never fail refresh due to config file IO.
        pass

    # Also export legacy env vars / non-skill sections for compatibility.
    try:
        _flatten_secrets_mapping(merged_secrets)
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

    auto_create_tables: bool = Field(
        default=False,
        description=(
            "If true, the app will call Base.metadata.create_all() on startup. "
            "This is convenient for quick local experimentation, but it bypasses "
            "Alembic versioning and can cause later migrations to fail (e.g. 'table already exists'). "
            "For containers/production, keep this false and rely on Alembic migrations."
        ),
    )

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

    user_skills_dir_template: str | None = Field(
        default=None,
        description=(
            "Optional template for resolving the per-user skills directory. "
            "If set, the per-user skills directory is resolved from this pattern (e.g. '/app/skills/{username}'). "
            "Supported placeholders: {username}, {user_id} and legacy docs-style [USERNAME]/[USER_ID]."
        ),
    )

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        """Post-init normalization.

        If a caller overrides skills_base_dir but leaves shared_skills_dir at
        its default, treat shared_skills_dir as a subdirectory of skills_base_dir.

        This keeps test/dev setups isolated (tmp skills dirs won't accidentally
        sync from the repository's ./skills/shared).
        """
        try:
            # 0) Normalize user_skills_dir_template early.
            raw_template = (self.user_skills_dir_template or "").strip()
            template = _normalize_user_skills_dir_template(raw_template) if raw_template else ""

            # If a template is set and skills_base_dir is still the default,
            # derive a scan-friendly base directory from the template.
            #
            # Example:
            #   template = /app/skills/{username}
            #   skills_base_dir becomes /app/skills
            if template and self.skills_base_dir == "./skills":
                try:
                    parts = list(Path(template).parts)
                    placeholder_idx: int | None = None
                    for i, part in enumerate(parts):
                        if "{username}" in part or "{user_id}" in part:
                            placeholder_idx = i
                            break
                    if placeholder_idx is not None and placeholder_idx > 0:
                        self.skills_base_dir = str(Path(*parts[:placeholder_idx]))
                except Exception:
                    # Never fail config initialization due to template parsing.
                    pass

            # 1) If caller overrides skills_base_dir but leaves shared_skills_dir at the
            # default, treat shared_skills_dir as a subdirectory of skills_base_dir.
            #
            # IMPORTANT: when user_skills_dir_template is set, we do NOT automatically
            # relocate shared_skills_dir based on skills_base_dir, because the template
            # often points to a persistent volume while shared skills remain in the repo.
            if (
                self.shared_skills_dir == "./skills/shared"
                and self.skills_base_dir != "./skills"
                and not template
            ):
                self.shared_skills_dir = str(Path(self.skills_base_dir) / "shared")

            # 2) Resolve relative paths against the repository root rather than CWD.
            repo_root = _find_repo_root(start=Path(__file__))

            skills_base = Path(self.skills_base_dir).expanduser()
            if not skills_base.is_absolute():
                skills_base = repo_root / skills_base
            self.skills_base_dir = str(skills_base.resolve())

            shared_skills = Path(self.shared_skills_dir).expanduser()
            if not shared_skills.is_absolute():
                shared_skills = repo_root / shared_skills
            self.shared_skills_dir = str(shared_skills.resolve())

            # Normalize + resolve template against repo_root for consistency.
            if template:
                tp = Path(template).expanduser()
                if not tp.is_absolute():
                    tp = repo_root / tp
                self.user_skills_dir_template = str(tp)

            # Optional: allow relative obsidian_vault_root in dev (e.g., ./vault).
            if self.obsidian_vault_root:
                vault = Path(str(self.obsidian_vault_root)).expanduser()
                if not vault.is_absolute():
                    self.obsidian_vault_root = str((repo_root / vault).resolve())
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

        # Optional config.yml overlay (repo-root). This is intended for non-secret config.
        # Precedence: config.json < config.yml < secrets.yml < env
        try:
            repo_root = _find_repo_root(start=Path(__file__))
            cfg_yml = repo_root / "config.yml"
            if cfg_yml.exists() and cfg_yml.is_file():
                with cfg_yml.open("r", encoding="utf-8") as f:
                    yml_data = yaml.safe_load(f) or {}
                if isinstance(yml_data, dict):
                    config_data.update(yml_data)
        except Exception:
            pass

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

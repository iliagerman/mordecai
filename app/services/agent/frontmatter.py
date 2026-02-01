from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.agent.types import RequirementSpec, WhenClause


def _coerce_when_clause(value: Any) -> WhenClause | None:
    """Best-effort coercion of YAML-parsed 'when' objects into a WhenClause.

    YAML parsing can yield arbitrary dict keys and non-string scalar values.
    We only keep the supported keys and coerce simple scalars to strings.
    """

    if not isinstance(value, Mapping):
        return None

    out: WhenClause = {}

    for key in ("config", "env", "equals"):
        raw = value.get(key)
        if raw is None:
            continue

        if isinstance(raw, str):
            s = raw.strip()
        elif isinstance(raw, (int, float, bool)):
            s = str(raw)
        else:
            continue

        if s:
            out[key] = s

    return out or None


def parse_skill_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from SKILL.md content.

    This intentionally supports nested YAML (e.g., requires.env) so skills can
    declare required environment variables.

    Notes:
        - YAML forbids tab indentation, but some historical SKILL.md files used
          leading tabs. We normalize *leading* tabs to 2 spaces.
    """

    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    raw = parts[1]

    # Normalize leading tabs to spaces (YAML forbids tabs for indentation).
    try:
        normalized_lines: list[str] = []
        for ln in raw.splitlines():
            if ln.startswith("\t"):
                prefix_tabs = len(ln) - len(ln.lstrip("\t"))
                normalized_lines.append(("  " * prefix_tabs) + ln.lstrip("\t"))
            else:
                normalized_lines.append(ln)
        raw = "\n".join(normalized_lines)
    except Exception:
        # Best-effort only.
        pass

    try:
        import yaml

        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dedupe_requirements(reqs: list[RequirementSpec]) -> list[RequirementSpec]:
    seen: set[str] = set()
    out: list[RequirementSpec] = []
    for r in reqs:
        n = (r.get("name") or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(r)
    return out


def extract_required_env(frontmatter: dict[str, Any]) -> list[RequirementSpec]:
    """Extract requires.env entries (string or dict) from frontmatter."""

    requires = frontmatter.get("requires")
    if not isinstance(requires, dict):
        return []

    env_list = requires.get("env")
    if not isinstance(env_list, list):
        return []

    out: list[RequirementSpec] = []
    for item in env_list:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"name": name})
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue

            rec: RequirementSpec = {"name": name}

            prompt = item.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                rec["prompt"] = prompt.strip()

            example = item.get("example")
            if isinstance(example, str) and example.strip():
                rec["example"] = example.strip()

            when = _coerce_when_clause(item.get("when"))
            if when is not None:
                rec["when"] = when

            out.append(rec)

    return _dedupe_requirements(out)


def extract_required_config(frontmatter: dict[str, Any]) -> list[RequirementSpec]:
    """Extract requires.config entries (string or dict) from frontmatter."""

    requires = frontmatter.get("requires")
    if not isinstance(requires, dict):
        return []

    cfg_list = requires.get("config")
    if not isinstance(cfg_list, list):
        return []

    out: list[RequirementSpec] = []
    for item in cfg_list:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"name": name})
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue

            rec: RequirementSpec = {"name": name}

            prompt = item.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                rec["prompt"] = prompt.strip()

            example = item.get("example")
            if isinstance(example, str) and example.strip():
                rec["example"] = example.strip()

            when = _coerce_when_clause(item.get("when"))
            if when is not None:
                rec["when"] = when

            out.append(rec)

    return _dedupe_requirements(out)

from __future__ import annotations

import re
from pathlib import Path

from app.services.agent.frontmatter import extract_required_env, parse_skill_frontmatter


_API_KEY_REF_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,}_API_KEY)\b")


def _find_repo_root(start: Path) -> Path:
    cur = start
    for _ in range(12):
        if (cur / "pyproject.toml").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise RuntimeError("Could not locate repo root (pyproject.toml not found)")


def test_shared_skills_that_reference_api_keys_declare_requires_env() -> None:
    """Guardrail: if SKILL.md mentions an *_API_KEY, it must declare it in frontmatter.

    This ensures the agent can detect missing credentials and prompt the user to persist
    them into skills_secrets.yml before running shell commands.
    """

    repo_root = _find_repo_root(Path(__file__).resolve())
    shared_skills_dir = repo_root / "skills" / "shared"

    assert shared_skills_dir.exists(), f"Expected {shared_skills_dir} to exist"

    failures: list[str] = []

    for skill_md in sorted(shared_skills_dir.glob("*/SKILL.md")):
        content = skill_md.read_text(encoding="utf-8")

        frontmatter = parse_skill_frontmatter(content)
        declared = {
            (r.name or "").strip()
            for r in extract_required_env(frontmatter)
            if (r.name or "").strip()
        }

        referenced = set(_API_KEY_REF_RE.findall(content))

        # If the skill references an API key at all (docs, examples, metadata), it should
        # be declared so Mordecai can prompt and store it.
        missing = sorted([k for k in referenced if k not in declared])
        if missing:
            failures.append(
                f"{skill_md.relative_to(repo_root)} missing requires.env for: {', '.join(missing)}"
            )

    assert not failures, "\n".join(failures)

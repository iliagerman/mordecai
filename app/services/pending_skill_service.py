"""Pending skill onboarding and preflight.

A "pending" skill is a skill directory staged for validation and onboarding.
Pending skills live under:
- shared: {shared_skills_dir}/pending/<skill_name>/
- per-user: {skills_base_dir}/{user_id}/pending/<skill_name>/

This service:
- Normalizes SKILL.md (accepts skill.md and renames to SKILL.md)
- Performs safe, non-executing validation (Python syntax checks)
- Installs per-skill dependencies into a per-skill venv
- Writes FAILED.json on any failure
- Promotes pending skills into active skill folders on success

Design goals:
- Pending skills are never loaded automatically by the agent.
- Operations are idempotent and produce clear reports.
"""

# ruff: noqa: I001

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import ast
import re
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.config import (
    AgentConfig,
    refresh_runtime_env_from_secrets,
    resolve_user_pending_skills_dir,
    resolve_user_skills_dir,
)

from app.services.agent.frontmatter import parse_skill_frontmatter


_RESERVED_DIR_NAMES = {"pending", "failed", ".venvs", ".venv", "__pycache__"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class PendingSkillCandidate:
    scope: Literal["shared", "user"]
    user_id: str | None
    skill_name: str
    skill_dir: Path


class PendingSkillService:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.skills_base_dir = Path(config.skills_base_dir)
        self.shared_skills_dir = Path(config.shared_skills_dir)

        self.skills_base_dir.mkdir(parents=True, exist_ok=True)
        self.shared_skills_dir.mkdir(parents=True, exist_ok=True)

        # Ensure pending + venv roots exist
        (self.shared_skills_dir / "pending").mkdir(parents=True, exist_ok=True)
        # NOTE: Per-skill venvs are created on demand during onboarding.

    # ---------------------------
    # Utilities
    # ---------------------------

    def _dir_fingerprint(self, root: Path) -> str:
        """Compute a stable fingerprint of a skill directory.

        Used to decide whether onboarding should re-run when a skill already
        exists.

        Excludes common runtime artifacts.
        """
        ignore_names = {
            ".venv",
            "__pycache__",
            "FAILED.json",
            "onboarding_report.json",
            "ONBOARDING_REPORT.md",
        }

        h = hashlib.sha256()
        if not root.exists() or not root.is_dir():
            return h.hexdigest()

        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if any(part in ignore_names for part in rel.parts):
                continue
            # Avoid hashing extremely large files accidentally.
            if p.is_file():
                h.update(str(rel).encode("utf-8"))
                try:
                    h.update(p.read_bytes())
                except Exception:
                    # If unreadable, include metadata so fingerprint still changes.
                    h.update(str(p.stat().st_size).encode("utf-8"))
            elif p.is_dir():
                h.update((str(rel) + "/").encode("utf-8"))
        return h.hexdigest()

    def _backup_existing_skill_dir(self, dest: Path) -> Path:
        """Move an existing active skill directory to a backup location."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_root = dest.parent / "failed"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"{dest.name}.{ts}.bak"
        shutil.move(str(dest), str(backup_path))
        return backup_path

    # ---------------------------
    # Discovery
    # ---------------------------

    def _iter_user_ids(self) -> Iterable[str]:
        if not self.skills_base_dir.exists():
            return []

        user_ids: list[str] = []
        for item in self.skills_base_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith("."):
                continue
            if item.name in _RESERVED_DIR_NAMES:
                continue
            if item.name == "shared":
                continue
            user_ids.append(item.name)
        return sorted(user_ids)

    def _pending_dir_shared(self) -> Path:
        return self.shared_skills_dir / "pending"

    def _pending_dir_user(self, user_id: str) -> Path:
        return resolve_user_pending_skills_dir(self.config, user_id, create=True)

    def list_pending(
        self, *, user_id: str | None, include_shared: bool = True
    ) -> list[PendingSkillCandidate]:
        candidates: list[PendingSkillCandidate] = []

        if include_shared:
            shared_pending = self._pending_dir_shared()
            if shared_pending.exists():
                for item in shared_pending.iterdir():
                    if not item.is_dir() or item.name.startswith("__"):
                        continue
                    if item.name in _RESERVED_DIR_NAMES:
                        continue
                    candidates.append(
                        PendingSkillCandidate(
                            scope="shared",
                            user_id=None,
                            skill_name=item.name,
                            skill_dir=item,
                        )
                    )

        if user_id is not None:
            user_pending = self._pending_dir_user(user_id)
            if user_pending.exists():
                for item in user_pending.iterdir():
                    if not item.is_dir() or item.name.startswith("__"):
                        continue
                    if item.name in _RESERVED_DIR_NAMES:
                        continue
                    candidates.append(
                        PendingSkillCandidate(
                            scope="user",
                            user_id=user_id,
                            skill_name=item.name,
                            skill_dir=item,
                        )
                    )

        return candidates

    # ---------------------------
    # Normalization + validation
    # ---------------------------

    def _skill_md_path(self, skill_dir: Path) -> Path:
        return skill_dir / "SKILL.md"

    def _skill_md_alt_path(self, skill_dir: Path) -> Path:
        return skill_dir / "skill.md"

    def _requirements_path(self, skill_dir: Path) -> Path:
        return skill_dir / "requirements.txt"

    def _parse_requirements_lines(self, txt: str) -> list[str]:
        out: list[str] = []
        for raw in txt.splitlines():
            ln = raw.strip()
            if not ln or ln.startswith("#"):
                continue
            # Skip common pip options / includes.
            if ln.startswith("-"):
                continue
            out.append(ln)
        # stable + de-duped
        seen: set[str] = set()
        deduped: list[str] = []
        for ln in out:
            if ln in seen:
                continue
            seen.add(ln)
            deduped.append(ln)
        return sorted(deduped)

    def sync_requires_pip_from_requirements(self, candidate: PendingSkillCandidate) -> dict:
        """Best-effort: keep SKILL.md frontmatter `requires.pip` aligned with requirements.txt.

        This improves consistency for onboarded skills and makes it easier for agents
        to understand which Python packages are needed.

        Conservative behavior:
        - If requirements.txt is missing/empty, do nothing.
        - If SKILL.md frontmatter is missing, do nothing (normalize_skill_md should run first).
        - Only writes SKILL.md if an update is actually needed.
        """
        skill_dir = candidate.skill_dir
        req_path = self._requirements_path(skill_dir)
        skill_md = self._skill_md_path(skill_dir)

        if not req_path.exists() or not req_path.is_file():
            return {"ok": True, "updated": False, "reason": "no requirements.txt"}
        if not skill_md.exists() or not skill_md.is_file():
            return {"ok": True, "updated": False, "reason": "no SKILL.md"}

        try:
            req_txt = req_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"Failed to read requirements.txt: {e}"}

        reqs = self._parse_requirements_lines(req_txt)
        if not reqs:
            return {"ok": True, "updated": False, "reason": "requirements.txt empty"}

        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"Failed to read SKILL.md: {e}"}

        if not content.startswith("---"):
            return {"ok": True, "updated": False, "reason": "no frontmatter"}

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {"ok": True, "updated": False, "reason": "invalid frontmatter"}

        # Parse existing frontmatter
        fm = parse_skill_frontmatter(content)
        requires = fm.get("requires")
        if not isinstance(requires, dict):
            requires = {}
            fm["requires"] = requires

        existing = requires.get("pip")
        existing_list: list[str] = []
        if isinstance(existing, list):
            existing_list = [str(x).strip() for x in existing if str(x).strip()]
        elif isinstance(existing, str) and existing.strip():
            existing_list = [existing.strip()]

        existing_norm = sorted({x for x in existing_list if x})
        desired_norm = sorted({x for x in reqs if x})

        if existing_norm == desired_norm:
            return {"ok": True, "updated": False, "reason": "already in sync"}

        requires["pip"] = desired_norm

        try:
            import yaml

            new_frontmatter = yaml.safe_dump(
                fm,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            ).strip()
        except Exception as e:
            return {"ok": False, "error": f"Failed to serialize frontmatter YAML: {e}"}

        new_content = "---\n" + new_frontmatter + "\n---" + parts[2]
        try:
            skill_md.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"Failed to write SKILL.md: {e}"}

        return {"ok": True, "updated": True, "pip": desired_norm}

    def normalize_skill_md(self, candidate: PendingSkillCandidate) -> dict:
        """Normalize SKILL.md in-place.

        Conservative behavior:
        - Rename skill.md -> SKILL.md
        - If missing, create a minimal SKILL.md with frontmatter.
        - If frontmatter missing, prepend a minimal frontmatter block.
        """
        skill_dir = candidate.skill_dir
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md = self._skill_md_path(skill_dir)
        alt = self._skill_md_alt_path(skill_dir)

        actions: list[str] = []

        if not skill_md.exists() and alt.exists():
            alt.rename(skill_md)
            actions.append("renamed skill.md -> SKILL.md")

        if not skill_md.exists():
            content = (
                "---\n"
                f"name: {candidate.skill_name}\n"
                "description: Pending skill (auto-generated)\n"
                "---\n\n"
                f"# {candidate.skill_name}\n\n"
                "## What this skill does\n\n"
                "(Describe the capability here.)\n\n"
                "## How to use\n\n"
                "(Step-by-step instructions for the agent.)\n"
            )
            skill_md.write_text(content, encoding="utf-8")
            actions.append("created SKILL.md")

        # Ensure frontmatter exists
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            # Rewrite as minimal if unreadable
            content = ""

        if not content.startswith("---"):
            new_content = (
                "---\n"
                f"name: {candidate.skill_name}\n"
                "description: Pending skill\n"
                "---\n\n" + (content.strip() + "\n" if content.strip() else "")
            )
            skill_md.write_text(new_content, encoding="utf-8")
            actions.append("added frontmatter")

        return {"ok": True, "actions": actions, "skill_md": str(skill_md)}

    def _python_files(self, root: Path) -> list[Path]:
        return [p for p in root.rglob("*.py") if p.is_file()]

    def validate_python_syntax(self, candidate: PendingSkillCandidate) -> dict:
        """Validate that all Python files in the skill compile.

        Does NOT execute any skill code.
        """
        py_files = self._python_files(candidate.skill_dir)
        if not py_files:
            return {"ok": True, "checked": 0}

        try:
            # Compile each file individually to get actionable errors
            import py_compile

            for p in py_files:
                py_compile.compile(str(p), doraise=True)
            return {"ok": True, "checked": len(py_files)}
        except Exception as e:
            return {"ok": False, "checked": len(py_files), "error": str(e)}

    # ---------------------------
    # FAILED.json
    # ---------------------------

    def _failed_path(self, skill_dir: Path) -> Path:
        return skill_dir / "FAILED.json"

    def write_failed(
        self,
        candidate: PendingSkillCandidate,
        *,
        stage: str,
        error: str,
        details: dict | None = None,
    ) -> None:
        payload: dict = {
            "status": "failed",
            "stage": stage,
            "error": error,
            "timestamp": _utc_now_iso(),
            "scope": candidate.scope,
            "user_id": candidate.user_id,
            "skill_name": candidate.skill_name,
        }
        if details:
            payload["details"] = details
        self._failed_path(candidate.skill_dir).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def clear_failed(self, candidate: PendingSkillCandidate) -> None:
        p = self._failed_path(candidate.skill_dir)
        if p.exists():
            p.unlink()

    # ---------------------------
    # Dependencies (per-skill venv)
    # ---------------------------

    def _venv_dir(self, candidate: PendingSkillCandidate) -> Path:
        # Keep the venv inside the skill directory so a move/copy of the skill
        # also moves/copies its dependencies.
        return candidate.skill_dir / ".venv"

    def _venv_python(self, venv_dir: Path) -> Path:
        return venv_dir / "bin" / "python"

    def _requirements_path(self, skill_dir: Path) -> Path:
        return skill_dir / "requirements.txt"

    def _read_skill_frontmatter(self, skill_dir: Path) -> str:
        """Return raw YAML frontmatter text (without --- delimiters)."""
        skill_md = self._skill_md_path(skill_dir)
        if not skill_md.exists():
            return ""
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            return ""

        if not content.startswith("---"):
            return ""
        parts = content.split("---", 2)
        if len(parts) < 3:
            return ""
        return parts[1].strip("\n")

    def _read_skill_frontmatter_data(self, skill_dir: Path) -> dict:
        """Parse YAML frontmatter into a dict.

        Returns {} on missing/invalid YAML.
        """
        frontmatter = self._read_skill_frontmatter(skill_dir)
        if not frontmatter:
            return {}
        try:
            import yaml

            data = yaml.safe_load(frontmatter) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _extract_required_env_from_frontmatter_data(self, data: dict) -> list[dict]:
        """Extract required env var declarations from skill frontmatter.

        Supported schema:
            requires:
              env:
                - SOME_KEY
                - name: SOME_KEY
                  prompt: "..."
                  example: "..."
        """
        requires = data.get("requires")
        if not isinstance(requires, dict):
            return []

        env_list = requires.get("env")
        if not isinstance(env_list, list):
            return []

        out: list[dict] = []
        for item in env_list:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    out.append({"name": name})
                continue
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                rec = {"name": name}
                prompt = item.get("prompt")
                if isinstance(prompt, str) and prompt.strip():
                    rec["prompt"] = prompt.strip()
                example = item.get("example")
                if isinstance(example, str) and example.strip():
                    rec["example"] = example.strip()
                out.append(rec)

        # de-dup preserving order
        seen: set[str] = set()
        deduped: list[dict] = []
        for r in out:
            n = r.get("name")
            if not n or n in seen:
                continue
            seen.add(n)
            deduped.append(r)
        return deduped

    def validate_required_env(
        self, candidate: PendingSkillCandidate, *, runtime_user_id: str | None
    ) -> dict:
        """Validate required env vars declared in SKILL.md frontmatter.

        This does NOT print secret values; it only reports missing keys.
        """
        # Always refresh from secrets.yml so newly-provided values are visible
        # immediately without server restart.
        try:
            refresh_runtime_env_from_secrets(
                secrets_path=Path(getattr(self.config, "secrets_path", "secrets.yml")),
                user_id=runtime_user_id,
            )
        except Exception:
            # Never crash validation.
            pass

        data = self._read_skill_frontmatter_data(candidate.skill_dir)
        reqs = self._extract_required_env_from_frontmatter_data(data)
        if not reqs:
            return {"ok": True, "checked": 0, "reason": "no required env declared"}

        missing: list[dict] = []
        for r in reqs:
            name = r.get("name")
            if not name:
                continue
            val = os.environ.get(name)
            if val is None or str(val).strip() == "":
                missing.append(r)

        if missing:
            return {
                "ok": False,
                "checked": len(reqs),
                "missing": missing,
                "note": "missing required env vars; ask the user and persist them to secrets.yml",
            }

        return {"ok": True, "checked": len(reqs)}

    def validate_required_config_files(
        self, candidate: PendingSkillCandidate, *, runtime_user_id: str | None
    ) -> dict:
        """Validate that config files rendered from templates exist.

        This checks for *_example and *.example templates and verifies that
        the corresponding rendered files exist in the user's skills directory.
        """
        if runtime_user_id is None:
            return {"ok": True, "checked": 0, "reason": "no user_id provided"}

        # First, refresh to trigger template rendering
        try:
            refresh_runtime_env_from_secrets(
                secrets_path=Path(getattr(self.config, "secrets_path", "secrets.yml")),
                user_id=runtime_user_id,
            )
        except Exception:
            pass

        try:
            user_root = resolve_user_skills_dir(self.config, runtime_user_id, create=True)
        except Exception:
            return {"ok": True, "checked": 0, "reason": "could not resolve user skills dir"}

        # Find all *_example and *.example templates
        example_templates: list[Path] = []
        try:
            for tpl in candidate.skill_dir.rglob("*_example"):
                if tpl.is_file():
                    example_templates.append(tpl)
            for tpl in candidate.skill_dir.rglob("*.example"):
                if tpl.is_file():
                    example_templates.append(tpl)
        except Exception:
            pass

        if not example_templates:
            return {"ok": True, "checked": 0, "reason": "no config templates found"}

        skill_name = candidate.skill_name
        missing: list[dict] = []

        for tpl in example_templates:
            # Determine the destination name
            if tpl.name.endswith("_example"):
                dest_name = tpl.name[: -len("_example")]
            elif tpl.name.endswith(".example"):
                dest_name = tpl.name[: -len(".example")]
            else:
                continue

            # Apply naming convention (skill prefix if not already present)
            if dest_name.startswith(f"{skill_name}.") or dest_name == skill_name:
                out_name = dest_name
            else:
                out_name = f"{skill_name}__{dest_name}"

            dest_path = user_root / out_name

            if not dest_path.exists() or not dest_path.is_file():
                missing.append(
                    {
                        "template": tpl.name,
                        "expected_path": str(dest_path),
                        "note": f"Config file {out_name} not found. Ensure skill secrets are configured.",
                    }
                )

        if missing:
            return {
                "ok": False,
                "checked": len(example_templates),
                "missing": missing,
                "note": "required config files not rendered; ensure skill secrets are set and refresh_runtime_env_from_secrets is called",
            }

        return {"ok": True, "checked": len(example_templates)}

    def _extract_pip_packages_from_frontmatter(self, frontmatter: str) -> list[str]:
        """Best-effort extraction of pip packages from frontmatter.

        Supports the common skill schema:
        install:
          - kind: pip
            package: nano-pdf
        """
        if not frontmatter:
            return []

        pkgs: list[str] = []

        in_install = False
        cur: dict[str, str] = {}
        cur_indent: int | None = None

        def flush_current() -> None:
            nonlocal cur
            kind = (cur.get("kind") or "").strip().lower()
            package = (cur.get("package") or "").strip()
            if kind == "pip" and package:
                pkgs.append(package)
            cur = {}

        for raw in frontmatter.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue

            indent = len(raw) - len(raw.lstrip(" "))
            line = raw.strip()

            # Start/end of install section
            if indent == 0 and line.startswith("install:"):
                flush_current()
                in_install = True
                cur_indent = None
                continue
            if indent == 0 and not line.startswith("install:"):
                # leaving install section
                if in_install:
                    flush_current()
                in_install = False
                cur_indent = None

            if not in_install:
                continue

            # Expect list items under install:
            if line.startswith("-"):
                # new item
                flush_current()
                cur_indent = indent
                # support inline form: - kind: pip
                m = re.match(r"^-\s*(\w+)\s*:\s*(.+)$", line)
                if m:
                    cur[m.group(1).strip()] = m.group(2).strip().strip('"').strip("'")
                continue

            # key/value lines within an item
            if cur_indent is not None and indent > cur_indent:
                m = re.match(r"^(\w+)\s*:\s*(.+)$", line)
                if m:
                    cur[m.group(1).strip()] = m.group(2).strip().strip('"').strip("'")

        if in_install:
            flush_current()

        # de-dup preserving order
        seen: set[str] = set()
        out: list[str] = []
        for p in pkgs:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _extract_required_bins_from_frontmatter(self, frontmatter: str) -> list[str]:
        """Best-effort extraction of required binaries from frontmatter.

        Supports:
        requires:
          bins:
            - nano-pdf
        """
        if not frontmatter:
            return []

        bins: list[str] = []
        in_requires = False
        in_bins = False
        requires_indent: int | None = None
        bins_indent: int | None = None

        for raw in frontmatter.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            line = raw.strip()

            if indent == 0 and line.startswith("requires:"):
                in_requires = True
                in_bins = False
                requires_indent = indent
                bins_indent = None
                continue
            if indent == 0 and not line.startswith("requires:"):
                in_requires = False
                in_bins = False
                requires_indent = None
                bins_indent = None

            if not in_requires or requires_indent is None:
                continue

            if indent > requires_indent and line.startswith("bins:"):
                in_bins = True
                bins_indent = indent
                continue

            # leaving bins section
            if in_bins and bins_indent is not None and indent <= bins_indent:
                in_bins = False
                bins_indent = None

            if not in_bins:
                continue

            if line.startswith("-"):
                val = line[1:].strip().strip('"').strip("'")
                if val:
                    bins.append(val)

        # de-dup preserving order
        seen: set[str] = set()
        out: list[str] = []
        for b in bins:
            if b not in seen:
                seen.add(b)
                out.append(b)
        return out

    def _stdlib_module_names(self) -> set[str]:
        # Python 3.10+ provides sys.stdlib_module_names
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib:
            return set(stdlib)

        # Best-effort fallback if running on older Python
        # (Not expected for this project, but keep it safe.)
        return {
            "os",
            "sys",
            "json",
            "re",
            "pathlib",
            "typing",
            "datetime",
            "time",
            "subprocess",
            "shutil",
            "itertools",
            "functools",
            "collections",
            "math",
            "statistics",
            "random",
            "uuid",
            "logging",
            "asyncio",
        }

    def _is_local_import(self, module: str, skill_dir: Path) -> bool:
        """Return True if the import looks like a module provided by the skill itself."""
        parts = module.split(".")
        if not parts:
            return False
        root = parts[0]

        # A directory/package in the skill folder
        if (skill_dir / root).is_dir() and (skill_dir / root / "__init__.py").exists():
            return True

        # A plain .py module in the skill folder
        if (skill_dir / f"{root}.py").exists():
            return True

        return False

    def _extract_python_import_roots(self, skill_dir: Path) -> tuple[set[str], list[str]]:
        """Extract top-level import roots from *.py files.

        Returns (imports, warnings).
        """
        stdlib = self._stdlib_module_names()
        roots: set[str] = set()
        warnings: list[str] = []

        for py_file in self._python_files(skill_dir):
            try:
                src = py_file.read_text(encoding="utf-8")
            except Exception as e:
                warnings.append(f"Failed to read {py_file}: {e}")
                continue

            try:
                tree = ast.parse(src, filename=str(py_file))
            except SyntaxError:
                # Syntax check is handled elsewhere; don't duplicate here.
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = (alias.name or "").strip()
                        if not name:
                            continue
                        roots.add(name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        # relative import
                        continue
                    mod = (node.module or "").strip()
                    if not mod:
                        continue
                    roots.add(mod.split(".")[0])

        # Filter out stdlib and local modules
        filtered: set[str] = set()
        for r in roots:
            if r in stdlib:
                continue
            if self._is_local_import(r, skill_dir):
                continue
            filtered.add(r)

        return filtered, warnings

    def _map_import_to_package(self, import_root: str) -> str:
        """Map common import roots to their pip package names.

        This is necessarily heuristic; unknown modules return unchanged.
        """
        mapping = {
            "yaml": "PyYAML",
            "PIL": "Pillow",
            "bs4": "beautifulsoup4",
            "cv2": "opencv-python",
            "sklearn": "scikit-learn",
            "dateutil": "python-dateutil",
            "Crypto": "pycryptodome",
            "tqdm": "tqdm",
        }
        return mapping.get(import_root, import_root)

    def generate_requirements(self, candidate: PendingSkillCandidate) -> dict:
        """Generate or refresh requirements.txt by analyzing imports.

        Conservative rules:
        - If requirements.txt already exists, we do NOT overwrite by default.
          Instead, we append missing inferred packages under a marker.
        - If no third-party imports are detected, we do not create the file.
        """
        enabled = bool(getattr(self.config, "pending_skills_generate_requirements", True))
        if not enabled:
            return {"ok": True, "enabled": False}

        inferred_imports, warnings = self._extract_python_import_roots(candidate.skill_dir)
        inferred_pkgs = sorted({self._map_import_to_package(x) for x in inferred_imports})

        # Also honor declared installs in SKILL.md frontmatter
        frontmatter = self._read_skill_frontmatter(candidate.skill_dir)
        declared_pip = self._extract_pip_packages_from_frontmatter(frontmatter)

        inferred_set = set(inferred_pkgs)
        for p in declared_pip:
            inferred_set.add(p)
        inferred_pkgs = sorted(inferred_set)

        req_path = self._requirements_path(candidate.skill_dir)
        if not inferred_pkgs:
            return {
                "ok": True,
                "generated": False,
                "reason": "no third-party imports detected",
                "warnings": warnings,
            }

        marker_start = "# --- AUTO-GENERATED (imports) ---"
        marker_end = "# --- END AUTO-GENERATED (imports) ---"

        if not req_path.exists():
            content = "\n".join(
                [
                    marker_start,
                    *inferred_pkgs,
                    marker_end,
                    "",
                ]
            )
            req_path.write_text(content, encoding="utf-8")
            return {
                "ok": True,
                "generated": True,
                "created": True,
                "requirements": inferred_pkgs,
                "path": str(req_path),
                "warnings": warnings,
                "declared_from_skill_md": declared_pip,
            }

        # Merge with existing requirements.txt
        try:
            existing = req_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"Failed to read requirements.txt: {e}"}

        existing_lines = [ln.strip() for ln in existing.splitlines()]
        existing_pkgs = {
            ln.split("==")[0].split(">=")[0].split("<=")[0].strip()
            for ln in existing_lines
            if ln and not ln.startswith("#")
        }

        missing = [p for p in inferred_pkgs if p not in existing_pkgs]
        if not missing:
            return {
                "ok": True,
                "generated": False,
                "reason": "requirements already contain inferred packages",
                "requirements": inferred_pkgs,
                "warnings": warnings,
                "declared_from_skill_md": declared_pip,
            }

        # Append under marker block
        block = "\n".join([marker_start, *missing, marker_end, ""]) + "\n"
        req_path.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")
        return {
            "ok": True,
            "generated": True,
            "created": False,
            "added": missing,
            "requirements": inferred_pkgs,
            "path": str(req_path),
            "warnings": warnings,
            "declared_from_skill_md": declared_pip,
        }

    def validate_required_bins(self, candidate: PendingSkillCandidate) -> dict:
        """Validate required binaries declared in SKILL.md frontmatter.

        If a per-skill venv exists, we check for the binary under `.venv/bin/`.
        Otherwise we fall back to PATH lookup.
        """
        frontmatter = self._read_skill_frontmatter(candidate.skill_dir)
        bins = self._extract_required_bins_from_frontmatter(frontmatter)
        if not bins:
            return {"ok": True, "checked": 0, "reason": "no required bins declared"}

        venv_dir = self._venv_dir(candidate)
        venv_bin = venv_dir / "bin"

        missing: list[str] = []
        found: dict[str, str] = {}

        for b in bins:
            p = venv_bin / b
            if p.exists():
                found[b] = str(p)
                continue
            # fallback to PATH
            which = shutil.which(b)
            if which:
                found[b] = which
            else:
                missing.append(b)

        if missing:
            return {
                "ok": False,
                "checked": len(bins),
                "missing": missing,
                "found": found,
                "note": "missing required binaries; install packages or ensure venv contains bin stubs",
            }

        return {"ok": True, "checked": len(bins), "found": found}

    def ensure_venv(self, candidate: PendingSkillCandidate) -> dict:
        venv_dir = self._venv_dir(candidate)
        py = self._venv_python(venv_dir)

        if py.exists():
            return {"ok": True, "created": False, "venv_dir": str(venv_dir)}

        venv_dir.parent.mkdir(parents=True, exist_ok=True)

        # Prefer uv for speed/reproducibility
        try:
            proc = subprocess.run(
                ["uv", "venv", str(venv_dir)],
                cwd=str(candidate.skill_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "error": "uv venv failed",
                    "stdout": proc.stdout[-8000:],
                    "stderr": proc.stderr[-8000:],
                    "venv_dir": str(venv_dir),
                }
            return {"ok": True, "created": True, "venv_dir": str(venv_dir)}
        except FileNotFoundError:
            # Fallback for environments without uv installed
            try:
                import venv

                builder = venv.EnvBuilder(with_pip=True, clear=False)
                builder.create(str(venv_dir))
                return {
                    "ok": True,
                    "created": True,
                    "venv_dir": str(venv_dir),
                    "note": "uv not found; used python venv fallback",
                }
            except Exception as e:
                return {"ok": False, "error": str(e), "venv_dir": str(venv_dir)}
        except Exception as e:
            return {"ok": False, "error": str(e), "venv_dir": str(venv_dir)}

    def install_dependencies(
        self, candidate: PendingSkillCandidate, *, runtime_user_id: str | None
    ) -> dict:
        req = self._requirements_path(candidate.skill_dir)
        if not req.exists():
            return {"ok": True, "installed": False, "reason": "no requirements.txt"}

        ensure = self.ensure_venv(candidate)
        if not ensure.get("ok"):
            return {
                "ok": False,
                "error": f"Failed to create venv: {ensure.get('error', 'unknown')}",
            }

        venv_dir = Path(ensure["venv_dir"])
        py = self._venv_python(venv_dir)
        if not py.exists():
            return {
                "ok": False,
                "error": "Venv python not found after creation",
                "venv_dir": str(venv_dir),
            }

        timeout = int(getattr(self.config, "pending_skills_pip_timeout_seconds", 180))

        try:
            refresh_runtime_env_from_secrets(
                secrets_path=Path(getattr(self.config, "secrets_path", "secrets.yml")),
                user_id=runtime_user_id,
            )
        except Exception:
            pass

        env = os.environ.copy()
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        env.setdefault("PIP_NO_INPUT", "1")

        try:
            # Prefer uv pip for speed; point it at the venv interpreter.
            try:
                proc = subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(py),
                        "-r",
                        str(req),
                    ],
                    cwd=str(candidate.skill_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except FileNotFoundError:
                proc = subprocess.run(
                    [
                        str(py),
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(req),
                    ],
                    cwd=str(candidate.skill_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "error": "pip install failed",
                    "stdout": proc.stdout[-8000:],
                    "stderr": proc.stderr[-8000:],
                }
            return {"ok": True, "installed": True, "venv_dir": str(venv_dir)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"pip install timed out after {timeout}s"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------------------------
    # Script smoke test (execution)
    # ---------------------------

    def _runtime_python(self, candidate: PendingSkillCandidate) -> Path:
        """Pick the Python interpreter used to run scripts during onboarding.

        Prefer the per-skill venv interpreter when present.
        """
        venv_py = self._venv_python(self._venv_dir(candidate))
        if venv_py.exists():
            return venv_py
        return Path(sys.executable)

    def _scripts_to_smoke_test(self, skill_dir: Path) -> list[Path]:
        """Return a conservative list of scripts to run.

        We intentionally avoid executing every *.py file to reduce side effects.
        """
        scripts: list[Path] = []

        skill_py = skill_dir / "skill.py"
        if skill_py.exists() and skill_py.is_file():
            scripts.append(skill_py)

        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists() and scripts_dir.is_dir():
            for p in sorted(scripts_dir.rglob("*.py")):
                if p.is_file() and not p.name.startswith("__"):
                    scripts.append(p)

        max_files = int(getattr(self.config, "pending_skills_run_scripts_max_files", 5))
        return scripts[:max_files]

    def _parse_missing_module(self, stderr: str) -> str | None:
        """Extract missing module name from common Python error text."""
        # Example: ModuleNotFoundError: No module named 'requests'
        marker = "No module named"
        if marker not in stderr:
            return None

        # best-effort parse of the first quoted module name
        for quote in ("'", '"'):
            idx = stderr.find(marker)
            if idx == -1:
                continue
            tail = stderr[idx:]
            q1 = tail.find(quote)
            if q1 == -1:
                continue
            q2 = tail.find(quote, q1 + 1)
            if q2 == -1:
                continue
            mod = tail[q1 + 1 : q2].strip()
            return mod or None
        return None

    def run_scripts_smoke_test(
        self, candidate: PendingSkillCandidate, *, runtime_user_id: str | None
    ) -> dict:
        """Attempt to run a small set of skill scripts.

        This is meant to surface runtime missing dependencies (e.g., dynamic imports)
        that AST-based requirements inference may miss.
        """
        skill_dir = candidate.skill_dir
        scripts = self._scripts_to_smoke_test(skill_dir)
        if not scripts:
            return {"ok": True, "ran": 0, "skipped": True, "reason": "no scripts to run"}

        timeout = int(getattr(self.config, "pending_skills_run_scripts_timeout_seconds", 20))
        py = self._runtime_python(candidate)

        try:
            refresh_runtime_env_from_secrets(
                secrets_path=Path(getattr(self.config, "secrets_path", "secrets.yml")),
                user_id=runtime_user_id,
            )
        except Exception:
            pass

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["STRANDS_PENDING_SKILL_ONBOARDING"] = "1"

        failures: list[dict] = []
        missing_modules: set[str] = set()

        for script in scripts:
            proc = subprocess.run(
                [str(py), str(script)],
                cwd=str(skill_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or "")[-8000:]
                stdout = (proc.stdout or "")[-8000:]
                missing = self._parse_missing_module(stderr)
                if missing:
                    missing_modules.add(missing)
                failures.append(
                    {
                        "script": str(script.relative_to(skill_dir)),
                        "returncode": proc.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "missing_module": missing,
                    }
                )

        if failures:
            return {
                "ok": False,
                "ran": len(scripts),
                "failures": failures,
                "missing_modules": sorted(missing_modules),
                "timeout_seconds": timeout,
                "python": str(py),
            }

        return {
            "ok": True,
            "ran": len(scripts),
            "timeout_seconds": timeout,
            "python": str(py),
        }

    # ---------------------------
    # Preflight + onboarding
    # ---------------------------

    def preflight(
        self,
        candidate: PendingSkillCandidate,
        *,
        install_deps: bool,
        run_scripts: bool = False,
        runtime_user_id: str | None = None,
    ) -> dict:
        """Preflight a pending skill.

        Returns a dict summary and writes FAILED.json on failures.
        """
        report: dict = {
            "skill": candidate.skill_name,
            "scope": candidate.scope,
            "user_id": candidate.user_id,
            "timestamp": _utc_now_iso(),
            "ok": True,
            "steps": {},
        }

        try:
            report["steps"]["normalize_skill_md"] = self.normalize_skill_md(candidate)

            # Validate required env vars early so the agent can prompt the user.
            # This is checked even in dry-run mode.
            env_rep = self.validate_required_env(candidate, runtime_user_id=runtime_user_id)
            report["steps"]["validate_required_env"] = env_rep
            if not env_rep.get("ok"):
                report["ok"] = False
                self.write_failed(
                    candidate,
                    stage="validate_required_env",
                    error="missing required env vars",
                    details=env_rep,
                )
                self._write_preflight_reports(candidate, report)
                return report

            # Validate required config files rendered from templates.
            # This happens after env validation because refresh_runtime_env_from_secrets
            # is called there, which triggers template rendering.
            cfg_rep = self.validate_required_config_files(
                candidate, runtime_user_id=runtime_user_id
            )
            report["steps"]["validate_required_config_files"] = cfg_rep
            if not cfg_rep.get("ok"):
                report["ok"] = False
                self.write_failed(
                    candidate,
                    stage="validate_required_config_files",
                    error="required config files not rendered",
                    details=cfg_rep,
                )
                self._write_preflight_reports(candidate, report)
                return report

            report["steps"]["generate_requirements"] = self.generate_requirements(candidate)
            if not report["steps"]["generate_requirements"].get("ok", True):
                report["ok"] = False
                self.write_failed(
                    candidate,
                    stage="generate_requirements",
                    error=report["steps"]["generate_requirements"].get(
                        "error", "requirements generation failed"
                    ),
                )
                self._write_preflight_reports(candidate, report)
                return report

            # Keep SKILL.md frontmatter consistent with requirements.txt.
            # This is best-effort and should not block onboarding.
            report["steps"]["sync_requires_pip"] = self.sync_requires_pip_from_requirements(
                candidate
            )

            syntax = self.validate_python_syntax(candidate)
            report["steps"]["validate_python_syntax"] = syntax
            if not syntax.get("ok"):
                report["ok"] = False
                self.write_failed(
                    candidate,
                    stage="validate_python_syntax",
                    error=syntax.get("error", "syntax error"),
                )
                self._write_preflight_reports(candidate, report)
                return report

            if install_deps:
                deps = self.install_dependencies(candidate, runtime_user_id=runtime_user_id)
                report["steps"]["install_dependencies"] = deps
                if not deps.get("ok"):
                    report["ok"] = False
                    self.write_failed(
                        candidate,
                        stage="install_dependencies",
                        error=deps.get("error", "dependency install failed"),
                        details={
                            "stdout": deps.get("stdout"),
                            "stderr": deps.get("stderr"),
                        },
                    )
                    self._write_preflight_reports(candidate, report)
                    return report

                bins_rep = self.validate_required_bins(candidate)
                report["steps"]["validate_required_bins"] = bins_rep
                if not bins_rep.get("ok"):
                    report["ok"] = False
                    self.write_failed(
                        candidate,
                        stage="validate_required_bins",
                        error=f"missing required binaries: {', '.join(bins_rep.get('missing') or [])}",
                        details=bins_rep,
                    )
                    self._write_preflight_reports(candidate, report)
                    return report

            if run_scripts:
                run_rep = self.run_scripts_smoke_test(candidate, runtime_user_id=runtime_user_id)
                report["steps"]["run_scripts_smoke_test"] = run_rep
                if not run_rep.get("ok"):
                    report["ok"] = False
                    details = {
                        "missing_modules": run_rep.get("missing_modules"),
                        "failures": run_rep.get("failures"),
                    }
                    msg = "script smoke test failed"
                    if run_rep.get("missing_modules"):
                        msg = (
                            f"missing module(s): {', '.join(run_rep.get('missing_modules') or [])}"
                        )
                    self.write_failed(
                        candidate,
                        stage="run_scripts_smoke_test",
                        error=msg,
                        details=details,
                    )
                    self._write_preflight_reports(candidate, report)
                    return report

            # If we got here, clear any previous failure marker
            self.clear_failed(candidate)
            self._write_preflight_reports(candidate, report)
            return report

        except Exception as e:
            report["ok"] = False
            report["error"] = str(e)
            self.write_failed(candidate, stage="preflight_exception", error=str(e))
            self._write_preflight_reports(candidate, report)
            return report

    def _write_preflight_reports(self, candidate: PendingSkillCandidate, report: dict) -> None:
        # JSON
        (candidate.skill_dir / "onboarding_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        # Markdown summary
        lines = [
            f"# Pending Skill Preflight Report: {candidate.skill_name}",
            "",
            f"- Scope: {candidate.scope}",
            f"- User: {candidate.user_id or '(n/a)'}",
            f"- Timestamp: {report.get('timestamp')}",
            f"- Status: {'OK' if report.get('ok') else 'FAILED'}",
            "",
        ]
        steps = report.get("steps", {})
        for step_name, step in steps.items():
            ok = step.get("ok", True)
            lines.append(f"## {step_name} ({'OK' if ok else 'FAILED'})")
            lines.append("")
            # include a compact JSON snippet
            try:
                snippet = json.dumps(step, indent=2, sort_keys=True)
            except Exception:
                snippet = str(step)
            lines.append("```json")
            lines.append(snippet)
            lines.append("```")
            lines.append("")

        (candidate.skill_dir / "ONBOARDING_REPORT.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    def preflight_all(self) -> dict:
        """Preflight all pending skills found (shared + all users)."""
        install_deps = bool(getattr(self.config, "pending_skills_preflight_install_deps", True))
        max_skills = int(getattr(self.config, "pending_skills_preflight_max_skills", 200))

        processed = []
        failures = 0

        # Shared
        for c in self.list_pending(user_id=None, include_shared=True):
            if len(processed) >= max_skills:
                break
            processed.append(self.preflight(c, install_deps=install_deps, run_scripts=False))
            if not processed[-1].get("ok"):
                failures += 1

        # Users
        for uid in self._iter_user_ids():
            for c in self.list_pending(user_id=uid, include_shared=False):
                if len(processed) >= max_skills:
                    break
                processed.append(self.preflight(c, install_deps=install_deps, run_scripts=False))
                if not processed[-1].get("ok"):
                    failures += 1

        return {
            "ok": failures == 0,
            "processed": len(processed),
            "failures": failures,
            "reports": processed,
        }

    def onboard_pending(
        self,
        *,
        user_id: str,
        scope: Literal["user", "shared", "all"] = "all",
        dry_run: bool = False,
        skill_names: list[str] | None = None,
        install_deps: bool = True,
        run_scripts: bool = True,
    ) -> dict:
        """Onboard pending skills into active skill directories.

        - scope=user: only {user_id}/pending
        - scope=shared: only shared/pending
        - scope=all: both
        """
        include_shared = scope in ("shared", "all")
        include_user = scope in ("user", "all")

        candidates: list[PendingSkillCandidate] = []
        if include_shared:
            candidates.extend(self.list_pending(user_id=None, include_shared=True))
        if include_user:
            candidates.extend(self.list_pending(user_id=user_id, include_shared=False))

        if skill_names:
            wanted = {s.strip() for s in skill_names if (s or "").strip()}
            if wanted:
                candidates = [c for c in candidates if c.skill_name in wanted]

        results = []
        onboarded = 0
        failed = 0
        skipped = 0

        for c in candidates:
            # Determine destination early; if it already exists, skip without
            # writing FAILED.json (already-installed is not a failure).
            if c.scope == "shared":
                dest = self.shared_skills_dir / c.skill_name
            else:
                target_user_id = c.user_id or user_id
                dest = (
                    resolve_user_skills_dir(self.config, target_user_id, create=True) / c.skill_name
                )

            if dest.exists():
                # If the pending skill differs from the installed one, re-onboard
                # and replace (with backup). Otherwise, skip as up-to-date.
                pending_fp = self._dir_fingerprint(c.skill_dir)
                active_fp = self._dir_fingerprint(dest)

                if pending_fp == active_fp:
                    # Best-effort: don't leave stale failure markers behind
                    self.clear_failed(c)
                    results.append(
                        {
                            "candidate": c.skill_name,
                            "status": "skipped",
                            "reason": "already up-to-date",
                            "path": str(dest),
                        }
                    )
                    skipped += 1
                    continue

                # Changed skill -> run onboarding again
                pf = self.preflight(
                    c,
                    install_deps=(install_deps and (not dry_run)),
                    run_scripts=(run_scripts and (not dry_run)),
                    runtime_user_id=user_id,
                )
                if not pf.get("ok"):
                    results.append({"candidate": c.skill_name, "status": "failed", "report": pf})
                    failed += 1
                    continue

                if dry_run:
                    results.append(
                        {
                            "candidate": c.skill_name,
                            "status": "dry-run",
                            "would_update": str(dest),
                        }
                    )
                    continue

                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    backup = self._backup_existing_skill_dir(dest)
                    shutil.move(str(c.skill_dir), str(dest))
                    onboarded += 1
                    results.append(
                        {
                            "candidate": c.skill_name,
                            "status": "onboarded",
                            "path": str(dest),
                            "updated": True,
                            "backup": str(backup),
                        }
                    )
                except Exception as e:
                    failed += 1
                    if c.skill_dir.exists():
                        self.write_failed(c, stage="promotion", error=str(e))
                    results.append({"candidate": c.skill_name, "status": "failed", "error": str(e)})
                continue

            # Preflight.
            # - dry_run should avoid side effects like dependency installation and script execution.
            # - real onboarding performs deps install + script smoke test.
            pf = self.preflight(
                c,
                install_deps=(install_deps and (not dry_run)),
                run_scripts=(run_scripts and (not dry_run)),
                runtime_user_id=user_id,
            )
            if not pf.get("ok"):
                results.append({"candidate": c.skill_name, "status": "failed", "report": pf})
                failed += 1
                continue

            if dry_run:
                results.append(
                    {"candidate": c.skill_name, "status": "dry-run", "would_move_to": str(dest)}
                )
                continue

            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(c.skill_dir), str(dest))
                onboarded += 1
                results.append(
                    {"candidate": c.skill_name, "status": "onboarded", "path": str(dest)}
                )
            except Exception as e:
                failed += 1
                # write FAILED.json in the *source* if it still exists, else create failure marker in dest parent
                if c.skill_dir.exists():
                    self.write_failed(c, stage="promotion", error=str(e))
                else:
                    # best-effort: recreate marker in a fallback location
                    fallback = (
                        self._pending_dir_shared()
                        if c.scope == "shared"
                        else self._pending_dir_user(user_id)
                    ) / c.skill_name
                    fallback.mkdir(parents=True, exist_ok=True)
                    self._failed_path(fallback).write_text(
                        json.dumps(
                            {
                                "status": "failed",
                                "stage": "promotion",
                                "error": str(e),
                                "timestamp": _utc_now_iso(),
                                "scope": c.scope,
                                "user_id": c.user_id,
                                "skill_name": c.skill_name,
                            },
                            indent=2,
                            sort_keys=True,
                        ),
                        encoding="utf-8",
                    )
                results.append({"candidate": c.skill_name, "status": "failed", "error": str(e)})

        return {
            "ok": failed == 0,
            "onboarded": onboarded,
            "failed": failed,
            "skipped": skipped,
            "total": len(candidates),
            "results": results,
        }

    # ---------------------------
    # Repair installed skills
    # ---------------------------

    def repair_installed_skill(
        self,
        *,
        user_id: str,
        skill_name: str,
        scope: Literal["user", "shared"] = "user",
        run_scripts: bool = True,
    ) -> dict:
        """Repair an already-installed skill.

        This is useful when a skill was promoted but later we realize it requires
        binaries/packages declared in SKILL.md frontmatter (install/requires).

        Actions:
        - generate/merge requirements.txt (imports + SKILL.md install)
        - install into per-skill .venv
        - validate required bins
        - optionally run script smoke test
        """
        if scope == "shared":
            skill_dir = self.shared_skills_dir / skill_name
            c = PendingSkillCandidate(
                scope="shared",
                user_id=None,
                skill_name=skill_name,
                skill_dir=skill_dir,
            )
        else:
            skill_dir = resolve_user_skills_dir(self.config, user_id, create=False) / skill_name
            c = PendingSkillCandidate(
                scope="user",
                user_id=user_id,
                skill_name=skill_name,
                skill_dir=skill_dir,
            )

        if not skill_dir.exists() or not skill_dir.is_dir():
            return {"ok": False, "error": f"Skill not found: {skill_dir}"}

        rep = self.preflight(
            c,
            install_deps=True,
            run_scripts=run_scripts,
            runtime_user_id=user_id if scope != "shared" else user_id,
        )
        rep["repaired"] = bool(rep.get("ok"))
        rep["skill_dir"] = str(skill_dir)
        return rep

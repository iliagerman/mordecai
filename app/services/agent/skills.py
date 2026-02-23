from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import _find_repo_root, refresh_runtime_env_from_secrets, resolve_user_skills_dir
from app.models.agent import MissingSkillRequirements, RequirementSpec, SkillInfo, WhenClause
from app.services.agent.frontmatter import (
    extract_required_bins,
    extract_required_config,
    extract_required_env,
    parse_skill_frontmatter,
)

logger = logging.getLogger(__name__)


# Directories inside skills/ that are not actual skills
RESERVED_SKILL_DIR_NAMES: set[str] = {
    "pending",
    "failed",
    ".venvs",
    ".venv",
    "__pycache__",
}


@dataclass(slots=True)
class SharedSkillsSynchronizer:
    """Mirror shared skills into a per-user skills directory."""

    shared_dir: Path

    def sync(self, *, user_dir: Path) -> None:
        if not self.shared_dir.exists():
            logger.debug("Shared skills dir does not exist: %s", self.shared_dir)
            return

        manifest_path = user_dir / ".shared_skills_sync.json"

        def load_manifest() -> dict[str, Any]:
            try:
                if manifest_path.exists():
                    return json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            return {"synced": {}}

        def save_manifest(data: dict[str, Any]) -> None:
            try:
                tmp = manifest_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                tmp.replace(manifest_path)
            except Exception as e:
                logger.debug("Failed to write shared skills manifest: %s", e)

        def fingerprint_path(p: Path) -> dict[str, Any]:
            """Compute a best-effort fingerprint for p.

            For directories, we hash only metadata (mtime_ns + size) of contained
            files, not file contents.
            """

            try:
                if p.is_file():
                    st = p.stat()
                    return {
                        "kind": "file",
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                    }
                if p.is_dir():
                    max_mtime_ns = 0
                    total_size = 0
                    file_count = 0
                    for root, _dirs, files in os.walk(p):
                        for fn in files:
                            fp = Path(root) / fn
                            try:
                                st = fp.stat()
                            except OSError:
                                continue
                            file_count += 1
                            total_size += int(st.st_size)
                            max_mtime_ns = max(max_mtime_ns, int(st.st_mtime_ns))
                    return {
                        "kind": "dir",
                        "max_mtime_ns": max_mtime_ns,
                        "total_size": total_size,
                        "file_count": file_count,
                    }
            except Exception:
                pass

            return {"kind": "unknown"}

        manifest = load_manifest()
        synced: dict[str, Any] = dict(manifest.get("synced", {}))

        # Determine which shared entries should be mirrored.
        shared_items: dict[str, Path] = {}
        for item in self.shared_dir.iterdir():
            # Skip private/under entries and non-skill reserved dirs.
            if item.name.startswith("__"):
                continue
            if item.is_file() and item.name == "__init__.py":
                continue
            if item.is_dir() and item.name in RESERVED_SKILL_DIR_NAMES:
                continue

            shared_items[item.name] = item

        removed: list[str] = []
        updated: list[str] = []

        # Remove entries that were previously synced but no longer exist in shared.
        for name in list(synced.keys()):
            if name in shared_items:
                continue
            dest = user_dir / name
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                    removed.append(name)
                except Exception as e:
                    logger.warning("Failed to remove stale shared skill %s: %s", name, e)
            synced.pop(name, None)

        # Mirror/overwrite current shared skills.
        for name, src in shared_items.items():
            dest = user_dir / name
            fp = fingerprint_path(src)
            prev_fp = (synced.get(name) or {}).get("fingerprint")

            # Skip if unchanged and destination exists.
            if prev_fp == fp and dest.exists():
                continue

            # Overwrite destination.
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                except Exception as e:
                    logger.warning(
                        "Failed to remove existing dest for shared skill %s: %s",
                        name,
                        e,
                    )
                    continue

            try:
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                synced[name] = {
                    "fingerprint": fp,
                    "synced_at": datetime.utcnow().isoformat(),
                    "source": str(src),
                }
                updated.append(name)
            except Exception as e:
                logger.warning("Failed to sync shared skill %s: %s", name, e)

        if removed or updated:
            manifest["synced"] = synced
            save_manifest(manifest)

        if removed:
            logger.info("Removed stale shared skills for user: %s", ", ".join(removed))
        if updated:
            logger.info("Synced/updated shared skills for user: %s", ", ".join(updated))


@dataclass(slots=True)
class SkillRepository:
    """Discover skills and evaluate setup requirements."""

    config: Any
    _shared_sync: SharedSkillsSynchronizer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._shared_sync = SharedSkillsSynchronizer(shared_dir=Path(self.config.shared_skills_dir))

    def get_user_skills_dir(self, user_id: str, *, create: bool = True) -> Path:
        user_dir = resolve_user_skills_dir(self.config, user_id, create=create)
        self._shared_sync.sync(user_dir=user_dir)
        return user_dir

    def sync_shared_skills_for_user(self, user_id: str) -> Path:
        return self.get_user_skills_dir(user_id, create=True)

    def sync_shared_skills(self, user_dir: Path) -> None:
        self._shared_sync.sync(user_dir=user_dir)

    def discover(self, user_id: str) -> list[SkillInfo]:
        skills_by_name: dict[str, SkillInfo] = {}

        user_skills_dir = self.get_user_skills_dir(user_id, create=True)
        if not user_skills_dir.exists():
            return []

        for item in user_skills_dir.iterdir():
            if not item.is_dir() or item.name.startswith("__"):
                continue
            if item.name in RESERVED_SKILL_DIR_NAMES:
                continue

            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                frontmatter = parse_skill_frontmatter(content)
                skill_name = str(frontmatter.get("name") or item.name)
                skills_by_name[skill_name] = SkillInfo(
                    name=skill_name,
                    description=str(frontmatter.get("description") or ""),
                    path=str(item.resolve()),
                )
            except Exception as e:
                logger.warning("Failed to read skill %s: %s", item, e)

        return list(skills_by_name.values())

    def load_merged_skill_secrets(self, user_id: str) -> dict[str, Any]:
        """Load skill secrets for a user from the database cache.

        The DB is the single source of truth for per-user skill secrets.
        Returns ``{"skills": {...}}`` dict compatible with existing callers.
        """

        try:
            from app.tools.skill_secrets import get_cached_skill_secrets

            db_secrets = get_cached_skill_secrets()
            if db_secrets:
                return {"skills": db_secrets}
        except Exception:
            pass

        return {}

    def get_missing_skill_requirements(self, user_id: str) -> dict[str, MissingSkillRequirements]:
        """Return missing required env/config/bins values and config files for installed skills."""

        try:
            refresh_runtime_env_from_secrets(
                secrets_path=Path(getattr(self.config, "secrets_path", "secrets.yml")),
                user_id=user_id,
                config=self.config,
            )
        except Exception:
            pass

        merged_skill_secrets = self.load_merged_skill_secrets(user_id)
        skills_block = merged_skill_secrets.get("skills")
        if not isinstance(skills_block, dict):
            skills_block = {}

        def is_active_req(req: RequirementSpec, *, skill_cfg: dict[str, Any]) -> bool:
            """Return True if a requirement is active given its optional `when` clause."""

            when = req.when
            if not isinstance(when, WhenClause) or not when:
                return True

            cfg_key = when.config
            if isinstance(cfg_key, str) and cfg_key.strip():
                actual = skill_cfg.get(cfg_key)
                if when.equals is not None:
                    return str(actual) == str(when.equals)
                return bool(actual)

            env_key = when.env
            if isinstance(env_key, str) and env_key.strip():
                actual = os.environ.get(env_key)
                if when.equals is not None:
                    return str(actual) == str(when.equals)
                return bool(actual)

            return True

        def check_bin_exists(bin_name: str, skill_dir: Path) -> bool:
            """Check if a binary exists in PATH or skill venv."""
            # Check skill venv first
            venv_bin = skill_dir / ".venv" / "bin" / bin_name
            if venv_bin.exists():
                return True
            # Fallback to PATH
            return shutil.which(bin_name) is not None

        def get_rendered_config_files(skill_name: str, skill_dir: Path, output_dir: Path) -> list[Path]:
            """Get list of config files that should be rendered from *_example templates.

            Returns the destination paths where rendered config files should exist
            (in workspace/<user>/tmp/).
            """
            config_files: list[Path] = []

            # Look for *_example and *.example templates in the skill directory
            try:
                for tpl in skill_dir.rglob("*_example"):
                    if tpl.is_file():
                        # Determine the destination name
                        if tpl.name.endswith("_example"):
                            dest_name = tpl.name[: -len("_example")]
                        else:
                            continue
                        # Apply naming convention (skill prefix if not already present)
                        if dest_name.startswith(f"{skill_name}.") or dest_name == skill_name:
                            out_name = dest_name
                        else:
                            out_name = f"{skill_name}__{dest_name}"
                        config_files.append(output_dir / out_name)

                for tpl in skill_dir.rglob("*.example"):
                    if tpl.is_file():
                        if tpl.name.endswith(".example"):
                            dest_name = tpl.name[: -len(".example")]
                        else:
                            continue
                        if dest_name.startswith(f"{skill_name}.") or dest_name == skill_name:
                            out_name = dest_name
                        else:
                            out_name = f"{skill_name}__{dest_name}"
                        config_files.append(output_dir / out_name)
            except Exception:
                pass

            return config_files

        missing_by_skill: dict[str, MissingSkillRequirements] = {}

        for info in self.discover(user_id):
            skill_name = (info.name or "").strip()
            skill_path = (info.path or "").strip()
            if not skill_name or not skill_path:
                continue

            skill_md = Path(skill_path) / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue

            frontmatter = parse_skill_frontmatter(content)
            env_reqs = extract_required_env(frontmatter)
            cfg_reqs = extract_required_config(frontmatter)
            bins_reqs = extract_required_bins(frontmatter)
            if not env_reqs and not cfg_reqs and not bins_reqs:
                continue

            # Per-skill config block from merged secrets.
            skill_cfg = skills_block.get(skill_name) or skills_block.get(skill_name.lower())
            if not isinstance(skill_cfg, dict):
                skill_cfg = {}

            missing_env: list[RequirementSpec] = []
            for r in env_reqs:
                if not is_active_req(r, skill_cfg=skill_cfg):
                    continue
                n = (r.name or "").strip()
                if not n:
                    continue
                val = os.environ.get(n)
                if val is None or str(val).strip() == "":
                    missing_env.append(r)

            missing_cfg: list[RequirementSpec] = []
            if cfg_reqs:
                # Heuristic: if a per-user generated config file exists in
                # workspace/<user>/tmp/ (e.g. himalaya.toml), do not keep prompting.
                try:
                    work_base_cfg = Path(getattr(self.config, "working_folder_base_dir", "./workspace")).expanduser()
                    if not work_base_cfg.is_absolute():
                        work_base_cfg = _find_repo_root(start=Path(__file__)) / work_base_cfg
                    ws_tmp = (work_base_cfg / str(user_id) / "tmp").resolve()
                    generated_cfg = ws_tmp / f"{skill_name.lower()}.toml"
                    if generated_cfg.exists() and generated_cfg.is_file():
                        cfg_reqs = []
                except Exception:
                    pass

                for r in cfg_reqs:
                    if not is_active_req(r, skill_cfg=skill_cfg):
                        continue
                    n = (r.name or "").strip()
                    if not n:
                        continue
                    v = skill_cfg.get(n)
                    if v is None or str(v).strip() == "":
                        missing_cfg.append(r)

            missing_bins: list[RequirementSpec] = []
            if bins_reqs:
                skill_dir_obj = Path(skill_path)
                for r in bins_reqs:
                    if not is_active_req(r, skill_cfg=skill_cfg):
                        continue
                    n = (r.name or "").strip()
                    if not n:
                        continue
                    if not check_bin_exists(n, skill_dir_obj):
                        missing_bins.append(r)

            missing_config_files: list[RequirementSpec] = []
            # Check if rendered config files exist in workspace/<user>/tmp/
            try:
                work_base = Path(getattr(self.config, "working_folder_base_dir", "./workspace")).expanduser()
                if not work_base.is_absolute():
                    work_base = _find_repo_root(start=Path(__file__)) / work_base
                workspace_tmp = (work_base / str(user_id) / "tmp").resolve()
                skill_dir_obj = Path(skill_path)
                expected_configs = get_rendered_config_files(skill_name, skill_dir_obj, workspace_tmp)
                for config_path in expected_configs:
                    if not config_path.exists() or not config_path.is_file():
                        missing_config_files.append(
                            RequirementSpec(
                                name=config_path.name,
                                prompt=f"Config file {config_path.name} not found. Run skill onboarding to generate it.",
                                example=str(config_path),
                            )
                        )
            except Exception:
                pass

            if missing_env or missing_cfg or missing_bins or missing_config_files:
                missing_by_skill[skill_name] = MissingSkillRequirements(
                    env=missing_env,
                    config=missing_cfg,
                    bins=missing_bins,
                    config_files=missing_config_files,
                )

        return missing_by_skill

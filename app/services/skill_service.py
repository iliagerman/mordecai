"""Skill management service.

This service handles skill (plugin) installation, uninstallation, and listing
on a per-user basis. Each user (identified by telegram_id) has their own
skills directory.

Supports:
- GitHub repository URLs (directories with SKILL.md and nested folders)
- Direct .py file downloads
- .zip archive downloads

Requirements:
- 2.1: Download and install skills from user-provided URLs
- 2.2: Uninstall skills by name
- 2.3: Auto-load installed skills on startup (handled by Strands)
- 2.4: Make skill tools available after installation
- 2.5: Remove skill tools after uninstallation
- 2.6: Notify user with error message if download fails
"""

import json
import re
import shutil
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError

from app.config import (
    AgentConfig,
    resolve_user_pending_skills_dir,
    resolve_user_skills_dir,
)
from app.models.domain import SkillMetadata


class SkillInstallError(Exception):
    """Raised when skill installation fails."""

    pass


class SkillNotFoundError(Exception):
    """Raised when a skill is not found."""

    pass


class SkillService:
    """Per-user skill management service.

    Each user has their own skills directory at:
        {skills_base_dir}/{user_id}/

    Strands auto-loads tools from the user's directory when creating
    their agent instance.
    """

    # Pattern to match GitHub tree URLs
    GITHUB_TREE_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$")

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the skill service.

        Args:
            config: Application configuration with skills_base_dir.
        """
        self.config = config
        self.skills_base_dir = Path(config.skills_base_dir)
        self.skills_base_dir.mkdir(parents=True, exist_ok=True)
        self.shared_skills_dir = Path(config.shared_skills_dir)
        self.shared_skills_dir.mkdir(parents=True, exist_ok=True)

    def migrate_user_skills_dir(self, *, legacy_user_id: str, user_id: str) -> bool:
        """One-way migration: move the per-user skills directory.

        We no longer use numeric Telegram IDs as the primary user identifier.
        This helper migrates any existing legacy folder at:

            {skills_base_dir}/{legacy_user_id}/

        into the new location:

            {skills_base_dir}/{user_id}/

        Args:
            legacy_user_id: Previous user identifier (often numeric telegram user ID).
            user_id: New primary user identifier (Telegram username).

        Returns:
            True if a migration occurred, False otherwise.

        Raises:
            SkillInstallError: If both legacy and new directories exist.
        """
        legacy_user_id = (legacy_user_id or "").strip()
        user_id = (user_id or "").strip()

        if not legacy_user_id or not user_id or legacy_user_id == user_id:
            return False

        legacy_dir = resolve_user_skills_dir(self.config, legacy_user_id, create=False)
        new_dir = resolve_user_skills_dir(self.config, user_id, create=False)

        if not legacy_dir.exists():
            return False

        if new_dir.exists():
            raise SkillInstallError(
                f"Cannot migrate skills dir: both legacy '{legacy_user_id}' and new '{user_id}' exist"
            )

        # Use shutil.move for robustness across filesystems.
        shutil.move(str(legacy_dir), str(new_dir))
        return True

    def _get_user_skills_dir(self, user_id: str) -> Path:
        """Get the skills directory for a specific user.

        Args:
            user_id: Primary user identifier (Telegram username).

        Returns:
            Path to user's skills directory.
        """
        return resolve_user_skills_dir(self.config, user_id, create=True)

    def _get_user_pending_skills_dir(self, user_id: str) -> Path:
        """Get the pending skills directory for a specific user."""
        return resolve_user_pending_skills_dir(self.config, user_id, create=True)

    def _get_shared_skills_dir(self) -> Path:
        """Get the shared skills directory.

        Returns:
            Path to shared skills directory.
        """
        return self.shared_skills_dir

    def _get_shared_pending_skills_dir(self) -> Path:
        """Get the pending skills directory for shared skills."""
        d = self._get_shared_skills_dir() / "pending"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_extract_zip(self, zip_ref: zipfile.ZipFile, dest_dir: Path) -> int:
        """Safely extract a zip into dest_dir (prevents path traversal).

        Returns number of extracted files.
        """
        extracted = 0
        for member in zip_ref.namelist():
            # Skip directories
            if member.endswith("/"):
                continue

            # Normalize and prevent traversal/absolute paths
            member_path = Path(member)
            if member_path.is_absolute() or ".." in member_path.parts:
                continue

            out_path = dest_dir / member_path
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with zip_ref.open(member) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1

        return extracted

    def _download_skill_into_directory(
        self, url: str, dest_skill_dir: Path, user_id: str
    ) -> SkillMetadata:
        """Download a skill from a URL into an explicit directory.

        This is used for both active installs and pending downloads.
        """
        # GitHub directory URLs
        if self._parse_github_url(url):
            parsed = self._parse_github_url(url)
            if not parsed:
                raise SkillInstallError("Invalid GitHub URL format")

            owner, repo, branch, path = parsed
            skill_name = path.rstrip("/").split("/")[-1]

            if not skill_name or skill_name.startswith("__"):
                raise SkillInstallError(f"Invalid skill name: {skill_name}")
            if len(skill_name) > 64:
                raise SkillInstallError(f"Skill name too long (max 64 chars): {skill_name}")

            if dest_skill_dir.exists():
                shutil.rmtree(dest_skill_dir)
            dest_skill_dir.mkdir(parents=True)

            try:
                files_downloaded = self._download_github_directory(
                    owner, repo, branch, path, dest_skill_dir
                )
                if files_downloaded == 0:
                    shutil.rmtree(dest_skill_dir)
                    raise SkillInstallError("No files found in skill directory")

                return SkillMetadata(
                    name=skill_name,
                    source_url=url,
                    installed_at=datetime.utcnow(),
                )
            except SkillInstallError:
                if dest_skill_dir.exists():
                    shutil.rmtree(dest_skill_dir)
                raise

        # Non-GitHub URLs (.zip, .py, or other)
        filename = url.split("/")[-1]
        if filename.endswith(".zip"):
            skill_name = filename.replace(".zip", "")
            is_zip = True
        elif filename.endswith(".py"):
            skill_name = filename.replace(".py", "")
            is_zip = False
        else:
            skill_name = filename
            is_zip = True

        if not skill_name or skill_name.startswith("__"):
            raise SkillInstallError(f"Invalid skill name: {skill_name}")

        # Always materialize as a directory so pending onboarding can see it.
        if dest_skill_dir.exists():
            shutil.rmtree(dest_skill_dir)
        dest_skill_dir.mkdir(parents=True, exist_ok=True)

        download_path = dest_skill_dir / (f"{skill_name}.zip" if is_zip else f"{skill_name}.py")

        try:
            urllib.request.urlretrieve(url, download_path)
        except Exception as e:
            raise SkillInstallError(f"Failed to download skill: {str(e)}") from e

        if is_zip:
            try:
                with zipfile.ZipFile(download_path, "r") as zip_ref:
                    extracted_files = self._safe_extract_zip(zip_ref, dest_skill_dir)
                    if extracted_files == 0:
                        raise SkillInstallError("No files found in skill archive")
            except zipfile.BadZipFile as e:
                download_path.unlink(missing_ok=True)
                raise SkillInstallError("Invalid zip file") from e
            except Exception as e:
                download_path.unlink(missing_ok=True)
                if isinstance(e, SkillInstallError):
                    raise
                raise SkillInstallError(f"Failed to extract skill: {str(e)}") from e
            finally:
                download_path.unlink(missing_ok=True)
        else:
            # Rename downloaded .py into a conventional skill entrypoint.
            try:
                target = dest_skill_dir / "skill.py"
                if download_path != target:
                    download_path.rename(target)
            except Exception:
                # If rename fails, leave as-is; PendingSkillService can still normalize.
                pass

        return SkillMetadata(
            name=skill_name,
            source_url=url,
            installed_at=datetime.utcnow(),
        )

    def _parse_github_url(self, url: str) -> tuple[str, str, str, str] | None:
        """Parse a GitHub tree URL into components.

        Args:
            url: GitHub URL to parse.

        Returns:
            Tuple of (owner, repo, branch, path) or None.
        """
        match = self.GITHUB_TREE_PATTERN.match(url)
        if match:
            # Pylance struggles to narrow re.Match.groups() to a fixed-size tuple.
            # Return an explicit 4-tuple for type safety.
            return (
                match.group(1),
                match.group(2),
                match.group(3),
                match.group(4),
            )
        return None

    def _fetch_github_contents(self, owner: str, repo: str, branch: str, path: str) -> list[dict]:
        """Fetch directory contents from GitHub API.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Branch name.
            path: Path within the repository.

        Returns:
            List of content items from GitHub API.

        Raises:
            SkillInstallError: If API request fails.
        """
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        req = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "mordecai",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                raise SkillInstallError(f"GitHub path not found: {path}") from e
            raise SkillInstallError(f"GitHub API error: {e.code} {e.reason}") from e
        except URLError as e:
            raise SkillInstallError(f"Network error: {str(e.reason)}") from e
        except json.JSONDecodeError as e:
            raise SkillInstallError("Invalid response from GitHub API") from e

    def _download_file(self, url: str, dest: Path) -> None:
        """Download a file from URL to destination.

        Args:
            url: URL to download from.
            dest: Destination path.

        Raises:
            SkillInstallError: If download fails.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            raise SkillInstallError(f"Failed to download {url}: {str(e)}") from e

    def _download_github_directory(
        self,
        owner: str,
        repo: str,
        branch: str,
        path: str,
        dest_dir: Path,
    ) -> int:
        """Recursively download a GitHub directory.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Branch name.
            path: Path within the repository.
            dest_dir: Local destination directory.

        Returns:
            Number of files downloaded.

        Raises:
            SkillInstallError: If download fails.
        """
        contents = self._fetch_github_contents(owner, repo, branch, path)
        files_downloaded = 0

        for item in contents:
            item_name = item["name"]
            item_type = item["type"]

            if item_type == "file":
                download_url = item.get("download_url")
                if download_url:
                    dest_path = dest_dir / item_name
                    self._download_file(download_url, dest_path)
                    files_downloaded += 1

            elif item_type == "dir":
                sub_path = item["path"]
                sub_dest = dest_dir / item_name
                files_downloaded += self._download_github_directory(
                    owner, repo, branch, sub_path, sub_dest
                )

        return files_downloaded

    def _install_from_github(self, url: str, user_id: str) -> SkillMetadata:
        """Install a skill from a GitHub directory URL.

        Args:
            url: GitHub tree URL pointing to a skill directory.
            user_id: User's telegram ID.

        Returns:
            SkillMetadata with information about the installed skill.

        Raises:
            SkillInstallError: If installation fails.
        """
        parsed = self._parse_github_url(url)
        if not parsed:
            raise SkillInstallError("Invalid GitHub URL format")

        owner, repo, branch, path = parsed
        skill_name = path.rstrip("/").split("/")[-1]

        if not skill_name or skill_name.startswith("__"):
            raise SkillInstallError(f"Invalid skill name: {skill_name}")
        if len(skill_name) > 64:
            raise SkillInstallError(f"Skill name too long (max 64 chars): {skill_name}")

        user_skills_dir = self._get_user_skills_dir(user_id)
        skill_dir = user_skills_dir / skill_name
        return self._download_skill_into_directory(url, skill_dir, user_id)

    def install_skill(self, url: str, user_id: str) -> SkillMetadata:
        """Download and install a skill for a user.

        Supports:
        - GitHub directory URLs (tree URLs with nested files/folders)
        - .zip archives containing Python files
        - Direct .py file downloads

        Args:
            url: URL to download the skill from.
            user_id: User's telegram ID.

        Returns:
            SkillMetadata with information about the installed skill.

        Raises:
            SkillInstallError: If download or extraction fails.
        """
        if self._parse_github_url(url):
            return self._install_from_github(url, user_id)

        # Preserve legacy behavior: non-GitHub installs land in the user's root
        # skills folder (not pending) and may extract files directly.
        user_skills_dir = self._get_user_skills_dir(user_id)

        filename = url.split("/")[-1]
        if filename.endswith(".zip"):
            skill_name = filename.replace(".zip", "")
            is_zip = True
        elif filename.endswith(".py"):
            skill_name = filename.replace(".py", "")
            is_zip = False
        else:
            skill_name = filename
            is_zip = True

        if not skill_name or skill_name.startswith("__"):
            raise SkillInstallError(f"Invalid skill name: {skill_name}")

        download_path = user_skills_dir / (f"{skill_name}.zip" if is_zip else f"{skill_name}.py")

        try:
            urllib.request.urlretrieve(url, download_path)
        except Exception as e:
            raise SkillInstallError(f"Failed to download skill: {str(e)}") from e

        if is_zip:
            try:
                with zipfile.ZipFile(download_path, "r") as zip_ref:
                    extracted_files = []
                    for file in zip_ref.namelist():
                        if file.endswith(".py"):
                            zip_ref.extract(file, user_skills_dir)
                            extracted_files.append(file)

                    if not extracted_files:
                        raise SkillInstallError("No Python files found in skill archive")
            except zipfile.BadZipFile as e:
                download_path.unlink(missing_ok=True)
                raise SkillInstallError("Invalid zip file") from e
            except Exception as e:
                download_path.unlink(missing_ok=True)
                if isinstance(e, SkillInstallError):
                    raise
                raise SkillInstallError(f"Failed to extract skill: {str(e)}") from e
            finally:
                download_path.unlink(missing_ok=True)

        return SkillMetadata(
            name=skill_name,
            source_url=url,
            installed_at=datetime.utcnow(),
        )

    def download_skill_to_pending(
        self,
        url: str,
        user_id: str,
        *,
        scope: Literal["user", "shared"] = "user",
    ) -> dict:
        """Download a skill into pending/ (shared or per-user).

        Returns a dict with metadata + destination path.
        """
        pending_root = (
            self._get_shared_pending_skills_dir()
            if scope == "shared"
            else self._get_user_pending_skills_dir(user_id)
        )

        # Derive skill name early to decide destination folder.
        parsed = self._parse_github_url(url)
        if parsed:
            _owner, _repo, _branch, path = parsed
            skill_name = path.rstrip("/").split("/")[-1]
        else:
            filename = url.split("/")[-1]
            if filename.endswith(".zip"):
                skill_name = filename.replace(".zip", "")
            elif filename.endswith(".py"):
                skill_name = filename.replace(".py", "")
            else:
                skill_name = filename

        if not skill_name or skill_name.startswith("__"):
            raise SkillInstallError(f"Invalid skill name: {skill_name}")

        dest_skill_dir = pending_root / skill_name

        # If already present in pending, overwrite (user asked to download).
        metadata = self._download_skill_into_directory(url, dest_skill_dir, user_id)

        return {
            "ok": True,
            "scope": scope,
            "pending_dir": str(dest_skill_dir),
            "metadata": metadata,
        }

    def uninstall_skill(self, skill_name: str, user_id: str) -> str:
        """Remove a skill from a user's tools folder.

        Args:
            skill_name: Name of the skill to uninstall.
            user_id: User's telegram ID.

        Returns:
            Success message.

        Raises:
            SkillNotFoundError: If skill is not found.
        """
        user_skills_dir = self._get_user_skills_dir(user_id)
        skill_path = user_skills_dir / f"{skill_name}.py"
        skill_dir = user_skills_dir / skill_name

        removed = False

        if skill_path.exists():
            skill_path.unlink()
            removed = True

        if skill_dir.exists() and skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed = True

        if not removed:
            raise SkillNotFoundError(f"Skill '{skill_name}' not found")

        return f"Skill '{skill_name}' uninstalled successfully"

    def list_skills(self, user_id: str) -> list[str]:
        """List all installed skills for a user (shared + user-specific).

        Args:
            user_id: User's telegram ID.

        Returns:
            List of installed skill names (shared skills + user skills).
        """
        skills = set()

        reserved_dir_names = {
            "pending",
            "failed",
            ".venvs",
            ".venv",
            "__pycache__",
        }

        # Add shared skills first
        shared_dir = self._get_shared_skills_dir()
        if shared_dir.exists():
            for item in shared_dir.iterdir():
                if item.suffix == ".py" and item.name != "__init__.py":
                    skills.add(item.stem)
                elif item.is_dir() and not item.name.startswith("__"):
                    if item.name in reserved_dir_names:
                        continue
                    has_skill_md = (item / "SKILL.md").exists()
                    has_skill_py = (item / "skill.py").exists()
                    has_init_py = (item / "__init__.py").exists()
                    if has_skill_md or has_skill_py or has_init_py:
                        skills.add(item.name)

        # Add user-specific skills (may override shared)
        user_skills_dir = self._get_user_skills_dir(user_id)
        if user_skills_dir.exists():
            for item in user_skills_dir.iterdir():
                if item.suffix == ".py" and item.name != "__init__.py":
                    skills.add(item.stem)
                elif item.is_dir() and not item.name.startswith("__"):
                    if item.name in reserved_dir_names:
                        continue
                    has_skill_md = (item / "SKILL.md").exists()
                    has_skill_py = (item / "skill.py").exists()
                    has_init_py = (item / "__init__.py").exists()
                    if has_skill_md or has_skill_py or has_init_py:
                        skills.add(item.name)

        return sorted(skills)

    def get_skill_path(self, skill_name: str, user_id: str) -> Path | None:
        """Get the path to a skill file or directory (user skills override shared).

        Args:
            skill_name: Name of the skill.
            user_id: User's telegram ID.

        Returns:
            Path to the skill file or directory, or None if not found.
        """
        # Check user skills first (user overrides shared)
        user_skills_dir = self._get_user_skills_dir(user_id)

        skill_path = user_skills_dir / f"{skill_name}.py"
        if skill_path.exists():
            return skill_path

        skill_dir = user_skills_dir / skill_name
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir

        # Fall back to shared skills
        shared_skills_dir = self._get_shared_skills_dir()

        shared_skill_path = shared_skills_dir / f"{skill_name}.py"
        if shared_skill_path.exists():
            return shared_skill_path

        shared_skill_dir = shared_skills_dir / skill_name
        if shared_skill_dir.exists() and shared_skill_dir.is_dir():
            return shared_skill_dir

        return None

    def skill_exists(self, skill_name: str, user_id: str) -> bool:
        """Check if a skill is installed for a user.

        Args:
            skill_name: Name of the skill to check.
            user_id: User's telegram ID.

        Returns:
            True if the skill exists, False otherwise.
        """
        return self.get_skill_path(skill_name, user_id) is not None

    def get_skill_metadata(self, skill_name: str, user_id: str) -> dict | None:
        """Read SKILL.md metadata for an installed skill (user skills override shared).

        Args:
            skill_name: Name of the skill.
            user_id: User's telegram ID.

        Returns:
            Dict with skill metadata (name, description, etc.) or None.
        """
        # Check user skills first
        user_skills_dir = self._get_user_skills_dir(user_id)
        skill_dir = user_skills_dir / skill_name
        skill_md = skill_dir / "SKILL.md"

        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        import yaml

                        return yaml.safe_load(parts[1])
            except Exception:
                pass

        # Fall back to shared skills
        shared_skills_dir = self._get_shared_skills_dir()
        shared_skill_dir = shared_skills_dir / skill_name
        shared_skill_md = shared_skill_dir / "SKILL.md"

        if shared_skill_md.exists():
            try:
                content = shared_skill_md.read_text(encoding="utf-8")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        import yaml

                        return yaml.safe_load(parts[1])
            except Exception:
                pass

        return None

    def get_user_skills_directory(self, user_id: str) -> str:
        """Get the skills directory path for a user.

        Used by AgentService to configure Strands load_tools_from_directory.

        Args:
            user_id: User's telegram ID.

        Returns:
            String path to user's skills directory.
        """
        return str(self._get_user_skills_dir(user_id))

    def get_shared_skills_directory(self) -> str:
        """Get the shared skills directory path.

        Used by AgentService to load shared skills for all users.

        Returns:
            String path to shared skills directory.
        """
        return str(self._get_shared_skills_dir())

    def list_shared_skills(self) -> list[str]:
        """List all shared skills available to all users.

        Returns:
            List of shared skill names.
        """
        skills = []
        shared_dir = self._get_shared_skills_dir()

        if not shared_dir.exists():
            return skills

        reserved_dir_names = {
            "pending",
            "failed",
            ".venvs",
            ".venv",
            "__pycache__",
        }

        for item in shared_dir.iterdir():
            if item.suffix == ".py" and item.name != "__init__.py":
                skills.append(item.stem)
            elif item.is_dir() and not item.name.startswith("__"):
                if item.name in reserved_dir_names:
                    continue
                has_skill_md = (item / "SKILL.md").exists()
                has_skill_py = (item / "skill.py").exists()
                has_init_py = (item / "__init__.py").exists()
                if has_skill_md or has_skill_py or has_init_py:
                    skills.append(item.name)

        return sorted(skills)

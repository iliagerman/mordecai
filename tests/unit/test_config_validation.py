"""Tests to validate config.json matches actual project structure."""

import json
from pathlib import Path

import pytest


class TestConfigValidation:
    """Validate config.json settings match actual project structure."""

    @pytest.fixture
    def config_json(self) -> dict:
        """Load config.json from project root."""
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path) as f:
            return json.load(f)

    def test_skills_base_dir_exists(self, config_json: dict):
        """Verify skills_base_dir in config.json points to existing directory."""
        skills_dir = config_json.get("skills_base_dir", "./skills")
        project_root = Path(__file__).parent.parent.parent
        skills_path = project_root / skills_dir.lstrip("./")

        assert skills_path.exists(), (
            f"skills_base_dir '{skills_dir}' does not exist. "
            f"Expected path: {skills_path}"
        )

    def test_skills_base_dir_is_skills_not_tools(self, config_json: dict):
        """Ensure skills_base_dir is './skills' not './tools'."""
        skills_dir = config_json.get("skills_base_dir", "./skills")

        assert skills_dir == "./skills", (
            f"skills_base_dir should be './skills', got '{skills_dir}'. "
            "The tools directory was renamed to skills."
        )

    def test_shared_skills_dir_default_is_correct(self):
        """Verify shared_skills_dir default matches expected structure."""
        from app.config import AgentConfig

        # Check the default value in the model
        default_shared = AgentConfig.model_fields["shared_skills_dir"].default
        assert default_shared == "./skills/shared", (
            f"shared_skills_dir default should be './skills/shared', "
            f"got '{default_shared}'"
        )

    def test_skills_directory_structure(self, config_json: dict):
        """Verify expected skills directory structure exists."""
        project_root = Path(__file__).parent.parent.parent
        skills_dir = config_json.get("skills_base_dir", "./skills")
        skills_path = project_root / skills_dir.lstrip("./")

        # shared subdirectory should exist
        shared_path = skills_path / "shared"
        assert shared_path.exists(), (
            f"Shared skills directory should exist at {shared_path}"
        )

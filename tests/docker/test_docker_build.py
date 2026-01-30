"""Tests for Docker build configuration.

Requirements: 6.1 - Verify Docker container builds successfully
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from .conftest import requires_docker


class TestDockerfileValidity:
    """Tests for Dockerfile syntax and structure."""

    def test_dockerfile_exists(self, dockerfile_path: Path) -> None:
        """Test that Dockerfile exists in the project root."""
        assert dockerfile_path.exists(), "Dockerfile not found"

    def test_dockerfile_has_from_instruction(self, dockerfile_path: Path) -> None:
        """Test that Dockerfile has a valid FROM instruction."""
        content = dockerfile_path.read_text()
        assert "FROM ubuntu:24.04" in content, "Dockerfile should use Ubuntu 24.04 base"

    def test_dockerfile_has_workdir(self, dockerfile_path: Path) -> None:
        """Test that Dockerfile sets a working directory."""
        content = dockerfile_path.read_text()
        assert "WORKDIR /app" in content, "Dockerfile should set WORKDIR to /app"

    def test_dockerfile_has_entrypoint(self, dockerfile_path: Path) -> None:
        """Test that Dockerfile has an entrypoint."""
        content = dockerfile_path.read_text()
        assert "ENTRYPOINT" in content, "Dockerfile should have an ENTRYPOINT"

    def test_dockerfile_exposes_port(self, dockerfile_path: Path) -> None:
        """Test that Dockerfile exposes the API port."""
        content = dockerfile_path.read_text()
        assert "EXPOSE 8000" in content, "Dockerfile should expose port 8000"


class TestDockerComposeValidity:
    """Tests for docker-compose.yml validity."""

    def test_docker_compose_exists(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml exists."""
        assert docker_compose_path.exists(), "docker-compose.yml not found"

    def test_docker_compose_is_valid_yaml(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml is valid YAML."""
        content = docker_compose_path.read_text()
        try:
            parsed = yaml.safe_load(content)
            assert parsed is not None, "docker-compose.yml should not be empty"
        except yaml.YAMLError as e:
            pytest.fail(f"docker-compose.yml is not valid YAML: {e}")

    def test_docker_compose_has_services(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml defines services."""
        content = docker_compose_path.read_text()
        parsed = yaml.safe_load(content)
        assert "services" in parsed, "docker-compose.yml should define services"
        assert "mordecai" in parsed["services"], "Should define mordecai service"

    def test_docker_compose_has_volumes(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml defines named volumes."""
        content = docker_compose_path.read_text()
        parsed = yaml.safe_load(content)
        assert "volumes" in parsed, "docker-compose.yml should define volumes"
        assert "agent-db" in parsed["volumes"], "Should define agent-db volume"
        assert "agent-sessions" in parsed["volumes"], "Should define agent-sessions volume"

    def test_docker_compose_has_healthcheck(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml configures health check."""
        content = docker_compose_path.read_text()
        parsed = yaml.safe_load(content)
        service = parsed["services"]["mordecai"]
        assert "healthcheck" in service, "Service should have healthcheck configured"

    def test_docker_compose_has_restart_policy(self, docker_compose_path: Path) -> None:
        """Test that docker-compose.yml configures restart policy."""
        content = docker_compose_path.read_text()
        parsed = yaml.safe_load(content)
        service = parsed["services"]["mordecai"]
        assert "restart" in service, "Service should have restart policy configured"
        # Should use on-failure with retry limit for unhealthy containers
        assert service["restart"] == "on-failure:5", (
            "Service should restart on failure with max 5 retries"
        )


class TestEntrypointScript:
    """Tests for docker-entrypoint.sh."""

    def test_entrypoint_exists(self, docker_entrypoint_path: Path) -> None:
        """Test that docker-entrypoint.sh exists."""
        assert docker_entrypoint_path.exists(), "docker-entrypoint.sh not found"

    def test_entrypoint_is_bash_script(self, docker_entrypoint_path: Path) -> None:
        """Test that entrypoint starts with bash shebang."""
        content = docker_entrypoint_path.read_text()
        assert content.startswith("#!/bin/bash"), "Entrypoint should be a bash script"

    def test_entrypoint_handles_migrations(self, docker_entrypoint_path: Path) -> None:
        """Test that entrypoint handles database migrations."""
        content = docker_entrypoint_path.read_text()
        assert "alembic upgrade head" in content, "Entrypoint should run migrations"
        assert "SKIP_MIGRATIONS" in content, "Entrypoint should support skipping migrations"


@requires_docker
class TestDockerBuild:
    """Tests that require Docker to be available."""

    def test_docker_image_builds_successfully(self, built_image: str) -> None:
        """Test that the Docker image builds without errors.

        Requirements: 6.1 - Verify Docker container builds successfully
        """
        # The built_image fixture handles the build and will fail if build fails
        assert built_image == "mordecai:test"

    def test_docker_compose_config_is_valid(self, project_root: Path) -> None:
        """Test that docker-compose config is valid."""
        result = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"docker-compose config failed:\n{result.stderr}"

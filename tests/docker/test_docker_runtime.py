"""Tests for Docker container runtime behavior.

Requirements: 6.2, 6.4 - Verify container starts and responds to health checks,
configuration files are mounted correctly, environment variables are passed through,
migrations run on startup, and tools directory is writable.
"""

import subprocess
import time
from pathlib import Path

import pytest

from .conftest import requires_docker


@requires_docker
class TestContainerStartup:
    """Tests for container startup behavior."""

    def test_container_starts_successfully(self, running_container: str) -> None:
        """Test that the container starts without immediate crash.

        Requirements: 6.2 - Verify container starts
        """
        # Check container is running
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", running_container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout.strip() == "true", "Container should be running"

    def test_container_logs_show_startup(self, running_container: str) -> None:
        """Test that container logs show startup messages."""
        # Wait a moment for startup logs
        time.sleep(2)

        result = subprocess.run(
            ["docker", "logs", running_container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should see either startup message or migration skip message
        logs = result.stdout + result.stderr
        assert "Starting Mordecai" in logs or "Skipping database migrations" in logs, (
            f"Container should show startup logs, got:\n{logs}"
        )


@requires_docker
class TestEnvironmentVariables:
    """Tests for environment variable handling."""

    def test_skip_migrations_env_is_respected(self, built_image: str) -> None:
        """Test that SKIP_MIGRATIONS environment variable is respected.

        Requirements: 6.2 - Verify environment variables are passed through
        """
        container_name = "mordecai-env-test"

        # Cleanup any existing container
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=30,
        )

        try:
            # Start container with SKIP_MIGRATIONS=true
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-e",
                    "SKIP_MIGRATIONS=true",
                    built_image,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, f"Failed to start container:\n{result.stderr}"

            # Wait for startup
            time.sleep(3)

            # Check logs for skip message
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logs = logs_result.stdout + logs_result.stderr
            assert "Skipping database migrations" in logs, (
                f"Should skip migrations when SKIP_MIGRATIONS=true, got:\n{logs}"
            )
        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=30,
            )


@requires_docker
class TestRequiredBinaries:
    """Tests for required binary availability in the container."""

    @pytest.mark.parametrize(
        "binary",
        [
            "python3",
            "uv",
            "cargo",
            "node",
            "npm",
            "pip3",
            "yt-dlp",
            "ffmpeg",
        ],
    )
    def test_binary_is_available(self, built_image: str, binary: str) -> None:
        """Test that required binaries are available in the container.

        Requirements: 6.3 - Verify all required binaries are available
        """
        # Use --entrypoint to bypass the default entrypoint and run command directly
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "which", built_image, binary],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Binary '{binary}' not found in container:\n{result.stderr}"

    def test_himalaya_is_available(self, built_image: str) -> None:
        """Test that Himalaya email CLI is available in the container.

        Himalaya is installed via cargo and lives in /usr/local/cargo/bin/
        Requirements: 6.3 - Verify all required binaries are available
        """
        # Use sh -c to get the full PATH including cargo bin
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                built_image,
                "-c",
                "which himalaya || test -x /usr/local/cargo/bin/himalaya",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Himalaya not found in container:\n{result.stderr}"


@requires_docker
class TestDirectoryPermissions:
    """Tests for directory permissions in the container."""

    def test_tools_directory_is_writable(self, built_image: str) -> None:
        """Test that the tools directory is writable.

        Requirements: 6.4 - Verify tools directory is writable
        """
        # Use --entrypoint to bypass the default entrypoint
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                built_image,
                "-c",
                "touch /app/tools/test_file && rm /app/tools/test_file",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Tools directory should be writable:\n{result.stderr}"

    def test_data_directory_exists(self, built_image: str) -> None:
        """Test that the data directory exists."""
        # Use --entrypoint to bypass the default entrypoint
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "test", built_image, "-d", "/app/data"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, "Data directory should exist"

    def test_sessions_directory_exists(self, built_image: str) -> None:
        """Test that the sessions directory exists."""
        # Use --entrypoint to bypass the default entrypoint
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "test", built_image, "-d", "/app/sessions"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, "Sessions directory should exist"


@requires_docker
class TestConfigurationMounting:
    """Tests for configuration file mounting."""

    def test_container_warns_on_missing_secrets(self, built_image: str) -> None:
        """Test that container warns when secrets.yml is missing.

        Requirements: 6.2 - Verify configuration files are handled correctly
        """
        container_name = "strands-agent-config-test"

        # Cleanup any existing container
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=30,
        )

        try:
            # Start container without mounting secrets.yml
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-e",
                    "SKIP_MIGRATIONS=true",
                    built_image,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, f"Failed to start container:\n{result.stderr}"

            # Wait for startup
            time.sleep(2)

            # Check logs for warning
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logs = logs_result.stdout + logs_result.stderr
            assert "WARNING: secrets.yml not found" in logs, (
                f"Should warn about missing secrets.yml, got:\n{logs}"
            )
        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=30,
            )

    def test_container_warns_on_missing_config(self, built_image: str) -> None:
        """Test that container warns when config.json is missing.

        Requirements: 6.2 - Verify configuration files are handled correctly
        """
        container_name = "strands-agent-config-test-2"

        # Cleanup any existing container
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=30,
        )

        try:
            # Start container without mounting config.json
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-e",
                    "SKIP_MIGRATIONS=true",
                    built_image,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, f"Failed to start container:\n{result.stderr}"

            # Wait for startup
            time.sleep(2)

            # Check logs for warning
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logs = logs_result.stdout + logs_result.stderr
            assert "WARNING: config.json not found" in logs, (
                f"Should warn about missing config.json, got:\n{logs}"
            )
        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=30,
            )

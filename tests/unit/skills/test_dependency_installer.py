"""Unit tests for dependency installer skill.

Tests the runtime package installation capability for the Docker container.
"""

import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path

SKILL_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "skills/shared/dependency-installer"
)
sys.path.insert(0, str(SKILL_DIR))

from skill import (
    _find_command,
    _install_apt_package,
    _install_npm_package,
    _install_python_package,
    _is_apt_package_installed,
    _is_npm_package_installed,
    _is_python_package_installed,
    check_package,
    install_package,
)


class TestFindCommand:
    """Tests for command finding utility."""

    def test_find_existing_command(self):
        """Test finding an existing command."""
        result = _find_command(["python3", "python"])
        assert result is not None

    def test_find_nonexistent_command(self):
        """Test finding a non-existent command returns None."""
        result = _find_command(["nonexistent_command_xyz123"])
        assert result is None


class TestIsPythonPackageInstalled:
    """Tests for Python package detection."""

    def test_detect_installed_package(self):
        """Test detecting an installed pip package."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _is_python_package_installed("pytest")
            assert result is True

    def test_detect_missing_package(self):
        """Test detecting a missing pip package."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = _is_python_package_installed("nonexistent_xyz")
            assert result is False

    def test_handle_version_specifier(self):
        """Test handling package with version specifier."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _is_python_package_installed("requests>=2.0")
            # Should call with base name only
            call_args = mock_run.call_args[0][0]
            assert "requests" in call_args


class TestIsNpmPackageInstalled:
    """Tests for npm package detection."""

    def test_npm_package_installed(self):
        """Test detecting installed npm package."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _is_npm_package_installed("axios")
            assert result is True

    def test_npm_package_not_installed(self):
        """Test detecting missing npm package."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = _is_npm_package_installed("nonexistent")
            assert result is False


class TestIsAptPackageInstalled:
    """Tests for system package detection."""

    def test_detect_installed_binary(self):
        """Test detecting an installed binary."""
        with patch("shutil.which", return_value="/usr/bin/python3"):
            assert _is_apt_package_installed("python3") is True

    def test_detect_missing_binary(self):
        """Test detecting a missing binary."""
        with patch("shutil.which", return_value=None):
            assert _is_apt_package_installed("nonexistent") is False


class TestInstallPythonPackage:
    """Tests for Python package installation."""

    def test_already_installed(self):
        """Test that already installed packages are detected."""
        with patch(
            "skill._is_python_package_installed", return_value=True
        ):
            success, message = _install_python_package("pytest")
            assert success is True
            assert "already installed" in message.lower()

    def test_install_success(self):
        """Test successful installation."""
        with patch(
            "skill._is_python_package_installed", return_value=False
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                success, message = _install_python_package("requests")
                assert success is True
                assert "successfully" in message.lower()

    def test_install_failure(self):
        """Test handling installation failure."""
        with patch(
            "skill._is_python_package_installed", return_value=False
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Package not found"
                )
                success, message = _install_python_package("nonexistent")
                assert success is False
                assert "failed" in message.lower()

    def test_install_timeout(self):
        """Test handling installation timeout."""
        with patch(
            "skill._is_python_package_installed", return_value=False
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(
                    cmd="uv", timeout=300
                )
                success, message = _install_python_package("slow_package")
                assert success is False
                assert "timed out" in message.lower()


class TestInstallNpmPackage:
    """Tests for npm package installation."""

    def test_already_installed(self):
        """Test that already installed packages are detected."""
        with patch("skill._is_npm_package_installed", return_value=True):
            success, message = _install_npm_package("axios")
            assert success is True
            assert "already installed" in message.lower()

    def test_npm_not_available(self):
        """Test error when npm is not available."""
        with patch("skill._is_npm_package_installed", return_value=False):
            with patch("skill._find_command", return_value=None):
                success, message = _install_npm_package("axios")
                assert success is False
                assert "not found" in message.lower()

    def test_install_success(self):
        """Test successful npm installation."""
        with patch("skill._is_npm_package_installed", return_value=False):
            with patch("skill._find_command", return_value="npm"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=0, stdout="", stderr=""
                    )
                    success, message = _install_npm_package("axios")
                    assert success is True


class TestInstallAptPackage:
    """Tests for apt package installation."""

    def test_already_installed(self):
        """Test that already installed packages are detected."""
        with patch("skill._is_apt_package_installed", return_value=True):
            success, message = _install_apt_package("ffmpeg")
            assert success is True
            assert "already installed" in message.lower()

    def test_apt_not_available(self):
        """Test error when apt-get is not available."""
        with patch("skill._is_apt_package_installed", return_value=False):
            with patch("skill._find_command", return_value=None):
                success, message = _install_apt_package("ffmpeg")
                assert success is False
                assert "not found" in message.lower()

    def test_install_success(self):
        """Test successful apt installation."""
        with patch("skill._is_apt_package_installed", return_value=False):
            with patch("skill._find_command", return_value="apt-get"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=0, stdout="", stderr=""
                    )
                    success, message = _install_apt_package("curl")
                    assert success is True


class TestInstallPackageTool:
    """Tests for the install_package Strands tool."""

    def test_install_python_package(self):
        """Test installing a Python package via the tool."""
        with patch(
            "skill._install_python_package",
            return_value=(True, "Installed"),
        ):
            result = install_package(package="requests", manager="uv")
            assert "Installed" in result

    def test_install_npm_package(self):
        """Test installing an npm package via the tool."""
        with patch(
            "skill._install_npm_package",
            return_value=(True, "Installed"),
        ):
            result = install_package(package="axios", manager="npm")
            assert "Installed" in result

    def test_install_apt_package(self):
        """Test installing an apt package via the tool."""
        with patch(
            "skill._install_apt_package",
            return_value=(True, "Installed"),
        ):
            result = install_package(package="ffmpeg", manager="apt")
            assert "Installed" in result

    def test_unknown_manager(self):
        """Test error for unknown package manager."""
        result = install_package(package="test", manager="unknown")
        assert "unknown" in result.lower()

    def test_manager_aliases(self):
        """Test that manager aliases work."""
        with patch(
            "skill._install_python_package",
            return_value=(True, "OK"),
        ):
            # pip should map to uv
            result = install_package(package="test", manager="pip")
            assert "OK" in result

        with patch(
            "skill._install_npm_package",
            return_value=(True, "OK"),
        ):
            # node should map to npm
            result = install_package(package="test", manager="node")
            assert "OK" in result

        with patch(
            "skill._install_apt_package",
            return_value=(True, "OK"),
        ):
            # system should map to apt
            result = install_package(package="test", manager="system")
            assert "OK" in result


class TestCheckPackageTool:
    """Tests for the check_package Strands tool."""

    def test_check_installed_python(self):
        """Test checking installed Python package."""
        with patch(
            "skill._is_python_package_installed", return_value=True
        ):
            result = check_package(package="pytest", manager="uv")
            assert "is installed" in result.lower()

    def test_check_missing_python(self):
        """Test checking missing Python package."""
        with patch(
            "skill._is_python_package_installed", return_value=False
        ):
            result = check_package(package="nonexistent", manager="uv")
            assert "not installed" in result.lower()

    def test_check_unknown_manager(self):
        """Test error for unknown package manager."""
        result = check_package(package="test", manager="unknown")
        assert "unknown" in result.lower()

"""Docker test fixtures and configuration."""

import os
import subprocess
import time
from pathlib import Path
from typing import Generator

import pytest


# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def project_root() -> Path:
    """Return the project root directory."""
    return PROJECT_ROOT


@pytest.fixture(scope="module")
def dockerfile_path(project_root: Path) -> Path:
    """Return the path to the Dockerfile."""
    return project_root / "Dockerfile"


@pytest.fixture(scope="module")
def docker_compose_path(project_root: Path) -> Path:
    """Return the path to docker-compose.yml."""
    return project_root / "docker-compose.yml"


@pytest.fixture(scope="module")
def docker_entrypoint_path(project_root: Path) -> Path:
    """Return the path to docker-entrypoint.sh."""
    return project_root / "docker-entrypoint.sh"


@pytest.fixture(scope="module")
def built_image(project_root: Path) -> Generator[str, None, None]:
    """Build the Docker image and return the image tag.

    This fixture builds the image once per test module and cleans up after.
    """
    image_tag = "mordecai:test"

    # Build the Docker image.
    # By default we capture output (cleaner test logs). If you want to see
    # progress live (helpful when docker builds take a while), set:
    #   PYTEST_DOCKER_BUILD_STREAM=1
    stream_build = os.environ.get("PYTEST_DOCKER_BUILD_STREAM", "").strip() in {
        "1",
        "true",
        "yes",
    }

    build_cmd = ["docker", "build", "--progress=plain", "-t", image_tag, "."]

    if stream_build:
        print(f"[docker] building image {image_tag} in {project_root} ...")
        result = subprocess.run(
            build_cmd,
            cwd=project_root,
            timeout=600,  # 10 minute timeout for build
        )
        if result.returncode != 0:
            pytest.fail("Docker build failed (see build output above).")
    else:
        result = subprocess.run(
            build_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for build
        )
        if result.returncode != 0:
            pytest.fail(f"Docker build failed:\n{result.stderr}")

    yield image_tag

    # Cleanup: remove the test image
    subprocess.run(
        ["docker", "rmi", "-f", image_tag],
        capture_output=True,
        timeout=60,
    )


@pytest.fixture
def running_container(built_image: str, project_root: Path) -> Generator[str, None, None]:
    """Start a container for testing and return the container ID.

    The container is started with SKIP_MIGRATIONS=true for faster startup.
    """
    container_name = "mordecai-test"

    # Remove any existing container with the same name
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        timeout=30,
    )

    # Start the container
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-e",
            "SKIP_MIGRATIONS=true",
            "-p",
            "18000:8000",  # Use different port to avoid conflicts
            built_image,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to start container:\n{result.stderr}")

    container_id = result.stdout.strip()

    # Wait for container to be ready (up to 30 seconds)
    for _ in range(30):
        check = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check.stdout.strip() == "true":
            break
        time.sleep(1)

    yield container_id

    # Cleanup: stop and remove the container
    subprocess.run(
        ["docker", "stop", container_id],
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        timeout=30,
    )


def docker_available() -> bool:
    """Check if Docker is available on the system."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Skip marker for tests that require Docker
requires_docker = pytest.mark.skipif(not docker_available(), reason="Docker is not available")

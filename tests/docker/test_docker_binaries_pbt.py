"""Property-based tests for Docker container binary availability.

Note: This test is redundant with test_docker_runtime.py::TestRequiredBinaries
which already tests all required binaries using pytest.mark.parametrize.
The runtime tests use the built_image fixture which is more reliable.

This file is kept for demonstration of hypothesis-based property testing,
but the actual binary availability testing is done in test_docker_runtime.py.
"""

import subprocess

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from .conftest import requires_docker, docker_available


# List of required binaries that must be available in the container
REQUIRED_BINARIES = ["python3", "uv", "cargo", "node", "npm", "himalaya"]


@requires_docker
class TestBinaryAvailabilityPBT:
    """Property-based tests for required binary availability.
    
    These tests demonstrate hypothesis-based property testing.
    For actual CI testing, use test_docker_runtime.py::TestRequiredBinaries.
    """

    @settings(max_examples=1, deadline=None)
    @given(binary=st.sampled_from(REQUIRED_BINARIES))
    def test_required_binary_available(self, built_image: str, binary: str):
        """
        Property: For any binary in {python3, uv, cargo, node, npm, himalaya},
        executing `which <binary>` SHALL return exit code 0.
        
        This validates that all required binaries are installed and accessible
        in the container's PATH.
        
        Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 6.3
        """
        # Use --entrypoint to bypass the default entrypoint and run command directly
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "which", built_image, binary],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, \
            f"Binary '{binary}' should be available in container PATH. " \
            f"stderr: {result.stderr}"

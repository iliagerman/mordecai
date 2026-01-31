"""Deprecated.

This file used to contain Hypothesis-based demonstration tests.
It is intentionally left empty to avoid noisy/flaky property-based tests.

Binary availability is covered by deterministic tests in
`tests/docker/test_docker_runtime.py`.
"""


def test_placeholder__docker_binaries_pbt_removed():
    # Kept so pytest collection does not error on an empty file.
    assert True

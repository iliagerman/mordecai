"""Dependency installer skill.

This skill provides a Strands tool to install runtime dependencies inside the
container. It is intentionally pragmatic and supports multiple ecosystems.

Supported managers (via install_package / check_package):
- uv / pip: Python packages
- npm: global Node.js packages
- apt / apt-get: Debian/Ubuntu system packages (best for CLIs)
- cargo: Rust crates (often used to install CLIs like himalaya)
- brew: Homebrew packages (useful on macOS hosts)
- url: download a single-file binary from a URL into /usr/local/bin

Note: This runs commands on the host/container. Use with care.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    # Allow importing this module in environments where `strands` isn't installed
    # (e.g., IDE/static analysis or isolated unit tests).
    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


def _find_command(candidates: Iterable[str]) -> str | None:
    """Return the first executable found in PATH from a list of candidates."""
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


_VERSION_SPLIT_RE = re.compile(r"(==|>=|<=|~=|!=|>|<)")


def _base_name(spec: str) -> str:
    """Strip version constraints from a package spec (best-effort)."""
    s = (spec or "").strip()
    if not s:
        return ""
    # e.g. requests>=2.0 -> requests
    parts = _VERSION_SPLIT_RE.split(s, maxsplit=1)
    return parts[0].strip() if parts else s


# ---------------------------
# Python packages (uv/pip)
# ---------------------------


def _is_python_package_installed(package: str) -> bool:
    pkg = _base_name(package)
    if not pkg:
        return False

    py = _find_command(["python3", "python"])
    if not py:
        return False

    proc = subprocess.run(
        [py, "-m", "pip", "show", pkg],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _install_python_package(package: str, *, timeout_seconds: int = 300) -> tuple[bool, str]:
    pkg = package.strip() if package else ""
    if not pkg:
        return False, "No package specified"

    if _is_python_package_installed(pkg):
        return True, f"Python package '{_base_name(pkg)}' is already installed"

    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_NO_INPUT", "1")

    uv = _find_command(["uv"])
    if uv:
        cmd = [uv, "pip", "install", pkg]
    else:
        py = _find_command(["python3", "python"])
        if not py:
            return False, "Python not found"
        cmd = [py, "-m", "pip", "install", pkg]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"Python package install timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to install Python package '{pkg}': {proc.stderr or proc.stdout}"

    return True, f"Successfully installed Python package '{pkg}'"


# ---------------------------
# npm packages
# ---------------------------


def _is_npm_package_installed(package: str) -> bool:
    pkg = _base_name(package)
    if not pkg:
        return False

    npm = _find_command(["npm"])
    if not npm:
        return False

    proc = subprocess.run(
        [npm, "list", "-g", pkg, "--depth=0"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _install_npm_package(package: str, *, timeout_seconds: int = 300) -> tuple[bool, str]:
    pkg = package.strip() if package else ""
    if not pkg:
        return False, "No package specified"

    if _is_npm_package_installed(pkg):
        return True, f"npm package '{_base_name(pkg)}' is already installed"

    npm = _find_command(["npm"])
    if not npm:
        return False, "npm not found"

    try:
        proc = subprocess.run(
            [npm, "install", "-g", pkg],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"npm install timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to install npm package '{pkg}': {proc.stderr or proc.stdout}"

    return True, f"Successfully installed npm package '{pkg}'"


# ---------------------------
# apt packages (system)
# ---------------------------


def _is_apt_package_installed(package: str) -> bool:
    # For our purposes, treat the apt package name as a binary name.
    # This keeps the check cheap and avoids dpkg querying.
    pkg = _base_name(package)
    return bool(pkg and shutil.which(pkg))


def _install_apt_package(package: str, *, timeout_seconds: int = 300) -> tuple[bool, str]:
    pkg = package.strip() if package else ""
    if not pkg:
        return False, "No package specified"

    if _is_apt_package_installed(pkg):
        return True, f"System package/binary '{_base_name(pkg)}' is already installed"

    apt = _find_command(["apt-get", "apt"])
    if not apt:
        return False, "apt-get not found"

    try:
        # update first (best-effort)
        subprocess.run(
            [apt, "update"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        proc = subprocess.run(
            [apt, "install", "-y", pkg],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"apt-get install timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to install apt package '{pkg}': {proc.stderr or proc.stdout}"

    return True, f"Successfully installed apt package '{pkg}'"


# ---------------------------
# cargo (Rust crates)
# ---------------------------


def _is_cargo_crate_installed(crate: str) -> bool:
    # Best-effort: most crates install a binary with the same name.
    name = _base_name(crate)
    return bool(name and shutil.which(name))


def _install_cargo_crate(crate: str, *, timeout_seconds: int = 900) -> tuple[bool, str]:
    name = crate.strip() if crate else ""
    if not name:
        return False, "No crate specified"

    if _is_cargo_crate_installed(name):
        return True, f"cargo crate/binary '{_base_name(name)}' is already installed"

    cargo = _find_command(["cargo"])
    if not cargo:
        return False, "cargo not found"

    try:
        proc = subprocess.run(
            [cargo, "install", name],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"cargo install timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to install cargo crate '{name}': {proc.stderr or proc.stdout}"

    return True, f"Successfully installed cargo crate '{name}'"


# ---------------------------
# brew (macOS)
# ---------------------------


def _is_brew_package_installed(package: str) -> bool:
    name = _base_name(package)
    if not name:
        return False

    brew = _find_command(["brew"])
    if not brew:
        return False

    proc = subprocess.run(
        [brew, "list", "--formula", name],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _install_brew_package(package: str, *, timeout_seconds: int = 600) -> tuple[bool, str]:
    name = package.strip() if package else ""
    if not name:
        return False, "No package specified"

    if _is_brew_package_installed(name):
        return True, f"brew package '{_base_name(name)}' is already installed"

    brew = _find_command(["brew"])
    if not brew:
        return False, "brew not found"

    try:
        proc = subprocess.run(
            [brew, "install", name],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"brew install timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to install brew package '{name}': {proc.stderr or proc.stdout}"

    return True, f"Successfully installed brew package '{name}'"


# ---------------------------
# URL binary download
# ---------------------------


def _install_url_binary(url: str, *, timeout_seconds: int = 300) -> tuple[bool, str]:
    u = (url or "").strip()
    if not u:
        return False, "No URL specified"

    # Determine destination name from URL basename
    name = u.split("?")[0].rstrip("/").split("/")[-1]
    if not name:
        return False, "Could not determine filename from URL"

    dest_dir = "/usr/local/bin"
    dest = f"{dest_dir}/{name}"

    downloader = _find_command(["curl", "wget"])
    if not downloader:
        return False, "Neither curl nor wget found"

    os.makedirs(dest_dir, exist_ok=True)

    try:
        if downloader.endswith("curl"):
            proc = subprocess.run(
                [downloader, "-fsSL", "-o", dest, u],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        else:
            proc = subprocess.run(
                [downloader, "-q", "-O", dest, u],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired:
        return False, f"Download timed out after {timeout_seconds}s"

    if proc.returncode != 0:
        return False, f"Failed to download '{u}': {proc.stderr or proc.stdout}"

    try:
        os.chmod(dest, 0o755)
    except Exception as e:
        return False, f"Downloaded but could not chmod binary: {e}"

    return True, f"Downloaded binary to {dest}"


# ---------------------------
# Strands tools
# ---------------------------


@tool(
    name="install_package",
    description=(
        "Install a dependency at runtime. manager can be: uv/pip, npm, apt/system, cargo, brew, url."
    ),
)
def install_package(package: str, manager: str = "uv") -> str:
    m = (manager or "").strip().lower()

    # aliases
    if m == "pip":
        m = "uv"
    if m == "node":
        m = "npm"
    if m in ("system", "apt-get"):
        m = "apt"

    if m in ("uv", "python"):
        ok, msg = _install_python_package(package)
    elif m == "npm":
        ok, msg = _install_npm_package(package)
    elif m == "apt":
        ok, msg = _install_apt_package(package)
    elif m == "cargo":
        ok, msg = _install_cargo_crate(package)
    elif m == "brew":
        ok, msg = _install_brew_package(package)
    elif m == "url":
        ok, msg = _install_url_binary(package)
    else:
        return f"Unknown manager '{manager}'. Supported: uv/pip, npm, apt/system, cargo, brew, url."

    return msg if ok else f"ERROR: {msg}"


@tool(
    name="check_package",
    description=(
        "Check whether a dependency is installed. manager can be: uv/pip, npm, apt/system, cargo, brew."
    ),
)
def check_package(package: str, manager: str = "uv") -> str:
    m = (manager or "").strip().lower()

    if m == "pip":
        m = "uv"
    if m == "node":
        m = "npm"
    if m in ("system", "apt-get"):
        m = "apt"

    if m in ("uv", "python"):
        ok = _is_python_package_installed(package)
    elif m == "npm":
        ok = _is_npm_package_installed(package)
    elif m == "apt":
        ok = _is_apt_package_installed(package)
    elif m == "cargo":
        ok = _is_cargo_crate_installed(package)
    elif m == "brew":
        ok = _is_brew_package_installed(package)
    else:
        return f"Unknown manager '{manager}'. Supported: uv/pip, npm, apt/system, cargo, brew."

    if ok:
        return f"{_base_name(package)} is installed (manager={m})."
    return f"{_base_name(package)} is not installed (manager={m})."

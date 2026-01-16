"""Utility functions for the installer."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PosixPath


def run_command(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.returncode, result.stdout, result.stderr


def ensure_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Keep going if we cannot create the directory (e.g., sandboxed tests)
        print(f"Warning: could not create directory {path} (permission denied)")


def is_tool_installed(tool_name: str) -> bool:
    """Check if a tool is installed and available in PATH (cross-platform)."""
    return shutil.which(tool_name) is not None


def get_platform() -> str:
    return sys.platform


def platform_path(path: str | os.PathLike | Path) -> Path:
    """Create a Path without forcing WindowsPath on non-Windows test runs."""
    if isinstance(path, Path):
        if os.name == "nt" and sys.platform != "win32":
            return PosixPath(os.fspath(path))
        return path
    if os.name == "nt" and sys.platform != "win32":
        return PosixPath(os.fspath(path))
    return Path(path)


def safe_home(ignore_temp_home: bool = False) -> Path:
    """Resolve a usable home path even when os.name is patched to 'nt'."""
    if not ignore_temp_home:
        temp_home = os.environ.get("CHAD_TEMP_HOME")
        if temp_home:
            return platform_path(temp_home)
    try:
        return platform_path(Path.home())
    except RuntimeError:
        fallback = os.environ.get("HOME") or tempfile.gettempdir()
        return platform_path(fallback)

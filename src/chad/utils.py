"""Utility functions for the installer."""

import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_tool_installed(tool_name: str) -> bool:
    return subprocess.run(
        ["which", tool_name],
        capture_output=True
    ).returncode == 0


def get_platform() -> str:
    return sys.platform

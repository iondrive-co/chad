#!/usr/bin/env python3

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

try:
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - packaging is part of pip but fallback kept minimal
    InvalidVersion = Version = None  # type: ignore[misc]


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "src" / "chad" / "__init__.py"
DIST_DIR = ROOT / "dist"


def _get_current_version() -> str:
    if not PYPROJECT.exists():
        raise SystemExit(f"{PYPROJECT} does not exist.")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"Could not find current version in {PYPROJECT}.")
    return match.group(1).strip()


def _validate_version(raw: str) -> str:
    if not raw:
        raise SystemExit("Version is required.")
    if Version is None:
        return raw
    try:
        Version(raw)
    except InvalidVersion as exc:
        raise SystemExit(f"Invalid version: {exc}") from exc
    return raw


def _prompt_version(current: str) -> str:
    while True:
        proposed = input(f"Enter new version (current: {current}): ").strip()
        try:
            return _validate_version(proposed)
        except SystemExit as exc:
            print(exc, file=sys.stderr)


def _replace_line(path: Path, pattern: str, replacement: str, label: str) -> None:
    content = path.read_text(encoding="utf-8")
    new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.MULTILINE)
    if count == 0:
        raise SystemExit(f"Could not update {label} in {path}.")
    path.write_text(new_content, encoding="utf-8")
    print(f"Updated {label} in {path}.")


def update_versions(version: str) -> None:
    _replace_line(PYPROJECT, r'^version\s*=\s*["\'].*["\']', f'version = "{version}"', "project version")
    if PACKAGE_INIT.exists():
        _replace_line(
            PACKAGE_INIT,
            r'^__version__\s*=\s*["\'].*["\']',
            f'__version__ = "{version}"',
            "__version__",
        )


def clean_dist() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
        print(f"Removed {DIST_DIR}.")


def run_command(cmd: Sequence[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> None:
    current_version = _get_current_version()
    version = _prompt_version(current_version)
    update_versions(version)
    clean_dist()
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "build", "twine"])
    run_command([sys.executable, "-m", "build"])
    artifacts = sorted(DIST_DIR.glob("*"))
    if not artifacts:
        raise SystemExit(f"No artifacts found in {DIST_DIR} after build.")
    run_command([sys.executable, "-m", "twine", "upload", *map(str, artifacts)])


if __name__ == "__main__":
    main()

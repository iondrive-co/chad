#!/usr/bin/env python3
"""Build platform-specific installers for Chad using PyInstaller.

This script creates standalone executables for Linux, macOS, and Windows.
Run locally with PyCharm or via GitHub Actions for releases.

Usage:
    python scripts/build_release.py              # Build for current platform
    python scripts/build_release.py --output DIR # Specify output directory
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BUILD_UI_SCRIPT = ROOT / "scripts" / "build_ui.py"
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "release"


def get_current_version() -> str:
    """Extract the current version from pyproject.toml."""
    if not PYPROJECT.exists():
        raise SystemExit(f"{PYPROJECT} does not exist.")
    match = re.search(
        r'^version\s*=\s*["\']([^"\']+)["\']',
        PYPROJECT.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit(f"Could not find current version in {PYPROJECT}.")
    return match.group(1).strip()


def get_platform_name() -> str:
    """Get the platform name for the current system."""
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    else:
        return "linux"


def get_installer_filename(version: str) -> str:
    """Get the installer filename for the current platform."""
    platform = get_platform_name()
    if platform == "windows":
        return f"chad-{version}-{platform}.exe"
    return f"chad-{version}-{platform}"


def build_ui() -> None:
    """Build the React UI and bundle it into the Python package."""
    if not BUILD_UI_SCRIPT.exists():
        print("Skipping UI build - build_ui.py not found")
        return

    print("Building React UI for packaging ...")
    run_command([sys.executable, str(BUILD_UI_SCRIPT)])


def run_command(cmd: Sequence[str], cwd: Path | None = None) -> None:
    """Run a shell command."""
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd)


def _find_built_artifact(name: str) -> Path | None:
    """Find the built executable in the dist directory."""
    # PyInstaller puts executables in dist/<name>/<name> or dist/<name>.exe
    candidates = [
        DIST_DIR / name / name,
        DIST_DIR / name / f"{name}.exe",
        DIST_DIR / name,
        DIST_DIR / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def build_installer(output_dir: Path | None = None) -> Path:
    """Build the installer for the current platform.

    Args:
        output_dir: Directory to place the final installer. Defaults to ./release/

    Returns:
        Path to the built installer.
    """
    # Check for PyInstaller
    pyinstaller = shutil.which("pyinstaller")
    if pyinstaller is None:
        raise SystemExit(
            "PyInstaller is not installed. Install it with:\n"
            "  pip install pyinstaller\n"
            "Or include it in your virtualenv."
        )

    version = get_current_version()
    platform_name = get_platform_name()
    output_dir = output_dir or RELEASE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building Chad {version} installer for {platform_name}")

    # Build UI first
    build_ui()

    # Clean previous build
    for dir_to_clean in [DIST_DIR / "chad", ROOT / "build"]:
        if dir_to_clean.exists():
            shutil.rmtree(dir_to_clean)
            print(f"Cleaned {dir_to_clean}")

    # Build with PyInstaller
    pyinstaller_args = [
        pyinstaller,
        "--name", "chad",
        "--onedir",  # Create a directory with executable + dependencies
        "--noconfirm",  # Don't ask for confirmation
        # Add the main entry point
        str(ROOT / "src" / "chad" / "__main__.py"),
        # Add hidden imports that PyInstaller might miss
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        "--hidden-import", "httptools",
        "--hidden-import", "websockets",
        "--hidden-import", "pyte",
        "--hidden-import", "bcrypt",
        "--hidden-import", "cryptography",
        # Add data files
        "--add-data", f"{ROOT / 'src' / 'chad' / 'ui_dist'}:chad/ui_dist",
    ]

    # Platform-specific options
    if platform_name == "windows":
        pyinstaller_args.extend([
            "--console",  # Show console window on Windows
        ])
    elif platform_name == "macos":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "ai.chad.app",
        ])

    run_command(pyinstaller_args, cwd=ROOT)

    # Find the built artifact
    artifact = _find_built_artifact("chad")
    if artifact is None:
        raise SystemExit(f"Build failed - could not find artifact in {DIST_DIR}")

    # Copy to release directory with proper naming
    installer_name = get_installer_filename(version)
    final_path = output_dir / installer_name

    if artifact.is_dir():
        # For onedir builds, we need to create an archive
        shutil.make_archive(
            str(output_dir / f"chad-{version}-{platform_name}"),
            "zip" if platform_name == "windows" else "gztar",
            artifact.parent,
            artifact.name,
        )
        archive_ext = ".zip" if platform_name == "windows" else ".tar.gz"
        final_path = output_dir / f"chad-{version}-{platform_name}{archive_ext}"
    else:
        shutil.copy2(artifact, final_path)
        if platform_name != "windows":
            os.chmod(final_path, 0o755)

    print(f"Installer created: {final_path}")
    return final_path


def main() -> int:
    """Main entry point for build_release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for the installer (default: ./release/)",
    )
    args = parser.parse_args()

    try:
        build_installer(output_dir=args.output)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBuild cancelled")
        return 1


if __name__ == "__main__":
    sys.exit(main())

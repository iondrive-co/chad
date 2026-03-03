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
PYINSTALLER_ENTRY = ROOT / "scripts" / "pyinstaller_entry.py"
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
    if platform == "linux":
        return f"chad-{version}-{platform}.deb"
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


def ensure_pyinstaller() -> list[str]:
    """Ensure PyInstaller is available; install it if missing.

    Returns a command list that can be used to invoke PyInstaller. We prefer the
    installed executable when present and fall back to ``python -m PyInstaller``
    to avoid PATH issues after installation.
    """
    pyinstaller = shutil.which("pyinstaller")
    if pyinstaller:
        return [pyinstaller]

    print("PyInstaller not found. Installing via pip ...")
    run_command([sys.executable, "-m", "pip", "install", "pyinstaller"])

    pyinstaller = shutil.which("pyinstaller")
    if pyinstaller:
        return [pyinstaller]

    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:  # pragma: no cover - defensive fallback
        raise SystemExit(
            "PyInstaller installation failed. Install it manually with:\n"
            "  pip install pyinstaller"
        ) from exc

    return [sys.executable, "-m", "PyInstaller"]


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


def _build_linux_deb(app_binary: Path, version: str, output_dir: Path) -> Path:
    """Create a Debian package from the PyInstaller onedir build."""
    dpkg = shutil.which("dpkg-deb")
    if dpkg is None:
        raise SystemExit(
            "dpkg-deb is required to build a .deb package.\n"
            "On Debian/Ubuntu install it with: sudo apt-get install dpkg"
        )

    app_dir = app_binary.parent if app_binary.is_file() else app_binary
    staging = output_dir / f"chad-{version}-deb"
    install_root = staging
    opt_dir = install_root / "opt" / "chad"
    bin_dir = install_root / "usr" / "bin"
    control_dir = install_root / "DEBIAN"

    # Recreate staging area
    if staging.exists():
        shutil.rmtree(staging)
    opt_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    control_dir.mkdir(parents=True, exist_ok=True)

    # Copy PyInstaller output into /opt/chad
    shutil.copytree(app_dir, opt_dir, dirs_exist_ok=True)

    # Symlink /usr/bin/chad -> /opt/chad/chad
    symlink_target = Path("../../opt/chad/chad")
    symlink_path = bin_dir / "chad"
    if symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(symlink_target)

    # Control file
    control_contents = "\n".join(
        [
            "Package: chad-ai",
            f"Version: {version}",
            "Section: utils",
            "Priority: optional",
            "Architecture: amd64",
            "Maintainer: Team Chad <support@chad.ai>",
            "Description: Chad AI CLI packaged with PyInstaller",
            "",
        ]
    )
    (control_dir / "control").write_text(control_contents, encoding="utf-8")

    final_path = output_dir / get_installer_filename(version)
    cmd = [dpkg, "--build", "--root-owner-group", str(staging), str(final_path)]
    run_command(cmd)
    shutil.rmtree(staging)
    return final_path


def build_installer(output_dir: Path | None = None) -> Path:
    """Build the installer for the current platform.

    Args:
        output_dir: Directory to place the final installer. Defaults to ./release/

    Returns:
        Path to the built installer.
    """
    # Ensure PyInstaller is available (installs automatically if missing)
    pyinstaller_cmd = ensure_pyinstaller()

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

    # Build with PyInstaller using a wrapper entry point that avoids
    # relative import failures (PyInstaller runs scripts without a parent
    # package, so from .util... would fail in chad/__main__.py).
    data_sep = ";" if platform_name == "windows" else ":"
    pyinstaller_args = [
        *pyinstaller_cmd,
        "--name", "chad",
        "--onedir",  # Create a directory with executable + dependencies
        "--noconfirm",  # Don't ask for confirmation
        # Use the wrapper entry point with absolute imports
        str(PYINSTALLER_ENTRY),
        # Tell PyInstaller where to find the chad package
        "--paths", str(ROOT / "src"),
        # Collect all chad submodules (many are imported conditionally)
        "--collect-submodules", "chad",
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
        "--add-data", f"{ROOT / 'src' / 'chad' / 'ui_dist'}{data_sep}chad/ui_dist",
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

    # PyInstaller --onedir produces an executable inside a folder that carries
    # the bundled Python runtime. Packaging only the executable drops the
    # _internal/ directory (which causes the macOS failure reported in 0.11.0),
    # so we always package the containing directory when it exists.
    build_root = artifact if artifact.is_dir() else artifact.parent

    # Copy to release directory with proper naming
    installer_name = get_installer_filename(version)
    final_path = output_dir / installer_name

    if platform_name == "linux":
        # Debian packaging expects the whole onedir tree so we pass the root dir
        final_path = _build_linux_deb(build_root, version, output_dir)
    else:
        # Always archive the onedir tree so the bundled Python runtime (_internal/)
        # ships with the executable. This fixes the macOS launcher failure where
        # only the binary was copied.
        archive_format = "zip" if platform_name == "windows" else "gztar"
        archive_ext = ".zip" if platform_name == "windows" else ".tar.gz"
        shutil.make_archive(
            str(output_dir / f"chad-{version}-{platform_name}"),
            archive_format,
            build_root.parent,
            build_root.name,
        )
        final_path = output_dir / f"chad-{version}-{platform_name}{archive_ext}"

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

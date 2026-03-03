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

MIN_BUILD_PYTHON = (3, 12)
REEXEC_ENV_KEY = "CHAD_BUILD_RELEASE_REEXEC"


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
        return f"chad-{version}-{platform}.zip"
    if platform == "linux":
        return f"chad-{version}-{platform}.deb"
    if platform == "macos":
        return f"chad-{version}-{platform}.pkg"
    return f"chad-{version}-{platform}"


def _python_supports_minimum_version(python_executable: Path) -> bool:
    """Return whether the given Python executable satisfies MIN_BUILD_PYTHON."""
    cmd = [
        str(python_executable),
        "-c",
        (
            "import sys;"
            "raise SystemExit(0 if sys.version_info[:2] >= "
            f"{MIN_BUILD_PYTHON!r} else 1)"
        ),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def _find_project_python() -> Path | None:
    """Find a local project virtualenv python that meets MIN_BUILD_PYTHON."""
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / "venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists() and _python_supports_minimum_version(candidate):
            return candidate
    return None


def _require_min_python(*, allow_reexec: bool = False) -> None:
    """Ensure the build is run with a new enough Python.

    PyInstaller bundles the interpreter that runs this script. If someone runs
    build_release with an older system Python (e.g., 3.9 on macOS), the
    resulting binary will crash at startup because the codebase uses
    3.10+ syntax. Bail out early with a clear message instead of producing a
    broken artifact.
    """

    if sys.version_info >= MIN_BUILD_PYTHON:
        return

    ver = ".".join(map(str, MIN_BUILD_PYTHON))
    current_ver = ".".join(map(str, sys.version_info[:3]))

    if allow_reexec and os.environ.get(REEXEC_ENV_KEY) != "1":
        project_python = _find_project_python()
        if project_python is not None and str(project_python) != sys.executable:
            print(f"Re-launching build with {project_python} ...")
            env = os.environ.copy()
            env[REEXEC_ENV_KEY] = "1"
            os.execve(
                str(project_python),
                [str(project_python), str(Path(__file__)), *sys.argv[1:]],
                env,
            )
            return

    raise SystemExit(
        f"Python {ver}+ required to build Chad. "
        f"Current interpreter: {sys.executable} ({current_ver}). "
        "Create a 3.12+ virtualenv and re-run scripts/build_release.py."
    )


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


def _build_macos_pkg(build_root: Path, version: str, output_dir: Path) -> Path:
    """Create a macOS .pkg installer with a PATH-visible `chad` command."""
    pkgbuild = shutil.which("pkgbuild")
    if pkgbuild is None:
        raise SystemExit(
            "pkgbuild is required to build a macOS .pkg installer. "
            "It should be available by default on macOS."
        )

    staging = output_dir / f"chad-{version}-pkg"
    payload_root = staging / "payload"
    if staging.exists():
        shutil.rmtree(staging)

    app_dst = payload_root / "Applications" / "chad"
    app_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(build_root, app_dst)

    bin_dir = payload_root / "usr" / "local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    symlink_path = bin_dir / "chad"
    if symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(Path("../../../Applications/chad/chad"))

    pkg_path = output_dir / get_installer_filename(version)
    if pkg_path.exists():
        pkg_path.unlink()

    cmd = [
        pkgbuild,
        "--root",
        str(payload_root),
        "--identifier",
        "ai.chad.cli",
        "--version",
        version,
        "--install-location",
        "/",
        str(pkg_path),
    ]
    run_command(cmd)
    shutil.rmtree(staging)
    return pkg_path


def build_installer(output_dir: Path | None = None) -> Path:
    """Build the installer for the current platform.

    Args:
        output_dir: Directory to place the final installer. Defaults to ./release/

    Returns:
        Path to the built installer.
    """
    _require_min_python()

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

    if platform_name == "linux":
        # Debian packaging expects the whole onedir tree so we pass the root dir
        final_path = _build_linux_deb(build_root, version, output_dir)
    elif platform_name == "macos":
        final_path = _build_macos_pkg(build_root, version, output_dir)
    else:
        # Windows: zip the onedir bundle so the runtime stays alongside chad.exe.
        archive_base = output_dir / f"chad-{version}-{platform_name}"
        shutil.make_archive(
            str(archive_base),
            "zip",
            build_root.parent,
            build_root.name,
        )
        final_path = archive_base.with_suffix(".zip")

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
        _require_min_python(allow_reexec=True)
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

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
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BUILD_UI_SCRIPT = ROOT / "scripts" / "build_ui.py"
PYINSTALLER_ENTRY = ROOT / "scripts" / "pyinstaller_entry.py"
PYINSTALLER_HOOKS_DIR = ROOT / "scripts" / "pyinstaller_hooks"
WIX_LICENSE_RTF = ROOT / "scripts" / "wix" / "license.rtf"
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
        return f"chad-{version}-{platform}.msi"
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


def _ensure_wix_tools() -> dict[str, Path]:
    """Locate WiX Toolset binaries on Windows (install via choco/winget if missing)."""
    tools: dict[str, Path] = {}
    default_dirs = [
        Path("C:/Program Files (x86)/WiX Toolset v3.14/bin"),
        Path("C:/Program Files (x86)/WiX Toolset v3.12/bin"),
        Path("C:/Program Files (x86)/WiX Toolset v3.11/bin"),
        Path("C:/Program Files (x86)/WiX Toolset v3.10/bin"),
        Path("C:/Program Files/WiX Toolset v3.14/bin"),
        Path("C:/Program Files/WiX Toolset v3.12/bin"),
        Path("C:/Program Files/WiX Toolset v3.11/bin"),
        Path("C:/Program Files/WiX Toolset v3.10/bin"),
        Path("C:/Program Files/Windows Installer XML v3.11/bin"),
    ]
    names = ["heat.exe", "candle.exe", "light.exe"]
    winget_id = "WiXToolset.WiXToolset"

    def _locate(name: str) -> Path | None:
        found = shutil.which(name)
        if found:
            return Path(found)
        for base in default_dirs:
            candidate = base / name
            if candidate.exists():
                return candidate
        return None

    for name in names:
        path = _locate(name)
        if path:
            tools[name] = path

    missing = [n for n in names if n not in tools]
    if missing:
        choco = shutil.which("choco")
        if choco:
            print("WiX not found; installing via choco ...")
            run_command([choco, "install", "wixtoolset", "-y", "--no-progress"])
        else:
            winget = shutil.which("winget")
            if winget:
                print("WiX not found; installing via winget ...")
                subprocess.run([winget, "install", "--id", winget_id, "-e"], check=False)
            else:
                raise SystemExit(
                    "WiX tools not found (heat.exe, candle.exe, light.exe) and no package manager is available. "
                    "Install WiX v3.11+ or add it to PATH."
                )
        for name in missing:
            path = _locate(name)
            if path:
                tools[name] = path

    still_missing = [n for n in names if n not in tools]
    if still_missing:
        raise SystemExit(f"WiX tools missing after install: {', '.join(still_missing)}")
    return tools


def _ensure_wix_file_language(harvest_wxs: Path) -> None:
    """Add Language="0" to versioned File entries missing a Language value."""
    ET.register_namespace("", "http://schemas.microsoft.com/wix/2006/wi")
    tree = ET.parse(harvest_wxs)
    root = tree.getroot()
    ns = {"wix": "http://schemas.microsoft.com/wix/2006/wi"}
    updated = False

    for file_elem in root.findall(".//wix:File", ns):
        if "Version" in file_elem.attrib and "Language" not in file_elem.attrib:
            file_elem.set("Language", "0")
            updated = True

    if updated:
        tree.write(harvest_wxs, encoding="utf-8", xml_declaration=True)


def _ensure_build_dependencies() -> None:
    """Ensure required runtime deps are installed before PyInstaller runs."""
    required = ["pyte", "websockets"]
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if not missing:
        return

    print(f"Installing build dependencies: {', '.join(missing)}")
    run_command([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])

    still_missing = [name for name in required if importlib.util.find_spec(name) is None]
    if still_missing:
        raise SystemExit(
            "Missing required dependencies after install: "
            f"{', '.join(still_missing)}"
        )


def _build_windows_msi(build_root: Path, version: str, output_dir: Path) -> Path:
    """Create a Windows MSI installer using WiX harvest + candle + light."""
    tools = _ensure_wix_tools()

    if not WIX_LICENSE_RTF.exists():
        raise SystemExit(f"Missing WiX license file at {WIX_LICENSE_RTF}")

    staging = output_dir / f"chad-{version}-msi"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    harvest_wxs = staging / "harvest.wxs"
    product_wxs = staging / "product.wxs"
    obj_dir = staging / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)

    run_command([
        str(tools["heat.exe"]),
        "dir",
        str(build_root),
        "-o",
        str(harvest_wxs),
        "-cg",
        "ChadComponents",
        "-dr",
        "INSTALLDIR",
        "-srd",
        "-gg",
        "-sreg",
        "-var",
        "var.SourceDir",
    ])
    _ensure_wix_file_language(harvest_wxs)

    product_wxs.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="Chad" Manufacturer="Chad AI" Version="{version}" Language="1033" UpgradeCode="6f0d5c39-5f9f-4b64-8cb6-9a7f3e9c5a9c">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perMachine"/>
    <MediaTemplate/>
    <MajorUpgrade AllowDowngrades="no" DowngradeErrorMessage="A newer version of Chad is already installed."/>
    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFilesFolder">
        <Directory Id="INSTALLDIR" Name="Chad">
          <Component Id="PathComponent" Guid="*">
            <CreateFolder/>
            <Environment Id="ChadPath" Name="PATH" Action="set" Part="first" Permanent="no" System="yes" Value="[INSTALLDIR]"/>
            <RegistryValue Root="HKCU" Key="Software\\Chad" Name="PathComponent" Type="integer" Value="1" KeyPath="yes"/>
          </Component>
        </Directory>
      </Directory>
      <Directory Id="ProgramMenuFolder">
        <Directory Id="ChadProgramMenu" Name="Chad">
          <Component Id="MenuComponent" Guid="*">
            <Shortcut Id="ChadShortcut" Directory="ChadProgramMenu" Name="Chad CLI" Target="[SystemFolder]cmd.exe" Arguments='/k "[INSTALLDIR]chad.exe"' WorkingDirectory="INSTALLDIR" />
            <RemoveFolder Id="RemoveChadMenu" Directory="ChadProgramMenu" On="uninstall"/>
            <RegistryValue Root="HKCU" Key="Software\\Chad" Name="MenuComponent" Type="integer" Value="1" KeyPath="yes"/>
          </Component>
        </Directory>
      </Directory>
    </Directory>
    <Feature Id="ChadFeature" Title="Chad" Level="1">
      <ComponentGroupRef Id="ChadComponents"/>
      <ComponentRef Id="PathComponent"/>
      <ComponentRef Id="MenuComponent"/>
    </Feature>
    <UIRef Id="WixUI_InstallDir"/>
    <UIRef Id="WixUI_ErrorProgressText"/>
    <Property Id="WIXUI_INSTALLDIR" Value="INSTALLDIR"/>
    <WixVariable Id="WixUILicenseRtf" Value="{WIX_LICENSE_RTF.as_posix()}"/>
  </Product>
</Wix>
""",
        encoding="utf-8",
    )

    harvest_obj = obj_dir / "harvest.wixobj"
    product_obj = obj_dir / "product.wixobj"

    run_command([
        str(tools["candle.exe"]),
        "-dSourceDir=" + str(build_root),
        "-out",
        str(harvest_obj),
        str(harvest_wxs),
    ])
    run_command([
        str(tools["candle.exe"]),
        "-out",
        str(product_obj),
        str(product_wxs),
    ])

    final_path = output_dir / get_installer_filename(version)
    if final_path.exists():
        final_path.unlink()

    run_command([
        str(tools["light.exe"]),
        "-ext",
        "WixUIExtension",
        "-out",
        str(final_path),
        str(product_obj),
        str(harvest_obj),
    ])

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

    _ensure_build_dependencies()

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
        "--additional-hooks-dir", str(PYINSTALLER_HOOKS_DIR),
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
        "--collect-all", "pyte",
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
        final_path = _build_linux_deb(build_root, version, output_dir)
    elif platform_name == "macos":
        final_path = _build_macos_pkg(build_root, version, output_dir)
    else:
        final_path = _build_windows_msi(build_root, version, output_dir)

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

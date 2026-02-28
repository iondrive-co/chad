#!/usr/bin/env python3
"""Build the React UI and bundle it into the Python package.

Builds ui/ with Vite and copies the output into src/chad/ui_dist/ so that
setuptools includes it in the wheel.  Run this before ``python -m build``.

Usage:
    python scripts/build_ui.py          # build + copy
    python scripts/build_ui.py --skip-build  # copy existing ui/dist/ without rebuilding
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
UI_DIST = UI_DIR / "dist"
PACKAGE_DIST = ROOT / "src" / "chad" / "ui_dist"


def build_ui() -> None:
    """Run the Vite production build inside ui/."""
    if not (UI_DIR / "package.json").exists():
        raise SystemExit(f"ui/package.json not found at {UI_DIR}")

    # Ensure dependencies are installed
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is not installed. Install Node.js to build the React UI.")

    print("Installing UI dependencies ...")
    subprocess.run([npm, "install"], cwd=str(UI_DIR), check=True)

    print("Building React UI ...")
    subprocess.run([npm, "run", "build"], cwd=str(UI_DIR), check=True)

    if not UI_DIST.is_dir():
        raise SystemExit(f"Build succeeded but {UI_DIST} was not created.")


def copy_to_package() -> None:
    """Copy ui/dist/ into src/chad/ui_dist/ for packaging."""
    if not UI_DIST.is_dir():
        raise SystemExit(
            f"{UI_DIST} does not exist. Run the build first (drop --skip-build)."
        )

    # Wipe previous bundled copy
    if PACKAGE_DIST.exists():
        shutil.rmtree(PACKAGE_DIST)

    shutil.copytree(UI_DIST, PACKAGE_DIST)

    # Add __init__.py so importlib.resources can find it as a package
    (PACKAGE_DIST / "__init__.py").touch()

    # Report what was copied
    files = list(PACKAGE_DIST.rglob("*"))
    file_count = sum(1 for f in files if f.is_file())
    print(f"Copied {file_count} files to {PACKAGE_DIST.relative_to(ROOT)}")


def build_portable() -> None:
    """Build a self-contained single HTML file for portable use.

    Uses vite-plugin-singlefile to inline all JS and CSS into index.html.
    This works when:
    - Opened directly as a file:// URL in any browser
    - Deployed on Cloudflare Pages (at root or any subpath)
    - Served by any static file server
    """
    npx = shutil.which("npx")
    print("Building portable single-file UI ...")
    subprocess.run(
        [npx, "vite", "build", "--config", "vite.portable.config.ts"],
        cwd=str(UI_DIR), check=True,
    )

    portable_dist = UI_DIR / "dist-portable"
    index = portable_dist / "index.html"
    size_kb = index.stat().st_size / 1024
    print(f"Portable UI built: {index} ({size_kb:.0f} KB)")
    print(f"\nOpen {index} in a browser, or deploy to Cloudflare Pages")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip npm build and just copy existing ui/dist/",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Also build a portable copy with /chad/ base path for static hosting",
    )
    args = parser.parse_args()

    if not args.skip_build:
        build_ui()

    copy_to_package()
    print("Done. The wheel will now include the React UI.")

    if args.portable:
        build_portable()


if __name__ == "__main__":
    main()

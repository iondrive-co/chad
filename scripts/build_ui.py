#!/usr/bin/env python3
"""Build the React UI and bundle it into the Python package.

Builds ui/ with Vite (portable single-file mode) and copies the output into
src/chad/ui_dist/ so that setuptools includes it in the wheel.  Run this
before ``python -m build``.

Usage:
    python scripts/build_ui.py              # build + copy
    python scripts/build_ui.py --skip-build # copy existing build without rebuilding
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
PORTABLE_DIST = UI_DIR / "dist-portable"
PACKAGE_DIST = ROOT / "src" / "chad" / "ui_dist"


def build_ui() -> None:
    """Run the Vite portable (single-file) production build inside ui/."""
    if not (UI_DIR / "package.json").exists():
        raise SystemExit(f"ui/package.json not found at {UI_DIR}")

    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is not installed. Install Node.js to build the React UI.")

    print("Installing UI dependencies ...")
    subprocess.run([npm, "install"], cwd=str(UI_DIR), check=True)

    npx = shutil.which("npx")
    print("Building portable single-file UI ...")
    subprocess.run(
        [npx, "vite", "build", "--config", "vite.portable.config.ts"],
        cwd=str(UI_DIR), check=True,
    )

    if not PORTABLE_DIST.is_dir():
        raise SystemExit(f"Build succeeded but {PORTABLE_DIST} was not created.")


def copy_to_package() -> None:
    """Copy the portable build into src/chad/ui_dist/ for packaging."""
    if not PORTABLE_DIST.is_dir():
        raise SystemExit(
            f"{PORTABLE_DIST} does not exist. Run the build first (drop --skip-build)."
        )

    # Wipe previous bundled copy
    if PACKAGE_DIST.exists():
        shutil.rmtree(PACKAGE_DIST)

    PACKAGE_DIST.mkdir(parents=True)

    # Copy just the single index.html
    shutil.copy2(PORTABLE_DIST / "index.html", PACKAGE_DIST / "index.html")

    # Add __init__.py so importlib.resources can find it as a package
    (PACKAGE_DIST / "__init__.py").touch()

    size_kb = (PACKAGE_DIST / "index.html").stat().st_size / 1024
    print(f"Copied index.html ({size_kb:.0f} KB) to {PACKAGE_DIST.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip npm build and just copy existing build",
    )
    args = parser.parse_args()

    if not args.skip_build:
        build_ui()

    copy_to_package()
    print("Done. The wheel will now include the React UI.")


if __name__ == "__main__":
    main()

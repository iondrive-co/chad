#!/usr/bin/env python3
"""Build the React UI and bundle it into the Python package.

Builds ``ui/dist`` and copies the packaged assets into ``src/chad/ui_dist`` so
that setuptools includes the same asset layout the API serves in development
and tests. Run this before ``python -m build``.

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
UI_DIST = UI_DIR / "dist"
PACKAGE_DIST = ROOT / "src" / "chad" / "ui_dist"


def build_ui() -> None:
    """Run the production build inside ui/."""
    if not (UI_DIR / "package.json").exists():
        raise SystemExit(f"ui/package.json not found at {UI_DIR}")

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
    """Copy the production build into src/chad/ui_dist/ for packaging."""
    if not UI_DIST.is_dir():
        raise SystemExit(
            f"{UI_DIST} does not exist. Run the build first (drop --skip-build)."
        )

    PACKAGE_DIST.mkdir(parents=True, exist_ok=True)
    (PACKAGE_DIST / "__init__.py").touch()

    for item in ("assets", "index.html"):
        dest = PACKAGE_DIST / item
        src = UI_DIST / item
        if not src.exists():
            continue
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)

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

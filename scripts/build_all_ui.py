#!/usr/bin/env python3
"""Build all UI assets (client lib, React UI, portable UI) and sync to package.

This is the one-stop script for rebuilding everything after UI/client changes.
It replaces the manual multi-step process of:
  1. cd client && npm run build
  2. cd ui && npm run build
  3. cd ui && npx vite build --config vite.portable.config.ts
  4. rm -rf src/chad/ui_dist/assets src/chad/ui_dist/index.html
  5. cp -r ui/dist/* src/chad/ui_dist/

Usage:
    python scripts/build_all_ui.py          # full build
    python scripts/build_all_ui.py --quick  # skip client lib, just rebuild ui + sync
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = ROOT / "client"
UI_DIR = ROOT / "ui"
UI_DIST = UI_DIR / "dist"
PORTABLE_DIST = UI_DIR / "dist-portable"
PACKAGE_DIST = ROOT / "src" / "chad" / "ui_dist"


def run(cmd: list[str], cwd: Path, label: str) -> None:
    print(f"\n--- {label} ---")
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        print(f"FAILED: {label}", file=sys.stderr)
        sys.exit(1)


def find_npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        print("npm is not installed. Install Node.js first.", file=sys.stderr)
        sys.exit(1)
    return npm


def build_client(npm: str) -> None:
    run([npm, "run", "build"], CLIENT_DIR, "Building client library")


def build_ui(npm: str) -> None:
    npx = shutil.which("npx") or npm.replace("npm", "npx")
    run([npm, "run", "build"], UI_DIR, "Building React UI (ui/dist)")
    run(
        [npx, "vite", "build", "--config", "vite.portable.config.ts",
         "--outDir", "dist-portable"],
        UI_DIR,
        "Building portable UI (ui/dist-portable)",
    )


def sync_to_package() -> None:
    print("\n--- Syncing to src/chad/ui_dist/ ---")

    # Sync the regular build (with assets/) for the dev server
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

    # Verify
    html = PACKAGE_DIST / "index.html"
    if html.exists():
        size_kb = html.stat().st_size / 1024
        print(f"  index.html: {size_kb:.0f} KB")

    assets = PACKAGE_DIST / "assets"
    if assets.is_dir():
        n = len(list(assets.iterdir()))
        print(f"  assets/: {n} files")

    print("Sync complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true",
                        help="Skip client lib build, just rebuild UI + sync")
    args = parser.parse_args()

    npm = find_npm()

    if not args.quick:
        build_client(npm)

    build_ui(npm)
    sync_to_package()

    print("\nAll UI assets built and synced.")


if __name__ == "__main__":
    main()

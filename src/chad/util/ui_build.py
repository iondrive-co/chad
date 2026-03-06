"""
Auto-build helpers for the React UI and client SDK.

Intended for local development/launchers (e.g., PyCharm run configs) so you
don't have to remember to rebuild the UI bundles and sync them into
src/chad/ui_dist before launching.
"""

from __future__ import annotations

import shutil
from subprocess import CalledProcessError, run
from pathlib import Path
from typing import Iterable

from .config import resolve_project_root


def _latest_mtime(paths: Iterable[Path]) -> float:
    latest = 0.0
    for p in paths:
        if p.is_file():
            latest = max(latest, p.stat().st_mtime)
        elif p.is_dir():
            for sub in p.rglob("*"):
                if sub.is_file():
                    latest = max(latest, sub.stat().st_mtime)
    return latest


def _is_stale(src: Path, built: Path) -> bool:
    if not built.exists():
        return True
    return _latest_mtime([src]) > built.stat().st_mtime


def _safe_run(cmd: list[str], cwd: Path) -> None:
    run(cmd, cwd=cwd, check=True)


def ensure_ui_built(force: bool = False) -> None:
    """Rebuild client + UI if source is newer than dist, then sync to ui_dist.

    Safe to call on every launch; it skips work when bundles are fresh.
    """
    project_root, _reason = resolve_project_root()
    client_src = project_root / "client" / "src"
    client_dist = project_root / "client" / "dist" / "index.js"
    ui_src = project_root / "ui" / "src"
    ui_dist = project_root / "ui" / "dist" / "index.html"
    packaged_ui = project_root / "src" / "chad" / "ui_dist"

    try:
        if force or _is_stale(client_src, client_dist):
            print("↻ Rebuilding chad-client…")
            _safe_run(["npm", "run", "build"], cwd=client_src.parent)

        if force or _is_stale(ui_src, ui_dist):
            print("↻ Rebuilding React UI…")
            _safe_run(["npm", "run", "build"], cwd=ui_src.parent)

        # Always sync dist → packaged assets if dist exists
        if ui_dist.exists():
            print("↻ Syncing ui/dist → src/chad/ui_dist …")
            # Clean packaged directory except for __init__.py / __pycache__
            for child in packaged_ui.iterdir():
                if child.name in {"__init__.py", "__pycache__"}:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            shutil.copytree(ui_dist.parent, packaged_ui, dirs_exist_ok=True)
    except FileNotFoundError as exc:
        print(f"UI autobuild skipped (missing tool): {exc}")
    except CalledProcessError as exc:
        print(f"UI autobuild failed: {exc}")

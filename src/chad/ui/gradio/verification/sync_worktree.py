from __future__ import annotations

"""Utilities to sync a worktree with the MCP server repo path."""

import argparse
import filecmp
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

EXCLUDES = {".git", ".chad-worktrees", "__pycache__", ".pytest_cache", ".mypy_cache", "venv", ".venv"}


@dataclass
class SyncResult:
    method: str
    source: Path
    dest: Path
    copied: int = 0
    deleted: int = 0
    dry_run: bool = False
    delete: bool = False
    command: list[str] | None = None


def _should_skip(path: Path) -> bool:
    return any(part in EXCLUDES for part in path.parts)


def _rsync_available() -> bool:
    return shutil.which("rsync") is not None


def _sync_with_rsync(source: Path, dest: Path, delete: bool, dry_run: bool) -> SyncResult:
    args = ["rsync", "-az", "--info=stats1"]
    for item in EXCLUDES:
        args += ["--exclude", item]
    if delete:
        args.append("--delete")
    if dry_run:
        args.append("--dry-run")
    args += [f"{source}/", f"{dest}/"]
    subprocess.run(args, check=True)
    return SyncResult(
        method="rsync",
        source=source,
        dest=dest,
        dry_run=dry_run,
        delete=delete,
        command=args,
    )


def _copy_if_changed(src: Path, dest: Path, dry_run: bool) -> bool:
    if dest.exists() and filecmp.cmp(src, dest, shallow=False):
        return False
    if dry_run:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _sync_with_python(source: Path, dest: Path, delete: bool, dry_run: bool) -> SyncResult:
    copied = 0
    deleted = 0

    for root, dirs, files in os.walk(source):
        rel_root = Path(root).relative_to(source)
        dirs[:] = [d for d in dirs if not _should_skip(rel_root / d)]
        for file_name in files:
            rel_file = rel_root / file_name
            if _should_skip(rel_file):
                continue
            src_file = Path(root) / file_name
            dest_file = dest / rel_file
            if _copy_if_changed(src_file, dest_file, dry_run):
                copied += 1

    if delete:
        for root, dirs, files in os.walk(dest):
            rel_root = Path(root).relative_to(dest)
            dirs[:] = [d for d in dirs if not _should_skip(rel_root / d)]
            for file_name in files:
                rel_file = rel_root / file_name
                if _should_skip(rel_file):
                    continue
                src_file = source / rel_file
                dest_file = Path(root) / file_name
                if not src_file.exists():
                    deleted += 1
                    if not dry_run:
                        dest_file.unlink()

    return SyncResult(
        method="python",
        source=source,
        dest=dest,
        copied=copied,
        deleted=deleted,
        dry_run=dry_run,
        delete=delete,
    )


def sync_paths(
    source: Path,
    dest: Path,
    delete: bool = False,
    dry_run: bool = False,
    prefer_rsync: bool = True,
) -> SyncResult:
    source = source.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not dest.exists():
        dest.mkdir(parents=True, exist_ok=True)

    if prefer_rsync and _rsync_available():
        return _sync_with_rsync(source, dest, delete, dry_run)
    return _sync_with_python(source, dest, delete, dry_run)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync a worktree into another repo path (e.g., MCP server root).")
    parser.add_argument("--source", required=True, help="Source path (e.g., current worktree)")
    parser.add_argument("--dest", required=True, help="Destination path (e.g., MCP server repo)")
    parser.add_argument("--delete", action="store_true", help="Delete files in dest that no longer exist in source")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without copying/deleting")
    parser.add_argument("--no-rsync", action="store_true", help="Force pure Python copy instead of rsync")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> None:
    args = _parse_args(argv)
    result = sync_paths(
        Path(args.source),
        Path(args.dest),
        delete=args.delete,
        dry_run=args.dry_run,
        prefer_rsync=not args.no_rsync,
    )
    summary = (
        f"[{result.method}] {result.source} -> {result.dest} "
        f"(copied={result.copied}, deleted={result.deleted}, dry_run={result.dry_run}, delete={result.delete})"
    )
    print(summary)
    raise SystemExit(0)


if __name__ == "__main__":
    main()

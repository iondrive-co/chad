"""Cleanup utilities for Chad temporary files, logs, worktrees, and screenshots."""

import os
import shutil
import tempfile
import time
from pathlib import Path


def _is_older_than_days(path: Path, days: int) -> bool:
    """Check if a path's modification time is older than N days."""
    try:
        mtime = path.stat().st_mtime
        age_seconds = time.time() - mtime
        age_days = age_seconds / (24 * 60 * 60)
        return age_days > days
    except OSError:
        return False


def cleanup_old_worktrees(project_path: Path, days: int) -> list[str]:
    """Remove worktrees older than N days.

    Args:
        project_path: Path to the project root
        days: Number of days after which to clean up

    Returns:
        List of cleaned up task IDs
    """
    from chad.util.git_worktree import GitWorktreeManager

    worktree_base = project_path / ".chad-worktrees"
    if not worktree_base.exists():
        return []

    cleaned = []
    manager = GitWorktreeManager(project_path)

    for worktree_dir in worktree_base.iterdir():
        if not worktree_dir.is_dir():
            continue
        task_id = worktree_dir.name
        if _is_older_than_days(worktree_dir, days):
            if manager.delete_worktree(task_id):
                cleaned.append(task_id)

    # Remove .chad-worktrees dir if empty
    try:
        if worktree_base.exists() and not any(worktree_base.iterdir()):
            worktree_base.rmdir()
    except OSError:
        pass

    return cleaned


def cleanup_old_logs(days: int) -> list[str]:
    """Remove session logs older than N days.

    Args:
        days: Number of days after which to clean up

    Returns:
        List of cleaned up log filenames
    """
    log_dir = Path(os.environ.get("CHAD_SESSION_LOG_DIR", "")) or (
        Path(tempfile.gettempdir()) / "chad"
    )
    if not log_dir.exists():
        return []

    cleaned = []
    for log_file in log_dir.glob("chad_session_*.json"):
        if _is_older_than_days(log_file, days):
            try:
                log_file.unlink()
                cleaned.append(log_file.name)
            except OSError:
                pass

    return cleaned


def cleanup_old_screenshots(days: int) -> list[str]:
    """Remove old screenshot temp directories.

    Chad creates temp directories with prefixes:
    - chad_visual_ (screenshot artifacts)
    - chad_ui_runner_ (UI test environments)

    Args:
        days: Number of days after which to clean up

    Returns:
        List of cleaned up directory names
    """
    temp_dir = Path(tempfile.gettempdir())
    cleaned = []

    prefixes = ["chad_visual_", "chad_ui_runner_"]

    for item in temp_dir.iterdir():
        if not item.is_dir():
            continue
        if any(item.name.startswith(prefix) for prefix in prefixes):
            if _is_older_than_days(item, days):
                try:
                    shutil.rmtree(item)
                    cleaned.append(item.name)
                except OSError:
                    pass

    return cleaned


def cleanup_temp_files() -> list[str]:
    """Remove Chad temporary files from system temp directory.

    Called on shutdown to clean up:
    - PID files (chad_processes.pid, chad_test_servers.pids)
    - Lock files (chad_processes.pid.lock)
    - Empty chad log directory

    Returns:
        List of cleaned up file/directory names
    """
    temp_dir = Path(tempfile.gettempdir())
    cleaned = []

    # PID and lock files
    temp_files = [
        "chad_processes.pid",
        "chad_processes.pid.lock",
        "chad_test_servers.pids",
    ]

    for filename in temp_files:
        filepath = temp_dir / filename
        if filepath.exists():
            try:
                filepath.unlink()
                cleaned.append(filename)
            except OSError:
                pass

    # Remove chad log directory if empty
    chad_dir = temp_dir / "chad"
    if chad_dir.exists():
        try:
            if not any(chad_dir.iterdir()):
                chad_dir.rmdir()
                cleaned.append("chad/")
        except OSError:
            pass

    return cleaned


def cleanup_on_startup(project_path: Path, days: int) -> dict[str, list[str]]:
    """Run all cleanup tasks on startup.

    Args:
        project_path: Path to the project root
        days: Number of days after which to clean up old files

    Returns:
        Dict mapping cleanup type to list of cleaned items
    """
    results = {}

    worktrees = cleanup_old_worktrees(project_path, days)
    if worktrees:
        results["worktrees"] = worktrees

    logs = cleanup_old_logs(days)
    if logs:
        results["logs"] = logs

    screenshots = cleanup_old_screenshots(days)
    if screenshots:
        results["screenshots"] = screenshots

    return results


def cleanup_on_shutdown() -> dict[str, list[str]]:
    """Run cleanup tasks on shutdown.

    Returns:
        Dict mapping cleanup type to list of cleaned items
    """
    results = {}

    temp_files = cleanup_temp_files()
    if temp_files:
        results["temp_files"] = temp_files

    return results

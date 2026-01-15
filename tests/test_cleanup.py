"""Tests for cleanup module."""

import os
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest

from chad.cleanup import (
    _is_older_than_days,
    cleanup_old_worktrees,
    cleanup_old_logs,
    cleanup_old_screenshots,
    cleanup_temp_files,
    cleanup_on_startup,
    cleanup_on_shutdown,
)


class TestIsOlderThanDays:
    """Tests for _is_older_than_days helper."""

    def test_new_file_is_not_old(self, tmp_path):
        """A freshly created file should not be older than 1 day."""
        test_file = tmp_path / "new_file.txt"
        test_file.write_text("content")
        assert _is_older_than_days(test_file, 1) is False

    def test_old_file_is_detected(self, tmp_path):
        """A file with old mtime should be detected as old."""
        test_file = tmp_path / "old_file.txt"
        test_file.write_text("content")
        # Set mtime to 5 days ago
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(test_file, (old_time, old_time))
        assert _is_older_than_days(test_file, 3) is True
        assert _is_older_than_days(test_file, 7) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        """A nonexistent path should return False."""
        nonexistent = tmp_path / "does_not_exist"
        assert _is_older_than_days(nonexistent, 1) is False


class TestCleanupOldWorktrees:
    """Tests for cleanup_old_worktrees."""

    def test_no_worktree_dir(self, tmp_path):
        """Should return empty list when worktree dir doesn't exist."""
        result = cleanup_old_worktrees(tmp_path, 3)
        assert result == []

    def test_empty_worktree_dir(self, tmp_path):
        """Should return empty list when worktree dir is empty."""
        worktree_base = tmp_path / ".chad-worktrees"
        worktree_base.mkdir()
        result = cleanup_old_worktrees(tmp_path, 3)
        assert result == []

    def test_cleans_old_worktrees(self, tmp_path):
        """Should clean worktrees older than N days."""
        worktree_base = tmp_path / ".chad-worktrees"
        worktree_base.mkdir()

        # Create mock old worktree
        old_worktree = worktree_base / "abc12345"
        old_worktree.mkdir()
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(old_worktree, (old_time, old_time))

        # Create mock new worktree
        new_worktree = worktree_base / "def67890"
        new_worktree.mkdir()

        with patch("chad.git_worktree.GitWorktreeManager") as mock_manager_cls:
            mock_manager = MagicMock()
            mock_manager.delete_worktree.return_value = True
            mock_manager_cls.return_value = mock_manager

            result = cleanup_old_worktrees(tmp_path, 3)

            # Only old worktree should be cleaned
            assert "abc12345" in result
            assert "def67890" not in result
            mock_manager.delete_worktree.assert_called_once_with("abc12345")


class TestCleanupOldLogs:
    """Tests for cleanup_old_logs."""

    def test_no_log_dir(self, tmp_path, monkeypatch):
        """Should return empty list when log dir doesn't exist."""
        monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(tmp_path / "nonexistent"))
        result = cleanup_old_logs(3)
        assert result == []

    def test_cleans_old_logs(self, tmp_path, monkeypatch):
        """Should clean logs older than N days."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))

        # Create old log
        old_log = log_dir / "chad_session_20200101_120000_000000.json"
        old_log.write_text("{}")
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(old_log, (old_time, old_time))

        # Create new log
        new_log = log_dir / "chad_session_20991231_235959_999999.json"
        new_log.write_text("{}")

        result = cleanup_old_logs(3)

        assert old_log.name in result
        assert new_log.name not in result
        assert not old_log.exists()
        assert new_log.exists()

    def test_ignores_non_session_files(self, tmp_path, monkeypatch):
        """Should only clean chad_session_*.json files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))

        # Create old non-session file
        other_file = log_dir / "other_file.json"
        other_file.write_text("{}")
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(other_file, (old_time, old_time))

        result = cleanup_old_logs(3)

        assert result == []
        assert other_file.exists()


class TestCleanupOldScreenshots:
    """Tests for cleanup_old_screenshots."""

    def test_cleans_old_screenshot_dirs(self, tmp_path, monkeypatch):
        """Should clean old screenshot temp directories."""
        # Patch tempfile.gettempdir to use our temp path
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Create old screenshot dirs
        old_visual = tmp_path / "chad_visual_abc123"
        old_visual.mkdir()
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(old_visual, (old_time, old_time))

        old_runner = tmp_path / "chad_ui_runner_def456"
        old_runner.mkdir()
        os.utime(old_runner, (old_time, old_time))

        # Create new screenshot dir
        new_visual = tmp_path / "chad_visual_ghi789"
        new_visual.mkdir()

        # Create unrelated dir (should not be cleaned)
        other_dir = tmp_path / "other_dir"
        other_dir.mkdir()
        os.utime(other_dir, (old_time, old_time))

        result = cleanup_old_screenshots(3)

        assert old_visual.name in result
        assert old_runner.name in result
        assert new_visual.name not in result
        assert other_dir.name not in result
        assert not old_visual.exists()
        assert not old_runner.exists()
        assert new_visual.exists()
        assert other_dir.exists()


class TestCleanupTempFiles:
    """Tests for cleanup_temp_files."""

    def test_cleans_pid_files(self, tmp_path, monkeypatch):
        """Should clean chad PID and lock files."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Create PID files
        pid_file = tmp_path / "chad_processes.pid"
        pid_file.write_text("12345")
        lock_file = tmp_path / "chad_processes.pid.lock"
        lock_file.write_text("")
        test_pids = tmp_path / "chad_test_servers.pids"
        test_pids.write_text("67890")

        result = cleanup_temp_files()

        assert "chad_processes.pid" in result
        assert "chad_processes.pid.lock" in result
        assert "chad_test_servers.pids" in result
        assert not pid_file.exists()
        assert not lock_file.exists()
        assert not test_pids.exists()

    def test_removes_empty_chad_dir(self, tmp_path, monkeypatch):
        """Should remove empty chad directory."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        chad_dir = tmp_path / "chad"
        chad_dir.mkdir()

        result = cleanup_temp_files()

        assert "chad/" in result
        assert not chad_dir.exists()

    def test_keeps_nonempty_chad_dir(self, tmp_path, monkeypatch):
        """Should not remove chad directory if not empty."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        chad_dir = tmp_path / "chad"
        chad_dir.mkdir()
        (chad_dir / "some_file.json").write_text("{}")

        result = cleanup_temp_files()

        assert "chad/" not in result
        assert chad_dir.exists()


class TestCleanupOnStartup:
    """Tests for cleanup_on_startup."""

    def test_runs_all_cleanups(self, tmp_path, monkeypatch):
        """Should run all cleanup functions."""
        # Setup log dir
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))

        # Create old log
        old_log = log_dir / "chad_session_20200101_120000_000000.json"
        old_log.write_text("{}")
        old_time = time.time() - (5 * 24 * 60 * 60)
        os.utime(old_log, (old_time, old_time))

        result = cleanup_on_startup(tmp_path, 3)

        assert "logs" in result
        assert old_log.name in result["logs"]

    def test_empty_result_when_nothing_to_clean(self, tmp_path, monkeypatch):
        """Should return empty dict when nothing to clean."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))

        result = cleanup_on_startup(tmp_path, 3)

        assert result == {}


class TestCleanupOnShutdown:
    """Tests for cleanup_on_shutdown."""

    def test_runs_temp_file_cleanup(self, tmp_path, monkeypatch):
        """Should run temp file cleanup."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        pid_file = tmp_path / "chad_processes.pid"
        pid_file.write_text("12345")

        result = cleanup_on_shutdown()

        assert "temp_files" in result
        assert "chad_processes.pid" in result["temp_files"]


class TestConfigManagerCleanupDays:
    """Tests for ConfigManager cleanup_days methods."""

    def test_get_cleanup_days_default(self, tmp_path):
        """Should return default of 3 when not configured."""
        from chad.config_manager import ConfigManager

        mgr = ConfigManager(tmp_path / "test.conf")
        assert mgr.get_cleanup_days() == 3

    def test_set_and_get_cleanup_days(self, tmp_path):
        """Should store and retrieve cleanup_days."""
        from chad.config_manager import ConfigManager

        mgr = ConfigManager(tmp_path / "test.conf")
        mgr.set_cleanup_days(7)
        assert mgr.get_cleanup_days() == 7

    def test_set_cleanup_days_invalid(self, tmp_path):
        """Should reject invalid cleanup_days values."""
        from chad.config_manager import ConfigManager

        mgr = ConfigManager(tmp_path / "test.conf")
        with pytest.raises(ValueError):
            mgr.set_cleanup_days(0)
        with pytest.raises(ValueError):
            mgr.set_cleanup_days(-1)

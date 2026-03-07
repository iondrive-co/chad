"""Tests for session creation behavior in SessionManager."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chad.server.services.session_manager import SessionManager


def _write_config(config_path: Path) -> str:
    """Write a minimal config file and return its contents."""
    contents = json.dumps({"accounts": {"demo": {}}})
    config_path.write_text(contents, encoding="utf-8")
    return contents


def _backup_path(config_path: Path) -> Path:
    return Path(f"{config_path}.bak")


def test_creates_backup_when_missing(tmp_path, monkeypatch):
    """A backup is created when none exists in the last 2 days."""
    config_path = tmp_path / "test_chad.conf"
    expected_contents = _write_config(config_path)
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    manager = SessionManager()
    manager.create_session(name="backup-create-test")

    backup_path = _backup_path(config_path)
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == expected_contents


def test_skips_backup_when_recent(tmp_path, monkeypatch):
    """No new backup is created if a recent one already exists."""
    config_path = tmp_path / "test_chad.conf"
    expected_contents = _write_config(config_path)
    backup_path = _backup_path(config_path)
    backup_path.write_text(expected_contents, encoding="utf-8")
    recent_time = (datetime.now(timezone.utc) - timedelta(hours=12)).timestamp()
    os.utime(backup_path, (recent_time, recent_time))
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    manager = SessionManager()
    before_mtime = backup_path.stat().st_mtime
    manager.create_session(name="backup-skip-test")
    after_mtime = backup_path.stat().st_mtime

    assert after_mtime == before_mtime
    assert backup_path.read_text(encoding="utf-8") == expected_contents


def test_refreshes_backup_when_stale(tmp_path, monkeypatch):
    """An outdated backup is refreshed during session creation."""
    config_path = tmp_path / "test_chad.conf"
    expected_contents = _write_config(config_path)
    backup_path = _backup_path(config_path)
    backup_path.write_text("outdated", encoding="utf-8")
    stale_time = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    os.utime(backup_path, (stale_time, stale_time))
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    manager = SessionManager()
    before_mtime = backup_path.stat().st_mtime
    manager.create_session(name="backup-refresh-test")
    after_mtime = backup_path.stat().st_mtime

    assert after_mtime > before_mtime
    assert backup_path.read_text(encoding="utf-8") == expected_contents


class TestLoadFromLogs:
    """Tests for session restoration from event logs."""

    def _write_log(self, log_dir, session_id, events):
        """Write a JSONL log file with the given events."""
        log_file = log_dir / f"{session_id}.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")
        return log_file

    def test_restores_completed_session(self, tmp_path, monkeypatch):
        """A completed session is restored with status 'completed'."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        self._write_log(log_dir, "abc123", [
            {"type": "session_started", "seq": 1, "ts": now,
             "task_description": "Fix the login bug",
             "project_path": "/tmp/myproject",
             "coding_account": "claude-main",
             "coding_provider": "anthropic"},
            {"type": "user_message", "seq": 2, "ts": now, "content": "Fix the login bug"},
            {"type": "session_ended", "seq": 3, "ts": now, "success": True, "reason": "completed"},
        ])

        manager = SessionManager()
        restored = manager.load_from_logs(max_age_days=7)

        assert restored == 1
        session = manager.get_session("abc123")
        assert session is not None
        assert session.task_description == "Fix the login bug"
        assert session.project_path == "/tmp/myproject"
        assert session.coding_account == "claude-main"
        assert session.provider_type == "anthropic"
        assert session.status == "completed"
        assert session.active is False

    def test_restores_interrupted_session(self, tmp_path, monkeypatch):
        """A session without session_ended is marked 'interrupted'."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        self._write_log(log_dir, "def456", [
            {"type": "session_started", "seq": 1, "ts": now,
             "task_description": "Add search feature",
             "project_path": "/tmp/myproject",
             "coding_account": "codex-main",
             "coding_provider": "openai"},
            {"type": "user_message", "seq": 2, "ts": now, "content": "Add search feature"},
        ])

        manager = SessionManager()
        restored = manager.load_from_logs(max_age_days=7)

        assert restored == 1
        session = manager.get_session("def456")
        assert session is not None
        assert session.status == "interrupted"

    def test_skips_old_logs(self, tmp_path, monkeypatch):
        """Log files older than max_age_days are skipped."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        log_file = self._write_log(log_dir, "old123", [
            {"type": "session_started", "seq": 1, "ts": now,
             "task_description": "Old task",
             "project_path": "/tmp/myproject",
             "coding_account": "claude-main",
             "coding_provider": "anthropic"},
        ])
        # Make the file old
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(log_file, (old_time, old_time))

        manager = SessionManager()
        restored = manager.load_from_logs(max_age_days=3)

        assert restored == 0
        assert manager.get_session("old123") is None

    def test_skips_already_loaded_sessions(self, tmp_path, monkeypatch):
        """Sessions already in memory are not overwritten."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))
        monkeypatch.setenv("CHAD_CONFIG", str(tmp_path / "test.conf"))

        now = datetime.now(timezone.utc).isoformat()
        self._write_log(log_dir, "exist1", [
            {"type": "session_started", "seq": 1, "ts": now,
             "task_description": "Already loaded",
             "project_path": "/tmp/myproject",
             "coding_account": "claude-main",
             "coding_provider": "anthropic"},
        ])

        manager = SessionManager()
        # Pre-create a session with same ID
        existing = manager.create_session(name="existing")
        # Manually set the ID to match (for testing)
        manager._sessions["exist1"] = existing

        restored = manager.load_from_logs(max_age_days=7)
        assert restored == 0

    def test_session_name_from_task_description(self, tmp_path, monkeypatch):
        """Session name is derived from task description, truncated to 60 chars."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        long_task = "A" * 100
        self._write_log(log_dir, "trunc1", [
            {"type": "session_started", "seq": 1, "ts": now,
             "task_description": long_task,
             "project_path": "/tmp/myproject",
             "coding_account": "claude-main",
             "coding_provider": "anthropic"},
        ])

        manager = SessionManager()
        manager.load_from_logs(max_age_days=7)

        session = manager.get_session("trunc1")
        assert session is not None
        assert len(session.name) <= 64  # 60 + "..."
        assert session.name.endswith("...")

    def test_multiple_sessions_restored(self, tmp_path, monkeypatch):
        """Multiple log files result in multiple restored sessions."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        for i in range(5):
            self._write_log(log_dir, f"sess{i}", [
                {"type": "session_started", "seq": 1, "ts": now,
                 "task_description": f"Task {i}",
                 "project_path": "/tmp/myproject",
                 "coding_account": "claude-main",
                 "coding_provider": "anthropic"},
                {"type": "session_ended", "seq": 2, "ts": now,
                 "success": True, "reason": "completed"},
            ])

        manager = SessionManager()
        restored = manager.load_from_logs(max_age_days=7)

        assert restored == 5
        sessions = manager.list_sessions()
        assert len(sessions) == 5

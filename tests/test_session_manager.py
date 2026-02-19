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

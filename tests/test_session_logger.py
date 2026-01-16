from __future__ import annotations

import os
import json
from pathlib import Path

from chad.util.session_logger import SessionLogger


def test_session_logger_respects_env_dir(tmp_path, monkeypatch):
    env_dir = tmp_path / "env_logs"
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(env_dir))
    logger = SessionLogger()

    path = logger.precreate_log()
    assert path.parent == env_dir
    assert path.exists()


def test_session_logger_prunes_old_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "prune_logs"
    log_dir.mkdir()
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))
    monkeypatch.setenv("CHAD_SESSION_LOG_MAX_FILES", "0")

    # Seed five fake logs with ascending mtimes
    paths: list[Path] = []
    for idx in range(5):
        path = log_dir / f"chad_session_000{idx}.json"
        path.write_text(json.dumps({"idx": idx}), encoding="utf-8")
        os.utime(path, (1_000 + idx, 1_000 + idx))
        paths.append(path)

    logger = SessionLogger(max_logs=2)
    remaining = sorted(p.name for p in log_dir.glob("chad_session_*.json"))

    # Two newest (idx 3 and 4) should be kept
    assert remaining == ["chad_session_0003.json", "chad_session_0004.json"]

    # Creating a new log should still respect the limit
    new_path = logger.create_log(
        task_description="sample", project_path=str(tmp_path), coding_account="acct", coding_provider="mock"
    )
    assert new_path.exists()
    remaining_after = sorted(p.name for p in log_dir.glob("chad_session_*.json"))
    assert len(remaining_after) == 2


def test_update_log_includes_timestamp(tmp_path, monkeypatch):
    env_dir = tmp_path / "env_logs"
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(env_dir))
    logger = SessionLogger()

    path = logger.precreate_log()
    logger.update_log(
        path,
        [{"role": "user", "content": "hi"}],
        streaming_history=[("AI", "chunk1"), ("AI", "chunk2")],
        verification_attempts=[{"attempt": 1, "status": "failed"}],
    )

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    assert "last_updated" in data
    assert all("timestamp" in entry for entry in data.get("streaming_history", []))

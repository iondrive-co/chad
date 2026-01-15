from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

# Force current worktree src to the front of sys.path so imports use this tree,
# not any installed or sibling worktrees.
SRC_STR = str(SRC_PATH)
sys.path = [SRC_STR] + [p for p in sys.path if p != SRC_STR]


@pytest.fixture(autouse=True)
def _isolate_session_logs(tmp_path_factory, monkeypatch):
    """Keep session logs isolated per test run."""
    log_dir = tmp_path_factory.mktemp("session_logs")
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))
    monkeypatch.setenv("CHAD_SESSION_LOG_MAX_FILES", "200")
    yield

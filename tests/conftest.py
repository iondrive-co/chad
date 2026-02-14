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


class _NoOpSlackService:
    """SlackService stand-in that never contacts real Slack."""

    def post_milestone(self, *a, **kw):
        return False

    def post_milestone_async(self, *a, **kw):
        pass

    def get_signing_secret(self):
        return None

    def forward_message_to_session(self, *a, **kw):
        return False

    @staticmethod
    def verify_webhook_signature(signing_secret, timestamp, signature, body):
        return False


@pytest.fixture(autouse=True)
def _isolate_session_logs(tmp_path_factory, monkeypatch):
    """Keep session logs isolated and Slack disabled per test run."""
    log_dir = tmp_path_factory.mktemp("session_logs")
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))
    monkeypatch.setenv("CHAD_SESSION_LOG_MAX_FILES", "200")

    # Prevent any test from sending real Slack notifications.
    noop = _NoOpSlackService()
    monkeypatch.setattr(
        "chad.server.services.slack_service.get_slack_service", lambda: noop,
    )

    # Prevent tests from opening real browser windows.
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)

    yield

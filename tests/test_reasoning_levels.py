"""Per-provider reasoning levels and the per-task Slack notification toggle.

Two behaviours are covered:

1. Reasoning levels are provider-specific. The old UI always offered
   default/low/medium/high regardless of the provider; instead the backend now
   declares the levels each provider actually supports (Codex has four, Claude
   Code has more graduations, providers without reasoning have none) and exposes
   them via ``GET /providers`` so the UI can render the right options.

2. A task can opt out of Slack milestone posting. ``TaskCreate.notify_slack``
   defaults to True and is forwarded to the session event loop, so the composer's
   "post to Slack" checkbox can turn it off per task.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.state import reset_state
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.services.session_manager import SessionManager
from chad.server.services.task_executor import TaskExecutor, TaskState
from chad.util.config_manager import ConfigManager
from chad.util.providers import (
    CLAUDE_THINKING_BUDGETS,
    REASONING_LEVELS,
    get_reasoning_levels,
)


# ── Reasoning level source of truth ─────────────────────────────────────────


def test_get_reasoning_levels_are_provider_specific():
    """Providers expose different graduations; some have none at all."""
    anthropic = get_reasoning_levels("anthropic")
    openai = get_reasoning_levels("openai")

    # Claude Code offers more graduations than a flat low/medium/high.
    assert len(anthropic) > 3, anthropic
    assert "minimal" in anthropic and "max" in anthropic
    for level in ("low", "medium", "high"):
        assert level in anthropic

    # Codex uses OpenAI's reasoning effort values, including "minimal".
    assert openai == ["minimal", "low", "medium", "high"]

    # Providers without a reasoning knob return an empty list.
    for provider in ("gemini", "qwen", "local", "mistral", "kimi", "mock"):
        assert get_reasoning_levels(provider) == []


def test_claude_thinking_budgets_match_declared_levels():
    """Every Claude reasoning level must map to a thinking-token budget."""
    assert list(CLAUDE_THINKING_BUDGETS) == REASONING_LEVELS["anthropic"]
    # Ordered from least to most thinking.
    budgets = list(CLAUDE_THINKING_BUDGETS.values())
    assert budgets == sorted(budgets)


# ── Providers API exposes reasoning_levels ──────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))
    temp_config.write_text(json.dumps({
        "encryption_salt": "dGVzdHNhbHQ=",
        "password_hash": "",
        "accounts": {},
    }))

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()

    app = create_app()
    with TestClient(app) as c:
        yield c

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


def test_providers_endpoint_reports_reasoning_levels(client):
    """Each provider advertises the exact reasoning levels it supports."""
    resp = client.get("/api/v1/providers")
    assert resp.status_code == 200
    by_type = {p["type"]: p for p in resp.json()["providers"]}

    # Field is present for every provider.
    for info in by_type.values():
        assert "reasoning_levels" in info
        # supports_reasoning stays consistent with the level list.
        assert info["supports_reasoning"] == (len(info["reasoning_levels"]) > 0)

    assert by_type["anthropic"]["reasoning_levels"] == get_reasoning_levels("anthropic")
    assert by_type["openai"]["reasoning_levels"] == ["minimal", "low", "medium", "high"]
    for provider in ("gemini", "qwen", "local", "mistral", "kimi"):
        assert by_type[provider]["reasoning_levels"] == []


# ── Per-task Slack toggle ───────────────────────────────────────────────────


def _init_git_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_path, check=False)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True)


def test_task_create_notify_slack_defaults_true():
    from chad.server.api.schemas import TaskCreate

    task = TaskCreate(
        project_path="/tmp/x",
        task_description="do the thing",
        coding_agent="agent",
    )
    assert task.notify_slack is True
    assert TaskCreate(
        project_path="/tmp/x",
        task_description="do the thing",
        coding_agent="agent",
        notify_slack=False,
    ).notify_slack is False


@pytest.mark.parametrize("notify_slack", [True, False])
def test_start_task_forwards_notify_slack(tmp_path, monkeypatch, notify_slack):
    """The notify_slack flag reaches the SessionEventLoop that posts to Slack."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"idle": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="slack-toggle")
    executor = TaskExecutor(ConfigManager(), session_manager)

    # SessionEventLoop is imported lazily inside _run_task, so patch it on its
    # own module. A stub avoids actually running a provider.
    import chad.server.services.session_event_loop as sel

    captured: dict[str, bool] = {}

    class StubLoop:
        def __init__(self, *args, **kwargs):
            captured["notify_slack"] = kwargs.get("notify_slack")

        def run(self, *args, **kwargs):
            return 0, ""

    monkeypatch.setattr(sel, "SessionEventLoop", StubLoop)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="a task",
        coding_account="idle",
        notify_slack=notify_slack,
    )
    task._thread.join(timeout=10)

    assert task.state != TaskState.RUNNING
    assert captured.get("notify_slack") is notify_slack

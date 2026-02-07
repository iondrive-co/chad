"""Tests for the reusable test utility tools in test_helpers.py."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state

from tests.test_helpers import (
    CollectedEvents,
    CapturedCommand,
    ConfigParityResult,
    ProviderOutputSimulator,
    StreamInspection,
    TaskPhaseMonitor,
    capture_provider_command,
    cli_config_parity_check,
    collect_stream_events,
    inspect_stream_output,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create an isolated FastAPI test client."""
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    initial_config = {
        "encryption_salt": "dGVzdHNhbHQ=",
        "password_hash": "",
        "accounts": {},
    }
    temp_config.write_text(json.dumps(initial_config))

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


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo."""
    import subprocess

    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "README.md").write_text("# Test\n")
    (repo / "BUGS.md").write_text("# Bugs\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


def _create_session_and_account(client, git_repo, account_name="mock-agent"):
    """Helper: create a session and add a mock account via API."""
    sess = client.post("/api/v1/sessions", json={"name": "tool-test"})
    assert sess.status_code == 201
    session_id = sess.json()["id"]

    acc = client.post(
        "/api/v1/accounts",
        json={"name": account_name, "provider": "mock"},
    )
    assert acc.status_code in (200, 201, 409)

    return session_id


# ── Tool 1: collect_stream_events ─────────────────────────────────────────


class TestCollectStreamEvents:
    """Tests for collect_stream_events."""

    def test_collects_events_from_completed_task(self, client, git_repo):
        session_id = _create_session_and_account(client, git_repo)

        task = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Test task",
                "coding_agent": "mock-agent",
            },
        )
        assert task.status_code in (200, 201)

        # Give mock agent time to run
        time.sleep(2)

        events = collect_stream_events(
            client, session_id, timeout=10, wait_for_completion=True
        )

        assert isinstance(events, CollectedEvents)
        assert len(events.all_events) > 0
        assert isinstance(events.terminal_events, list)
        assert isinstance(events.structured_events, list)
        assert isinstance(events.decoded_output, str)

    def test_returns_empty_on_no_events(self, client):
        sess = client.post("/api/v1/sessions", json={"name": "empty"})
        session_id = sess.json()["id"]

        events = collect_stream_events(
            client, session_id, timeout=1, wait_for_completion=False
        )

        assert isinstance(events, CollectedEvents)
        assert events.all_events == []
        assert events.decoded_output == ""

    def test_non_blocking_mode_returns_immediately(self, client, git_repo):
        session_id = _create_session_and_account(client, git_repo)

        start = time.monotonic()
        events = collect_stream_events(
            client, session_id, timeout=5, wait_for_completion=False
        )
        elapsed = time.monotonic() - start

        assert isinstance(events, CollectedEvents)
        # Should return quickly, not wait for timeout
        assert elapsed < 3


# ── Tool 2: ProviderOutputSimulator ───────────────────────────────────────


class TestProviderOutputSimulator:
    """Tests for ProviderOutputSimulator."""

    def test_unknown_scenario_raises(self, monkeypatch):
        with pytest.raises(ValueError, match="Unknown scenario"):
            ProviderOutputSimulator(monkeypatch, "nonexistent_scenario")

    def test_qwen_duplicate_scenario_patches(self, client, git_repo, monkeypatch):
        session_id = _create_session_and_account(client, git_repo)
        ProviderOutputSimulator(monkeypatch, "qwen_duplicate")

        task = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Test qwen",
                "coding_agent": "mock-agent",
            },
        )
        assert task.status_code in (200, 201)

        time.sleep(3)
        events = collect_stream_events(client, session_id, timeout=5)
        # The simulated script should have produced some terminal output
        assert isinstance(events.decoded_output, str)

    def test_codex_system_prompt_scenario(self, client, git_repo, monkeypatch):
        session_id = _create_session_and_account(client, git_repo)
        ProviderOutputSimulator(monkeypatch, "codex_system_prompt")

        task = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Test codex",
                "coding_agent": "mock-agent",
            },
        )
        assert task.status_code in (200, 201)
        time.sleep(3)

        events = collect_stream_events(client, session_id, timeout=5)
        assert isinstance(events, CollectedEvents)

    def test_all_scenarios_are_valid(self, monkeypatch):
        """Every registered scenario should be installable without error."""
        from tests.test_helpers import SCENARIOS

        for name in SCENARIOS:
            # Reinstantiate monkeypatch context each time
            ProviderOutputSimulator(monkeypatch, name)


# ── Tool 3: TaskPhaseMonitor ──────────────────────────────────────────────


class TestTaskPhaseMonitor:
    """Tests for TaskPhaseMonitor."""

    def test_empty_events(self):
        monitor = TaskPhaseMonitor([])
        assert monitor.phases == []
        assert monitor.phase_names() == []

    def test_coding_phase_detection(self):
        events = [
            {"type": "session_started", "seq": 1},
            {"type": "terminal_output", "seq": 2, "data": "Hello"},
            {"type": "terminal_output", "seq": 3, "data": "World"},
        ]
        monitor = TaskPhaseMonitor(events)
        assert "coding" in monitor.phase_names()
        counts = monitor.terminal_counts_by_phase()
        assert counts.get("coding", 0) == 2

    def test_verification_phase_detection(self):
        events = [
            {"type": "session_started", "seq": 1},
            {"type": "terminal_output", "seq": 2, "data": "code output"},
            {"type": "verification_attempt", "seq": 3, "attempt_number": 1},
            {"type": "terminal_output", "seq": 4, "data": "verify output"},
            {"type": "terminal_output", "seq": 5, "data": "more verify"},
        ]
        monitor = TaskPhaseMonitor(events)
        names = monitor.phase_names()
        assert "coding" in names
        assert "verification_1" in names
        counts = monitor.terminal_counts_by_phase()
        assert counts.get("coding", 0) == 1
        assert counts.get("verification_1", 0) == 2

    def test_multiple_verification_rounds(self):
        events = [
            {"type": "session_started", "seq": 1},
            {"type": "terminal_output", "seq": 2, "data": "coding"},
            {"type": "verification_attempt", "seq": 3, "attempt_number": 1},
            {"type": "terminal_output", "seq": 4, "data": "v1"},
            {"type": "verification_attempt", "seq": 5, "attempt_number": 2},
            {"type": "terminal_output", "seq": 6, "data": "v2"},
        ]
        monitor = TaskPhaseMonitor(events)
        names = monitor.phase_names()
        assert names == ["coding", "verification_1", "verification_2"]

    def test_text_based_phase_markers(self):
        events = [
            {"type": "terminal_output", "seq": 1, "data": "Phase 1: explore"},
            {"type": "terminal_output", "seq": 2, "data": "reading files"},
            {"type": "terminal_output", "seq": 3, "data": "Phase 2: implement"},
            {"type": "terminal_output", "seq": 4, "data": "writing code"},
        ]
        monitor = TaskPhaseMonitor(events)
        names = monitor.phase_names()
        assert "phase_1" in names
        assert "phase_2" in names


# ── Tool 4: capture_provider_command ──────────────────────────────────────


class TestCaptureProviderCommand:
    """Tests for capture_provider_command."""

    def test_mock_provider(self, tmp_path):
        result = capture_provider_command(
            provider="mock",
            account_name="test-mock",
            project_path=str(tmp_path),
            task_description="Fix the bug",
        )
        assert isinstance(result, CapturedCommand)
        assert isinstance(result.cmd, list)
        assert len(result.cmd) > 0
        assert result.cmd[0] == "python3"

    def test_anthropic_provider_includes_stream_json(self, tmp_path):
        result = capture_provider_command(
            provider="anthropic",
            account_name="my-claude",
            project_path=str(tmp_path),
            task_description="Test task",
        )
        assert "--output-format" in result.cmd
        assert "stream-json" in result.cmd
        assert "CLAUDE_CONFIG_DIR" in result.env

    def test_openai_provider_uses_exec(self, tmp_path):
        result = capture_provider_command(
            provider="openai",
            account_name="my-codex",
            project_path=str(tmp_path),
            task_description="Test task",
        )
        assert "exec" in result.cmd
        assert "HOME" in result.env
        # Codex reads prompt from stdin
        assert result.initial_input is not None

    def test_qwen_provider(self, tmp_path):
        result = capture_provider_command(
            provider="qwen",
            account_name="my-qwen",
            project_path=str(tmp_path),
            task_description="Test task",
        )
        assert "--output-format" in result.cmd
        assert "stream-json" in result.cmd

    def test_no_task_description(self, tmp_path):
        result = capture_provider_command(
            provider="mock",
            account_name="test",
            project_path=str(tmp_path),
        )
        assert isinstance(result, CapturedCommand)

    def test_implementation_phase(self, tmp_path):
        result = capture_provider_command(
            provider="anthropic",
            account_name="test",
            project_path=str(tmp_path),
            task_description="Implement feature",
            phase="implementation",
            exploration_output="Found 5 relevant files.",
        )
        assert isinstance(result.cmd, list)


# ── Tool 5: cli_config_parity_check ──────────────────────────────────────


class TestCliConfigParityCheck:
    """Tests for cli_config_parity_check."""

    def test_returns_result(self):
        result = cli_config_parity_check()
        assert isinstance(result, ConfigParityResult)
        assert isinstance(result.api_keys, set)
        assert isinstance(result.cli_keys, set)
        assert isinstance(result.missing_from_cli, set)

    def test_api_keys_excludes_internal(self):
        result = cli_config_parity_check()
        internal = {
            "password_hash", "encryption_salt", "accounts",
            "role_assignments", "preferences", "projects",
            "mock_remaining_usage", "mock_context_remaining",
        }
        assert not result.api_keys.intersection(internal)

    def test_coverage_is_union(self):
        result = cli_config_parity_check()
        assert result.cli_keys | result.missing_from_cli == result.api_keys


# ── Tool 6: inspect_stream_output ─────────────────────────────────────────


class TestInspectStreamOutput:
    """Tests for inspect_stream_output."""

    def test_clean_output(self):
        result = inspect_stream_output("Hello world\nTask complete")
        assert isinstance(result, StreamInspection)
        assert not result.has_raw_json
        assert not result.has_binary_data
        assert result.json_fragments == []
        assert result.binary_fragments == []

    def test_detects_raw_json(self):
        output = 'Some text {"type": "assistant", "message": {"content": "hi"}} more text'
        result = inspect_stream_output(output)
        assert result.has_raw_json
        assert len(result.json_fragments) > 0

    def test_detects_binary_garbage(self):
        output = "Normal text\n@@@@@@@%#%%#@@@%#####%@@%%%%#%%%%\nMore text"
        result = inspect_stream_output(output)
        assert result.has_binary_data
        assert len(result.binary_fragments) > 0

    def test_detects_both(self):
        output = '{"type": "item"} and @@@@@@@@@@@@@@@@@@'
        result = inspect_stream_output(output)
        assert result.has_raw_json
        assert result.has_binary_data

    def test_content_pattern_detection(self):
        output = '{"content": "some value"}'
        result = inspect_stream_output(output)
        assert result.has_raw_json

    def test_short_at_signs_not_flagged(self):
        # Less than 10 consecutive chars should not be flagged
        result = inspect_stream_output("email@user.com @@merge")
        assert not result.has_binary_data

    def test_message_pattern(self):
        output = '{"message": "hello"}'
        result = inspect_stream_output(output)
        assert result.has_raw_json

    def test_role_pattern(self):
        output = '{"role": "assistant"}'
        result = inspect_stream_output(output)
        assert result.has_raw_json

import json
import subprocess
from pathlib import Path

from chad.server.services.session_manager import SessionManager
from chad.server.services.pty_stream import get_pty_stream_service
from chad.server.services.task_executor import TaskExecutor, TaskState, build_agent_command
from chad.util.config_manager import ConfigManager


class TestBuildAgentCommand:
    """Tests for build_agent_command function."""

    def test_anthropic_includes_task_as_cli_arg(self, tmp_path):
        """Anthropic provider passes task description as CLI argument, not stdin."""
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, "Fix the bug"
        )

        # Task should be in command (wrapped in full prompt), not initial_input
        assert "claude" in Path(cmd[0]).name
        assert "-p" in cmd
        # The task is now wrapped in a full prompt with instructions
        assert any("Fix the bug" in arg for arg in cmd)
        assert initial_input is None

    def test_anthropic_without_task(self, tmp_path):
        """Anthropic provider works without task description."""
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, None
        )

        assert "claude" in Path(cmd[0]).name
        assert "-p" in cmd
        assert len(cmd) == 4  # claude, -p, --permission-mode, bypassPermissions
        assert initial_input is None

    def test_mock_provider_produces_output(self, tmp_path):
        """Mock provider command produces ANSI-formatted output."""
        cmd, env, initial_input = build_agent_command(
            "mock", "test-account", tmp_path, "Test task"
        )

        # Run the mock command and verify it produces output
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        assert "Mock Agent" in result.stdout
        assert initial_input is None


def _init_git_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_path, check=False)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True)


def test_task_executor_times_out_hung_agent(tmp_path, monkeypatch):
    """Hung agent processes are terminated after inactivity and logged as timeout."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    # Keep config and logs isolated
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"idle": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="timeout-test")

    # Force a tiny inactivity timeout and a command that never produces output
    executor = TaskExecutor(ConfigManager(), session_manager, inactivity_timeout=0.5)

    import chad.server.services.task_executor as te

    def sleepy_command(provider, account_name, project_path, task_description=None):
        return ["bash", "-c", "sleep 5"], {}, None

    monkeypatch.setattr(te, "build_agent_command", sleepy_command)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="hang forever",
        coding_account="idle",
    )

    task._thread.join(timeout=5)

    assert task.state == TaskState.FAILED
    assert "timed out" in (task.error or "")
    assert task.completed_at is not None

    # Event log should contain the timeout marker
    events = task.event_log.get_events()
    reasons = [e.get("reason") for e in events if e.get("type") == "session_ended"]
    assert "timeout" in reasons

    # Ensure the PTY session was cleaned up
    assert get_pty_stream_service().list_sessions() == []


def test_terminal_output_is_batched_and_decoded(tmp_path, monkeypatch):
    """Terminal output is batched for readability and includes decoded text."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"idle": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="batch-test")

    executor = TaskExecutor(
        ConfigManager(),
        session_manager,
        inactivity_timeout=10.0,
        terminal_flush_interval=0.1,
    )

    import chad.server.services.task_executor as te

    script = "/usr/bin/env python3 -c \"import sys,time;[sys.stdout.write(f'line {i}\\n') or sys.stdout.flush() or time.sleep(0.05) for i in range(5)]\""

    def noisy_command(provider, account_name, project_path, task_description=None):
        return ["bash", "-c", script], {}, None

    monkeypatch.setattr(te, "build_agent_command", noisy_command)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="log batching",
        coding_account="idle",
    )

    task._thread.join(timeout=5)

    terminal_events = [
        e for e in task.event_log.get_events() if e.get("type") == "terminal_output"
    ]
    # Should be fewer events than individual lines because of batching
    assert len(terminal_events) < 5
    # Terminal output now stores human-readable text in 'data' field (not base64)
    combined_text = "\n".join([e.get("data", "") or "" for e in terminal_events])
    assert "line 0" in combined_text and "line 4" in combined_text

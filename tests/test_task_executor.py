import json
import subprocess
from pathlib import Path

from chad.server.services.session_manager import SessionManager
from chad.server.services.pty_stream import get_pty_stream_service
from chad.server.services.task_executor import (
    TaskExecutor,
    TaskState,
    build_agent_command,
    ClaudeStreamJsonParser,
)
from chad.util.config_manager import ConfigManager


class TestClaudeStreamJsonParser:
    """Tests for ClaudeStreamJsonParser."""

    def test_parses_assistant_text_message(self):
        """Parser extracts text from assistant messages."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"assistant","message":{"content":[{"type":"text","text":"Hello world"}]}}\n'
        results = parser.feed(data)
        assert results == ["Hello world"]

    def test_tool_use_accumulated_not_returned_immediately(self):
        """Parser accumulates tool uses instead of returning them immediately."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/src/main.py"}}]}}\n'
        results = parser.feed(data)
        # Tool uses are accumulated, not returned immediately
        assert results == []
        # But the tool is tracked
        assert parser.has_pending_tools()
        assert parser._tool_counts == {"Read": 1}

    def test_tool_summary_emitted_before_text(self):
        """Parser emits tool summary when text content arrives."""
        parser = ClaudeStreamJsonParser()
        # First, a tool use
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/src/main.py"}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest"}}]}}\n')
        # Then text content arrives
        results = parser.feed(b'{"type":"assistant","message":{"content":[{"type":"text","text":"Done!"}]}}\n')
        # Summary is emitted before text
        assert len(results) == 2
        assert results[0] == "• 1 file read, 1 command"
        assert results[1] == "Done!"
        # Tool tracking is cleared
        assert not parser.has_pending_tools()

    def test_get_tool_summary_formats_correctly(self):
        """Parser formats tool summary with correct grammar."""
        parser = ClaudeStreamJsonParser()
        # Add multiple tools
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Glob","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Grep","input":{}}]}}\n')
        summary = parser.get_tool_summary()
        assert summary == "• 3 files read, 2 searches"

    def test_get_tool_details_returns_full_descriptions(self):
        """Parser stores full tool descriptions for expansion."""
        parser = ClaudeStreamJsonParser()
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/src/main.py"}}]}}\n')
        details = parser.get_tool_details()
        assert details == ["• Reading /src/main.py"]

    def test_ignores_system_init(self):
        """Parser skips system init messages."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"system","subtype":"init","cwd":"/test"}\n'
        results = parser.feed(data)
        assert results == []

    def test_ignores_result(self):
        """Parser skips result messages."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"result","result":"Done"}\n'
        results = parser.feed(data)
        assert results == []

    def test_handles_incomplete_lines(self):
        """Parser buffers incomplete JSON lines."""
        parser = ClaudeStreamJsonParser()
        # Send partial line
        results = parser.feed(b'{"type":"assistant","message":')
        assert results == []
        # Complete the line
        results = parser.feed(b'{"content":[{"type":"text","text":"Hi"}]}}\n')
        assert results == ["Hi"]

    def test_handles_multiple_lines(self):
        """Parser handles multiple JSON lines in one chunk."""
        parser = ClaudeStreamJsonParser()
        data = (
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"Line 1"}]}}\n'
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"Line 2"}]}}\n'
        )
        results = parser.feed(data)
        assert results == ["Line 1", "Line 2"]

    def test_passes_through_non_json(self):
        """Parser passes through non-JSON lines as-is."""
        parser = ClaudeStreamJsonParser()
        data = b"Plain text output\n"
        results = parser.feed(data)
        assert results == ["Plain text output"]

    def test_parses_qwen_message_format(self):
        """Parser handles Qwen/Gemini CLI message format."""
        parser = ClaudeStreamJsonParser()
        # Qwen uses {type: "message", role: "assistant", content: "..."}
        data = b'{"type":"message","role":"assistant","content":"Hello from Qwen"}\n'
        results = parser.feed(data)
        assert results == ["Hello from Qwen"]

    def test_ignores_qwen_user_message(self):
        """Parser ignores user messages in Qwen format."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"message","role":"user","content":"User input"}\n'
        results = parser.feed(data)
        assert results == []

    def test_ignores_qwen_system_init(self):
        """Parser ignores Qwen system init events."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"system","session_id":"abc123"}\n'
        results = parser.feed(data)
        assert results == []


class TestBuildAgentCommand:
    """Tests for build_agent_command function."""

    def test_anthropic_uses_stream_json_and_stdin_prompt(self, tmp_path):
        """Anthropic provider sends task via stdin in stream-json mode."""
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, "Fix the bug"
        )

        assert "claude" in Path(cmd[0]).name
        assert "-p" in cmd
        assert "--output-format" in cmd and "stream-json" in cmd
        assert "--permission-mode" in cmd
        # The task must be in argv (stdin is TTY with -p)
        assert any("Fix the bug" in arg for arg in cmd)
        assert initial_input is None

    def test_anthropic_without_task(self, tmp_path):
        """Anthropic provider works without task description."""
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, None
        )

        assert "claude" in Path(cmd[0]).name
        assert "-p" in cmd
        assert "--verbose" in cmd
        assert "--output-format" in cmd and "stream-json" in cmd
        assert "--permission-mode" in cmd
        assert len(cmd) == 7  # claude, -p, --verbose, --output-format, stream-json, --permission-mode, bypassPermissions
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

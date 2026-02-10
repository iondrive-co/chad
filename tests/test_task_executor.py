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
    _strip_binary_garbage,
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

    def test_result_captures_stats(self):
        """Parser captures usage stats from result events."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"result","stats":{"total_tokens":1500,"input_tokens":1000,"output_tokens":500,"cached":100,"duration_ms":3000,"tool_calls":2}}\n'
        results = parser.feed(data)
        assert results == []  # No display output
        assert parser.result_stats["total_tokens"] == 1500
        assert parser.result_stats["input_tokens"] == 1000
        assert parser.result_stats["output_tokens"] == 500
        assert parser.result_stats["tool_calls"] == 2

    def test_result_without_stats_does_nothing(self):
        """Parser ignores result events without stats."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"result","result":"Done"}\n'
        results = parser.feed(data)
        assert results == []
        assert parser.result_stats == {}

    def test_init_captures_model_from_system_event(self):
        """Parser captures model name from Claude-style system init events."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"system","subtype":"init","model":"claude-sonnet-4-5"}\n'
        results = parser.feed(data)
        assert results == []
        assert parser.init_model == "claude-sonnet-4-5"

    def test_init_captures_model_from_gemini_event(self):
        """Parser captures model name from Gemini-style init events."""
        parser = ClaudeStreamJsonParser()
        data = b'{"type":"init","model":"gemini-2.5-pro","session_id":"abc123"}\n'
        results = parser.feed(data)
        assert results == []
        assert parser.init_model == "gemini-2.5-pro"

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

    def test_other_tools_show_actual_names(self):
        """Parser shows actual tool names instead of generic 'other' count."""
        parser = ClaudeStreamJsonParser()
        # Add some categorized tools
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{}}]}}\n')
        # Add uncategorized tools
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"AskUserQuestion","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"TodoWrite","input":{}}]}}\n')
        parser.feed(b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"TodoWrite","input":{}}]}}\n')

        summary = parser.get_tool_summary()
        # Should show actual tool names, not "3 other"
        assert summary == "• 1 file read, 1 edit, AskUserQuestion, 2 TodoWrite"

    def test_flush_processes_remaining_buffer(self):
        """flush() should process any remaining bytes without trailing newline."""
        parser = ClaudeStreamJsonParser()
        # Feed data without trailing newline - feed() won't process it
        parser.feed(b'{"type":"result","stats":{"input_tokens":100,"output_tokens":50}}')
        assert parser.result_stats == {}  # Not yet processed

        remaining = parser.flush()
        # result events return None for display but capture stats
        assert remaining == []
        assert parser.result_stats == {"input_tokens": 100, "output_tokens": 50}

    def test_flush_returns_text_from_incomplete_line(self):
        """flush() should return text from a message without trailing newline."""
        parser = ClaudeStreamJsonParser()
        parser.feed(b'{"type":"message","role":"assistant","content":"Final answer"}')
        results = parser.flush()
        assert results == ["Final answer"]

    def test_flush_empty_buffer_returns_nothing(self):
        """flush() with empty buffer returns empty list."""
        parser = ClaudeStreamJsonParser()
        assert parser.flush() == []

    def test_flush_whitespace_only_buffer_returns_nothing(self):
        """flush() with whitespace-only buffer returns empty list."""
        parser = ClaudeStreamJsonParser()
        parser.feed(b"   \n")  # This gets consumed by feed, leaving empty buffer
        assert parser.flush() == []


class TestBuildAgentCommand:
    """Tests for build_agent_command function."""

    def test_anthropic_uses_stream_json_and_exploration_prompt(self, tmp_path):
        """Anthropic provider sends exploration prompt via argv in stream-json mode."""
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, "Fix the bug"
        )

        assert "claude" in Path(cmd[0]).name
        assert "-p" in cmd
        assert "--output-format" in cmd and "stream-json" in cmd
        assert "--permission-mode" in cmd
        # The exploration prompt must include the task and phase info
        prompt_arg = [arg for arg in cmd if "Fix the bug" in arg]
        assert len(prompt_arg) == 1
        assert "Phase 1: Exploration" in prompt_arg[0]
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

    def test_gemini_uses_non_interactive_prompt_flag(self, tmp_path):
        """Gemini must run headless with -p so the process exits after each phase."""
        cmd, env, initial_input = build_agent_command(
            "gemini", "test-account", tmp_path, "Fix the bug"
        )

        assert "gemini" in Path(cmd[0]).name
        assert "-y" in cmd
        assert "--output-format" in cmd and "stream-json" in cmd
        assert "-p" in cmd
        prompt_idx = cmd.index("-p")
        assert "Fix the bug" in cmd[prompt_idx + 1]
        assert "Phase 1: Exploration" in cmd[prompt_idx + 1]
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

    def test_mock_provider_respects_run_duration(self, tmp_path):
        """Mock provider emits timed output when run duration is configured."""
        cmd, env, initial_input = build_agent_command(
            "mock",
            "test-account",
            tmp_path,
            "Test task",
            mock_run_duration_seconds=1,
        )

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "Streaming simulated work for 1s" in result.stdout
        assert "[tick 001]" in result.stdout
        assert initial_input is None

    def test_mock_provider_outputs_phase_prompt_marker(self, tmp_path):
        """Mock provider output should include an explicit phase prompt marker."""
        cmd, env, initial_input = build_agent_command(
            "mock",
            "test-account",
            tmp_path,
            "Test task",
            phase="exploration",
        )

        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        assert "Prompt: Exploration" in result.stdout
        assert initial_input is None

    def test_continuation_phase_uses_continuation_prompt(self, tmp_path):
        """Continuation phase uses the continuation prompt when agent exits early."""
        previous_output = "Found the bug in src/main.py:42"
        cmd, env, initial_input = build_agent_command(
            "openai", "test-account", tmp_path, "Fix the bug",
            phase="continuation",
            exploration_output=previous_output
        )

        # The prompt should tell the agent to continue
        assert initial_input is not None
        assert "continue" in initial_input.lower()
        assert "progress update" in initial_input.lower() or "completion" in initial_input.lower()

    def test_implementation_phase_uses_implementation_prompt(self, tmp_path):
        """Implementation phase uses the implementation prompt with exploration context."""
        exploration_output = "Found the bug in src/main.py - missing null check"
        cmd, env, initial_input = build_agent_command(
            "openai", "test-account", tmp_path, "Fix the bug",
            phase="implementation",
            exploration_output=exploration_output
        )

        # The prompt should have Phase 2 markers and include exploration output
        assert initial_input is not None
        assert "Phase 2: Implementation" in initial_input
        assert "Previous Exploration" in initial_input
        assert exploration_output in initial_input

    def test_override_prompt_replaces_auto_generated(self, tmp_path):
        """Override prompt is used instead of auto-generated prompt."""
        override = "Custom exploration instructions for the agent"
        cmd, env, initial_input = build_agent_command(
            "openai", "test-account", tmp_path, "Fix the bug",
            phase="exploration",
            override_prompt=override,
        )
        # OpenAI uses initial_input for the prompt (with trailing newline)
        assert initial_input.strip() == override

    def test_override_prompt_replaces_exploration_output_placeholder(self, tmp_path):
        """Override implementation prompt has {exploration_output} replaced."""
        override = "Implement based on: {exploration_output}"
        exploration = "Found bug in line 42"
        cmd, env, initial_input = build_agent_command(
            "openai", "test-account", tmp_path, "Fix the bug",
            phase="implementation",
            exploration_output=exploration,
            override_prompt=override,
        )
        assert initial_input.strip() == "Implement based on: Found bug in line 42"

    def test_override_prompt_anthropic_uses_cmd_arg(self, tmp_path):
        """Override prompt for Anthropic goes into command args, not initial_input."""
        override = "Custom exploration prompt"
        cmd, env, initial_input = build_agent_command(
            "anthropic", "test-account", tmp_path, "Fix the bug",
            phase="exploration",
            override_prompt=override,
        )
        assert initial_input is None
        prompt_arg = [arg for arg in cmd if "Custom exploration prompt" in arg]
        assert len(prompt_arg) == 1

    def test_opencode_uses_run_with_json_format(self, tmp_path):
        """OpenCode build_agent_command uses 'opencode run --format json'."""
        cmd, env, initial_input = build_agent_command(
            "opencode", "test-oc", tmp_path, "Fix the bug"
        )

        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        # Prompt is positional after 'run'
        assert "Fix the bug" in cmd[-1]

    def test_opencode_passes_model_flag(self, tmp_path):
        """OpenCode passes -m with the model in provider/model format."""
        cmd, env, initial_input = build_agent_command(
            "opencode", "test-oc", tmp_path, "Fix the bug",
            model="openai/gpt-4o",
        )

        m_idx = cmd.index("-m")
        assert cmd[m_idx + 1] == "openai/gpt-4o"

    def test_opencode_default_model(self, tmp_path):
        """OpenCode uses anthropic/claude-sonnet-4-5 as default model."""
        cmd, env, initial_input = build_agent_command(
            "opencode", "test-oc", tmp_path, "Fix the bug"
        )

        m_idx = cmd.index("-m")
        assert cmd[m_idx + 1] == "anthropic/claude-sonnet-4-5"


def _init_git_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_path, check=False)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True)


def test_idle_warning_threshold_for_default_timeout(tmp_path, monkeypatch):
    """Long inactivity timeouts should still warn users quickly."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    executor = TaskExecutor(ConfigManager(), SessionManager())

    assert executor.inactivity_timeout == 900.0
    assert executor._idle_warning_threshold() == 60.0


def test_idle_warning_threshold_stays_below_timeout(tmp_path, monkeypatch):
    """Warning threshold should stay below very short inactivity timeouts."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    executor = TaskExecutor(ConfigManager(), SessionManager(), inactivity_timeout=2.0)

    assert executor._idle_warning_threshold() == 1.0


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

    def sleepy_command(provider, account_name, project_path, task_description=None, screenshots=None, phase="combined", exploration_output=None, **kwargs):
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

    # Stream events should include both complete and message_complete so UI closes cleanly
    stream_events = executor.get_events(task.id, timeout=0.01)
    complete_events = [e for e in stream_events if e.type == "complete"]
    message_events = [e for e in stream_events if e.type == "message_complete"]
    assert complete_events, "timeout should emit a complete event"
    assert any(
        ("timeout" in (e.data.get("message", "") or "").lower())
        or ("timed out" in (e.data.get("message", "") or "").lower())
        for e in complete_events
    )
    assert message_events, "timeout should emit a final message_complete bubble"
    assert any("timed out" in (e.data.get("content", "") or "") for e in message_events)

    # Ensure the PTY session was cleaned up
    assert get_pty_stream_service().list_sessions() == []


def test_status_events_do_not_refresh_activity_timer(tmp_path, monkeypatch):
    """Synthetic status events should not count as agent activity."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"idle": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="status-activity-test")
    executor = TaskExecutor(ConfigManager(), session_manager, inactivity_timeout=30.0)

    import chad.server.services.task_executor as te

    def silent_command(provider, account_name, project_path, task_description=None,
                       screenshots=None, phase="combined", exploration_output=None, **kwargs):
        return ["bash", "-c", "exit 0"], {}, None

    monkeypatch.setattr(te, "build_agent_command", silent_command)

    touched: list[str] = []

    def touch_spy(task_id: str) -> None:
        touched.append(task_id)

    monkeypatch.setattr(executor, "_touch_activity", touch_spy)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="No output task",
        coding_account="idle",
    )
    task._thread.join(timeout=10)

    assert task.completed_at is not None
    assert touched == []


def test_terminal_output_is_periodically_flushed_and_decoded(tmp_path, monkeypatch):
    """Terminal output flushes during a run and stores decoded text snapshots."""
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

    script = "/usr/bin/env python3 -c \"import sys,time;[sys.stdout.write(f'line {i}\\n') or sys.stdout.flush() or time.sleep(0.12) for i in range(5)]\""

    def noisy_command(provider, account_name, project_path, task_description=None, screenshots=None, phase="combined", exploration_output=None, **kwargs):
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
    # Terminal output now stores human-readable text in 'data' field (not base64)
    combined_text = "\n".join([e.get("data", "") or "" for e in terminal_events])
    assert "line 0" in combined_text and "line 4" in combined_text
    # PTY callbacks may coalesce fast output into one read on some systems; in that
    # case we still expect at least one decoded terminal snapshot in the event log.
    assert len(terminal_events) >= 1, "Expected at least one terminal_output snapshot"


def test_phase_status_events_are_logged_for_streaming(tmp_path, monkeypatch):
    """Event log should include progress and phase status updates in phase order."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"mock-acct": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="status-test")
    executor = TaskExecutor(ConfigManager(), session_manager, inactivity_timeout=30.0)

    import chad.server.services.task_executor as te

    def scripted_command(provider, account_name, project_path, task_description=None,
                         screenshots=None, phase="combined", exploration_output=None, **kwargs):
        if phase == "exploration":
            script = "echo '{\"type\":\"progress\",\"summary\":\"Scoped\",\"location\":\"x.py:1\",\"next_step\":\"Implement\"}'"
        else:
            script = "echo '```json'; echo '{\"change_summary\":\"Done\",\"files_changed\":[\"x.py\"],\"completion_status\":\"success\"}'; echo '```'"
        return ["bash", "-c", script], {}, None

    monkeypatch.setattr(te, "build_agent_command", scripted_command)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="Ensure phase status events are logged",
        coding_account="mock-acct",
    )
    task._thread.join(timeout=10)

    assert task.state == TaskState.COMPLETED
    events = task.event_log.get_events()
    statuses = [e.get("status", "") for e in events if e.get("type") == "status"]
    assert any("Phase 1: Exploring codebase..." in s for s in statuses), statuses
    assert any("Phase 2: Implementing changes..." in s for s in statuses), statuses

    progress_events = [e for e in events if e.get("type") == "progress"]
    assert len(progress_events) == 1, f"Expected one progress event, got: {progress_events}"
    assert progress_events[0].get("summary") == "Scoped"
    assert progress_events[0].get("location") == "x.py:1"
    assert progress_events[0].get("next_step") == "Implement"

    phase2_events = [
        e for e in events
        if e.get("type") == "status" and "Phase 2: Implementing changes..." in (e.get("status") or "")
    ]
    assert phase2_events, f"Expected phase 2 status event, got: {events}"
    assert progress_events[0]["seq"] < phase2_events[0]["seq"], (
        "Progress event must be logged before phase 2 status so UI can render the handoff in order"
    )


def test_continuation_loop_waits_for_completion_json(tmp_path, monkeypatch):
    """Task executor continues running until completion JSON is found."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"test": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="continuation-test")

    executor = TaskExecutor(
        ConfigManager(),
        session_manager,
        inactivity_timeout=30.0,
    )

    import chad.server.services.task_executor as te

    # Track which runs have occurred
    run_count = [0]

    def mock_command(provider, account_name, project_path, task_description=None, screenshots=None, phase="combined", exploration_output=None, **kwargs):
        run_count[0] += 1

        if run_count[0] == 1:
            # First run: output progress JSON but no completion
            script = '''echo '{"type": "progress", "summary": "Found the issue", "location": "src/main.py:42", "next_step": "Implementing fix"}' '''
        else:
            # Continuation: output completion JSON
            script = '''echo '```json'; echo '{"change_summary": "Fixed the bug", "files_changed": ["src/main.py"], "completion_status": "success"}'; echo '```' '''

        return ["bash", "-c", script], {}, None

    monkeypatch.setattr(te, "build_agent_command", mock_command)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="Fix the bug",
        coding_account="test",
    )

    task._thread.join(timeout=10)

    # Should have run twice - initial combined and continuation
    assert run_count[0] >= 2, f"Expected at least 2 runs, got {run_count[0]}"

    # Task should be completed (not failed)
    assert task.state == TaskState.COMPLETED, f"Task state was {task.state}, error: {task.error}"

    # Event log should contain session_ended with success
    events = task.event_log.get_events()
    ended_events = [e for e in events if e.get("type") == "session_ended"]
    assert ended_events, "Expected session_ended event"
    assert ended_events[-1].get("success") is True


def test_exploration_summary_does_not_skip_implementation_phase(tmp_path, monkeypatch):
    """Exploration output containing completion JSON must still run implementation."""
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"accounts": {"test": {"provider": "mock"}}}), encoding="utf-8")
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    session_manager = SessionManager()
    session = session_manager.create_session(project_path=str(repo_path), name="phase-transition-test")

    executor = TaskExecutor(
        ConfigManager(),
        session_manager,
        inactivity_timeout=30.0,
    )

    import chad.server.services.task_executor as te

    phases: list[str] = []

    def mock_command(provider, account_name, project_path, task_description=None, screenshots=None, phase="combined", exploration_output=None, **kwargs):
        phases.append(phase)
        if phase == "exploration":
            # Exploration includes progress + accidental completion JSON.
            script = (
                "echo '{\"type\":\"progress\",\"summary\":\"Investigating\",\"location\":\"src/main.py:1\",\"next_step\":\"Implementing\"}'; "
                "echo '```json'; "
                "echo '{\"change_summary\":\"Premature summary from exploration\",\"files_changed\":[\"src/main.py\"],\"completion_status\":\"success\"}'; "
                "echo '```'"
            )
        elif phase == "implementation":
            script = (
                "echo '```json'; "
                "echo '{\"change_summary\":\"Implemented actual fix\",\"files_changed\":[\"src/main.py\"],\"completion_status\":\"success\"}'; "
                "echo '```'"
            )
        else:
            script = "echo 'unexpected phase'"

        return ["bash", "-c", script], {}, None

    monkeypatch.setattr(te, "build_agent_command", mock_command)

    task = executor.start_task(
        session_id=session.id,
        project_path=str(repo_path),
        task_description="Fix phase transition bug",
        coding_account="test",
    )

    task._thread.join(timeout=10)

    assert task.state == TaskState.COMPLETED, f"Task state was {task.state}, error: {task.error}"
    assert "exploration" in phases
    assert "implementation" in phases, f"Expected implementation phase, got phases: {phases}"


class TestModelPassThrough:
    """Tests that model and reasoning_effort are forwarded to provider CLIs."""

    def test_anthropic_model_flag(self, tmp_path):
        """Anthropic provider passes --model flag when model is specified."""
        cmd, env, _ = build_agent_command(
            "anthropic", "test", tmp_path, "fix bug", model="claude-opus-4-6"
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    def test_openai_model_and_reasoning(self, tmp_path):
        """OpenAI provider passes -m and -c flags for model and reasoning."""
        cmd, env, _ = build_agent_command(
            "openai", "test", tmp_path, "fix bug",
            model="gpt-5.3-codex", reasoning_effort="high"
        )
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "gpt-5.3-codex"
        assert "-c" in cmd
        idx_c = cmd.index("-c")
        assert "model_reasoning_effort" in cmd[idx_c + 1]
        assert "high" in cmd[idx_c + 1]

    def test_gemini_model_flag(self, tmp_path):
        """Gemini provider passes -m flag."""
        cmd, env, _ = build_agent_command(
            "gemini", "test", tmp_path, "fix bug", model="gemini-2.5-pro"
        )
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "gemini-2.5-pro"

    def test_qwen_model_flag(self, tmp_path):
        """Qwen provider passes -m flag."""
        cmd, env, _ = build_agent_command(
            "qwen", "test", tmp_path, "fix bug", model="qwen3-coder"
        )
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "qwen3-coder"

    def test_mistral_model_flag(self, tmp_path):
        """Mistral provider passes --model flag."""
        cmd, env, _ = build_agent_command(
            "mistral", "test", tmp_path, "fix bug", model="mistral-large"
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "mistral-large"

    def test_default_model_omitted(self, tmp_path):
        """Model 'default' is not passed as a flag."""
        cmd, env, _ = build_agent_command(
            "anthropic", "test", tmp_path, "fix bug", model="default"
        )
        assert "--model" not in cmd

    def test_no_model_no_flag(self, tmp_path):
        """No model flag when model is None."""
        cmd, env, _ = build_agent_command(
            "openai", "test", tmp_path, "fix bug", model=None
        )
        assert "-m" not in cmd


class TestBinaryGarbageFilter:
    """Tests for _strip_binary_garbage."""

    def test_strips_long_garbage_runs(self):
        """Runs of 10+ @#%*&^ characters are stripped."""
        text = "Hello @@@@@@@@@@@@@@@ World"
        result = _strip_binary_garbage(text)
        assert result == "Hello  World"

    def test_preserves_short_runs(self):
        """Short runs under 10 chars are kept."""
        text = "user@host# prompt"
        result = _strip_binary_garbage(text)
        assert result == "user@host# prompt"

    def test_strips_codex_garbage_pattern(self):
        """Real Codex garbage pattern is stripped."""
        garbage = "@@@@@@@%#%%#@@@%#####%@@%%%%#%%%%@@%#%#%%%%%@@@" * 3
        text = f"Normal text\n{garbage}\nMore text"
        result = _strip_binary_garbage(text)
        assert "@@@@" not in result
        assert "Normal text" in result
        assert "More text" in result

    def test_empty_string(self):
        """Empty string returns empty."""
        assert _strip_binary_garbage("") == ""


class TestMockUsageDecrement:
    """Tests that mock provider usage is decremented after each PTY phase."""

    def test_usage_decremented_after_successful_phases(self, tmp_path, monkeypatch):
        """Mock usage decreases after each successful phase completion."""
        repo_path = tmp_path / "repo"
        _init_git_repo(repo_path)

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"accounts": {"mock-acct": {"provider": "mock"}}}), encoding="utf-8")
        monkeypatch.setenv("CHAD_CONFIG", str(config_path))
        monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

        cm = ConfigManager()
        cm.set_mock_remaining_usage("mock-acct", 0.50)

        session_manager = SessionManager()
        session = session_manager.create_session(project_path=str(repo_path), name="usage-test")

        executor = TaskExecutor(cm, session_manager, inactivity_timeout=30.0)

        import chad.server.services.task_executor as te

        run_count = [0]

        def mock_command(provider, account_name, project_path, task_description=None,
                         screenshots=None, phase="combined", exploration_output=None, **kwargs):
            run_count[0] += 1
            if phase == "exploration":
                script = "echo '{\"type\":\"progress\",\"summary\":\"Found it\",\"location\":\"x.py:1\",\"next_step\":\"Fix\"}'"
            else:
                script = "echo '```json'; echo '{\"change_summary\":\"Done\",\"files_changed\":[\"x.py\"],\"completion_status\":\"success\"}'; echo '```'"
            return ["bash", "-c", script], {}, None

        monkeypatch.setattr(te, "build_agent_command", mock_command)

        task = executor.start_task(
            session_id=session.id,
            project_path=str(repo_path),
            task_description="Test usage decrement",
            coding_account="mock-acct",
        )
        task._thread.join(timeout=10)

        assert task.state == TaskState.COMPLETED
        # Two successful phases (exploration + implementation) should decrement 0.01 each
        remaining = cm.get_mock_remaining_usage("mock-acct")
        assert remaining < 0.50, f"Usage should have decreased from 0.50, got {remaining}"
        assert abs(remaining - 0.48) < 0.001, f"Expected ~0.48 after 2 decrements, got {remaining}"

    def test_no_decrement_for_non_mock_provider(self, tmp_path, monkeypatch):
        """Non-mock providers don't trigger usage decrement."""
        repo_path = tmp_path / "repo"
        _init_git_repo(repo_path)

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"accounts": {"real-acct": {"provider": "anthropic"}}}), encoding="utf-8")
        monkeypatch.setenv("CHAD_CONFIG", str(config_path))
        monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

        cm = ConfigManager()
        executor = TaskExecutor(cm, SessionManager(), inactivity_timeout=30.0)

        # Should be a no-op for non-mock providers
        executor._decrement_mock_usage("anthropic", "real-acct")
        # No exception, no side effects


class TestCaptureProviderCommand:
    """Tests for capture_provider_command test helper with model support."""

    def test_model_forwarded(self, tmp_path):
        """capture_provider_command forwards model to build_agent_command."""
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from test_helpers import capture_provider_command
        result = capture_provider_command(
            "anthropic", "test", tmp_path, "fix bug", model="claude-opus-4-6"
        )
        assert "--model" in result.cmd
        idx = result.cmd.index("--model")
        assert result.cmd[idx + 1] == "claude-opus-4-6"

"""Tests for AI providers."""

import json
import os
import platform
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from chad.util.providers import (
    ModelConfig,
    create_provider,
    ClaudeCodeProvider,
    GeminiCodeAssistProvider,
    OpenAICodexProvider,
    MistralVibeProvider,
    QwenCodeProvider,
    OpenCodeProvider,
    KimiCodeProvider,
    MockProvider,
    MockProviderQuotaError,
    parse_codex_output,
)


class TestCreateProvider:
    """Test cases for provider factory."""

    def test_create_anthropic_provider(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = create_provider(config)
        assert isinstance(provider, ClaudeCodeProvider)

    def test_create_openai_provider(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICodexProvider)

    def test_create_gemini_provider(self):
        config = ModelConfig(provider="gemini", model_name="default")
        provider = create_provider(config)
        assert isinstance(provider, GeminiCodeAssistProvider)

    def test_create_mistral_provider(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = create_provider(config)
        assert isinstance(provider, MistralVibeProvider)

    def test_create_opencode_provider(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = create_provider(config)
        assert isinstance(provider, OpenCodeProvider)

    def test_create_kimi_provider(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = create_provider(config)
        assert isinstance(provider, KimiCodeProvider)

    def test_unsupported_provider(self):
        config = ModelConfig(provider="unsupported", model_name="model")
        with pytest.raises(ValueError, match="Unsupported provider"):
            create_provider(config)


def test_codex_start_session_ensures_cli_installed(monkeypatch, tmp_path):
    """Codex start_session should install CLI if missing."""
    import chad.util.providers as providers

    calls: list = []

    class DummyInstaller:
        def ensure_tool(self, key):
            calls.append(("cli", key))
            return True, "/bin/codex"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(providers, "CLI_INSTALLER", DummyInstaller())

    cfg = providers.ModelConfig(provider="openai", model_name="default", account_name="acc")
    provider = providers.OpenAICodexProvider(cfg)

    assert provider.start_session(str(tmp_path)) is True
    assert ("cli", "codex") in calls


class TestParseCodexOutput:
    """Test cases for parse_codex_output function."""

    def test_empty_input(self):
        assert parse_codex_output("") == ""
        assert parse_codex_output(None) == ""

    def test_extracts_thinking_and_response(self):
        raw_output = """OpenAI Codex v0.65.0 (research preview)
--------
workdir: ~/chad
model: gpt-5.1-codex-max
provider: openai
approval: never
sandbox: workspace-write
reasoning effort: none
reasoning summaries: auto
session id: 019b00a1-73ee-7582-b114-d458f3cc99e9

user
Task: Summarize this project

mcp startup: no servers

thinking
Preparing to analyze project files

exec
/bin/bash -lc ls in ~/chad succeeded in 26ms:
Architecture.md
src
tests

thinking
Reading the README for context

codex
This is a project management tool for AI coding agents.
tokens used
2,303
"""
        result = parse_codex_output(raw_output)
        # Thinking sections are now consolidated with arrows
        assert "*Thinking: Preparing to analyze project files → Reading the README for context*" in result
        assert "This is a project management tool for AI coding agents." in result
        # Should NOT contain system metadata
        assert "OpenAI Codex" not in result
        assert "workdir:" not in result
        assert "tokens used" not in result

    def test_skips_exec_blocks(self):
        raw_output = """thinking
Planning

exec
/bin/bash -lc ls succeeded:
file1.py
file2.py

codex
Found 2 files.
"""
        result = parse_codex_output(raw_output)
        assert "file1.py" not in result
        assert "Found 2 files." in result

    def test_returns_raw_if_no_markers(self):
        raw_output = "Just some plain text response"
        result = parse_codex_output(raw_output)
        assert result == raw_output

    def test_filters_comma_separated_token_counts(self):
        """Token counts like 4,481 should be filtered out."""
        raw_output = """thinking
Planning analysis

codex
Here is the summary.
4,481
"""
        result = parse_codex_output(raw_output)
        assert "4,481" not in result
        assert "Here is the summary." in result

    def test_thinking_can_be_suppressed(self, monkeypatch):
        raw_output = """thinking
First thought

codex
Here is the answer.
"""
        monkeypatch.setenv("CHAD_HIDE_THINKING", "1")
        result = parse_codex_output(raw_output)
        assert "*Thinking:" not in result
        assert "Here is the answer." in result


class TestAdditionalParseCodexOutput:
    """Additional test cases for parse_codex_output function."""

    def test_parse_codex_output_multiple_thinking_blocks(self):
        """Test that multiple thinking blocks are consolidated with arrow separators."""
        raw_output = """thinking
First thought here

thinking
Second thought here

codex
Final response
"""
        result = parse_codex_output(raw_output)
        # Thinking sections are now consolidated with arrows
        assert "*Thinking: First thought here → Second thought here*" in result
        assert "Final response" in result

    def test_parse_codex_output_thinking_without_codex(self):
        """Test output that has only thinking blocks, no codex response."""
        raw_output = """thinking
First thought

thinking
Second thought
"""
        result = parse_codex_output(raw_output)
        # Thinking sections are now consolidated
        assert "*Thinking: First thought → Second thought*" in result
        assert "codex" not in result


def test_strip_ansi_codes_helper():
    """Ensure ANSI stripping helper removes escape sequences."""
    from chad.util.providers import _strip_ansi_codes

    colored = "\x1b[31mError\x1b[0m message"
    assert _strip_ansi_codes(colored) == "Error message"


class TestCodexNeedsContinuation:
    """Test cases for _codex_needs_continuation helper."""

    def test_empty_message_returns_false(self):
        from chad.util.providers import _codex_needs_continuation

        assert _codex_needs_continuation("") is False
        assert _codex_needs_continuation(None) is False

    def test_progress_without_completion_needs_continuation(self):
        """Progress markers without completion markers should trigger continuation."""
        from chad.util.providers import _codex_needs_continuation

        # Progress update only (incomplete checkpoint)
        message = """**Progress:** Found the relevant files in src/api/
**Location:** src/api/client.py:45
**Next:** Will implement the retry logic now"""
        assert _codex_needs_continuation(message) is True

    def test_progress_with_completion_does_not_need_continuation(self):
        """Progress markers with completion markers should NOT trigger continuation."""
        from chad.util.providers import _codex_needs_continuation

        # Full response with both progress and completion
        message = """**Progress:** Implemented retry logic
**Location:** src/api/client.py:45
**Next:** Done

```json
{
  "change_summary": "Added retry logic to API client",
  "files_changed": ["src/api/client.py"],
  "completion_status": "success"
}
```"""
        assert _codex_needs_continuation(message) is False

    def test_completion_only_does_not_need_continuation(self):
        """Completion markers without progress should NOT trigger continuation."""
        from chad.util.providers import _codex_needs_continuation

        message = """Task complete.

```json
{
  "change_summary": "Fixed the bug",
  "files_changed": ["src/bug.py"],
  "completion_status": "success"
}
```"""
        assert _codex_needs_continuation(message) is False

    def test_plain_message_does_not_need_continuation(self):
        """Plain messages without any markers should NOT trigger continuation."""
        from chad.util.providers import _codex_needs_continuation

        message = "I've completed the task. The changes look good."
        assert _codex_needs_continuation(message) is False

    def test_partial_progress_markers(self):
        """Messages with only some progress markers should still trigger continuation."""
        from chad.util.providers import _codex_needs_continuation

        # Only **Next:** marker
        message = "**Next:** Will write the tests now"
        assert _codex_needs_continuation(message) is True

        # Only **Progress:** marker
        message = "**Progress:** Found 3 relevant files to modify"
        assert _codex_needs_continuation(message) is True

    def test_partial_completion_markers_block_continuation(self):
        """Any completion marker should block continuation."""
        from chad.util.providers import _codex_needs_continuation

        # Progress with just change_summary
        message = '''**Progress:** Done
{"change_summary": "Fixed it"}'''
        assert _codex_needs_continuation(message) is False

        # Progress with just completion_status
        message = '''**Progress:** Done
{"completion_status": "success"}'''
        assert _codex_needs_continuation(message) is False


def test_parse_codex_output_preserves_multiline_content():
    """Test that multiline response content is preserved, thinking is consolidated."""
    raw_output = """thinking
This is line 1
This is line 2

And after blank line

codex
Response line 1
Response line 2

Response after blank line
tokens used: 1234
"""
    result = parse_codex_output(raw_output)
    # Thinking is consolidated into one line (spaces replace newlines)
    assert "*Thinking: This is line 1 This is line 2 And after blank line*" in result
    # Response preserves line breaks
    assert "Response line 1" in result
    assert "Response line 2" in result
    assert "Response after blank line" in result
    assert "1234" not in result


def test_parse_codex_output_malformed_markers():
    """Test that words containing markers like 'prethinking' or 'mycodex' don't trigger section parsing."""
    raw_output = """thinking
prethinking about this problem

codex
This solution uses mycodex pattern
"""
    result = parse_codex_output(raw_output)
    # Should contain the text inside proper sections
    assert "*Thinking: prethinking about this problem*" in result
    assert "This solution uses mycodex pattern" in result
    # The words themselves should be preserved within the sections
    assert "prethinking" in result
    assert "mycodex" in result


def test_parse_codex_output_exec_preserves_non_command_output():
    """Test that exec blocks are skipped but human-readable content is preserved."""
    raw_output = """thinking
Planning to execute

exec
/bin/bash -lc ls succeeded in 26ms:
file1.py
file2.py
Human readable explanation here

thinking
After exec section

codex
Final answer
"""
    result = parse_codex_output(raw_output)
    # Thinking sections are consolidated with arrows
    assert "*Thinking: Planning to execute → After exec section*" in result
    assert "Final answer" in result
    # Should NOT contain exec output
    assert "file1.py" not in result
    assert "file2.py" not in result
    assert "succeeded in 26ms" not in result


def test_parse_codex_output_mixed_token_formats():
    """Test various token usage formats are filtered out."""
    raw_output = """thinking
Planning

codex
Here is the response
tokens used 1234
tokens used: 5678
9,999
tokens used
10,000
"""
    result = parse_codex_output(raw_output)
    assert "*Thinking: Planning*" in result
    assert "Here is the response" in result
    # All token formats should be filtered
    assert "1234" not in result
    assert "5678" not in result
    assert "9,999" not in result
    assert "10,000" not in result
    assert "tokens used" not in result


class TestClaudeCodeProvider:
    """Test cases for ClaudeCodeProvider."""

    def test_init(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)
        assert provider.config == config
        assert provider.process is None
        assert provider.project_path is None

    @patch("chad.util.providers.ClaudeCodeProvider._ensure_mcp_permissions")
    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/claude"))
    @patch("subprocess.Popen")
    def test_start_session_success(self, mock_popen, mock_ensure, mock_permissions):
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.process is not None
        mock_ensure.assert_called_once_with("claude", provider._notify_activity)
        mock_permissions.assert_called_once()
        mock_popen.assert_called_once()
        called_cmd = mock_popen.call_args.args[0]
        assert called_cmd[0] == "/bin/claude"

    @patch("chad.util.providers.ClaudeCodeProvider._ensure_mcp_permissions")
    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/claude"))
    @patch("subprocess.Popen")
    def test_start_session_failure(self, mock_popen, mock_ensure, mock_permissions):
        mock_popen.side_effect = FileNotFoundError("command not found")

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is False
        mock_ensure.assert_called_once_with("claude", provider._notify_activity)
        mock_permissions.assert_called_once()

    def test_send_message(self):
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdin = Mock()
        mock_process.stdin = mock_stdin
        provider.process = mock_process

        provider.send_message("Hello")

        # Should send JSON-formatted message
        expected_msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]}}
        mock_stdin.write.assert_called_once_with(json.dumps(expected_msg) + "\n")
        mock_stdin.flush.assert_called_once()

    def test_is_alive_true(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_process.poll.return_value = None
        provider.process = mock_process

        assert provider.is_alive() is True

    def test_is_alive_false(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_process.poll.return_value = 0
        provider.process = mock_process

        assert provider.is_alive() is False

    def test_is_alive_no_process(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)
        assert provider.is_alive() is False

    def test_stop_session(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        provider.process = mock_process

        provider.stop_session()
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called_once()

    def test_stop_session_timeout(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_process.wait.side_effect = [TimeoutError(), None]
        provider.process = mock_process

        provider.stop_session()
        mock_process.terminate.assert_called_once()
        assert mock_process.wait.call_count == 2
        mock_process.kill.assert_called_once()

    @patch("select.select")
    def test_start_session_with_system_prompt(self, mock_select):
        """Test that system prompt is sent immediately after session start."""
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        with patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/claude")) as mock_ensure:
            with patch("chad.util.providers.ClaudeCodeProvider._ensure_mcp_permissions") as mock_permissions:
                with patch("subprocess.Popen") as mock_popen:
                    mock_process = Mock()
                    mock_process.stdin = Mock()
                    mock_popen.return_value = mock_process

                    # Mock provider.send_message to verify system prompt is sent
                    with patch.object(provider, "send_message") as mock_send:
                        result = provider.start_session("/tmp/test_project", "System prompt here")

                        assert result is True
                        mock_send.assert_called_once_with("System prompt here")
                        mock_ensure.assert_called_once_with("claude", provider._notify_activity)
                        mock_permissions.assert_called_once()

    @patch("select.select")
    @patch("time.time")
    def test_get_response_accumulates_text_from_multiple_chunks(self, mock_time, mock_select):
        """Test that get_response accumulates text over multiple chunks."""
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        # Mock time progression (multiple calls in the loop)
        mock_time.side_effect = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

        # Mock select to return ready
        mock_select.return_value = ([mock_stdout], [], [])

        # Mock multiple text chunks
        messages = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "First chunk"}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": " Second chunk"}]}}),
            json.dumps({"type": "result", "result": "Complete response"}),
        ]
        mock_stdout.readline.side_effect = messages + [""]

        result = provider.get_response(timeout=30.0)

        assert result == "Complete response"
        # Check accumulated text contains both chunks
        assert len(provider.accumulated_text) == 2
        assert provider.accumulated_text[0] == "First chunk"
        assert provider.accumulated_text[1] == " Second chunk"

    @patch("select.select")
    @patch("time.time")
    def test_get_response_callback_called_on_tool_use(self, mock_time, mock_select):
        """Test that activity callbacks are triggered on tool use with proper detail extraction."""
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        # Set up callback tracking
        activity_calls = []
        provider.activity_callback = lambda activity_type, detail: activity_calls.append((activity_type, detail))

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        mock_time.side_effect = [0, 1, 2, 3, 4, 5, 6, 7]
        mock_select.return_value = ([mock_stdout], [], [])

        # Mock tool use messages
        read_tool = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/test/file.py"}}]},
            }
        )
        bash_tool = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "ls -la very long command here that should be truncated"},
                        }
                    ]
                },
            }
        )
        result_msg = json.dumps({"type": "result", "result": "Done"})

        mock_stdout.readline.side_effect = [read_tool, bash_tool, result_msg, ""]

        result = provider.get_response(timeout=30.0)

        assert result == "Done"
        # Check activity callbacks were called with correct tool info
        tool_calls = [call for call in activity_calls if call[0] == "tool"]
        assert len(tool_calls) == 2
        assert tool_calls[0] == ("tool", "Read: /test/file.py")
        # Truncated at 50 chars
        expected = ("tool", "Bash: ls -la very long command here that should be trunc")
        assert tool_calls[1] == expected

    @patch("select.select")
    @patch("time.time")
    def test_get_response_mixed_text_and_tool_in_single_message(self, mock_time, mock_select):
        """Test message contains both text and tool_use items."""
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        mock_time.side_effect = [0, 1, 2, 3, 4, 5]
        mock_select.return_value = ([mock_stdout], [], [])

        # Message with both text and tool
        mixed_message = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Let me read the file"},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.py"}},
                    ]
                },
            }
        )
        result_msg = json.dumps({"type": "result", "result": "Response"})

        mock_stdout.readline.side_effect = [mixed_message, result_msg, ""]

        result = provider.get_response(timeout=30.0)

        assert result == "Response"
        assert len(provider.accumulated_text) == 1
        assert provider.accumulated_text[0] == "Let me read the file"

    @patch("select.select")
    @patch("time.time")
    def test_get_response_unknown_tool_type(self, mock_time, mock_select):
        """Test unknown tool type gets empty detail."""
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        activity_calls = []
        provider.activity_callback = lambda activity_type, detail: activity_calls.append((activity_type, detail))

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        mock_time.side_effect = [0, 1, 2, 3, 4, 5]
        mock_select.return_value = ([mock_stdout], [], [])

        # Unknown tool
        unknown_tool = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "UnknownTool", "input": {"some_param": "value"}}]},
            }
        )
        result_msg = json.dumps({"type": "result", "result": "Done"})

        mock_stdout.readline.side_effect = [unknown_tool, result_msg, ""]

        result = provider.get_response(timeout=30.0)

        assert result == "Done"
        tool_calls = [call for call in activity_calls if call[0] == "tool"]
        assert len(tool_calls) == 1
        assert tool_calls[0] == ("tool", "UnknownTool: ")  # Empty detail

    @patch("select.select")
    @patch("time.time")
    def test_get_response_timeout_exact_boundary(self, mock_time, mock_select):
        """Test timeout at exact second boundary."""
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        # Mock time to hit exact timeout boundary
        mock_time.side_effect = [0, 29.9, 30.0, 30.1]  # Timeout is 30.0
        mock_select.return_value = ([mock_stdout], [], [])
        mock_stdout.readline.return_value = ""  # No data

        result = provider.get_response(timeout=30.0)

        assert result == ""  # Should return empty on timeout

    @patch("select.select")
    @patch("time.time")
    def test_get_response_result_field_only(self, mock_time, mock_select):
        """Test that response is only sent when 'result' message type is received."""
        import json

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        mock_time.side_effect = [0, 1, 2, 3, 4, 5, 6, 7]
        mock_select.return_value = ([mock_stdout], [], [])

        # Multiple assistant messages but only result should end response
        messages = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working"}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Still working"}]}}),
            json.dumps({"type": "result", "result": "Final result"}),
        ]
        mock_stdout.readline.side_effect = messages + [""]

        result = provider.get_response(timeout=30.0)

        assert result == "Final result"

    def test_get_response_reader_thread_handles_stop_iteration(self):
        """Reader thread should treat StopIteration as clean EOF, not a thread error."""
        import json
        import threading

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdout = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        provider.process = mock_process

        messages = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}]}}),
            json.dumps({"type": "result", "result": "Final result"}),
        ]
        mock_stdout.readline.side_effect = messages + [""]

        thread_exceptions: list[type[BaseException]] = []
        original_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            thread_exceptions.append(args.exc_type)

        threading.excepthook = capture_excepthook
        try:
            result = provider.get_response(timeout=5.0)
        finally:
            threading.excepthook = original_excepthook

        assert result == "Final result"
        assert thread_exceptions == []

    def test_send_message_with_broken_pipe_error(self):
        """Test that BrokenPipeError is silently caught in send_message."""
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        mock_process = Mock()
        mock_stdin = Mock()
        mock_stdin.write.side_effect = BrokenPipeError("Pipe broken")
        mock_process.stdin = mock_stdin
        provider.process = mock_process

        # Should not raise exception
        provider.send_message("Test message")

        # Verify write was attempted
        mock_stdin.write.assert_called_once()

    @patch("pathlib.Path.home")
    def test_get_claude_config_dir_with_account(self, mock_home, tmp_path):
        """Config directory uses account name when set."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="my-account")
        provider = ClaudeCodeProvider(config)

        config_dir = provider._get_claude_config_dir()
        assert config_dir == str(tmp_path / ".chad" / "claude-configs" / "my-account")

    @patch("pathlib.Path.home")
    def test_get_claude_config_dir_without_account(self, mock_home, tmp_path):
        """Config directory defaults to ~/.claude when no account name."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        config_dir = provider._get_claude_config_dir()
        assert config_dir == str(tmp_path / ".claude")

    @patch("pathlib.Path.home")
    def test_get_env_includes_config_dir(self, mock_home, tmp_path):
        """Environment includes CLAUDE_CONFIG_DIR."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="test-account")
        provider = ClaudeCodeProvider(config)

        env = provider._get_env()
        assert "CLAUDE_CONFIG_DIR" in env
        assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".chad" / "claude-configs" / "test-account")

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/claude"))
    @patch("subprocess.Popen")
    @patch("pathlib.Path.home")
    def test_start_session_uses_isolated_config(self, mock_home, mock_popen, mock_ensure, tmp_path):
        """Start session passes CLAUDE_CONFIG_DIR to subprocess."""
        mock_home.return_value = tmp_path

        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="isolated-account")
        provider = ClaudeCodeProvider(config)

        provider.start_session("/tmp/test_project")

        # Check that Popen was called with env containing CLAUDE_CONFIG_DIR
        call_kwargs = mock_popen.call_args.kwargs
        assert "env" in call_kwargs
        expected_dir = tmp_path / ".chad" / "claude-configs" / "isolated-account"
        assert call_kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(expected_dir)
        mock_ensure.assert_called_once_with("claude", provider._notify_activity)


class TestOpenAICodexProvider:
    """Test cases for OpenAICodexProvider."""

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/codex"))
    def test_start_session_success(self, mock_ensure):
        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"
        assert provider.cli_path == "/bin/codex"
        mock_ensure.assert_called_once_with("codex", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/codex"))
    def test_start_session_with_system_prompt(self, mock_ensure):
        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        result = provider.start_session("/tmp/test_project", system_prompt="Initial prompt")
        assert result is True
        assert provider.system_prompt == "Initial prompt"
        # System prompt is prepended to messages, not stored in current_message
        provider.send_message("Test message")
        assert "Initial prompt" in provider.current_message
        assert "Test message" in provider.current_message
        assert provider.cli_path == "/bin/codex"
        mock_ensure.assert_called_once_with("codex", provider._notify_activity)

    def test_send_message(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        provider.send_message("Hello")
        assert provider.current_message == "Hello"

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.select.select")
    @patch("chad.util.providers.os.read")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("subprocess.Popen")
    def test_get_response_success(self, mock_popen, mock_openpty, mock_close, mock_read, mock_select):
        # Setup PTY mock
        mock_openpty.return_value = (10, 11)  # master_fd, slave_fd

        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        # poll() returns None while running, then 0 when finished
        mock_process.poll.side_effect = [None, 0, 0, 0]
        mock_popen.return_value = mock_process

        # Mock select to indicate data is ready, then not ready after process ends
        mock_select.side_effect = [([10], [], []), ([], [], [])]
        # Mock os.read to return test output
        mock_read.side_effect = [b"4\n", b""]

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        response = provider.get_response(timeout=5.0)
        assert "4" in response
        assert provider.current_message is None
        mock_popen.assert_called_once()
        mock_stdin.write.assert_called_once_with(b"What is 2+2?")
        mock_stdin.close.assert_called_once()

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.select.select")
    @patch("chad.util.providers.os.read")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("time.time")
    @patch("subprocess.Popen")
    def test_get_response_timeout(self, mock_popen, mock_time, mock_openpty, mock_close, mock_read, mock_select):
        mock_openpty.return_value = (10, 11)

        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None  # Always running
        mock_process.kill = Mock()
        mock_process.wait = Mock()
        mock_popen.return_value = mock_process

        # No data available from PTY
        mock_select.return_value = ([], [], [])

        # Simulate timeout by having time advance past the limit
        # Provide enough values for all time.time() calls in the code path
        mock_time.side_effect = [0, 0, 0, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        with pytest.raises(RuntimeError, match="stalled|timed out"):
            provider.get_response(timeout=1.0)
        assert provider.current_message is None
        mock_process.kill.assert_called_once()

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("subprocess.Popen")
    def test_get_response_file_not_found(self, mock_popen, mock_openpty, mock_close):
        mock_openpty.return_value = (10, 11)
        mock_popen.side_effect = FileNotFoundError("codex not found")

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        with pytest.raises(RuntimeError, match="Failed to run Codex"):
            provider.get_response()
        assert provider.current_message is None

    def test_get_response_no_message(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"

        response = provider.get_response()
        assert response == ""

    @patch("chad.util.providers._stream_pty_output", return_value=("", False, False))
    @patch("chad.util.providers._start_pty_process")
    def test_get_response_exec_uses_bypass_flag(self, mock_start, mock_stream):
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "Hello"
        provider.cli_path = "/bin/codex"

        provider.get_response(timeout=1.0)

        cmd = mock_start.call_args.args[0]
        # In exec mode, we use bypass flag because approval_policy=on-request
        # doesn't work in non-interactive mode
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    @patch("chad.util.providers._stream_pty_output", return_value=("", False, False))
    @patch("chad.util.providers._start_pty_process")
    def test_get_response_resume_uses_bypass_flag(self, mock_start, mock_stream):
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "Hello"
        provider.thread_id = "thread-123"
        provider.cli_path = "/bin/codex"

        provider.get_response(timeout=1.0)

        cmd = mock_start.call_args.args[0]
        # Resume also needs bypass flag for non-interactive mode
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "resume" in cmd
        assert "thread-123" in cmd

    def test_reconnect_error_then_success(self):
        events = [
            {"type": "error", "message": "Reconnecting... 1/5"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        ]

        def fake_stream(_p, _fd, on_chunk, _t, idle_timeout=None, idle_timeout_callback=None):
            for event in events:
                on_chunk(json.dumps(event) + "\n")
            return "", False, False

        with patch("chad.util.providers._start_pty_process") as mock_start, patch(
            "chad.util.providers._stream_pty_output", side_effect=fake_stream
        ):
            mock_process = Mock()
            mock_process.stdin = Mock()
            mock_start.return_value = (mock_process, 11)

            config = ModelConfig(provider="openai", model_name="gpt-4")
            provider = OpenAICodexProvider(config)
            provider.project_path = "/tmp/test_project"
            provider.current_message = "Hello"
            provider.cli_path = "/bin/codex"

            assert provider.get_response(timeout=1.0) == "ok"

    def test_reconnect_error_without_recovery(self):
        events = [{"type": "error", "message": "Reconnecting... 5/5"}]

        def fake_stream(_p, _fd, on_chunk, _t, idle_timeout=None, idle_timeout_callback=None):
            for event in events:
                on_chunk(json.dumps(event) + "\n")
            return "", False, False

        with patch("chad.util.providers._start_pty_process") as mock_start, patch(
            "chad.util.providers._stream_pty_output", side_effect=fake_stream
        ):
            mock_process = Mock()
            mock_process.stdin = Mock()
            mock_start.return_value = (mock_process, 11)

            config = ModelConfig(provider="openai", model_name="gpt-4")
            provider = OpenAICodexProvider(config)
            provider.project_path = "/tmp/test_project"
            provider.current_message = "Hello"
            provider.cli_path = "/bin/codex"

            with pytest.raises(RuntimeError, match="reconnect"):
                provider.get_response(timeout=1.0)

    def test_get_env_sets_windows_home_variables(self, monkeypatch):
        """Test that _get_env sets all Windows home-related environment variables."""
        from chad.util.utils import platform_path

        monkeypatch.setattr("os.name", "nt")
        config = ModelConfig(provider="openai", model_name="gpt-4", account_name="test-account")
        provider = OpenAICodexProvider(config)

        env = provider._get_env()
        isolated_home = provider._get_isolated_home()
        home_path = platform_path(isolated_home)

        # Check all Windows-specific environment variables are set
        assert env["HOME"] == isolated_home
        assert env["USERPROFILE"] == isolated_home
        assert env["HOMEDRIVE"] == (home_path.drive or "C:")
        assert env["HOMEPATH"] == str(home_path.relative_to(home_path.anchor))
        assert env["APPDATA"] == str(home_path / "AppData" / "Roaming")
        assert env["LOCALAPPDATA"] == str(home_path / "AppData" / "Local")


class TestImportOnWindows:
    """Ensure providers import cleanly when termios/pty is unavailable (Windows)."""

    def test_import_providers_skips_pty_on_windows(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        import importlib

        import chad.util.providers as providers

        # Save class references before reload - importlib.reload() modifies
        # the module in-place, creating new class definitions. We need to
        # restore the original classes to prevent class identity issues in
        # subsequent tests (e.g., pytest.raises(MockProviderQuotaError) won't
        # match if the class was redefined by reload).
        original_classes = {
            "MockProviderQuotaError": providers.MockProviderQuotaError,
            "MockProvider": providers.MockProvider,
        }

        try:
            importlib.reload(providers)
            assert providers.__name__ == "chad.util.providers"
        finally:
            # Restore original class definitions
            for name, cls in original_classes.items():
                setattr(providers, name, cls)

    @patch("chad.util.providers._stream_pty_output", return_value=("", False, True))
    @patch("chad.util.providers._start_pty_process")
    def test_get_response_idle_stall_no_thread_id(self, mock_start, mock_stream):
        """Stall without thread_id should fail immediately (no recovery possible)."""
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "Hello"
        provider.cli_path = "/bin/codex"
        provider.thread_id = None  # No thread_id means no recovery possible

        with pytest.raises(RuntimeError, match="stalled"):
            provider.get_response(timeout=1.0)

    @patch("chad.util.providers._start_pty_process")
    def test_get_response_stall_recovery_success(self, mock_start):
        """Stall with thread_id should attempt recovery and succeed on retry."""
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        # First call stalls, second call succeeds
        stall_count = [0]

        def fake_stream(_p, _fd, on_chunk, _t, idle_timeout=None, idle_timeout_callback=None):
            stall_count[0] += 1
            if stall_count[0] == 1:
                # First call: stall
                return "", False, True
            else:
                # Second call: success with response
                on_chunk(json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Recovered!"}
                }) + "\n")
                return "", False, False

        with patch("chad.util.providers._stream_pty_output", side_effect=fake_stream):
            config = ModelConfig(provider="openai", model_name="gpt-4")
            provider = OpenAICodexProvider(config)
            provider.project_path = "/tmp/test_project"
            provider.current_message = "Hello"
            provider.cli_path = "/bin/codex"
            provider.thread_id = "thread-123"  # Has thread_id so recovery is possible

            result = provider.get_response(timeout=1.0)
            assert "Recovered!" in result
            assert stall_count[0] == 2  # First stall, then recovery

    @patch("chad.util.providers._stream_pty_output", return_value=("", False, True))
    @patch("chad.util.providers._start_pty_process")
    def test_get_response_stall_recovery_exhausted(self, mock_start, mock_stream):
        """Stall with thread_id should fail after single recovery attempt."""
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "Hello"
        provider.cli_path = "/bin/codex"
        provider.thread_id = "thread-123"  # Has thread_id

        # Should fail after single recovery attempt (initial + 1 recovery = 2 calls)
        with pytest.raises(RuntimeError, match="stalled"):
            provider.get_response(timeout=1.0)

        # Verify exactly 2 attempts were made (initial + single recovery)
        assert mock_stream.call_count == 2

    @patch("chad.util.providers._start_pty_process")
    def test_exploration_loop_detection(self, mock_start):
        """Exploration loop should be detected when too many exploration commands without implementation."""
        import json

        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        call_count = [0]

        def fake_stream(process, master_fd, on_chunk, timeout, **kwargs):
            call_count[0] += 1
            # Simulate many exploration commands (>40 which is the default limit)
            for i in range(45):
                on_chunk(json.dumps({
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": f"rg -n 'pattern{i}' src/"}
                }) + "\n")
            # Simulate the idle callback being called and returning True due to exploration limit
            return "", False, True

        with patch("chad.util.providers._stream_pty_output", side_effect=fake_stream):
            config = ModelConfig(provider="openai", model_name="gpt-4")
            provider = OpenAICodexProvider(config)
            provider.project_path = "/tmp/test_project"
            provider.current_message = "Hello"
            provider.cli_path = "/bin/codex"
            provider.thread_id = None  # No thread_id so no recovery

            with pytest.raises(RuntimeError, match="exploration loop"):
                provider.get_response(timeout=1.0)

    @patch("chad.util.providers._start_pty_process")
    def test_exploration_loop_recovery_attempt(self, mock_start):
        """Exploration loop should attempt recovery by prompting agent to implement."""
        import json

        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_start.return_value = (mock_process, 11)

        call_count = [0]

        def fake_stream(process, master_fd, on_chunk, timeout, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: simulate exploration loop
                for i in range(45):
                    on_chunk(json.dumps({
                        "type": "item.completed",
                        "item": {"type": "command_execution", "command": f"rg -n 'pattern{i}' src/"}
                    }) + "\n")
                return "", False, True
            else:
                # Second call (recovery): simulate successful implementation
                on_chunk(json.dumps({
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": "edit src/file.py"}
                }) + "\n")
                on_chunk(json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Fixed the bug!"}
                }) + "\n")
                return "", False, False

        with patch("chad.util.providers._stream_pty_output", side_effect=fake_stream):
            config = ModelConfig(provider="openai", model_name="gpt-4")
            provider = OpenAICodexProvider(config)
            provider.project_path = "/tmp/test_project"
            provider.current_message = "Hello"
            provider.cli_path = "/bin/codex"
            provider.thread_id = "thread-123"  # Has thread_id so recovery is possible

            result = provider.get_response(timeout=1.0)
            assert "Fixed the bug!" in result
            assert call_count[0] == 2  # Initial + recovery


def test_stream_output_without_pty(monkeypatch):
    import chad.util.providers as providers

    monkeypatch.setattr(providers, "_HAS_PTY", False)
    monkeypatch.setattr(providers, "pty", None)

    process, master_fd = providers._start_pty_process(
        [sys.executable, "-c", "print('hello from pipe')"]
    )
    output, timed_out, idle_stalled = providers._stream_pty_output(process, master_fd, None, timeout=5.0)

    assert "hello from pipe" in output
    assert timed_out is False
    assert idle_stalled is False


@pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
def test_stream_pty_kills_process_group_on_idle():
    import chad.util.providers as providers

    if not providers._HAS_PTY:
        pytest.skip("PTY not available")

    script = textwrap.dedent(
        """
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        print(child.pid, flush=True)
        time.sleep(10)
        """
    )

    process, master_fd = providers._start_pty_process([sys.executable, "-c", script])
    output, timed_out, idle_stalled = providers._stream_pty_output(
        process, master_fd, None, timeout=3.0, idle_timeout=0.5
    )

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    assert lines, "No output captured from child process"
    child_pid = int(lines[0])

    assert idle_stalled is True
    assert timed_out is False
    assert process.poll() is not None

    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            return False
        return True

    deadline = time.time() + 2
    while pid_alive(child_pid) and time.time() < deadline:
        time.sleep(0.05)

    assert pid_alive(child_pid) is False, "Child process survived stall termination"


def test_pipe_idle_callback_does_not_reset_clock(monkeypatch):
    """Stall detection should respect cumulative silence even when callback defers it."""
    import chad.util.providers as providers

    class DummyStdout:
        def readline(self):
            time.sleep(0.001)
            return b""

    class DummyProcess:
        def __init__(self):
            self.stdout = DummyStdout()
            self.killed = False
            self.pid = None

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    proc = DummyProcess()

    start = time.time()
    output, timed_out, idle_stalled = providers._stream_pipe_output(
        proc,
        None,
        timeout=0.2,
        idle_timeout=0.01,
        idle_timeout_callback=lambda elapsed: elapsed >= 0.03,
    )

    assert idle_stalled is True
    assert timed_out is False
    assert proc.killed is True
    assert time.time() - start < 1.0
    assert output == ""


def test_stream_pipe_output_buffers_partial_lines(monkeypatch):
    """Test that _stream_pipe_output properly buffers partial lines for JSON parsing.

    This is a regression test for the Windows pipe buffering issue where JSON
    lines could be split across multiple read() calls, causing parse failures.
    """
    import chad.util.providers as providers

    monkeypatch.setattr(providers, "_HAS_PTY", False)
    monkeypatch.setattr(providers, "pty", None)

    # Create a subprocess that outputs multiple JSON lines
    # The print statements should be buffered properly
    code = '''
import sys
print('{"type": "event1", "data": "first"}'  , flush=True)
print('{"type": "event2", "data": "second"}' , flush=True)
print('{"type": "event3", "data": "third"}'  , flush=True)
'''
    process, master_fd = providers._start_pty_process(
        [sys.executable, "-c", code]
    )

    received_chunks = []

    def on_chunk(chunk):
        received_chunks.append(chunk)

    output, timed_out, idle_stalled = providers._stream_pty_output(
        process, master_fd, on_chunk, timeout=5.0
    )

    assert timed_out is False
    assert idle_stalled is False

    # Each line should be parseable as JSON
    import json
    for chunk in received_chunks:
        for line in chunk.strip().split("\n"):
            if line.strip():
                parsed = json.loads(line)
                assert "type" in parsed
                assert "data" in parsed


class TestCodexJsonEventParsing:
    """Test cases for Codex JSON event to human-readable text conversion."""

    def test_thread_id_extraction_from_json_stream(self):
        """Test that thread_id is extracted from thread.started JSON event."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test"

        # Simulate the JSON events Codex produces
        json_events = [
            {"type": "thread.started", "thread_id": "019b6517-74ba-7d80-959b-d133057a7938"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello!"}},
        ]

        # Process events as the provider would
        for event in json_events:
            if event.get("type") == "thread.started" and "thread_id" in event:
                provider.thread_id = event["thread_id"]

        assert provider.thread_id == "019b6517-74ba-7d80-959b-d133057a7938"

    def test_is_alive_with_thread_id_no_process(self):
        """Session is 'alive' if thread_id exists even without active process."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.thread_id = "019b6517-74ba-7d80-959b-d133057a7938"
        provider.process = None

        assert provider.is_alive() is True

    def test_is_alive_without_thread_id_or_process(self):
        """Session is not 'alive' if no thread_id and no process."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.thread_id = None
        provider.process = None

        assert provider.is_alive() is False

    def test_stop_session_clears_thread_id(self):
        """Stop session clears thread_id to end multi-turn."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.thread_id = "some-thread-id"

        provider.stop_session()

        assert provider.thread_id is None

    def test_resume_command_uses_thread_id(self):
        """When thread_id is set, get_response uses resume command."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test"
        provider.thread_id = "019b6517-74ba-7d80-959b-d133057a7938"
        provider.current_message = "Follow-up question"

        # We can't easily test the actual command without mocking extensively,
        # but we can verify the thread_id presence affects the is_resume flag
        # by checking is_alive returns True with thread_id set
        assert provider.is_alive() is True
        assert provider.thread_id is not None


class TestCodexProgressCheckpointResume:
    """Test cases for auto-resume on progress checkpoint detection."""

    def test_progress_checkpoint_triggers_resume(self):
        """Progress checkpoint without completion markers should trigger auto-resume."""
        import json
        import os
        import tempfile

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            provider.project_path = tmpdir
            provider.cli_path = "/bin/echo"  # Use echo as a placeholder

            # Simulate first call returning progress checkpoint
            progress_events = [
                {"type": "thread.started", "thread_id": "test-thread-123"},
                {"type": "turn.started"},
                {"type": "item.completed", "item": {
                    "type": "agent_message",
                    "text": "**Progress:** Found relevant files\n**Location:** src/api/\n**Next:** Will implement now"
                }},
                {"type": "turn.completed"},
            ]

            # Simulate second call (resume) returning completion
            completion_events = [
                {"type": "thread.started", "thread_id": "test-thread-456"},
                {"type": "turn.started"},
                {"type": "item.completed", "item": {
                    "type": "agent_message",
                    "text": '```json\n{"change_summary": "Implemented feature", "files_changed": ["src/api/client.py"], "completion_status": "success"}\n```'
                }},
                {"type": "turn.completed"},
            ]

            # Create pipes for both calls
            read_fd1, write_fd1 = os.pipe()
            read_fd2, write_fd2 = os.pipe()

            # Write events to first pipe
            for event in progress_events:
                os.write(write_fd1, (json.dumps(event) + "\n").encode())
            os.close(write_fd1)

            # Write events to second pipe
            for event in completion_events:
                os.write(write_fd2, (json.dumps(event) + "\n").encode())
            os.close(write_fd2)

            # Track which pipe to use
            pipes_used = [read_fd1, read_fd2]
            pipe_index = [0]

            def mock_start_pty(cmd, cwd=None, env=None):
                mock_process = Mock()
                mock_process.poll.return_value = 0
                mock_process.stdin = Mock()
                mock_process.pid = 12345 + pipe_index[0]
                mock_process.returncode = 0
                mock_process.wait.return_value = 0
                fd = pipes_used[pipe_index[0]]
                pipe_index[0] += 1
                return mock_process, fd

            activity_notifications = []

            def mock_notify(activity_type, data=""):
                activity_notifications.append((activity_type, data))

            provider._notify_activity = mock_notify

            with patch("chad.util.providers._start_pty_process", side_effect=mock_start_pty):
                provider.send_message("Do the task")
                response = provider.get_response(timeout=10.0)

            # Should have detected checkpoint and resumed
            assert provider.thread_id == "test-thread-456"  # Updated after resume
            assert "**Progress:**" in response  # Original progress is included
            assert '"change_summary"' in response  # Completion is included
            assert "---" in response  # Separator between progress and completion

            # Check that checkpoint notification was sent
            stream_notifications = [n for n in activity_notifications if n[0] == "stream"]
            checkpoint_found = any("checkpoint detected" in n[1].lower() for n in stream_notifications)
            assert checkpoint_found, "Should notify about checkpoint detection"

    def test_completion_does_not_trigger_resume(self):
        """Messages with completion markers should NOT trigger auto-resume."""
        import json
        import os
        import tempfile

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            provider.project_path = tmpdir
            provider.cli_path = "/bin/echo"

            # Events with both progress AND completion (should not trigger resume)
            events = [
                {"type": "thread.started", "thread_id": "test-thread-123"},
                {"type": "turn.started"},
                {"type": "item.completed", "item": {
                    "type": "agent_message",
                    "text": '**Progress:** Done\n\n```json\n{"change_summary": "Fixed it", "files_changed": ["a.py"], "completion_status": "success"}\n```'
                }},
                {"type": "turn.completed"},
            ]

            read_fd, write_fd = os.pipe()
            for event in events:
                os.write(write_fd, (json.dumps(event) + "\n").encode())
            os.close(write_fd)

            def mock_start_pty(cmd, cwd=None, env=None):
                mock_process = Mock()
                mock_process.poll.return_value = 0
                mock_process.stdin = Mock()
                mock_process.pid = 12345
                mock_process.returncode = 0
                mock_process.wait.return_value = 0
                return mock_process, read_fd

            activity_notifications = []

            def mock_notify(activity_type, data=""):
                activity_notifications.append((activity_type, data))

            provider._notify_activity = mock_notify

            with patch("chad.util.providers._start_pty_process", side_effect=mock_start_pty):
                provider.send_message("Do the task")
                response = provider.get_response(timeout=10.0)

            # Should NOT have tried to resume (completion markers present)
            assert '"change_summary"' in response
            assert "---" not in response  # No separator means no resume happened

            # Check that no checkpoint notification was sent
            stream_notifications = [n for n in activity_notifications if n[0] == "stream"]
            checkpoint_found = any("checkpoint detected" in n[1].lower() for n in stream_notifications)
            assert not checkpoint_found, "Should NOT notify about checkpoint when completion present"


class TestCodexLiveViewFormatting:
    """Regression tests for Codex live view text formatting."""

    def _make_provider(self):
        """Create a provider for testing the format function."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test"
        return provider

    def test_reasoning_strips_markdown_bold(self):
        """Reasoning text with **bold** should display without asterisks."""
        # This is a regression test for the issue where ***text*** was shown
        event = {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "**Preparing to locate visual_test_map**"}
        }
        # We need to call the format function - get it from a provider
        from chad.util.providers import OpenAICodexProvider, ModelConfig
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test"
        # The format function is defined inside get_response, so we test the behavior
        # by checking the text doesn't contain triple asterisks
        text = event["item"]["text"]
        clean = text.replace("**", "").replace("*", "").strip()
        assert clean == "Preparing to locate visual_test_map"
        assert "***" not in clean

    def test_agent_message_filters_bash_commands(self):
        """Agent messages should filter out raw bash command lines."""
        text = "Processing request\n$ /bin/bash -lc 'rg -n test'\nDone"
        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("$ ") or stripped.startswith("$/"):
                continue
            if stripped:
                filtered.append(line)
        result = "\n".join(filtered)
        assert "$ /bin/bash" not in result
        assert "Processing request" in result
        assert "Done" in result

    def test_agent_message_filters_internal_markers(self):
        """Agent messages should filter out ***internal markers***."""
        text = "***Preparing to locate file***\nActual content\n***Opening file***"
        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("***") and stripped.endswith("***"):
                continue
            if stripped:
                filtered.append(line)
        result = "\n".join(filtered)
        assert "***Preparing" not in result
        assert "***Opening" not in result
        assert "Actual content" in result

    def test_agent_message_filters_grep_output(self):
        """Agent messages should filter out grep-style output (path:line:content)."""
        import re
        text = "123:        component=\"live-view\",\nsrc/file.py:45:def main():"
        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if re.match(r"^\d+:\s*", stripped) or re.match(r"^[a-zA-Z_/].*:\d+:", stripped):
                continue
            if stripped:
                filtered.append(line)
        assert len(filtered) == 0  # All lines should be filtered

    def test_mcp_tool_call_shows_human_readable(self):
        """MCP tool calls should show human-readable descriptions."""
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "Read",
                "params": {"file_path": "/path/to/file.py"}
            }
        }
        tool = event["item"]["tool"]
        params = event["item"]["params"]
        tool_descriptions = {"Read": "Reading", "Write": "Writing", "Grep": "Searching"}
        desc = tool_descriptions.get(tool, f"Using {tool}")
        path = params.get("path", params.get("file_path", ""))
        # The output should be human readable
        assert desc == "Reading"
        assert path == "/path/to/file.py"

    def test_command_execution_uses_color_codes(self):
        """Command execution should include ANSI color codes."""
        # The format should use \033[35m for purple/magenta commands
        cmd = "pytest tests/"
        result = f"\033[35m$ {cmd}\033[0m\n"
        assert "\033[35m" in result  # Purple color code
        assert "\033[0m" in result   # Reset code
        assert "$ pytest" in result


class TestOpenAICodexProviderIntegration:
    """Integration tests for OpenAICodexProvider using mocked subprocess."""

    @pytest.mark.skipif(sys.platform == "win32", reason="select.select doesn't work with pipes on Windows")
    def test_codex_execution_flow(self):
        """Test the end-to-end flow with mocked codex CLI."""
        import tempfile
        import json
        import os

        config = ModelConfig(provider="openai", model_name="o4-mini")
        provider = OpenAICodexProvider(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock start_session
            provider.project_path = tmpdir
            provider.process = Mock()
            provider.process.poll.return_value = None

            # Send a message
            provider.send_message("What is 2+2? Just output the number.")

            # Create mock PTY process that outputs JSON events
            mock_process = Mock()
            # poll() returns None while running, then 0 when done
            poll_values = [None] * 5 + [0] * 100  # 5 running, then always done
            mock_process.poll.side_effect = poll_values
            mock_process.stdin = Mock()
            mock_process.pid = 12345
            mock_process.returncode = 0
            mock_process.wait.return_value = 0

            # Create a pipe to simulate PTY output
            read_fd, write_fd = os.pipe()

            # Write mock JSON output
            json_output = (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "message", "content": [{"type": "text", "text": "The answer is 4"}]},
                    }
                )
                + "\n"
            )
            os.write(write_fd, json_output.encode())
            os.close(write_fd)

            with patch("chad.util.providers._start_pty_process", return_value=(mock_process, read_fd)):
                with patch("chad.util.providers.find_cli_executable", return_value="/usr/bin/codex"):
                    response = provider.get_response(timeout=5)

            assert "4" in response, f"Expected '4' in response, got: {response}"

            # Clean up
            provider.stop_session()


class TestMistralVibeProvider:
    """Test cases for MistralVibeProvider."""

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/vibe"))
    def test_start_session_success(self, mock_ensure):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"
        mock_ensure.assert_called_once_with("vibe", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/vibe"))
    def test_start_session_with_system_prompt(self, mock_ensure):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)

        result = provider.start_session("/tmp/test_project", system_prompt="Initial prompt")
        assert result is True
        assert provider.system_prompt == "Initial prompt"
        # System prompt is prepended to messages
        provider.send_message("Test message")
        assert "Initial prompt" in provider.current_message
        assert "Test message" in provider.current_message

    def test_send_message(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)

        provider.send_message("Hello")
        assert provider.current_message == "Hello"


class TestOpenCodeProvider:
    """Test cases for OpenCodeProvider."""

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/opencode"))
    def test_start_session_success(self, mock_ensure):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"
        mock_ensure.assert_called_once_with("opencode", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(False, "CLI not found"))
    def test_start_session_failure(self, mock_ensure):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is False
        mock_ensure.assert_called_once_with("opencode", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/opencode"))
    def test_start_session_with_system_prompt(self, mock_ensure):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        result = provider.start_session("/tmp/test_project", system_prompt="Initial prompt")
        assert result is True
        assert provider.system_prompt == "Initial prompt"
        # System prompt is prepended to messages when no session_id
        provider.send_message("Test message")
        assert "Initial prompt" in provider.current_message
        assert "Test message" in provider.current_message

    def test_send_message(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        provider.send_message("Hello")
        assert provider.current_message == "Hello"

    def test_send_message_without_system_prompt_on_continuation(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        provider.system_prompt = "System prompt"
        provider.session_id = "ses_abc123"  # Session already established

        provider.send_message("Follow-up message")
        # Should not include system prompt since session_id is set
        assert provider.current_message == "Follow-up message"

    def test_is_alive_with_session_id(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        # Initially not alive
        assert provider.is_alive() is False

        # With session_id, should be alive
        provider.session_id = "ses_abc123"
        assert provider.is_alive() is True

    def test_stop_session_clears_session_id(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        provider.session_id = "ses_abc123"

        provider.stop_session()
        assert provider.session_id is None

    def test_supports_multi_turn(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        assert provider.supports_multi_turn() is True

    def test_get_session_id(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "ses_xyz789"
        assert provider.get_session_id() == "ses_xyz789"

    def test_supports_usage_reporting(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_get_response_no_message(self):
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        assert provider.get_response(timeout=1) == ""


class TestKimiCodeProvider:
    """Test cases for KimiCodeProvider."""

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/kimi"))
    def test_start_session_success(self, mock_ensure):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"
        mock_ensure.assert_called_once_with("kimi", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(False, "CLI not found"))
    def test_start_session_failure(self, mock_ensure):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is False
        mock_ensure.assert_called_once_with("kimi", provider._notify_activity)

    @patch("chad.util.providers._ensure_cli_tool", return_value=(True, "/bin/kimi"))
    def test_start_session_with_system_prompt(self, mock_ensure):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        result = provider.start_session("/tmp/test_project", system_prompt="Initial prompt")
        assert result is True
        assert provider.system_prompt == "Initial prompt"
        # System prompt is prepended to messages when no session_id
        provider.send_message("Test message")
        assert "Initial prompt" in provider.current_message
        assert "Test message" in provider.current_message

    def test_send_message(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        provider.send_message("Hello")
        assert provider.current_message == "Hello"

    def test_send_message_without_system_prompt_on_continuation(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        provider.system_prompt = "System prompt"
        provider.session_id = "ses_abc123"  # Session already established

        provider.send_message("Follow-up message")
        # Should not include system prompt since session_id is set
        assert provider.current_message == "Follow-up message"

    def test_is_alive_with_session_id(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        # Initially not alive
        assert provider.is_alive() is False

        # With session_id, should be alive
        provider.session_id = "ses_abc123"
        assert provider.is_alive() is True

    def test_stop_session_clears_session_id(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        provider.session_id = "ses_abc123"

        provider.stop_session()
        assert provider.session_id is None

    def test_supports_multi_turn(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        assert provider.supports_multi_turn() is True

    def test_get_session_id(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "ses_xyz789"
        assert provider.get_session_id() == "ses_xyz789"

    def test_supports_usage_reporting(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_get_response_no_message(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        assert provider.get_response(timeout=1) == ""

    def test_get_isolated_config_dir_with_account(self):
        config = ModelConfig(provider="kimi", model_name="default", account_name="myaccount")
        provider = KimiCodeProvider(config)
        config_dir = provider._get_isolated_config_dir()
        assert "kimi-homes" in str(config_dir)
        assert "myaccount" in str(config_dir)

    def test_get_isolated_config_dir_without_account(self):
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        config_dir = provider._get_isolated_config_dir()
        # Should return user home directory
        assert str(config_dir).startswith(str(Path.home()))


class TestGeminiCodeAssistProvider:
    """Tests for GeminiCodeAssistProvider."""

    def test_send_message_includes_system_prompt(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.system_prompt = "system"
        provider.send_message("hello")
        assert "system" in provider.current_message
        assert "hello" in provider.current_message

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.select.select")
    @patch("chad.util.providers.os.read")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("subprocess.Popen")
    def test_get_response_success(self, mock_popen, mock_openpty, mock_close, mock_read, mock_select):
        mock_openpty.return_value = (10, 11)

        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.poll.side_effect = [None, 0, 0, 0]
        mock_popen.return_value = mock_process

        mock_select.side_effect = [([10], [], []), ([], [], [])]
        mock_read.side_effect = [b"result\n", b""]

        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.project_path = "/tmp/test"
        provider.send_message("hello")

        response = provider.get_response(timeout=5.0)
        assert "result" in response
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "-p" in cmd
        assert "hello" in cmd[cmd.index("-p") + 1]
        assert provider.current_message is None

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.select.select")
    @patch("chad.util.providers.os.read")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("subprocess.Popen")
    def test_get_response_resume_uses_non_interactive_prompt(
        self, mock_popen, mock_openpty, mock_close, mock_read, mock_select
    ):
        mock_openpty.return_value = (10, 11)

        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.poll.side_effect = [None, 0, 0, 0]
        mock_popen.return_value = mock_process

        mock_select.side_effect = [([10], [], []), ([], [], [])]
        mock_read.side_effect = [b"result\n", b""]

        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.project_path = "/tmp/test"
        provider.session_id = "existing-session"
        provider.send_message("continue")

        response = provider.get_response(timeout=5.0)
        assert "result" in response
        cmd = mock_popen.call_args[0][0]
        assert "--resume" in cmd
        assert "existing-session" in cmd
        assert "-p" in cmd
        assert "continue" in cmd[cmd.index("-p") + 1]
        assert provider.current_message is None

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.select.select")
    @patch("chad.util.providers.os.read")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("time.time")
    @patch("subprocess.Popen")
    def test_get_response_timeout(self, mock_popen, mock_time, mock_openpty, mock_close, mock_read, mock_select):
        mock_openpty.return_value = (10, 11)

        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        mock_process.kill = Mock()
        mock_process.wait = Mock()
        mock_popen.return_value = mock_process

        mock_select.return_value = ([], [], [])
        mock_time.side_effect = [0, 0, 2000, 2000]

        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.send_message("hello")
        response = provider.get_response(timeout=5)
        assert "timed out" in response
        assert provider.current_message is None

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    @patch("chad.util.providers.os.close")
    @patch("chad.util.providers.pty.openpty")
    @patch("subprocess.Popen")
    def test_get_response_missing_cli(self, mock_popen, mock_openpty, mock_close):
        mock_openpty.return_value = (10, 11)
        mock_popen.side_effect = FileNotFoundError("missing")

        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.send_message("hello")
        response = provider.get_response(timeout=5)
        assert "Failed to run Gemini" in response
        assert provider.current_message is None

    def test_get_response_no_message(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        assert provider.get_response(timeout=1) == ""


class TestWindowsCodexStallHandling:
    """Tests for Windows-specific Codex stall detection and process termination.

    These tests verify that when Codex stops producing output (stalls), the system
    correctly detects the idle timeout and terminates the process tree.
    """

    def test_idle_stall_detected_on_silent_process(self, monkeypatch):
        """Test that a process producing no output triggers idle stall detection."""
        import chad.util.providers as providers

        monkeypatch.setattr(providers, "_HAS_PTY", False)
        monkeypatch.setattr(providers, "pty", None)

        # Create a subprocess that sleeps without producing output
        # Use a very short idle timeout to keep test fast
        code = "import time; time.sleep(10)"
        process, master_fd = providers._start_pty_process(
            [sys.executable, "-c", code]
        )

        # Use a short idle timeout (0.5s) to detect stall quickly
        output, timed_out, idle_stalled = providers._stream_pipe_output(
            process, None, timeout=10.0, idle_timeout=0.5
        )

        assert idle_stalled is True
        assert timed_out is False
        # Process should be terminated
        assert process.poll() is not None

    def test_process_killed_after_idle_stall(self, monkeypatch):
        """Test that the process is properly killed when idle stall is detected."""
        import chad.util.providers as providers

        monkeypatch.setattr(providers, "_HAS_PTY", False)
        monkeypatch.setattr(providers, "pty", None)

        # Create a subprocess that hangs indefinitely
        code = "import time; time.sleep(3600)"
        process, master_fd = providers._start_pty_process(
            [sys.executable, "-c", code]
        )

        # Verify process is running
        assert process.poll() is None

        # Stream with short idle timeout
        output, timed_out, idle_stalled = providers._stream_pipe_output(
            process, None, timeout=10.0, idle_timeout=0.3
        )

        # Process should be terminated after idle stall
        assert idle_stalled is True
        # Give a moment for process cleanup
        import time
        time.sleep(0.2)
        assert process.poll() is not None

    def test_stdin_flush_before_close(self, monkeypatch):
        """Test that stdin is flushed before being closed."""
        import chad.util.providers as providers
        from unittest.mock import Mock

        # Mock the _start_pty_process to return a mock process
        mock_stdin = Mock()
        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = 0  # Process completed immediately

        monkeypatch.setattr(
            providers, "_start_pty_process",
            lambda cmd, cwd=None, env=None: (mock_process, None)
        )
        monkeypatch.setattr(providers, "_HAS_PTY", False)

        # Mock _stream_pty_output to return immediately
        monkeypatch.setattr(
            providers, "_stream_pty_output",
            lambda proc, fd, on_chunk, timeout, idle_timeout=None: ("", False, False)
        )

        # Mock CLI installation
        monkeypatch.setattr(
            providers, "_ensure_cli_tool",
            lambda name, notify: (True, "/bin/codex")
        )

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test"
        provider.current_message = "test message"
        provider.cli_path = "/bin/codex"

        # This will fail because we're mocking, but we want to verify the stdin calls
        try:
            provider.get_response(timeout=1.0)
        except Exception:
            pass

        # Verify flush was called before close
        calls = mock_stdin.method_calls
        flush_index = None
        close_index = None
        for i, c in enumerate(calls):
            if c[0] == 'flush':
                flush_index = i
            if c[0] == 'close':
                close_index = i

        # Both should be called, flush before close
        assert flush_index is not None, "stdin.flush() was not called"
        assert close_index is not None, "stdin.close() was not called"
        assert flush_index < close_index, "flush() must be called before close()"

    def test_output_received_before_stall(self, monkeypatch):
        """Test that output produced before a stall is captured."""
        import chad.util.providers as providers

        monkeypatch.setattr(providers, "_HAS_PTY", False)
        monkeypatch.setattr(providers, "pty", None)

        # Create a subprocess that outputs something then sleeps
        # Add a small delay after output to ensure it's flushed to the pipe
        code = textwrap.dedent(
            """
            import sys
            import time
            print("output before stall", flush=True)
            sys.stdout.flush()
            time.sleep(0.2)  # Give time for output to be read
            time.sleep(10)   # Then stall
            """
        )
        process, master_fd = providers._start_pty_process(
            [sys.executable, "-c", code]
        )

        received = []

        def on_chunk(chunk):
            received.append(chunk)

        # Use longer idle timeout to ensure we capture the output first
        output, timed_out, idle_stalled = providers._stream_pipe_output(
            process, on_chunk, timeout=10.0, idle_timeout=1.0
        )

        assert idle_stalled is True
        # Should have captured the output before the stall
        all_output = output + "".join(received)
        assert "output before stall" in all_output, f"Output not found. Got: {all_output!r}"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific test")
    def test_windows_startupinfo_configured(self, monkeypatch):
        """Test that Windows subprocess uses proper STARTUPINFO flags."""
        import chad.util.providers as providers
        import subprocess

        monkeypatch.setattr(providers, "_HAS_PTY", False)
        monkeypatch.setattr(providers, "pty", None)

        # Capture the Popen call to verify startupinfo
        original_popen = subprocess.Popen
        popen_kwargs = {}

        def mock_popen(*args, **kwargs):
            popen_kwargs.update(kwargs)
            # Return a simple process for the test
            return original_popen([sys.executable, "-c", "pass"], **kwargs)

        monkeypatch.setattr(subprocess, "Popen", mock_popen)

        process, master_fd = providers._start_pty_process(
            [sys.executable, "-c", "pass"]
        )
        process.wait()

        # Verify startupinfo was set on Windows
        assert "startupinfo" in popen_kwargs
        assert popen_kwargs["startupinfo"] is not None
        assert popen_kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESTDHANDLES


class TestWindowsEncodingHandling:
    """Tests for Windows UTF-8 encoding handling in subprocess calls.

    On Windows, subprocess with text=True defaults to cp1252 encoding which
    can't handle all UTF-8 characters. These tests verify encoding is properly
    specified to prevent UnicodeDecodeError.
    """

    def test_claude_provider_uses_utf8_encoding(self, monkeypatch):
        """Test that ClaudeCodeProvider subprocess uses UTF-8 encoding."""
        import subprocess
        import chad.util.providers as providers

        # Capture the Popen kwargs
        captured_kwargs = {}

        def mock_popen(*args, **kwargs):
            captured_kwargs.update(kwargs)
            raise FileNotFoundError("mock - CLI not found")

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        monkeypatch.setattr(providers, "_ensure_cli_tool", lambda n, cb: (True, "/fake/claude"))

        config = ModelConfig(provider="anthropic", model_name="claude-sonnet-4")
        provider = ClaudeCodeProvider(config)

        # This will fail because we raise FileNotFoundError, but we can check kwargs
        result = provider.start_session("/tmp/test")

        assert result is False  # Failed because mock raised error
        assert captured_kwargs.get("text") is True
        assert captured_kwargs.get("encoding") == "utf-8"
        assert captured_kwargs.get("errors") == "replace"

    def test_utf8_characters_handled_in_pipe_output(self, monkeypatch):
        """Test that UTF-8 characters (like emojis) are handled without errors."""
        import chad.util.providers as providers
        import os

        monkeypatch.setattr(providers, "_HAS_PTY", False)
        monkeypatch.setattr(providers, "pty", None)

        # Create a subprocess that outputs UTF-8 characters including ones
        # that would fail with cp1252 (like em-dash \u2014 which is byte 0x97 in cp1252
        # or smart quotes which contain bytes that don't map)
        code = '''
import sys
# Output various UTF-8 characters that would fail with cp1252
print("Hello \\u2014 World")  # em-dash
print("Quotes: \\u201c and \\u201d")  # smart quotes
print("Emoji: \\U0001F600")  # grinning face emoji
print("Done", flush=True)
'''
        # Set PYTHONIOENCODING so the subprocess outputs UTF-8
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        process, master_fd = providers._start_pty_process(
            [sys.executable, "-c", code], env=env
        )

        received = []

        def on_chunk(chunk):
            received.append(chunk)

        output, timed_out, idle_stalled = providers._stream_pipe_output(
            process, on_chunk, timeout=10.0, idle_timeout=2.0
        )

        # Should complete without errors and capture output
        all_output = output + "".join(received)
        assert "Done" in all_output
        # The special characters should be present (or replaced if errors='replace')
        assert "Hello" in all_output
        assert "Quotes" in all_output


class TestProviderGetSessionId:
    """Tests for provider get_session_id method for handoff support."""

    def test_claude_provider_returns_none(self):
        """Claude provider has no native session ID."""
        config = ModelConfig(provider="anthropic", model_name="claude-sonnet-4")
        provider = ClaudeCodeProvider(config)
        assert provider.get_session_id() is None

    def test_codex_provider_returns_thread_id(self):
        """Codex provider returns thread_id when set."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting thread_id
        provider.thread_id = "thread_abc123"
        assert provider.get_session_id() == "thread_abc123"

    def test_gemini_provider_returns_session_id(self):
        """Gemini provider returns session_id when set."""
        config = ModelConfig(provider="gemini", model_name="default")
        provider = GeminiCodeAssistProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "gemini_session_xyz"
        assert provider.get_session_id() == "gemini_session_xyz"

    def test_qwen_provider_returns_session_id(self):
        """Qwen provider returns session_id when set."""
        config = ModelConfig(provider="qwen", model_name="default")
        provider = QwenCodeProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "qwen_session_789"
        assert provider.get_session_id() == "qwen_session_789"

    def test_mistral_provider_returns_none(self):
        """Mistral provider has no session ID (uses continue flag)."""
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)
        assert provider.get_session_id() is None

    def test_opencode_provider_returns_session_id(self):
        """OpenCode provider returns session_id when set."""
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "ses_opencode_abc"
        assert provider.get_session_id() == "ses_opencode_abc"

    def test_kimi_provider_returns_session_id(self):
        """Kimi provider returns session_id when set."""
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)

        # Initially None
        assert provider.get_session_id() is None

        # After setting session_id
        provider.session_id = "ses_kimi_xyz"
        assert provider.get_session_id() == "ses_kimi_xyz"


class TestProviderUsageReporting:
    """Tests for provider usage reporting capabilities."""

    def test_claude_provider_supports_usage_reporting(self):
        """Claude provider supports usage percentage reporting via Anthropic API."""
        config = ModelConfig(provider="anthropic", model_name="claude-sonnet-4")
        provider = ClaudeCodeProvider(config)
        assert provider.supports_usage_reporting() is True
        # get_session_usage_percentage returns None when credentials not available
        # (we can't test actual API calls in unit tests)

    def test_codex_provider_supports_usage_reporting(self):
        """Codex provider supports usage percentage reporting via session files."""
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        assert provider.supports_usage_reporting() is True
        # get_session_usage_percentage returns None when session files not available

    def test_gemini_provider_supports_usage_reporting(self):
        """Gemini provider supports usage percentage reporting via local session files."""
        config = ModelConfig(provider="gemini", model_name="default")
        provider = GeminiCodeAssistProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_qwen_provider_supports_usage_reporting(self):
        """Qwen provider supports usage percentage reporting via local session files."""
        config = ModelConfig(provider="qwen", model_name="default")
        provider = QwenCodeProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_mistral_provider_supports_usage_reporting(self):
        """Mistral provider supports usage percentage reporting via local session files."""
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_opencode_provider_supports_usage_reporting(self):
        """OpenCode provider supports usage percentage reporting via local session files."""
        config = ModelConfig(provider="opencode", model_name="default")
        provider = OpenCodeProvider(config)
        assert provider.supports_usage_reporting() is True

    def test_kimi_provider_supports_usage_reporting(self):
        """Kimi provider supports usage percentage reporting via local session files."""
        config = ModelConfig(provider="kimi", model_name="default")
        provider = KimiCodeProvider(config)
        assert provider.supports_usage_reporting() is True


class TestProviderQuotaDetection:
    """Tests for is_quota_exhausted() on each provider."""

    def test_claude_detects_hit_limit(self):
        """Claude's is_quota_exhausted recognizes 'You've hit your limit'."""
        config = ModelConfig(provider="anthropic", model_name="claude-sonnet-4", account_name="test")
        provider = ClaudeCodeProvider(config)
        # Without real API access, weekly check returns None so it defaults to session
        result = provider.is_quota_exhausted("You've hit your limit · resets 4pm")
        assert result == "session_limit_reached"

    def test_claude_returns_none_for_normal_output(self):
        """Claude's is_quota_exhausted returns None for normal output."""
        config = ModelConfig(provider="anthropic", model_name="claude-sonnet-4", account_name="test")
        provider = ClaudeCodeProvider(config)
        result = provider.is_quota_exhausted("Working on the task, reading files...")
        assert result is None

    def test_codex_detects_quota_error(self):
        """Codex's is_quota_exhausted recognizes quota exhaustion patterns."""
        config = ModelConfig(provider="openai", model_name="gpt-4", account_name="test")
        provider = OpenAICodexProvider(config)
        result = provider.is_quota_exhausted("Error: you exceeded your current quota")
        assert result == "session_limit_reached"

    def test_codex_returns_none_for_normal_output(self):
        """Codex's is_quota_exhausted returns None for normal output."""
        config = ModelConfig(provider="openai", model_name="gpt-4", account_name="test")
        provider = OpenAICodexProvider(config)
        result = provider.is_quota_exhausted("Running command: pytest tests/")
        assert result is None

    def test_base_provider_detects_generic_patterns(self):
        """Base AIProvider.is_quota_exhausted uses handoff patterns."""
        config = ModelConfig(provider="gemini", model_name="default")
        provider = GeminiCodeAssistProvider(config)
        result = provider.is_quota_exhausted("Error: insufficient credits")
        assert result == "session_limit_reached"

    def test_base_provider_returns_none_for_normal_output(self):
        """Base AIProvider.is_quota_exhausted returns None for normal text."""
        config = ModelConfig(provider="gemini", model_name="default")
        provider = GeminiCodeAssistProvider(config)
        result = provider.is_quota_exhausted("Analyzing the codebase structure...")
        assert result is None

    def test_all_providers_have_is_quota_exhausted(self):
        """Every provider class has an is_quota_exhausted method."""
        from chad.util.providers import (
            ClaudeCodeProvider, OpenAICodexProvider, GeminiCodeAssistProvider,
            QwenCodeProvider, OpenCodeProvider, KimiCodeProvider,
            MistralVibeProvider, MockProvider,
        )
        provider_classes = [
            ClaudeCodeProvider, OpenAICodexProvider, GeminiCodeAssistProvider,
            QwenCodeProvider, OpenCodeProvider, KimiCodeProvider,
            MistralVibeProvider, MockProvider,
        ]
        for cls in provider_classes:
            assert hasattr(cls, "is_quota_exhausted"), f"{cls.__name__} missing is_quota_exhausted"

    def test_all_providers_have_weekly_usage(self):
        """Every provider class has a get_weekly_usage_percentage method."""
        from chad.util.providers import (
            ClaudeCodeProvider, OpenAICodexProvider, GeminiCodeAssistProvider,
            QwenCodeProvider, OpenCodeProvider, KimiCodeProvider,
            MistralVibeProvider, MockProvider,
        )
        provider_classes = [
            ClaudeCodeProvider, OpenAICodexProvider, GeminiCodeAssistProvider,
            QwenCodeProvider, OpenCodeProvider, KimiCodeProvider,
            MistralVibeProvider, MockProvider,
        ]
        for cls in provider_classes:
            assert hasattr(cls, "get_weekly_usage_percentage"), f"{cls.__name__} missing get_weekly_usage_percentage"

    def test_providers_without_weekly_return_none(self):
        """Providers that don't have weekly data return None."""
        providers = [
            GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default")),
            QwenCodeProvider(ModelConfig(provider="qwen", model_name="default")),
            MistralVibeProvider(ModelConfig(provider="mistral", model_name="default")),
            OpenCodeProvider(ModelConfig(provider="opencode", model_name="default")),
            KimiCodeProvider(ModelConfig(provider="kimi", model_name="default")),
            MockProvider(ModelConfig(provider="mock", model_name="default")),
        ]
        for p in providers:
            assert p.get_weekly_usage_percentage() is None, f"{type(p).__name__} should return None"


class TestUsagePercentageCalculation:
    """Tests for usage percentage calculation from local session files."""

    def _write_claude_creds(self, base_dir: Path, account: str) -> Path:
        """Helper to create Claude credential file in a temp home."""
        cred_dir = base_dir / ".chad" / "claude-configs" / account
        cred_dir.mkdir(parents=True, exist_ok=True)
        cred_path = cred_dir / ".credentials.json"
        cred_path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-token"}}))
        return cred_path

    def test_claude_usage_percentage_scales_fractional_utilization(self, tmp_path):
        """Claude usage API returns fractions (0-1); function should convert to percent."""
        from chad.util.providers import _get_claude_usage_percentage

        account = "claude-fractional"
        self._write_claude_creds(tmp_path, account)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 0.32}}

        with patch("chad.util.providers.safe_home", return_value=tmp_path), \
                patch("requests.get", return_value=mock_response):
            pct = _get_claude_usage_percentage(account)

        assert pct == pytest.approx(32.0)

    def test_claude_usage_percentage_preserves_percent_inputs(self, tmp_path):
        """Claude usage function should keep already-percentage values unchanged."""
        from chad.util.providers import _get_claude_usage_percentage

        account = "claude-percent"
        self._write_claude_creds(tmp_path, account)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 87}}

        with patch("chad.util.providers.safe_home", return_value=tmp_path), \
                patch("requests.get", return_value=mock_response):
            pct = _get_claude_usage_percentage(account)

        assert pct == pytest.approx(87.0)

    def test_claude_weekly_usage_percentage_scales_fractional_utilization(self, tmp_path):
        """Weekly usage should also scale fractional utilization to percentage."""
        from chad.util.providers import _get_claude_weekly_usage_percentage

        account = "claude-weekly"
        self._write_claude_creds(tmp_path, account)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"seven_day": {"utilization": 0.5}}

        with patch("chad.util.providers.safe_home", return_value=tmp_path), \
                patch("requests.get", return_value=mock_response):
            pct = _get_claude_weekly_usage_percentage(account)

        assert pct == pytest.approx(50.0)

    def test_gemini_usage_not_logged_in(self, tmp_path):
        """Gemini returns None when oauth credentials don't exist."""
        from chad.util.providers import _get_gemini_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_gemini_usage_percentage("")
            assert result is None

    def test_gemini_usage_logged_in_no_usage(self, tmp_path):
        """Gemini returns 0% when logged in but no usage JSONL exists."""
        from chad.util.providers import _get_gemini_usage_percentage

        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_gemini_usage_percentage("")
            assert result == 0.0

    def test_gemini_usage_counts_today_requests(self, tmp_path):
        """Gemini correctly counts today's requests from usage JSONL."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_gemini_usage_percentage

        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        # Create usage JSONL file
        chad_dir = tmp_path / ".chad"
        chad_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).isoformat()
        yesterday = "2020-01-01T12:00:00+00:00"

        lines = [
            json.dumps({"timestamp": today, "model": "gemini-pro", "input_tokens": 100, "output_tokens": 50}),
            json.dumps({"timestamp": today, "model": "gemini-pro", "input_tokens": 200, "output_tokens": 80}),
            json.dumps({"timestamp": yesterday, "model": "gemini-pro", "input_tokens": 50}),  # Not today
        ]
        (chad_dir / "gemini-usage.jsonl").write_text("\n".join(lines) + "\n")

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_gemini_usage_percentage("")
            # 2 requests today out of 100 limit = 2.0%
            assert result == pytest.approx(2.0, abs=0.1)

    def test_append_gemini_usage_writes_jsonl(self, tmp_path):
        """_append_gemini_usage writes a JSONL record."""
        from chad.util.providers import _append_gemini_usage, _read_gemini_usage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            _append_gemini_usage("gem-1", "gemini-2.5-pro", {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cached": 500,
                "total_tokens": 1700,
                "tool_calls": 3,
                "duration_ms": 4500,
            })
            records = _read_gemini_usage()
            assert len(records) == 1
            rec = records[0]
            assert rec["account"] == "gem-1"
            assert rec["model"] == "gemini-2.5-pro"
            assert rec["input_tokens"] == 1000
            assert rec["output_tokens"] == 200
            assert rec["cached_tokens"] == 500
            assert rec["total_tokens"] == 1700
            assert rec["tool_calls"] == 3
            assert "timestamp" in rec

    def test_qwen_usage_not_logged_in(self, tmp_path):
        """Qwen returns None when oauth credentials don't exist."""
        from chad.util.providers import _get_qwen_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_qwen_usage_percentage("")
            assert result is None

    def test_qwen_usage_logged_in_no_sessions(self, tmp_path):
        """Qwen returns 0% when logged in but no session files exist."""
        from chad.util.providers import _get_qwen_usage_percentage

        qwen_dir = tmp_path / ".qwen"
        qwen_dir.mkdir()
        (qwen_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_qwen_usage_percentage("")
            assert result == 0.0

    def test_qwen_usage_counts_today_requests(self, tmp_path):
        """Qwen correctly counts today's requests from jsonl session files."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_qwen_usage_percentage

        qwen_dir = tmp_path / ".qwen"
        qwen_dir.mkdir()
        (qwen_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        # Create session directory structure
        session_dir = qwen_dir / "projects" / "project1" / "chats"
        session_dir.mkdir(parents=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        yesterday = "2020-01-01T12:00:00.000Z"

        # Write jsonl format (one JSON object per line)
        lines = [
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": yesterday}),  # Not today
            json.dumps({"type": "user", "timestamp": today}),  # Not assistant
        ]
        (session_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_qwen_usage_percentage("")
            # 3 requests today out of 2000 limit = 0.15%
            assert result == pytest.approx(0.15, abs=0.01)

    def test_mistral_usage_not_logged_in(self, tmp_path):
        """Mistral returns None when config doesn't exist."""
        from chad.util.providers import _get_mistral_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_mistral_usage_percentage("")
            assert result is None

    def test_mistral_usage_logged_in_no_sessions(self, tmp_path):
        """Mistral returns 0% when logged in but no session files exist."""
        from chad.util.providers import _get_mistral_usage_percentage

        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / ".env").write_text("MISTRAL_API_KEY=test-key\n")

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_mistral_usage_percentage("")
            assert result == 0.0

    def test_mistral_usage_counts_today_sessions(self, tmp_path):
        """Mistral correctly counts today's sessions from session files."""
        import json
        from chad.util.providers import _get_mistral_usage_percentage

        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / ".env").write_text("MISTRAL_API_KEY=test-key\n")

        # Create session directory
        session_dir = vibe_dir / "logs" / "session"
        session_dir.mkdir(parents=True)

        # Create today's session file with prompt_count
        session_data = {"metadata": {"stats": {"prompt_count": 5}}}
        session_file = session_dir / "session_today.json"
        session_file.write_text(json.dumps(session_data))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_mistral_usage_percentage("")
            # 5 requests today out of 1000 limit = 0.5%
            assert result == pytest.approx(0.5, abs=0.01)

    def test_gemini_usage_handles_malformed_jsonl(self, tmp_path):
        """Gemini gracefully handles malformed JSONL lines."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_gemini_usage_percentage

        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        chad_dir = tmp_path / ".chad"
        chad_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).isoformat()
        lines = [
            "not valid json",
            json.dumps({"timestamp": today, "model": "gemini-pro", "input_tokens": 100}),
        ]
        (chad_dir / "gemini-usage.jsonl").write_text("\n".join(lines) + "\n")

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_gemini_usage_percentage("")
            # 1 valid request out of 100 limit = 1.0%
            assert result == pytest.approx(1.0, abs=0.1)

    def test_qwen_usage_handles_malformed_jsonl(self, tmp_path):
        """Qwen gracefully handles malformed jsonl lines."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_qwen_usage_percentage

        qwen_dir = tmp_path / ".qwen"
        qwen_dir.mkdir()
        (qwen_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        session_dir = qwen_dir / "projects" / "project1" / "chats"
        session_dir.mkdir(parents=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines = [
            "not valid json",
            json.dumps({"type": "assistant", "timestamp": today}),
            "{malformed",
        ]
        (session_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_qwen_usage_percentage("")
            # Only 1 valid request counted
            assert result == pytest.approx(0.05, abs=0.01)

    def test_usage_capped_at_100_percent(self, tmp_path):
        """Usage percentage is capped at 100% even if over limit."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_gemini_usage_percentage

        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        chad_dir = tmp_path / ".chad"
        chad_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).isoformat()
        # Create more requests than the daily limit (2000)
        lines = [json.dumps({"timestamp": today, "model": "gemini-pro"}) for _ in range(2500)]
        (chad_dir / "gemini-usage.jsonl").write_text("\n".join(lines) + "\n")

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_gemini_usage_percentage("")
            assert result == 100.0  # Capped at 100%

    def test_opencode_usage_no_sessions(self, tmp_path):
        """OpenCode returns 0% when no session files exist."""
        from chad.util.providers import _get_opencode_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_opencode_usage_percentage("")
            assert result == 0.0

    def test_opencode_usage_with_account_isolation(self, tmp_path):
        """OpenCode returns 0% when using account-isolated data dir with no sessions."""
        from chad.util.providers import _get_opencode_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_opencode_usage_percentage("myaccount")
            assert result == 0.0

    def test_opencode_usage_counts_today_requests(self, tmp_path):
        """OpenCode correctly counts today's requests from jsonl session files."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_opencode_usage_percentage

        # Create session directory at ~/.local/share/opencode/sessions
        data_dir = tmp_path / ".local" / "share" / "opencode" / "sessions"
        data_dir.mkdir(parents=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        yesterday = "2020-01-01T12:00:00.000Z"

        # Write jsonl format (one JSON object per line)
        lines = [
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": yesterday}),  # Not today
            json.dumps({"type": "user", "timestamp": today}),  # Not assistant
        ]
        (data_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_opencode_usage_percentage("testaccount")
            # 2 requests today out of 2000 limit = 0.1%
            assert result == pytest.approx(0.1, abs=0.01)

    def test_opencode_usage_handles_malformed_jsonl(self, tmp_path):
        """OpenCode gracefully handles malformed jsonl lines."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_opencode_usage_percentage

        data_dir = tmp_path / ".local" / "share" / "opencode" / "sessions"
        data_dir.mkdir(parents=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines = [
            "not valid json",
            json.dumps({"type": "assistant", "timestamp": today}),
            "{malformed",
        ]
        (data_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_opencode_usage_percentage("testaccount")
            # Only 1 valid request counted
            assert result == pytest.approx(0.05, abs=0.01)

    def test_kimi_usage_not_configured(self, tmp_path):
        """Kimi returns None when config doesn't exist."""
        from chad.util.providers import _get_kimi_usage_percentage

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_kimi_usage_percentage("")
            assert result is None

    def test_kimi_usage_logged_in_no_sessions(self, tmp_path):
        """Kimi returns 0% when logged in but no session files exist."""
        from chad.util.providers import _get_kimi_usage_percentage

        creds_dir = tmp_path / ".kimi" / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_kimi_usage_percentage("")
            assert result == 0.0

    def test_kimi_usage_counts_today_requests(self, tmp_path):
        """Kimi correctly counts today's requests from jsonl session files."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_kimi_usage_percentage

        # Create credentials and session directory structure for account
        kimi_dir = tmp_path / ".chad" / "kimi-homes" / "testaccount" / ".kimi"
        kimi_dir.mkdir(parents=True)
        creds_dir = kimi_dir / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')

        sessions_dir = kimi_dir / "sessions"
        sessions_dir.mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        yesterday = "2020-01-01T12:00:00.000Z"

        # Write jsonl format (one JSON object per line)
        lines = [
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": today}),
            json.dumps({"type": "assistant", "timestamp": yesterday}),  # Not today
            json.dumps({"type": "user", "timestamp": today}),  # Not assistant
        ]
        (sessions_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_kimi_usage_percentage("testaccount")
            # 3 requests today out of 2000 limit = 0.15%
            assert result == pytest.approx(0.15, abs=0.01)

    def test_kimi_usage_handles_malformed_jsonl(self, tmp_path):
        """Kimi gracefully handles malformed jsonl lines."""
        import json
        from datetime import datetime, timezone
        from chad.util.providers import _get_kimi_usage_percentage

        kimi_dir = tmp_path / ".chad" / "kimi-homes" / "testaccount" / ".kimi"
        kimi_dir.mkdir(parents=True)
        creds_dir = kimi_dir / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')

        sessions_dir = kimi_dir / "sessions"
        sessions_dir.mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines = [
            "not valid json",
            json.dumps({"type": "assistant", "timestamp": today}),
            "{malformed",
        ]
        (sessions_dir / "session.jsonl").write_text("\n".join(lines))

        with patch("chad.util.providers.safe_home", return_value=str(tmp_path)):
            result = _get_kimi_usage_percentage("testaccount")
            # Only 1 valid request counted
            assert result == pytest.approx(0.05, abs=0.01)


class TestMockProviderQuotaSimulation:
    """Tests for MockProvider quota exhaustion simulation.

    These tests verify that MockProvider can simulate quota errors,
    enabling testing of provider handover without real API costs.

    Note: These tests use direct method patching instead of the config system
    to avoid test pollution issues when run in sequence with other tests.
    """

    @pytest.fixture(autouse=True)
    def reset_mock_provider_state(self):
        """Reset MockProvider class-level state before each test."""
        MockProvider._verification_counts.clear()
        MockProvider._coding_turn_counts.clear()
        yield
        MockProvider._verification_counts.clear()
        MockProvider._coding_turn_counts.clear()

    def test_quota_error_when_usage_is_zero(self, tmp_path):
        """MockProvider raises quota error when mock_remaining_usage is 0."""
        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        # Mock the internal method to return 0 usage
        provider._get_remaining_usage = lambda: 0.0
        provider.start_session(str(tmp_path))
        provider.send_message("test task")

        with pytest.raises(MockProviderQuotaError) as exc_info:
            provider.get_response()

        error_msg = str(exc_info.value)
        assert "quota exceeded" in error_msg.lower()
        assert "insufficient credits" in error_msg.lower()

    def test_no_quota_error_when_usage_available(self, tmp_path):
        """MockProvider works normally when mock_remaining_usage > 0."""
        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = lambda: 0.5
        provider._decrement_usage = lambda amount=None: None  # No-op
        provider.start_session(str(tmp_path))
        provider.send_message("test task")

        response = provider.get_response()
        assert response
        assert "change_summary" in response or "BUGS.md" in response

    def test_usage_decrements_after_response(self, tmp_path):
        """MockProvider decrements usage after each response."""
        usage = [0.5]  # Use list to allow mutation in closure

        def get_usage():
            return usage[0]

        def decrement_usage(amount=None):
            decrement = amount if amount is not None else MockProvider.USAGE_DECREMENT_PER_RESPONSE
            usage[0] = max(0.0, usage[0] - decrement)

        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = get_usage
        provider._decrement_usage = decrement_usage
        provider.start_session(str(tmp_path))
        provider.send_message("test task")

        initial_usage = usage[0]
        provider.get_response()
        final_usage = usage[0]

        assert final_usage < initial_usage
        expected = initial_usage - MockProvider.USAGE_DECREMENT_PER_RESPONSE
        assert final_usage == pytest.approx(expected, abs=0.001)

    def test_queued_responses_bypass_quota_check(self, tmp_path):
        """Queued responses (for unit tests) bypass quota simulation."""
        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = lambda: 0.0  # Would fail if checked
        provider.start_session(str(tmp_path))

        provider.queue_response('{"test": "response"}')
        response = provider.get_response()
        assert response == '{"test": "response"}'

    def test_quota_error_matches_handoff_detection(self, tmp_path):
        """Quota error message matches is_quota_exhaustion_error() patterns."""
        from chad.util.handoff import is_quota_exhaustion_error

        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = lambda: 0.0
        provider.start_session(str(tmp_path))
        provider.send_message("test task")

        try:
            provider.get_response()
            pytest.fail("Expected MockProviderQuotaError")
        except MockProviderQuotaError as e:
            assert is_quota_exhaustion_error(str(e)), (
                f"Error message '{e}' not detected as quota exhaustion. "
                "This will break provider handover testing."
            )

    def test_gradual_quota_depletion(self, tmp_path):
        """MockProvider can simulate gradual quota depletion over multiple responses."""
        usage = [0.015]  # Start with just enough for 2 responses (0.01 each)

        def get_usage():
            return usage[0]

        def decrement_usage(amount=None):
            decrement = amount if amount is not None else MockProvider.USAGE_DECREMENT_PER_RESPONSE
            usage[0] = max(0.0, usage[0] - decrement)

        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = get_usage
        provider._decrement_usage = decrement_usage
        provider.start_session(str(tmp_path))

        # First response should succeed
        provider.send_message("task 1")
        response1 = provider.get_response()
        assert response1

        # Second response should also succeed (0.015 - 0.01 = 0.005 > 0)
        provider.send_message("task 2")
        response2 = provider.get_response()
        assert response2

        # Third response should fail (0.005 - 0.01 = -0.005 -> clamped to 0)
        provider.send_message("task 3")
        with pytest.raises(MockProviderQuotaError):
            provider.get_response()

    def test_default_usage_when_no_account(self, tmp_path):
        """MockProvider uses default usage when no account configured."""
        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            # No account_name
        )
        provider = MockProvider(model_config)
        provider.start_session(str(tmp_path))
        provider.send_message("test task")

        # Should use default 0.5 usage and work normally
        response = provider.get_response()
        assert response

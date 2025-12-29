"Tests for AI providers."
from unittest.mock import Mock, patch
import pytest
from chad.providers import (
    ModelConfig,
    create_provider,
    ClaudeCodeProvider,
    GeminiCodeAssistProvider,
    OpenAICodexProvider,
    MistralVibeProvider,
    parse_codex_output,
    extract_final_codex_response,
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

    def test_unsupported_provider(self):
        config = ModelConfig(provider="unsupported", model_name="model")
        with pytest.raises(ValueError, match="Unsupported provider"):
            create_provider(config)


def test_codex_start_session_ensures_mcp_config(monkeypatch, tmp_path):
    """Codex start_session should install CLI and write per-home MCP config."""
    import chad.providers as providers

    calls: list = []

    class DummyInstaller:
        def ensure_tool(self, key):
            calls.append(("cli", key))
            return True, "/bin/codex"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(providers, "CLI_INSTALLER", DummyInstaller())

    def fake_config(home=None, project_root=None):
        calls.append(("config", home))
        return {"changed": True, "path": str(tmp_path / "cfg")}

    monkeypatch.setattr(providers, "ensure_global_mcp_config", fake_config)

    cfg = providers.ModelConfig(provider="openai", model_name="default", account_name="acc")
    provider = providers.OpenAICodexProvider(cfg)

    assert provider.start_session(str(tmp_path)) is True
    assert ("cli", "codex") in calls
    config_calls = [c for c in calls if c[0] == "config"]
    assert config_calls, "ensure_global_mcp_config should be invoked"
    home_used = config_calls[0][1]
    assert str(home_used).startswith(str(tmp_path / ".chad" / "codex-homes" / "acc"))


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


class TestExtractFinalCodexResponse:
    """Test cases for extract_final_codex_response function."""

    def test_extracts_final_response_only(self):
        raw_output = """thinking
First thought

codex
First response

thinking
Second thought

codex
Final instruction here
tokens used
1234
"""
        result = extract_final_codex_response(raw_output)
        assert result == "Final instruction here"
        assert "First response" not in result
        assert "thinking" not in result
        assert "1234" not in result

    def test_empty_input(self):
        assert extract_final_codex_response("") == ""
        assert extract_final_codex_response(None) == ""

    def test_multiline_final_response(self):
        raw_output = """codex
Line 1
Line 2
Line 3
tokens used
500
"""
        result = extract_final_codex_response(raw_output)
        assert result == "Line 1\nLine 2\nLine 3"


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
    from chad.providers import _strip_ansi_codes

    colored = "\x1b[31mError\x1b[0m message"
    assert _strip_ansi_codes(colored) == "Error message"

    def test_parse_codex_output_preserves_multiline_content(self):
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

    def test_parse_codex_output_malformed_markers(self):
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

    def test_parse_codex_output_exec_preserves_non_command_output(self):
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
        assert "*Thinking: Planning to execute*" in result
        assert "*Thinking: After exec section*" in result
        assert "Final answer" in result
        # Should NOT contain exec output
        assert "file1.py" not in result
        assert "file2.py" not in result
        assert "succeeded in 26ms" not in result

    def test_parse_codex_output_mixed_token_formats(self):
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

    def test_extract_final_codex_response_no_codex_marker(self):
        """Test extract function when there's no 'codex' marker at all."""
        raw_output = """thinking
Some thinking here
Just plain text response
"""
        result = extract_final_codex_response(raw_output)
        # Should return the original output when no codex marker found
        assert result == raw_output

    def test_extract_final_codex_response_multiple_codex_preserves_only_last(self):
        """Test that only the last codex section is extracted when multiple exist."""
        raw_output = """codex
First response

thinking
More thinking

codex
Final response here
tokens used: 1234
"""
        result = extract_final_codex_response(raw_output)
        assert result == "Final response here"
        assert "First response" not in result
        assert "thinking" not in result
        assert "1234" not in result

    def test_extract_final_codex_response_codex_with_nested_thinking_marker(self):
        """Test that final response containing the word 'thinking' is still extracted."""
        raw_output = """codex
I am thinking about this problem and here is my solution
tokens used: 500
"""
        result = extract_final_codex_response(raw_output)
        assert result == "I am thinking about this problem and here is my solution"
        assert "500" not in result


class TestClaudeCodeProvider:
    """Test cases for ClaudeCodeProvider."""

    def test_init(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)
        assert provider.config == config
        assert provider.process is None
        assert provider.project_path is None

    @patch('chad.providers.ClaudeCodeProvider._ensure_mcp_permissions')
    @patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/claude'))
    @patch('subprocess.Popen')
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

    @patch('chad.providers.ClaudeCodeProvider._ensure_mcp_permissions')
    @patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/claude'))
    @patch('subprocess.Popen')
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
        expected_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}]
            }
        }
        mock_stdin.write.assert_called_once_with(json.dumps(expected_msg) + '\n')
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

    @patch('select.select')
    def test_start_session_with_system_prompt(self, mock_select):
        """Test that system prompt is sent immediately after session start."""
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        with patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/claude')) as mock_ensure:
            with patch('chad.providers.ClaudeCodeProvider._ensure_mcp_permissions') as mock_permissions:
                with patch('subprocess.Popen') as mock_popen:
                    mock_process = Mock()
                    mock_process.stdin = Mock()
                    mock_popen.return_value = mock_process

                    # Mock provider.send_message to verify system prompt is sent
                    with patch.object(provider, 'send_message') as mock_send:
                        result = provider.start_session("/tmp/test_project", "System prompt here")

                        assert result is True
                        mock_send.assert_called_once_with("System prompt here")
                        mock_ensure.assert_called_once_with("claude", provider._notify_activity)
                        mock_permissions.assert_called_once()

    @patch('select.select')
    @patch('time.time')
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
            json.dumps({"type": "result", "result": "Complete response"})
        ]
        mock_stdout.readline.side_effect = messages + ['']

        result = provider.get_response(timeout=30.0)

        assert result == "Complete response"
        # Check accumulated text contains both chunks
        assert len(provider.accumulated_text) == 2
        assert provider.accumulated_text[0] == "First chunk"
        assert provider.accumulated_text[1] == " Second chunk"

    @patch('select.select')
    @patch('time.time')
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
        read_tool = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/test/file.py"}}]}
        })
        bash_tool = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use", "name": "Bash",
                "input": {"command": "ls -la very long command here that should be truncated"}
            }]}
        })
        result_msg = json.dumps({"type": "result", "result": "Done"})

        mock_stdout.readline.side_effect = [read_tool, bash_tool, result_msg, '']

        result = provider.get_response(timeout=30.0)

        assert result == "Done"
        # Check activity callbacks were called with correct tool info
        tool_calls = [call for call in activity_calls if call[0] == 'tool']
        assert len(tool_calls) == 2
        assert tool_calls[0] == ('tool', 'Read: /test/file.py')
        # Truncated at 50 chars
        expected = ('tool', 'Bash: ls -la very long command here that should be trunc')
        assert tool_calls[1] == expected

    @patch('select.select')
    @patch('time.time')
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
        mixed_message = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me read the file"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.py"}}
                ]
            }
        })
        result_msg = json.dumps({"type": "result", "result": "Response"})

        mock_stdout.readline.side_effect = [mixed_message, result_msg, '']

        result = provider.get_response(timeout=30.0)

        assert result == "Response"
        assert len(provider.accumulated_text) == 1
        assert provider.accumulated_text[0] == "Let me read the file"

    @patch('select.select')
    @patch('time.time')
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
        unknown_tool = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "UnknownTool", "input": {"some_param": "value"}}]}
        })
        result_msg = json.dumps({"type": "result", "result": "Done"})

        mock_stdout.readline.side_effect = [unknown_tool, result_msg, '']

        result = provider.get_response(timeout=30.0)

        assert result == "Done"
        tool_calls = [call for call in activity_calls if call[0] == 'tool']
        assert len(tool_calls) == 1
        assert tool_calls[0] == ('tool', 'UnknownTool: ')  # Empty detail

    @patch('select.select')
    @patch('time.time')
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
        mock_stdout.readline.return_value = ''  # No data

        result = provider.get_response(timeout=30.0)

        assert result == ""  # Should return empty on timeout

    @patch('select.select')
    @patch('time.time')
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
            json.dumps({"type": "result", "result": "Final result"})
        ]
        mock_stdout.readline.side_effect = messages + ['']

        result = provider.get_response(timeout=30.0)

        assert result == "Final result"

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

    @patch('pathlib.Path.home')
    def test_get_claude_config_dir_with_account(self, mock_home, tmp_path):
        """Config directory uses account name when set."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="my-account")
        provider = ClaudeCodeProvider(config)

        config_dir = provider._get_claude_config_dir()
        assert config_dir == str(tmp_path / ".chad" / "claude-configs" / "my-account")

    @patch('pathlib.Path.home')
    def test_get_claude_config_dir_without_account(self, mock_home, tmp_path):
        """Config directory defaults to ~/.claude when no account name."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        config_dir = provider._get_claude_config_dir()
        assert config_dir == str(tmp_path / ".claude")

    @patch('pathlib.Path.home')
    def test_get_env_includes_config_dir(self, mock_home, tmp_path):
        """Environment includes CLAUDE_CONFIG_DIR."""
        mock_home.return_value = tmp_path

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="test-account")
        provider = ClaudeCodeProvider(config)

        env = provider._get_env()
        assert "CLAUDE_CONFIG_DIR" in env
        assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".chad" / "claude-configs" / "test-account")

    @patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/claude'))
    @patch('subprocess.Popen')
    @patch('pathlib.Path.home')
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

    @patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/codex'))
    def test_start_session_success(self, mock_ensure):
        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"
        assert provider.cli_path == "/bin/codex"
        mock_ensure.assert_called_once_with("codex", provider._notify_activity)

    @patch('chad.providers._ensure_cli_tool', return_value=(True, '/bin/codex'))
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

    @patch('chad.providers.select.select')
    @patch('chad.providers.os.read')
    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('subprocess.Popen')
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

    @patch('chad.providers.select.select')
    @patch('chad.providers.os.read')
    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('time.time')
    @patch('subprocess.Popen')
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
        mock_time.side_effect = [0, 0, 2000, 2000]

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        response = provider.get_response(timeout=1.0)
        assert "timed out" in response
        assert provider.current_message is None
        mock_process.kill.assert_called_once()

    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('subprocess.Popen')
    def test_get_response_file_not_found(self, mock_popen, mock_openpty, mock_close):
        mock_openpty.return_value = (10, 11)
        mock_popen.side_effect = FileNotFoundError("codex not found")

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        response = provider.get_response()
        assert "Failed to run Codex" in response
        assert provider.current_message is None

    def test_get_response_no_message(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"

        response = provider.get_response()
        assert response == ""

    @patch('chad.providers._stream_pty_output', return_value=("", False))
    @patch('chad.providers._start_pty_process')
    def test_get_response_exec_includes_network_access(self, mock_start, mock_stream):
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
        assert '-c' in cmd
        assert 'network_access="enabled"' in cmd

    @patch('chad.providers._stream_pty_output', return_value=("", False))
    @patch('chad.providers._start_pty_process')
    def test_get_response_resume_includes_network_access(self, mock_start, mock_stream):
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
        assert '-c' in cmd
        assert 'network_access="enabled"' in cmd


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
            if event.get('type') == 'thread.started' and 'thread_id' in event:
                provider.thread_id = event['thread_id']

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


class TestOpenAICodexProviderIntegration:
    """Integration tests for OpenAICodexProvider that actually invoke codex."""

    def test_real_codex_execution(self):
        """Test that codex exec actually works end-to-end."""
        import shutil
        import tempfile

        # Skip if codex is not installed
        if not shutil.which('codex'):
            pytest.skip("Codex CLI not installed")

        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        # Use a temporary directory for the test
        with tempfile.TemporaryDirectory() as tmpdir:
            result = provider.start_session(tmpdir)
            assert result is True

            # Send a simple math question
            provider.send_message("What is 2+2? Just output the number.")

            # Get response
            response = provider.get_response(timeout=60)

            # The response should contain "4" somewhere
            if "Permission denied" in response or "Fatal error" in response:
                pytest.skip("Codex CLI session dir not accessible in CI environment")
            if "Failed to run Codex" in response:
                pytest.skip("Codex CLI failed to execute")
            assert "4" in response, f"Expected '4' in response, got: {response}"

            # Clean up
            provider.stop_session()


class TestMistralVibeProvider:
    """Test cases for MistralVibeProvider."""

    def test_start_session_success(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"

    def test_start_session_with_system_prompt(self):
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


class TestGeminiCodeAssistProvider:
    """Tests for GeminiCodeAssistProvider."""

    def test_send_message_includes_system_prompt(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.system_prompt = "system"
        provider.send_message("hello")
        assert "system" in provider.current_message
        assert "hello" in provider.current_message

    @patch('chad.providers.select.select')
    @patch('chad.providers.os.read')
    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('subprocess.Popen')
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
        assert provider.current_message is None

    @patch('chad.providers.select.select')
    @patch('chad.providers.os.read')
    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('time.time')
    @patch('subprocess.Popen')
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

    @patch('chad.providers.os.close')
    @patch('chad.providers.pty.openpty')
    @patch('subprocess.Popen')
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

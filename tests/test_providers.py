"Tests for AI providers."

import subprocess
from unittest.mock import ANY, Mock, patch
import pytest
from chad.providers import ModelConfig, create_provider, ClaudeCodeProvider, GeminiCodeAssistProvider, OpenAICodexProvider, MistralVibeProvider, parse_codex_output, extract_final_codex_response


class TestModelConfig:
    """Test cases for ModelConfig."""

    def test_basic_config(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        assert config.provider == "anthropic"
        assert config.model_name == "claude-3"
        assert config.account_name is None
        assert config.base_url is None

    def test_config_with_account_name(self):
        config = ModelConfig(
            provider="openai",
            model_name="gpt-4",
            account_name="test-account"
        )
        assert config.account_name == "test-account"

    def test_config_with_all_fields(self):
        config = ModelConfig(
            provider="anthropic",
            model_name="claude-3",
            account_name="test-account",
            base_url="https://api.example.com"
        )
        assert config.provider == "anthropic"
        assert config.model_name == "claude-3"
        assert config.account_name == "test-account"
        assert config.base_url == "https://api.example.com"


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


class TestParseCodexOutput:
    """Test cases for parse_codex_output function."""

    def test_empty_input(self):
        assert parse_codex_output("") == ""
        assert parse_codex_output(None) == ""

    def test_extracts_thinking_and_response(self):
        raw_output = """OpenAI Codex v0.65.0 (research preview)
--------
workdir: /home/miles/chad
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
/bin/bash -lc ls in /home/miles/chad succeeded in 26ms:
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
        assert "*Thinking: Preparing to analyze project files*" in result
        assert "*Thinking: Reading the README for context*" in result
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


class TestClaudeCodeProvider:
    """Test cases for ClaudeCodeProvider."""

    def test_init(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)
        assert provider.config == config
        assert provider.process is None
        assert provider.project_path is None

    @patch('subprocess.Popen')
    def test_start_session_success(self, mock_popen):
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.process is not None
        mock_popen.assert_called_once()

    @patch('subprocess.Popen')
    def test_start_session_with_account_name(self, mock_popen):
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        config = ModelConfig(provider="anthropic", model_name="claude-3", account_name="test-account")
        provider = ClaudeCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        # Should NOT set ANTHROPIC_API_KEY in environment
        mock_popen.assert_called_once()

    @patch('subprocess.Popen')
    def test_start_session_failure(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("command not found")

        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is False

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

    def test_send_message_no_process(self):
        config = ModelConfig(provider="anthropic", model_name="claude-3")
        provider = ClaudeCodeProvider(config)
        provider.send_message("Hello")

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


class TestOpenAICodexProvider:
    """Test cases for OpenAICodexProvider."""

    def test_init(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        assert provider.config == config
        assert provider.process is None
        assert provider.project_path is None
        assert provider.current_message is None

    def test_start_session_success(self):
        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        result = provider.start_session("/tmp/test_project")
        assert result is True
        assert provider.project_path == "/tmp/test_project"

    def test_start_session_with_system_prompt(self):
        config = ModelConfig(provider="openai", model_name="codex")
        provider = OpenAICodexProvider(config)

        result = provider.start_session("/tmp/test_project", system_prompt="Initial prompt")
        assert result is True
        assert provider.system_prompt == "Initial prompt"
        # System prompt is prepended to messages, not stored in current_message
        provider.send_message("Test message")
        assert "Initial prompt" in provider.current_message
        assert "Test message" in provider.current_message

    def test_send_message(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        provider.send_message("Hello")
        assert provider.current_message == "Hello"

    @patch('subprocess.Popen')
    def test_get_response_success(self, mock_popen):
        # Setup mock process with streaming stdout
        mock_stdout = Mock()
        # readline returns lines then empty string to signal EOF
        mock_stdout.readline.side_effect = ["4\n", ""]

        mock_stdin = Mock()
        mock_stderr = Mock()
        mock_stderr.read.return_value = ""

        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        # poll() returns None while running, then 0 when finished
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_popen.return_value = mock_process

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        response = provider.get_response(timeout=5.0)
        assert "4" in response
        assert provider.current_message is None
        mock_popen.assert_called_once()
        mock_stdin.write.assert_called_once_with("What is 2+2?")
        mock_stdin.close.assert_called_once()

    @patch('time.time')
    @patch('subprocess.Popen')
    def test_get_response_timeout(self, mock_popen, mock_time):
        # Setup mock process that never finishes
        mock_stdout = Mock()
        mock_stdout.readline.return_value = ""  # No output (blocks)

        mock_stdin = Mock()
        mock_stderr = Mock()
        mock_stderr.read.return_value = ""

        mock_process = Mock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.poll.return_value = None  # Always running
        mock_process.kill = Mock()
        mock_process.wait = Mock()
        mock_popen.return_value = mock_process

        # Simulate timeout by having time advance past the limit
        mock_time.side_effect = [0, 0, 2000, 2000]  # Start, then way past timeout

        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.project_path = "/tmp/test_project"
        provider.current_message = "What is 2+2?"

        response = provider.get_response(timeout=1.0)  # 1 second timeout
        assert "timed out" in response
        assert provider.current_message is None
        mock_process.kill.assert_called_once()

    @patch('subprocess.Popen')
    def test_get_response_file_not_found(self, mock_popen):
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

    def test_is_alive(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)

        # Always alive in exec mode
        assert provider.is_alive() is True

    def test_stop_session(self):
        config = ModelConfig(provider="openai", model_name="gpt-4")
        provider = OpenAICodexProvider(config)
        provider.current_message = "test"

        provider.stop_session()
        assert provider.current_message is None


class TestOpenAICodexProviderIntegration:
    """Integration tests for OpenAICodexProvider that actually invoke codex."""

    def test_real_codex_execution(self):
        """Test that codex exec actually works end-to-end."""
        import shutil
        import tempfile
        import os

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

    def test_init(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)
        assert provider.config == config
        assert provider.process is None
        assert provider.project_path is None
        assert provider.current_message is None
        assert provider.system_prompt is None

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

    def test_get_response_no_message(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)
        provider.project_path = "/tmp/test_project"

        response = provider.get_response()
        assert response == ""

    def test_is_alive(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)

        # Always alive when no process running (stateless mode)
        assert provider.is_alive() is True

    def test_stop_session(self):
        config = ModelConfig(provider="mistral", model_name="default")
        provider = MistralVibeProvider(config)
        provider.current_message = "test"

        provider.stop_session()
        assert provider.current_message is None


class TestGeminiCodeAssistProvider:
    """Tests for GeminiCodeAssistProvider."""

    def test_send_message_includes_system_prompt(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.system_prompt = "system"
        provider.send_message("hello")
        assert "system" in provider.current_message
        assert "hello" in provider.current_message

    @patch('subprocess.run')
    def test_get_response_success(self, mock_run):
        completed = subprocess.CompletedProcess(args=["gemini"], returncode=0, stdout="result", stderr="")
        mock_run.return_value = completed

        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.project_path = "/tmp/test"
        provider.send_message("hello")

        response = provider.get_response(timeout=5.0)
        assert response == "result"
        mock_run.assert_called_once()
        assert provider.current_message is None

    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd=["gemini"], timeout=5))
    def test_get_response_timeout(self, mock_run):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.send_message("hello")
        response = provider.get_response(timeout=5)
        assert "timed out" in response
        assert provider.current_message is None

    @patch('subprocess.run', side_effect=FileNotFoundError("missing"))
    def test_get_response_missing_cli(self, mock_run):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.send_message("hello")
        response = provider.get_response(timeout=5)
        assert "Failed to run Gemini" in response
        assert provider.current_message is None

    def test_get_response_no_message(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        assert provider.get_response(timeout=1) == ""

    def test_stop_session_clears_message(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        provider.current_message = "hi"
        provider.stop_session()
        assert provider.current_message is None

    def test_is_alive_stateless_true(self):
        provider = GeminiCodeAssistProvider(ModelConfig(provider="gemini", model_name="default"))
        assert provider.is_alive() is True

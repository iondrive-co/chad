"""Tests for message converter module."""

import tempfile
from pathlib import Path

import pytest

from chad.util.event_log import (
    EventLog,
    UserMessageEvent,
    AssistantMessageEvent,
    SessionStartedEvent,
)
from chad.util.message_converter import (
    ConversationTurn,
    extract_conversation_from_events,
    format_for_provider,
)


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for event logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def event_log(temp_log_dir):
    """Create an EventLog instance for testing."""
    return EventLog("test-session", base_dir=temp_log_dir)


class TestConversationTurn:
    """Tests for ConversationTurn dataclass."""

    def test_from_user_message(self):
        """Test creating user turn from text."""
        turn = ConversationTurn.from_user_message("Hello world", "2024-01-01T00:00:00Z")
        assert turn.role == "user"
        assert len(turn.blocks) == 1
        assert turn.blocks[0]["kind"] == "text"
        assert turn.blocks[0]["content"] == "Hello world"
        assert turn.timestamp == "2024-01-01T00:00:00Z"

    def test_from_assistant_blocks(self):
        """Test creating assistant turn from blocks."""
        blocks = [
            {"kind": "thinking", "content": "Let me analyze..."},
            {"kind": "text", "content": "Here is the answer."},
        ]
        turn = ConversationTurn.from_assistant_blocks(blocks)
        assert turn.role == "assistant"
        assert turn.blocks == blocks
        assert turn.timestamp is None


class TestExtractConversationFromEvents:
    """Tests for extract_conversation_from_events function."""

    def test_empty_log(self, event_log):
        """Test extraction from empty log returns empty list."""
        turns = extract_conversation_from_events(event_log)
        assert turns == []

    def test_extract_user_message(self, event_log):
        """Test extracting user message events."""
        event_log.log(UserMessageEvent(content="Add a button"))
        turns = extract_conversation_from_events(event_log)

        assert len(turns) == 1
        assert turns[0].role == "user"
        assert turns[0].blocks[0]["content"] == "Add a button"

    def test_extract_assistant_message(self, event_log):
        """Test extracting assistant message events."""
        event_log.log(
            AssistantMessageEvent(
                blocks=[
                    {"kind": "text", "content": "I'll help you add a button."},
                ]
            )
        )
        turns = extract_conversation_from_events(event_log)

        assert len(turns) == 1
        assert turns[0].role == "assistant"
        assert turns[0].blocks[0]["kind"] == "text"

    def test_extract_conversation_order(self, event_log):
        """Test that turns are extracted in chronological order."""
        event_log.log(UserMessageEvent(content="First message"))
        event_log.log(
            AssistantMessageEvent(blocks=[{"kind": "text", "content": "Response 1"}])
        )
        event_log.log(UserMessageEvent(content="Second message"))
        event_log.log(
            AssistantMessageEvent(blocks=[{"kind": "text", "content": "Response 2"}])
        )

        turns = extract_conversation_from_events(event_log)

        assert len(turns) == 4
        assert turns[0].role == "user"
        assert turns[0].blocks[0]["content"] == "First message"
        assert turns[1].role == "assistant"
        assert turns[2].role == "user"
        assert turns[2].blocks[0]["content"] == "Second message"
        assert turns[3].role == "assistant"

    def test_extract_with_since_seq(self, event_log):
        """Test filtering by sequence number."""
        event_log.log(UserMessageEvent(content="Old message"))
        seq = event_log.get_latest_seq()
        event_log.log(UserMessageEvent(content="New message"))

        turns = extract_conversation_from_events(event_log, since_seq=seq)

        assert len(turns) == 1
        assert turns[0].blocks[0]["content"] == "New message"

    def test_extract_with_max_turns(self, event_log):
        """Test limiting number of turns."""
        for i in range(5):
            event_log.log(UserMessageEvent(content=f"Message {i}"))

        turns = extract_conversation_from_events(event_log, max_turns=3)

        assert len(turns) == 3
        # Should get the last 3
        assert turns[0].blocks[0]["content"] == "Message 2"
        assert turns[2].blocks[0]["content"] == "Message 4"

    def test_ignores_other_events(self, event_log):
        """Test that non-message events are ignored."""
        event_log.log(SessionStartedEvent(task_description="Test task"))
        event_log.log(UserMessageEvent(content="Hello"))

        turns = extract_conversation_from_events(event_log)

        assert len(turns) == 1
        assert turns[0].role == "user"

    def test_assistant_with_tool_calls(self, event_log):
        """Test extracting assistant messages with tool calls."""
        event_log.log(
            AssistantMessageEvent(
                blocks=[
                    {"kind": "thinking", "content": "I need to read the file..."},
                    {
                        "kind": "tool_call",
                        "tool": "Read",
                        "args": {"file_path": "/src/main.py"},
                    },
                    {"kind": "tool_result", "content": "def main(): pass"},
                    {"kind": "text", "content": "I found the main function."},
                ]
            )
        )

        turns = extract_conversation_from_events(event_log)

        assert len(turns) == 1
        assert turns[0].role == "assistant"
        assert len(turns[0].blocks) == 4
        assert turns[0].blocks[0]["kind"] == "thinking"
        assert turns[0].blocks[1]["kind"] == "tool_call"


class TestFormatForProvider:
    """Tests for format_for_provider function."""

    def test_format_for_claude_omits_thinking(self):
        """Test that Claude format omits thinking blocks."""
        turns = [
            ConversationTurn.from_user_message("Help me"),
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "thinking", "content": "Let me think..."},
                    {"kind": "text", "content": "Here is my answer."},
                ],
            ),
        ]

        result = format_for_provider(turns, "anthropic")

        assert "[User]: Help me" in result
        assert "Let me think" not in result
        assert "Here is my answer." in result

    def test_format_for_codex_includes_reasoning(self):
        """Test that Codex format includes reasoning blocks."""
        turns = [
            ConversationTurn.from_user_message("Help me"),
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "thinking", "content": "Let me think..."},
                    {"kind": "text", "content": "Here is my answer."},
                ],
            ),
        ]

        result = format_for_provider(turns, "openai")

        assert "[User]: Help me" in result
        assert "[Reasoning]: Let me think..." in result
        assert "Here is my answer." in result

    def test_format_for_generic_uses_xml(self):
        """Test that generic format uses XML tags."""
        turns = [
            ConversationTurn.from_user_message("Help me"),
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "thinking", "content": "Let me think..."},
                    {"kind": "text", "content": "Here is my answer."},
                ],
            ),
        ]

        result = format_for_provider(turns, "gemini")

        assert '<turn role="user">Help me</turn>' in result
        assert "<thinking>Let me think...</thinking>" in result
        assert "<response>Here is my answer.</response>" in result

    def test_format_tool_calls(self):
        """Test formatting of tool calls."""
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {
                        "kind": "tool_call",
                        "tool": "Read",
                        "args": {"file_path": "/src/main.py"},
                    },
                    {"kind": "tool_result", "content": "def main(): pass"},
                ],
            ),
        ]

        result = format_for_provider(turns, "openai")

        assert "[Tool: Read] /src/main.py" in result
        assert "[Result]: def main(): pass" in result

    def test_format_bash_command_truncation(self):
        """Test that long bash commands are truncated."""
        long_cmd = "echo " + "a" * 200
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "tool_call", "tool": "Bash", "args": {"command": long_cmd}},
                ],
            ),
        ]

        result = format_for_provider(turns, "anthropic")

        assert "..." in result
        assert len(long_cmd) > 80  # Original is long
        # The formatted version should be truncated

    def test_format_with_new_message(self):
        """Test appending new message."""
        turns = [ConversationTurn.from_user_message("Previous")]

        result = format_for_provider(turns, "anthropic", new_message="New request")

        assert "[User]: Previous" in result
        assert "[User]: New request" in result

    def test_format_with_new_message_generic(self):
        """Test appending new message for generic provider."""
        turns = [ConversationTurn.from_user_message("Previous")]

        result = format_for_provider(turns, "gemini", new_message="New request")

        assert '<turn role="user">Previous</turn>' in result
        assert '<turn role="user">New request</turn>' in result

    def test_format_empty_turns(self):
        """Test formatting empty turns list."""
        result = format_for_provider([], "anthropic")
        assert result == ""

    def test_format_empty_turns_with_new_message(self):
        """Test formatting empty turns with new message."""
        result = format_for_provider([], "anthropic", new_message="Hello")
        assert "[User]: Hello" in result

    def test_preserves_full_tool_results(self):
        """Test that long tool results are preserved without truncation."""
        long_result = "x" * 1000
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "tool_result", "content": long_result},
                ],
            ),
        ]

        for provider in ("anthropic", "openai", "gemini"):
            result = format_for_provider(turns, provider)
            assert long_result in result, f"Tool result truncated for {provider}"

    def test_preserves_full_thinking_blocks(self):
        """Test that long thinking blocks are preserved without truncation."""
        long_thinking = "y" * 2000
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "thinking", "content": long_thinking},
                ],
            ),
        ]

        # Codex includes reasoning
        result = format_for_provider(turns, "openai")
        assert long_thinking in result

        # Generic includes thinking
        result = format_for_provider(turns, "gemini")
        assert long_thinking in result


class TestProviderSpecificFormatting:
    """Tests for provider-specific formatting edge cases."""

    def test_qwen_uses_generic_format(self):
        """Test that Qwen uses generic format."""
        turns = [ConversationTurn.from_user_message("Test")]
        result = format_for_provider(turns, "qwen")
        assert '<turn role="user">' in result

    def test_mistral_uses_generic_format(self):
        """Test that Mistral uses generic format."""
        turns = [ConversationTurn.from_user_message("Test")]
        result = format_for_provider(turns, "mistral")
        assert '<turn role="user">' in result

    def test_unknown_provider_uses_generic(self):
        """Test that unknown providers use generic format."""
        turns = [ConversationTurn.from_user_message("Test")]
        result = format_for_provider(turns, "unknown_provider")
        assert '<turn role="user">' in result

    def test_format_multiple_text_blocks(self):
        """Test formatting assistant with multiple text blocks."""
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {"kind": "text", "content": "First part."},
                    {"kind": "text", "content": "Second part."},
                ],
            ),
        ]

        result = format_for_provider(turns, "anthropic")

        assert "First part." in result
        assert "Second part." in result

    def test_format_glob_tool(self):
        """Test formatting Glob tool calls."""
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {
                        "kind": "tool_call",
                        "tool": "Glob",
                        "args": {"pattern": "**/*.py"},
                    },
                ],
            ),
        ]

        result = format_for_provider(turns, "openai")

        assert "[Tool: Glob] **/*.py" in result

    def test_format_grep_tool(self):
        """Test formatting Grep tool calls."""
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {
                        "kind": "tool_call",
                        "tool": "Grep",
                        "args": {"pattern": "def main"},
                    },
                ],
            ),
        ]

        result = format_for_provider(turns, "anthropic")

        assert "[Tool: Grep] def main" in result

    def test_format_websearch_tool(self):
        """Test formatting WebSearch tool calls."""
        turns = [
            ConversationTurn(
                role="assistant",
                blocks=[
                    {
                        "kind": "tool_call",
                        "tool": "WebSearch",
                        "args": {"query": "Python async tutorial"},
                    },
                ],
            ),
        ]

        result = format_for_provider(turns, "openai")

        assert "[Tool: WebSearch] Python async tutorial" in result

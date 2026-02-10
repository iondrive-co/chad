"""Tests for inter-provider handoff utilities."""

import tempfile
from pathlib import Path

import pytest

from chad.util.event_log import (
    EventLog,
    SessionStartedEvent,
    ToolCallStartedEvent,
    ContextCondensedEvent,
    UserMessageEvent,
    AssistantMessageEvent,
)
from chad.util.handoff import (
    extract_progress_from_events,
    build_handoff_summary,
    log_handoff_checkpoint,
    build_resume_prompt,
    get_last_checkpoint_provider_session_id,
    is_quota_exhaustion_error,
    get_quota_error_reason,
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


class TestExtractProgressFromEvents:
    """Tests for extract_progress_from_events function."""

    def test_empty_log(self, event_log):
        """Test extraction from empty log returns empty progress."""
        result = extract_progress_from_events(event_log)
        assert result["files_changed"] == []
        assert result["files_created"] == []
        assert result["key_commands"] == []

    def test_extract_files_created(self, event_log):
        """Test extraction of created files from write tool calls."""
        event_log.log(ToolCallStartedEvent(tool="write", path="/project/src/new_file.py"))
        event_log.log(ToolCallStartedEvent(tool="write", path="/project/tests/test_new.py"))

        result = extract_progress_from_events(event_log)
        assert "/project/src/new_file.py" in result["files_created"]
        assert "/project/tests/test_new.py" in result["files_created"]
        assert result["files_changed"] == []

    def test_extract_files_changed(self, event_log):
        """Test extraction of modified files from edit tool calls."""
        event_log.log(ToolCallStartedEvent(tool="edit", path="/project/src/existing.py"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/project/README.md"))

        result = extract_progress_from_events(event_log)
        assert "/project/src/existing.py" in result["files_changed"]
        assert "/project/README.md" in result["files_changed"]
        assert result["files_created"] == []

    def test_extract_key_commands(self, event_log):
        """Test extraction of key commands like pytest, npm."""
        event_log.log(ToolCallStartedEvent(tool="bash", command="pytest tests/ -v"))
        event_log.log(ToolCallStartedEvent(tool="bash", command="npm run build"))
        event_log.log(ToolCallStartedEvent(tool="bash", command="ls -la"))  # Should be ignored

        result = extract_progress_from_events(event_log)
        assert "pytest tests/ -v" in result["key_commands"]
        assert "npm run build" in result["key_commands"]
        assert "ls -la" not in result["key_commands"]

    def test_deduplicate_files(self, event_log):
        """Test that multiple operations on same file only appear once."""
        event_log.log(ToolCallStartedEvent(tool="edit", path="/project/src/main.py"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/project/src/main.py"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/project/src/main.py"))

        result = extract_progress_from_events(event_log)
        assert result["files_changed"] == ["/project/src/main.py"]

    def test_since_seq_filtering(self, event_log):
        """Test filtering events by sequence number."""
        event_log.log(ToolCallStartedEvent(tool="write", path="/project/old.py"))
        seq = event_log.get_latest_seq()
        event_log.log(ToolCallStartedEvent(tool="write", path="/project/new.py"))

        result = extract_progress_from_events(event_log, since_seq=seq)
        assert "/project/old.py" not in result["files_created"]
        assert "/project/new.py" in result["files_created"]

    def test_command_truncation(self, event_log):
        """Test that long commands are truncated to 100 chars."""
        long_command = "pytest " + "a" * 200
        event_log.log(ToolCallStartedEvent(tool="bash", command=long_command))

        result = extract_progress_from_events(event_log)
        assert len(result["key_commands"][0]) == 100

    def test_key_commands_limited_to_10(self, event_log):
        """Test that key_commands only keeps last 10."""
        for i in range(15):
            event_log.log(ToolCallStartedEvent(tool="bash", command=f"pytest test_{i}.py"))

        result = extract_progress_from_events(event_log)
        assert len(result["key_commands"]) == 10
        # Should have the last 10 (5-14)
        assert "pytest test_5.py" in result["key_commands"]
        assert "pytest test_14.py" in result["key_commands"]


class TestBuildHandoffSummary:
    """Tests for build_handoff_summary function."""

    def test_basic_summary(self, event_log):
        """Test building a basic summary with task and files."""
        event_log.log(ToolCallStartedEvent(tool="write", path="/src/new.py"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/main.py"))

        summary = build_handoff_summary("Add authentication feature", event_log)

        assert "<previous_session>" in summary
        assert "</previous_session>" in summary
        assert "## Original Task" in summary
        assert "Add authentication feature" in summary
        assert "## Files Modified" in summary
        assert "Created: `/src/new.py`" in summary
        assert "Modified: `/src/main.py`" in summary

    def test_summary_with_commands(self, event_log):
        """Test that commands are included in summary."""
        event_log.log(ToolCallStartedEvent(tool="bash", command="pytest tests/"))

        summary = build_handoff_summary("Fix bug", event_log)

        assert "## Commands Run" in summary
        assert "`pytest tests/`" in summary

    def test_summary_with_remaining_work(self, event_log):
        """Test including remaining work in summary."""
        summary = build_handoff_summary(
            "Implement feature X",
            event_log,
            remaining_work="Still need to add tests",
        )

        assert "## Remaining Work" in summary
        assert "Still need to add tests" in summary

    def test_empty_progress(self, event_log):
        """Test summary with no files or commands."""
        summary = build_handoff_summary("Do something", event_log)

        assert "<previous_session>" in summary
        assert "## Original Task" in summary
        assert "Do something" in summary
        # Should not have Files Modified or Commands Run sections
        assert "## Files Modified" not in summary
        assert "## Commands Run" not in summary

    def test_summary_with_conversation_history(self, event_log):
        """Test that conversation history is included in summary."""
        event_log.log(UserMessageEvent(content="Add a logout button"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "text", "content": "I'll add the logout button."},
            ]
        ))

        summary = build_handoff_summary("Add a logout button", event_log)

        assert "## Conversation History" in summary
        assert "Add a logout button" in summary
        assert "logout button" in summary

    def test_summary_formats_for_claude(self, event_log):
        """Test that Claude format omits thinking blocks."""
        event_log.log(UserMessageEvent(content="Help me"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "Let me think about this..."},
                {"kind": "text", "content": "Here is my answer."},
            ]
        ))

        summary = build_handoff_summary("Help me", event_log, target_provider="anthropic")

        assert "## Conversation History" in summary
        assert "Here is my answer" in summary
        # Claude format omits thinking
        assert "Let me think about this" not in summary

    def test_summary_formats_for_codex(self, event_log):
        """Test that Codex format includes reasoning."""
        event_log.log(UserMessageEvent(content="Help me"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "Let me think about this..."},
                {"kind": "text", "content": "Here is my answer."},
            ]
        ))

        summary = build_handoff_summary("Help me", event_log, target_provider="openai")

        assert "## Conversation History" in summary
        assert "[Reasoning]: Let me think about this..." in summary
        assert "Here is my answer" in summary

    def test_summary_formats_for_generic(self, event_log):
        """Test that generic format uses XML tags."""
        event_log.log(UserMessageEvent(content="Help me"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "Let me think..."},
                {"kind": "text", "content": "Done."},
            ]
        ))

        summary = build_handoff_summary("Help me", event_log, target_provider="gemini")

        assert "## Conversation History" in summary
        assert '<turn role="user">' in summary
        assert "<thinking>" in summary
        assert "<response>" in summary


class TestLogHandoffCheckpoint:
    """Tests for log_handoff_checkpoint function."""

    def test_logs_context_condensed_event(self, event_log):
        """Test that checkpoint logs a ContextCondensedEvent."""
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/main.py"))

        log_handoff_checkpoint(event_log, "Original task")

        events = event_log.get_events(event_types=["context_condensed"])
        assert len(events) == 1
        event = events[0]
        assert event["policy"] == "provider_handoff"
        assert event["original_task"] == "Original task"

    def test_checkpoint_includes_progress(self, event_log):
        """Test that checkpoint includes file changes."""
        event_log.log(ToolCallStartedEvent(tool="write", path="/src/new.py"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/existing.py"))
        event_log.log(ToolCallStartedEvent(tool="bash", command="pytest tests/"))

        log_handoff_checkpoint(event_log, "Add feature")

        events = event_log.get_events(event_types=["context_condensed"])
        event = events[0]
        assert "/src/new.py" in event["files_created"]
        assert "/src/existing.py" in event["files_changed"]
        assert "pytest tests/" in event["key_commands"]

    def test_checkpoint_includes_provider_session_id(self, event_log):
        """Test that checkpoint includes provider session ID."""
        log_handoff_checkpoint(
            event_log,
            "Task",
            provider_session_id="thread_abc123",
        )

        events = event_log.get_events(event_types=["context_condensed"])
        assert events[0]["provider_session_id"] == "thread_abc123"

    def test_checkpoint_includes_summary_text(self, event_log):
        """Test that checkpoint includes markdown summary."""
        event_log.log(ToolCallStartedEvent(tool="write", path="/src/new.py"))

        log_handoff_checkpoint(event_log, "Create new module")

        events = event_log.get_events(event_types=["context_condensed"])
        summary = events[0]["summary_text"]
        assert "<previous_session>" in summary
        assert "Create new module" in summary

    def test_checkpoint_with_target_provider(self, event_log):
        """Test that checkpoint uses target provider for formatting."""
        event_log.log(UserMessageEvent(content="Test message"))
        event_log.log(AssistantMessageEvent(
            blocks=[{"kind": "thinking", "content": "Thinking..."}]
        ))

        log_handoff_checkpoint(event_log, "Test", target_provider="openai")

        events = event_log.get_events(event_types=["context_condensed"])
        summary = events[0]["summary_text"]
        # Codex format should include reasoning
        assert "[Reasoning]:" in summary


class TestBuildResumePrompt:
    """Tests for build_resume_prompt function."""

    def test_resume_from_session_start(self, event_log):
        """Test building resume prompt from session started event."""
        event_log.log(SessionStartedEvent(task_description="Fix the bug"))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/main.py"))

        prompt = build_resume_prompt(event_log, "Continue fixing")

        assert "<previous_session>" in prompt
        assert "Fix the bug" in prompt
        assert "Continue with: Continue fixing" in prompt

    def test_resume_without_new_message(self, event_log):
        """Test resume prompt without new instructions."""
        event_log.log(SessionStartedEvent(task_description="Original task"))

        prompt = build_resume_prompt(event_log)

        assert "<previous_session>" in prompt
        assert "Original task" in prompt
        assert "Continue with:" not in prompt

    def test_resume_builds_from_session_started(self, event_log):
        """Test that resume builds from session_started event."""
        event_log.log(SessionStartedEvent(task_description="Build feature X"))
        event_log.log(ToolCallStartedEvent(tool="write", path="/src/feature.py"))

        prompt = build_resume_prompt(event_log, "Add tests")

        assert "<previous_session>" in prompt
        assert "Build feature X" in prompt
        assert "Continue with: Add tests" in prompt

    def test_resume_formats_for_target_provider(self, event_log):
        """Test that resume formats for the target provider."""
        event_log.log(SessionStartedEvent(task_description="Test task"))
        event_log.log(UserMessageEvent(content="Hello"))
        event_log.log(AssistantMessageEvent(
            blocks=[{"kind": "thinking", "content": "Thinking..."}]
        ))

        # Codex should include reasoning
        prompt_codex = build_resume_prompt(event_log, target_provider="openai")
        assert "[Reasoning]:" in prompt_codex

        # Claude should omit thinking
        prompt_claude = build_resume_prompt(event_log, target_provider="anthropic")
        assert "[Reasoning]:" not in prompt_claude

    def test_resume_includes_conversation_history(self, event_log):
        """Test that resume includes conversation history."""
        event_log.log(SessionStartedEvent(task_description="Add feature"))
        event_log.log(UserMessageEvent(content="First request"))
        event_log.log(AssistantMessageEvent(
            blocks=[{"kind": "text", "content": "First response"}]
        ))
        event_log.log(UserMessageEvent(content="Second request"))
        event_log.log(AssistantMessageEvent(
            blocks=[{"kind": "text", "content": "Second response"}]
        ))

        prompt = build_resume_prompt(event_log, "Continue")

        assert "## Conversation History" in prompt
        assert "First request" in prompt
        assert "First response" in prompt
        assert "Second request" in prompt
        assert "Second response" in prompt


class TestGetLastCheckpointProviderSessionId:
    """Tests for get_last_checkpoint_provider_session_id function."""

    def test_returns_none_when_no_checkpoint(self, event_log):
        """Test returns None when no checkpoint exists."""
        result = get_last_checkpoint_provider_session_id(event_log)
        assert result is None

    def test_returns_session_id_from_checkpoint(self, event_log):
        """Test returns the provider session ID from checkpoint."""
        log_handoff_checkpoint(
            event_log,
            "Task",
            provider_session_id="gemini_session_xyz",
        )

        result = get_last_checkpoint_provider_session_id(event_log)
        assert result == "gemini_session_xyz"

    def test_returns_latest_session_id(self, event_log):
        """Test returns session ID from latest checkpoint."""
        log_handoff_checkpoint(event_log, "Task 1", provider_session_id="old_session")
        log_handoff_checkpoint(event_log, "Task 2", provider_session_id="new_session")

        result = get_last_checkpoint_provider_session_id(event_log)
        assert result == "new_session"

    def test_ignores_non_handoff_checkpoints(self, event_log):
        """Test ignores checkpoints that aren't provider_handoff."""
        # Log a regular context condensed event (not handoff)
        event = ContextCondensedEvent(
            summary_text="Some summary",
            policy="rolling_window",
        )
        event_log.log(event)

        result = get_last_checkpoint_provider_session_id(event_log)
        assert result is None


class TestQuotaExhaustionDetection:
    """Tests for quota/credit exhaustion detection."""

    def test_detects_rate_limit_exceeded(self):
        """Test detection of rate limit errors."""
        assert is_quota_exhaustion_error("Rate limit exceeded. Please try again.")
        assert is_quota_exhaustion_error("Error: rate_limit_exceeded")
        assert is_quota_exhaustion_error("Too many requests, please slow down")
        assert is_quota_exhaustion_error("Error 429: Too many requests")

    def test_detects_insufficient_quota(self):
        """Test detection of insufficient quota/credits."""
        assert is_quota_exhaustion_error("Insufficient credits on your account")
        assert is_quota_exhaustion_error("insufficient_quota")
        assert is_quota_exhaustion_error("You are out of credits")
        assert is_quota_exhaustion_error("Quota exceeded for this billing period")

    def test_detects_billing_issues(self):
        """Test detection of billing-related errors."""
        assert is_quota_exhaustion_error("Billing limit exceeded")
        assert is_quota_exhaustion_error("Payment required to continue")
        assert is_quota_exhaustion_error("billing_hard_limit_reached")

    def test_detects_resource_exhausted(self):
        """Test detection of resource exhaustion errors."""
        assert is_quota_exhaustion_error("RESOURCE_EXHAUSTED")
        assert is_quota_exhaustion_error("resource exhausted, try later")

    def test_detects_account_suspended(self):
        """Test detection of suspended/disabled accounts."""
        assert is_quota_exhaustion_error("Account suspended due to billing")
        assert is_quota_exhaustion_error("Your account has been disabled")

    def test_does_not_match_unrelated_errors(self):
        """Test that unrelated errors are not matched."""
        assert not is_quota_exhaustion_error("Connection timeout")
        assert not is_quota_exhaustion_error("Invalid API key")
        assert not is_quota_exhaustion_error("Model not found")
        assert not is_quota_exhaustion_error("Internal server error")
        assert not is_quota_exhaustion_error("")
        assert not is_quota_exhaustion_error(None)
        # These should NOT match - they're normal text, not error messages
        assert not is_quota_exhaustion_error("The rate of change exceeded expectations")
        assert not is_quota_exhaustion_error("Please limit your request to 100 items")
        assert not is_quota_exhaustion_error("The billing department will contact you")

    def test_get_quota_error_reason_rate_limit(self):
        """Test reason extraction for rate limit errors."""
        assert get_quota_error_reason("Rate limit exceeded") == "rate_limit"
        assert get_quota_error_reason("Too many requests") == "rate_limit"

    def test_get_quota_error_reason_credits(self):
        """Test reason extraction for credit errors."""
        assert get_quota_error_reason("Insufficient credits") == "insufficient_credits"
        assert get_quota_error_reason("insufficient_quota") == "insufficient_credits"

    def test_get_quota_error_reason_quota(self):
        """Test reason extraction for quota errors."""
        assert get_quota_error_reason("Quota exceeded for project") == "quota_exceeded"

    def test_get_quota_error_reason_billing(self):
        """Test reason extraction for billing errors."""
        assert get_quota_error_reason("Billing limit reached") == "billing_issue"

    def test_get_quota_error_reason_none(self):
        """Test that None is returned for non-quota errors."""
        assert get_quota_error_reason("Connection error") is None
        assert get_quota_error_reason("") is None
        assert get_quota_error_reason(None) is None


class TestEventLogPersistence:
    """Tests for event log persistence with handoff data."""

    def test_handoff_data_persists_to_disk(self, temp_log_dir):
        """Test that handoff data is correctly persisted and readable."""
        session_id = "persist-test"

        # Create log and add handoff checkpoint
        log1 = EventLog(session_id, base_dir=temp_log_dir)
        log1.log(ToolCallStartedEvent(tool="write", path="/src/new.py"))
        log_handoff_checkpoint(
            log1,
            "Create module",
            provider_session_id="thread_123",
        )
        log1.close()

        # Create new EventLog instance (simulating restart)
        log2 = EventLog(session_id, base_dir=temp_log_dir)

        # Verify data is readable
        events = log2.get_events(event_types=["context_condensed"])
        assert len(events) == 1
        event = events[0]
        assert event["policy"] == "provider_handoff"
        assert event["original_task"] == "Create module"
        assert event["provider_session_id"] == "thread_123"
        assert "/src/new.py" in event["files_created"]

    def test_resume_prompt_works_after_reload(self, temp_log_dir):
        """Test that resume prompt works with reloaded event log."""
        session_id = "resume-test"

        # Create log with session started event (needed for resume)
        log1 = EventLog(session_id, base_dir=temp_log_dir)
        log1.log(SessionStartedEvent(task_description="Original task"))
        log1.close()

        # Reload and build resume
        log2 = EventLog(session_id, base_dir=temp_log_dir)
        prompt = build_resume_prompt(log2, "Continue")

        assert "Original task" in prompt
        assert "Continue" in prompt


class TestConversationHandoff:
    """Tests for rich conversation context handoff."""

    def test_full_conversation_preserved(self, event_log):
        """Test that full conversation with tool calls is preserved."""
        event_log.log(SessionStartedEvent(task_description="Add logout button"))
        event_log.log(UserMessageEvent(content="Add a logout button to the header"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "I need to find the header component..."},
                {"kind": "tool_call", "tool": "Read", "args": {"file_path": "/src/Header.tsx"}},
                {"kind": "tool_result", "content": "export function Header() { return <div>...</div> }"},
                {"kind": "text", "content": "I've added the logout button to the header."},
            ]
        ))

        # Build for Codex (includes reasoning)
        prompt = build_resume_prompt(event_log, "Now add click handler", target_provider="openai")

        assert "Add logout button" in prompt
        assert "[User]: Add a logout button" in prompt
        assert "[Reasoning]: I need to find the header component" in prompt
        assert "[Tool: Read] /src/Header.tsx" in prompt
        assert "[Result]:" in prompt
        assert "I've added the logout button" in prompt
        assert "Continue with: Now add click handler" in prompt

    def test_multiple_turns_preserved(self, event_log):
        """Test that multiple conversation turns are preserved."""
        event_log.log(SessionStartedEvent(task_description="Build feature"))

        # First turn
        event_log.log(UserMessageEvent(content="Step 1"))
        event_log.log(AssistantMessageEvent(blocks=[{"kind": "text", "content": "Done with step 1"}]))

        # Second turn
        event_log.log(UserMessageEvent(content="Step 2"))
        event_log.log(AssistantMessageEvent(blocks=[{"kind": "text", "content": "Done with step 2"}]))

        # Third turn
        event_log.log(UserMessageEvent(content="Step 3"))
        event_log.log(AssistantMessageEvent(blocks=[{"kind": "text", "content": "Done with step 3"}]))

        prompt = build_resume_prompt(event_log, target_provider="anthropic")

        assert "Step 1" in prompt
        assert "Done with step 1" in prompt
        assert "Step 2" in prompt
        assert "Done with step 2" in prompt
        assert "Step 3" in prompt
        assert "Done with step 3" in prompt

    def test_provider_specific_xml_format(self, event_log):
        """Test XML format for generic providers."""
        event_log.log(SessionStartedEvent(task_description="Test"))
        event_log.log(UserMessageEvent(content="Hello"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "Analyzing..."},
                {"kind": "text", "content": "Response here"},
            ]
        ))

        prompt = build_resume_prompt(event_log, target_provider="gemini")

        assert '<turn role="user">Hello</turn>' in prompt
        assert '<turn role="assistant">' in prompt
        assert '<thinking>Analyzing...</thinking>' in prompt
        assert '<response>Response here</response>' in prompt
        assert '</turn>' in prompt


class TestMockProviderHandoffIntegration:
    """Tests for handoff using MockProvider quota simulation.

    These tests verify that the handoff system correctly preserves
    conversation context when a provider exhausts its quota.
    """

    def test_quota_error_triggers_handoff_with_context(self, temp_log_dir):
        """Test that quota error from MockProvider triggers handoff with full context."""
        from chad.util.providers import MockProvider, MockProviderQuotaError, ModelConfig

        # Set up event log with conversation history
        event_log = EventLog("quota-handoff-test", base_dir=temp_log_dir)
        event_log.log(SessionStartedEvent(task_description="Add feature X"))
        event_log.log(UserMessageEvent(content="Please add a new feature"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "I'll analyze the codebase first..."},
                {"kind": "tool_call", "tool": "Read", "args": {"file_path": "/src/main.py"}},
                {"kind": "tool_result", "content": "def main(): pass"},
                {"kind": "text", "content": "I found the entry point. Let me add the feature."},
            ]
        ))
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/main.py"))

        # Create MockProvider with quota simulation
        model_config = ModelConfig(
            provider="mock",
            model_name="default",
            account_name="test-mock-account",
        )
        provider = MockProvider(model_config)
        provider._get_remaining_usage = lambda: 0.0  # Quota exhausted

        # Start session and trigger quota error
        provider.start_session(str(temp_log_dir))
        provider.send_message("Continue working")

        # Verify quota error is raised
        with pytest.raises(MockProviderQuotaError) as exc_info:
            provider.get_response()

        # Verify error matches handoff detection
        assert is_quota_exhaustion_error(str(exc_info.value))

        # Build handoff context for new provider (e.g., switching to Codex)
        handoff_summary = build_handoff_summary(
            "Add feature X",
            event_log,
            target_provider="openai",  # Switching to Codex
        )

        # Verify conversation history is preserved in handoff
        assert "<previous_session>" in handoff_summary
        assert "Add feature X" in handoff_summary
        assert "## Conversation History" in handoff_summary
        assert "[User]: Please add a new feature" in handoff_summary
        assert "[Reasoning]: I'll analyze the codebase" in handoff_summary
        assert "[Tool: Read] /src/main.py" in handoff_summary
        assert "I found the entry point" in handoff_summary
        assert "## Files Modified" in handoff_summary
        assert "/src/main.py" in handoff_summary

    def test_handoff_preserves_context_across_provider_switch(self, temp_log_dir):
        """Test full handoff flow: quota exhaustion -> checkpoint -> resume."""
        from chad.util.providers import MockProvider, MockProviderQuotaError, ModelConfig

        # Create event log with multi-turn conversation
        event_log = EventLog("full-handoff-test", base_dir=temp_log_dir)
        event_log.log(SessionStartedEvent(task_description="Implement logout button"))

        # Turn 1: Initial request
        event_log.log(UserMessageEvent(content="Add logout button to header"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "I need to find the Header component..."},
                {"kind": "tool_call", "tool": "Glob", "args": {"pattern": "**/Header*"}},
                {"kind": "tool_result", "content": "src/components/Header.tsx"},
                {"kind": "tool_call", "tool": "Read", "args": {"file_path": "src/components/Header.tsx"}},
                {"kind": "tool_result", "content": "export function Header() { return <header>...</header> }"},
                {"kind": "text", "content": "Found the Header component. Adding logout button..."},
            ]
        ))
        event_log.log(ToolCallStartedEvent(tool="edit", path="src/components/Header.tsx"))

        # Turn 2: Follow-up
        event_log.log(UserMessageEvent(content="Also add a click handler"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "text", "content": "I'll add an onClick handler that calls the logout API."},
            ]
        ))

        # Simulate quota exhaustion on the original provider
        model_config = ModelConfig(provider="mock", model_name="default", account_name="exhausted-account")
        old_provider = MockProvider(model_config)
        old_provider._get_remaining_usage = lambda: 0.0

        old_provider.start_session(str(temp_log_dir))
        old_provider.send_message("Continue implementation")

        # Capture the error
        quota_error = None
        try:
            old_provider.get_response()
        except MockProviderQuotaError as e:
            quota_error = str(e)

        assert quota_error is not None
        assert is_quota_exhaustion_error(quota_error)

        # Log handoff checkpoint before switching
        log_handoff_checkpoint(
            event_log,
            "Implement logout button",
            provider_session_id="mock-session-123",
            target_provider="anthropic",  # Switching to Claude
        )

        # Build resume prompt for new provider
        resume_prompt = build_resume_prompt(
            event_log,
            new_message="Continue adding the click handler",
            target_provider="anthropic",
        )

        # Verify resume prompt has all the context
        assert "<previous_session>" in resume_prompt
        assert "Implement logout button" in resume_prompt
        assert "## Conversation History" in resume_prompt

        # Claude format: no thinking blocks shown
        assert "[User]: Add logout button to header" in resume_prompt
        assert "[Tool: Glob]" in resume_prompt
        assert "[Tool: Read]" in resume_prompt
        assert "Found the Header component" in resume_prompt
        assert "[User]: Also add a click handler" in resume_prompt
        assert "onClick handler" in resume_prompt

        # Verify the new message is included
        assert "Continue with: Continue adding the click handler" in resume_prompt

        # Verify files modified section
        assert "## Files Modified" in resume_prompt
        assert "src/components/Header.tsx" in resume_prompt

    def test_handoff_format_differs_by_provider(self, temp_log_dir):
        """Test that handoff format correctly adapts to target provider."""
        # Create event log with thinking content
        event_log = EventLog("format-diff-test", base_dir=temp_log_dir)
        event_log.log(SessionStartedEvent(task_description="Test task"))
        event_log.log(UserMessageEvent(content="Do something"))
        event_log.log(AssistantMessageEvent(
            blocks=[
                {"kind": "thinking", "content": "Let me think about this carefully..."},
                {"kind": "text", "content": "Here's what I'll do."},
            ]
        ))

        # Build for Claude (omits thinking)
        claude_prompt = build_resume_prompt(event_log, target_provider="anthropic")
        assert "Let me think about this carefully" not in claude_prompt
        assert "Here's what I'll do" in claude_prompt

        # Build for Codex (includes reasoning)
        codex_prompt = build_resume_prompt(event_log, target_provider="openai")
        assert "[Reasoning]: Let me think about this carefully" in codex_prompt
        assert "Here's what I'll do" in codex_prompt

        # Build for Gemini (XML format)
        gemini_prompt = build_resume_prompt(event_log, target_provider="gemini")
        assert "<thinking>Let me think about this carefully...</thinking>" in gemini_prompt
        assert "<response>Here's what I'll do.</response>" in gemini_prompt

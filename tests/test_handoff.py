"""Tests for inter-provider handoff utilities."""

import tempfile
from pathlib import Path

import pytest

from chad.util.event_log import (
    EventLog,
    SessionStartedEvent,
    ToolCallStartedEvent,
    ContextCondensedEvent,
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

        assert "# Session Handoff Context" in summary
        assert "## Original Task" in summary
        assert "Add authentication feature" in summary
        assert "## Work Completed" in summary
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

        assert "# Session Handoff Context" in summary
        assert "## Original Task" in summary
        assert "Do something" in summary
        # Should not have Work Completed or Commands Run sections
        assert "## Work Completed" not in summary
        assert "## Commands Run" not in summary


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
        assert "# Session Handoff Context" in summary
        assert "Create new module" in summary


class TestBuildResumePrompt:
    """Tests for build_resume_prompt function."""

    def test_resume_from_checkpoint(self, event_log):
        """Test building resume prompt from existing checkpoint."""
        event_log.log(ToolCallStartedEvent(tool="edit", path="/src/main.py"))
        log_handoff_checkpoint(event_log, "Fix the bug")

        prompt = build_resume_prompt(event_log, "Continue fixing")

        assert "# Session Handoff Context" in prompt
        assert "Fix the bug" in prompt
        assert "# New Instructions" in prompt
        assert "Continue fixing" in prompt

    def test_resume_without_new_message(self, event_log):
        """Test resume prompt without new instructions."""
        log_handoff_checkpoint(event_log, "Original task")

        prompt = build_resume_prompt(event_log)

        assert "# Session Handoff Context" in prompt
        assert "Original task" in prompt
        assert "# New Instructions" not in prompt

    def test_resume_builds_fresh_when_no_checkpoint(self, event_log):
        """Test that resume builds fresh summary when no checkpoint exists."""
        event_log.log(SessionStartedEvent(task_description="Build feature X"))
        event_log.log(ToolCallStartedEvent(tool="write", path="/src/feature.py"))

        prompt = build_resume_prompt(event_log, "Add tests")

        assert "# Session Handoff Context" in prompt
        assert "Build feature X" in prompt
        assert "# New Instructions" in prompt
        assert "Add tests" in prompt

    def test_resume_uses_latest_checkpoint(self, event_log):
        """Test that resume uses the most recent checkpoint."""
        log_handoff_checkpoint(event_log, "First task")
        event_log.log(ToolCallStartedEvent(tool="write", path="/new_file.py"))
        log_handoff_checkpoint(event_log, "Second task")

        prompt = build_resume_prompt(event_log)

        assert "Second task" in prompt


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

        # Create log and add checkpoint
        log1 = EventLog(session_id, base_dir=temp_log_dir)
        log_handoff_checkpoint(log1, "Original task")

        # Reload and build resume
        log2 = EventLog(session_id, base_dir=temp_log_dir)
        prompt = build_resume_prompt(log2, "Continue")

        assert "Original task" in prompt
        assert "Continue" in prompt

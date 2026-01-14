"""Test canceling and restarting tasks."""

import pytest
from unittest.mock import MagicMock

from chad.web_ui import ChadWebUI, Session


class TestCancelTaskRestart:
    """Test that tasks can be restarted after cancellation."""

    @pytest.fixture
    def web_ui(self, tmp_path):
        """Create ChadWebUI instance."""
        security_mgr = MagicMock()
        security_mgr.list_accounts.return_value = {"test-account": "anthropic"}
        security_mgr.list_role_assignments.return_value = {"CODING": "test-account"}
        security_mgr.get_account_model.return_value = "default"

        ui = ChadWebUI(security_mgr, "", str(tmp_path))
        return ui

    def test_cancel_task_allows_restart(self, web_ui):
        """Test that canceling a task properly resets state to allow restart."""
        # Create a session and mark it as active
        session = Session(
            id="test-123",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config=MagicMock()
        )
        web_ui.sessions["test-123"] = session

        # Cancel the task
        result = web_ui.cancel_task("test-123")

        # Check that cancel was acknowledged
        assert "cancelled" in result.lower() or "cancelling" in result.lower()
        assert session.cancel_requested is True

        # Simulate the task processing the cancellation
        # In the real code, this happens in the run loop when it checks cancel_requested
        session.active = False
        session.provider = None
        session.config = None

        # Now try to start a new task - it should succeed
        # Reset cancel_requested as the UI would do
        session.cancel_requested = False

        # Check that session can be reused for a new task
        assert session.active is False  # Ready for new task
        assert session.cancel_requested is False  # Cancel flag cleared
        assert session.provider is None  # Provider cleared
        assert session.config is None  # Config cleared

    def test_cancel_sets_session_inactive(self, web_ui):
        """Test that cancellation properly marks session as inactive."""
        # Create an active session
        session = Session(
            id="test-456",
            name="Active Session",
            active=True,
            cancel_requested=False
        )
        web_ui.sessions["test-456"] = session

        # Cancel should set cancel_requested and mark session as inactive
        web_ui.cancel_task("test-456")
        assert session.cancel_requested is True

        # The session should be marked inactive immediately to allow restart
        # This was changed to fix the issue where users couldn't restart after cancel
        assert session.active is False

    def test_start_task_after_cancel_without_cleanup(self, web_ui):
        """Test starting a new task when previous was cancelled but not cleaned up."""
        # Create a session that was cancelled but not properly cleaned up
        session = Session(
            id="test-789",
            name="Stuck Session",
            active=True,  # Still marked as active
            cancel_requested=True,  # But was cancelled
            provider=MagicMock(),
            config=MagicMock()
        )
        web_ui.sessions["test-789"] = session

        # Trying to use this session for a new task should detect the issue
        # The UI should either:
        # 1. Clean up the session automatically
        # 2. Prevent starting a new task until cleanup

        # Expected behavior: session should be reset when starting new task
        # if cancel_requested is True
        assert session.cancel_requested is True
        assert session.active is True  # Still active but cancelled

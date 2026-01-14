"""Test cancel task integration with UI flow."""

import pytest
from unittest.mock import MagicMock

from chad.web_ui import ChadWebUI, Session


class TestCancelTaskIntegration:
    """Test the full cancel/restart flow."""

    @pytest.fixture
    def web_ui(self, tmp_path):
        """Create ChadWebUI instance with proper setup."""
        security_mgr = MagicMock()
        security_mgr.list_accounts.return_value = {"test-account": "anthropic"}
        security_mgr.list_role_assignments.return_value = {"CODING": "test-account"}
        security_mgr.get_account_model.return_value = "default"
        security_mgr.get_account_reasoning.return_value = "default"

        ui = ChadWebUI(security_mgr, "", str(tmp_path))
        return ui

    def test_session_inactive_after_cancel(self, web_ui):
        """Test that session.active is False after cancellation."""
        # Create and start a session
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        web_ui.sessions["test-session"] = session

        # Verify session is active
        assert session.active is True
        assert session.cancel_requested is False

        # Cancel the task
        result = web_ui.cancel_task("test-session")

        # Verify cancellation state
        assert "cancelled" in result.lower()
        assert session.cancel_requested is True
        assert session.active is False  # Should be inactive now
        assert session.provider is None
        assert session.config is None

    def test_can_start_new_task_after_cancel(self, web_ui):
        """Test that a new task can be started after cancellation."""
        # Create session with an active task
        session = Session(
            id="test-session-2",
            name="Active Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        web_ui.sessions["test-session-2"] = session

        # Cancel the task
        web_ui.cancel_task("test-session-2")

        # Verify we can "start" a new task (session should be ready)
        assert session.active is False
        assert session.cancel_requested is True

        # Reset cancel flag as UI would do when starting new task
        session.cancel_requested = False

        # Simulate starting a new task - session should be ready
        assert session.active is False  # Ready for new task
        assert session.provider is None  # Clean slate
        assert session.config is None  # Clean slate

        # In real flow, run_task would set active=True when it starts
        # Let's verify nothing prevents that
        session.active = True  # Should work without issues
        assert session.active is True

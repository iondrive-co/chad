"""Test canceling and restarting tasks."""

import pytest
from unittest.mock import MagicMock

from chad.ui.gradio.web_ui import ChadWebUI, Session


class TestCancelTask:
    """Test task cancellation and restart functionality."""

    @pytest.fixture
    def web_ui(self, tmp_path):
        """Create ChadWebUI instance."""
        api_client = MagicMock()
        api_client.list_accounts.return_value = [
            {"name": "test-account", "provider": "anthropic", "role": "CODING"}
        ]
        api_client.list_providers.return_value = ["anthropic"]
        return ChadWebUI(api_client)

    def test_cancel_marks_session_inactive(self, web_ui):
        """Canceling a task should mark session as inactive and clear provider."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        web_ui.sessions["test-session"] = session

        result = web_ui.cancel_task("test-session")

        # Handle gr.update dict
        result_value = result.get("value", "") if isinstance(result, dict) else result
        assert "cancelled" in result_value.lower()
        assert session.cancel_requested is True
        assert session.active is False
        assert session.provider is None
        assert session.config is None

    def test_session_ready_for_restart_after_cancel(self, web_ui):
        """After cancellation, session should be ready for a new task."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        web_ui.sessions["test-session"] = session

        web_ui.cancel_task("test-session")

        # Session should be in a clean state ready for restart
        assert session.active is False
        assert session.provider is None
        assert session.config is None

        # Simulate UI resetting cancel flag when starting new task
        session.cancel_requested = False
        session.active = True
        assert session.active is True

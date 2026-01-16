"""Test that Start Task button stays disabled after task starts."""

import pytest
from unittest.mock import Mock
import queue
import threading
import time
import subprocess

from chad.ui.gradio.web_ui import ChadWebUI


class TestStartButtonBehavior:
    """Test Start Task button behavior during and after task execution."""

    def _init_git_repo(self, project_path):
        """Initialize a proper git repository."""
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=project_path, capture_output=True)
        (project_path / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=project_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=project_path, capture_output=True)

    @pytest.fixture
    def web_ui(self, tmp_path, monkeypatch):
        """Create a web UI instance with mocked security."""
        security_mgr = Mock()
        security_mgr.list_accounts.return_value = {"claude": "anthropic"}
        security_mgr.list_role_assignments.return_value = {"CODING": "claude"}
        security_mgr.get_account_model.return_value = "default"
        security_mgr.get_account_reasoning.return_value = "default"

        ui = ChadWebUI(security_mgr, main_password="test_password")
        return ui

    def test_start_button_stays_disabled_during_task(self, web_ui, tmp_path, monkeypatch):
        """Start Task button should remain disabled throughout task execution."""
        session_id = "test-button-disabled"
        web_ui.get_session(session_id)

        # Setup project path
        project_path = tmp_path / "test_project"
        project_path.mkdir()
        self._init_git_repo(project_path)

        # Mock provider to simulate task execution
        mock_provider = Mock()
        mock_provider.send_message = Mock()

        def mock_receive():
            # Simulate some work
            time.sleep(0.1)
            return '{"type": "response", "content": "Task completed"}'

        mock_provider.receive_message = mock_receive
        mock_provider.start_session = Mock(return_value=True)

        # Mock the provider factory
        monkeypatch.setattr(
            "chad.providers.create_provider",
            lambda config: mock_provider
        )

        # Track all button states during execution
        button_states = []

        # Generator to collect outputs
        outputs = []
        for output in web_ui.start_chad_task(
            session_id,
            str(project_path),
            "Test task",
            "claude",
            ChadWebUI.VERIFICATION_NONE
        ):
            outputs.append(output)

        # Extract button states from all yields
        for idx, output in enumerate(outputs):
            # Index 5 is start_btn in the output tuple
            start_btn_update = output[5]
            if isinstance(start_btn_update, dict):
                interactive = start_btn_update.get("interactive", True)
                button_states.append(interactive)

        # Verify button was disabled during execution
        assert any(state is False for state in button_states), \
            "Start button should have been disabled during task"

        # Check final state - button should still be disabled
        final_output = outputs[-1]
        final_start_btn = final_output[5]
        assert isinstance(final_start_btn, dict)
        assert final_start_btn.get("interactive") is False, \
            "Start button should remain disabled after task completion"

    def test_start_button_stays_disabled_after_cancel(self, web_ui, tmp_path, monkeypatch):
        """Start Task button should remain disabled after task is cancelled."""
        session_id = "test-button-cancel"
        session = web_ui.get_session(session_id)

        # Setup project path
        project_path = tmp_path / "test_project_cancel"
        project_path.mkdir()
        self._init_git_repo(project_path)

        # Mock provider with delayed response to allow cancellation
        mock_provider = Mock()
        mock_provider.send_message = Mock()

        message_queue = queue.Queue()

        def mock_receive():
            # Wait for cancel or timeout
            try:
                return message_queue.get(timeout=5)
            except queue.Empty:
                return '{"type": "response", "content": "Cancelled"}'

        mock_provider.receive_message = mock_receive
        mock_provider.start_session = Mock(return_value=True)

        monkeypatch.setattr(
            "chad.providers.create_provider",
            lambda config: mock_provider
        )

        # Start task in thread
        outputs = []

        def run_task():
            for output in web_ui.start_chad_task(
                session_id,
                str(project_path),
                "Test task to cancel",
                "claude",
                ChadWebUI.VERIFICATION_NONE
            ):
                outputs.append(output)
                # Cancel after first output
                if len(outputs) == 2:
                    session.cancel_requested = True

        task_thread = threading.Thread(target=run_task)
        task_thread.start()

        # Wait for thread to complete
        task_thread.join(timeout=2)

        # The last output should have the button states after cancellation
        assert len(outputs) > 0, "Should have gotten outputs"

        final_output = outputs[-1]
        # Check start button state after cancel (index 5)
        start_btn = final_output[5]
        assert isinstance(start_btn, dict)
        assert start_btn.get("interactive") is False, \
            "Start button should remain disabled after cancellation"

        # Also check follow-up is visible (index 10)
        followup_row = final_output[10]
        assert isinstance(followup_row, dict)
        assert followup_row.get("visible") is True, \
            "Follow-up panel should remain visible after cancellation"

    def test_followup_panel_shows_after_task_start(self, web_ui, tmp_path, monkeypatch):
        """Follow-up panel should be visible after task starts."""
        session_id = "test-followup-visible"
        web_ui.get_session(session_id)

        # Setup project path
        project_path = tmp_path / "test_project_followup"
        project_path.mkdir()
        self._init_git_repo(project_path)

        # Mock provider
        mock_provider = Mock()
        mock_provider.send_message = Mock()
        mock_provider.receive_message = Mock(
            return_value='{"type": "response", "content": "Task done"}'
        )
        mock_provider.start_session = Mock(return_value=True)

        monkeypatch.setattr(
            "chad.providers.create_provider",
            lambda config: mock_provider
        )

        # Track follow-up visibility
        followup_states = []

        outputs = list(web_ui.start_chad_task(
            session_id,
            str(project_path),
            "Test follow-up visibility",
            "claude",
            ChadWebUI.VERIFICATION_NONE
        ))

        # Extract follow-up row visibility from outputs
        for output in outputs:
            # Index 10 is followup_row in the output tuple
            if len(output) > 10:
                followup_update = output[10]
                if isinstance(followup_update, dict):
                    followup_states.append(followup_update.get("visible", False))

        # Verify follow-up became visible
        assert any(state is True for state in followup_states), \
            "Follow-up panel should have become visible"

        # Check final state
        final_output = outputs[-1]
        if len(final_output) > 10:
            final_followup = final_output[10]
            assert isinstance(final_followup, dict)
            assert final_followup.get("visible") is True, \
                "Follow-up panel should remain visible after task completion"

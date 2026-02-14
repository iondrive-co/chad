"""Test canceling and restarting tasks."""

import threading
import time
import pytest
from dataclasses import dataclass
from unittest.mock import MagicMock, Mock, patch

from chad.ui.gradio.gradio_ui import ChadWebUI, Session
from chad.ui.client.api_client import Account


@dataclass
class MockAccount:
    """Mock account for tests."""

    name: str
    provider: str
    model: str | None = "default"
    reasoning: str | None = "default"
    role: str | None = None


class TestCancelTask:
    """Test task cancellation and restart functionality."""

    @pytest.fixture
    def gradio_ui(self, tmp_path):
        """Create ChadWebUI instance."""
        api_client = MagicMock()
        api_client.list_accounts.return_value = [
            Account(name="test-account", provider="anthropic", model=None,
                    reasoning=None, role="CODING", ready=True)
        ]
        api_client.list_providers.return_value = ["anthropic"]
        return ChadWebUI(api_client)

    def test_cancel_marks_session_inactive(self, gradio_ui):
        """Canceling a task should mark session as inactive and clear provider."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        gradio_ui.sessions["test-session"] = session

        result = gradio_ui.cancel_task("test-session")

        # Result is a tuple, live_stream is at index 0
        assert isinstance(result, tuple), "cancel_task should return tuple of UI updates"
        live_stream_update = result[0]
        assert "cancelled" in live_stream_update.get("value", "").lower()
        assert session.cancel_requested is True
        assert session.active is False
        assert session.provider is None
        assert session.config is None

    def test_session_ready_for_restart_after_cancel(self, gradio_ui):
        """After cancellation, session should be ready for a new task."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        gradio_ui.sessions["test-session"] = session

        gradio_ui.cancel_task("test-session")

        # Session should be in a clean state ready for restart
        assert session.active is False
        assert session.provider is None
        assert session.config is None

        # Simulate UI resetting cancel flag when starting new task
        session.cancel_requested = False
        session.active = True
        assert session.active is True

    def test_cancel_returns_ui_updates(self, gradio_ui):
        """Canceling a task should return UI component updates to re-enable start button."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"}
        )
        gradio_ui.sessions["test-session"] = session

        result = gradio_ui.cancel_task("test-session")

        # Should be a tuple of UI component updates, not just live_stream
        assert isinstance(result, tuple), "cancel_task should return tuple of UI updates"
        # Tuple should have 9 elements matching cancel_btn.click outputs:
        # (live_stream, chatbot, task_status, project_path, task_description,
        #  start_btn, cancel_btn, followup_row, merge_section_group)
        assert len(result) == 9, f"Expected 9 UI updates, got {len(result)}"

        # Check live_stream shows cancellation message (index 0)
        live_stream_update = result[0]
        assert isinstance(live_stream_update, dict), "live_stream update should be a dict"
        assert "cancelled" in live_stream_update.get("value", "").lower()

        # Check that start_btn is re-enabled (index 5)
        start_btn_update = result[5]
        assert isinstance(start_btn_update, dict), "start_btn update should be a dict"
        assert start_btn_update.get("interactive") is True, "start_btn should be re-enabled after cancel"

        # Check that cancel_btn is disabled (index 6)
        cancel_btn_update = result[6]
        assert isinstance(cancel_btn_update, dict), "cancel_btn update should be a dict"
        assert cancel_btn_update.get("interactive") is False, "cancel_btn should be disabled after cancel"

        # Check task description input is re-enabled (index 4)
        task_description_update = result[4]
        assert isinstance(task_description_update, dict), "task_description update should be a dict"
        assert task_description_update.get("interactive") is True, (
            "task_description should be interactive=True after cancel so submit/start is available"
        )

        # Check that followup_row is hidden (index 7)
        followup_row_update = result[7]
        assert isinstance(followup_row_update, dict), "followup_row update should be a dict"
        assert followup_row_update.get("visible") is False, "followup_row should be hidden after cancel"

        # Check that merge_section_group is hidden (index 8)
        merge_section_update = result[8]
        assert isinstance(merge_section_update, dict), "merge_section_group update should be a dict"
        assert merge_section_update.get("visible") is False, "merge_section_group should be hidden after cancel"

    def test_cancel_requests_server_session_cancellation(self, gradio_ui):
        """Cancel should propagate to the server when an API session is active."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"},
            server_session_id="server-session-1",
        )
        gradio_ui.sessions["test-session"] = session
        gradio_ui.api_client.get_session.return_value = Mock(active=False)

        gradio_ui.cancel_task("test-session")

        gradio_ui.api_client.cancel_session.assert_called_once_with("server-session-1")

    @patch("chad.ui.gradio.gradio_ui.GitWorktreeManager")
    def test_cancel_skips_worktree_delete_if_server_still_active(self, mock_git_mgr_class, gradio_ui, tmp_path):
        """Don't delete worktree while server cancellation is still in-flight."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"},
            server_session_id="server-session-1",
            project_path=str(tmp_path),
            worktree_path=tmp_path / "worktree",
        )
        gradio_ui.sessions["test-session"] = session

        gradio_ui._wait_for_server_session_inactive = MagicMock(return_value=False)

        gradio_ui.cancel_task("test-session")
        time.sleep(0.05)

        mock_git_mgr_class.return_value.delete_worktree.assert_not_called()

    def test_cancel_returns_quickly_even_if_server_shutdown_is_slow(self, gradio_ui):
        """Cancel should return quickly and not block UI on server shutdown polling."""
        session = Session(
            id="test-session",
            name="Test Session",
            active=True,
            cancel_requested=False,
            provider=MagicMock(),
            config={"test": "config"},
            server_session_id="server-session-1",
        )
        gradio_ui.sessions["test-session"] = session

        started_wait = threading.Event()

        def slow_wait(_server_session_id, timeout_seconds=3.0, poll_interval=0.1):  # noqa: ARG001
            started_wait.set()
            time.sleep(0.25)
            return False

        gradio_ui.api_client.cancel_session = MagicMock()
        gradio_ui._wait_for_server_session_inactive = slow_wait

        start = time.perf_counter()
        result = gradio_ui.cancel_task("test-session")
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"cancel_task blocked for {elapsed:.3f}s"
        assert started_wait.wait(timeout=0.2), "Background shutdown wait should start asynchronously"
        assert isinstance(result, tuple)
        assert result[5].get("interactive") is True

    def test_final_yield_after_cancel_enables_start_button(self, gradio_ui, tmp_path, monkeypatch):
        """Final yield from start_chad_task should enable start button after cancel.

        This tests the race condition fix: when cancel_task() runs, it returns
        interactive=True for start_btn. But start_chad_task's generator continues
        and emits a final yield. The final yield must also have interactive=True
        when cancel_requested is True, otherwise it overwrites cancel_task's update.
        """
        # Create a git repo for the test
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()

        cancel_gate = threading.Event()
        stream_ready = threading.Event()

        def fake_run_task_via_api(session_id, project_path, task_description,
                                  coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working"))
            stream_ready.set()
            cancel_gate.wait(timeout=2.0)
            return False, "cancelled", "server-session"

        monkeypatch.setattr(gradio_ui, "run_task_via_api", fake_run_task_via_api)

        session = gradio_ui.create_session("test")
        updates = []

        def trigger_cancel():
            stream_ready.wait(timeout=2.0)
            session.cancel_requested = True
            cancel_gate.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        for update in gradio_ui.start_chad_task(session.id, str(git_dir), "test task", "test-account"):
            updates.append(update)

        # Check final yield has start button enabled (index 5)
        final_update = updates[-1]
        start_btn_update = final_update[5]
        assert isinstance(start_btn_update, dict), "start_btn update should be a dict"
        assert start_btn_update.get("interactive") is True, (
            "start_btn should be interactive=True in final yield after cancel"
        )

        # Check followup row is hidden after cancel (index 10)
        followup_row_update = final_update[10]
        assert isinstance(followup_row_update, dict), "followup_row update should be a dict"
        assert followup_row_update.get("visible") is False, (
            "followup_row should be visible=False in final yield after cancel"
        )

    def test_final_yield_after_cancel_reenables_task_input(self, gradio_ui, tmp_path, monkeypatch):
        """Final yield after cancel should re-enable task input for immediate restart."""
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()

        cancel_gate = threading.Event()
        stream_ready = threading.Event()

        def fake_run_task_via_api(
            session_id, project_path, task_description, coding_account, message_queue, **kwargs
        ):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working"))
            stream_ready.set()
            cancel_gate.wait(timeout=2.0)
            return False, "cancelled", "server-session", None

        monkeypatch.setattr(gradio_ui, "run_task_via_api", fake_run_task_via_api)

        session = gradio_ui.create_session("test")
        updates = []

        def trigger_cancel():
            stream_ready.wait(timeout=2.0)
            session.cancel_requested = True
            cancel_gate.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        for update in gradio_ui.start_chad_task(session.id, str(git_dir), "test task", "test-account"):
            updates.append(update)

        final_update = updates[-1]
        task_description_update = final_update[4]
        assert isinstance(task_description_update, dict), "task_description update should be a dict"
        assert task_description_update.get("interactive") is True, (
            "task_description should be interactive=True after cancel so submit/start arrow is enabled"
        )

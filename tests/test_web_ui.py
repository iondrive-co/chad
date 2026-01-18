"""Tests for web UI module."""

import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from chad.util.git_worktree import GitWorktreeManager
from chad.util.providers import ModelConfig, MockProvider


@dataclass
class MockAccount:
    """Mock account for tests."""

    name: str
    provider: str
    model: str | None = "default"
    reasoning: str | None = "default"
    role: str | None = None


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial file and commit
    (repo_path / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Ensure we're on main branch
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


class TestChadWebUI:
    """Test cases for ChadWebUI class."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client."""
        client = Mock()
        # Default accounts
        claude_account = MockAccount(name="claude", provider="anthropic", role="CODING")
        gpt_account = MockAccount(name="gpt", provider="openai")
        client.list_accounts.return_value = [claude_account, gpt_account]
        client.get_account.side_effect = lambda name: {
            "claude": claude_account,
            "gpt": gpt_account,
        }.get(name, Mock(name=name, provider="unknown", model="default", reasoning="default", role=None))
        client.get_verification_agent.return_value = None
        client.get_preferences.return_value = Mock(last_project_path=None, dark_mode=True, ui_mode="gradio")
        client.get_cleanup_settings.return_value = Mock(retention_days=7, auto_cleanup=True)
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.ui.gradio.web_ui import ChadWebUI

        ui = ChadWebUI(mock_api_client)
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/codex"))
        return ui

    def test_init(self, web_ui, mock_api_client):
        """Test ChadWebUI initialization."""
        assert web_ui.api_client == mock_api_client

    def test_progress_bar_helper(self, web_ui):
        """Progress bar helper should clamp values and preserve width."""
        half_bar = web_ui._progress_bar(50)
        assert len(half_bar) == 20
        assert half_bar.startswith("█████")
        assert half_bar.endswith("░░░░░")
        full_bar = web_ui._progress_bar(150)
        assert full_bar == "█" * 20

    @patch("subprocess.run")
    def test_add_provider_success(self, mock_run, web_ui, mock_api_client, tmp_path):
        """Test adding a new provider successfully (OpenAI/Codex)."""
        mock_api_client.list_accounts.return_value = []
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(web_ui.provider_ui, "_setup_codex_account", return_value=str(tmp_path)),
        ):
            result = web_ui.add_provider("my-codex", "openai")[0]

        assert "✅" in result or "✓" in result
        assert "my-codex" in result
        mock_api_client.create_account.assert_called_once_with("my-codex", "openai")

    @patch("subprocess.run")
    def test_add_provider_auto_name(self, mock_run, web_ui, mock_api_client, tmp_path):
        """Test adding provider with auto-generated name."""
        mock_api_client.list_accounts.return_value = []
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(web_ui.provider_ui, "_setup_codex_account", return_value=str(tmp_path)),
        ):
            result = web_ui.add_provider("", "openai")[0]

        assert "✓" in result or "Provider" in result
        assert "openai" in result
        mock_api_client.create_account.assert_called_once_with("openai", "openai")

    @patch("subprocess.run")
    def test_add_provider_duplicate_name(self, mock_run, web_ui, mock_api_client, tmp_path):
        """Test adding provider when name already exists (OpenAI/Codex)."""
        mock_api_client.list_accounts.return_value = [MockAccount(name="openai", provider="openai")]
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(web_ui.provider_ui, "_setup_codex_account", return_value=str(tmp_path)),
        ):
            result = web_ui.add_provider("", "openai")[0]

        # Should create openai-1
        assert "✅" in result or "✓" in result
        mock_api_client.create_account.assert_called_once_with("openai-1", "openai")

    @patch("subprocess.run")
    def test_add_provider_error(self, mock_run, web_ui, mock_api_client, tmp_path):
        """Test adding provider when login fails (OpenAI/Codex)."""
        mock_api_client.list_accounts.return_value = []
        # Mock Codex login to fail
        mock_run.return_value = Mock(returncode=1, stderr="Login cancelled", stdout="")

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(web_ui.provider_ui, "_setup_codex_account", return_value=str(tmp_path)),
        ):
            result = web_ui.add_provider("test", "openai")[0]

        assert "❌" in result
        assert "Login failed" in result or "cancelled" in result.lower()

    def test_assign_role_success(self, web_ui, mock_api_client):
        """Test assigning a role successfully."""
        # Use gpt account which doesn't have a role yet
        result = web_ui.assign_role("gpt", "CODING")[0]

        assert "✓" in result
        assert "CODING" in result
        mock_api_client.set_account_role.assert_called_once_with("gpt", "CODING")

    def test_assign_role_not_found(self, web_ui, mock_api_client):
        """Test assigning role to non-existent provider."""
        result = web_ui.assign_role("nonexistent", "CODING")[0]

        assert "❌" in result
        assert "not found" in result

    def test_assign_role_lowercase_converted(self, web_ui, mock_api_client):
        """Test that lowercase role names are converted to uppercase."""
        # Use gpt account which doesn't have an existing role
        web_ui.assign_role("gpt", "coding")

        mock_api_client.set_account_role.assert_called_once_with("gpt", "CODING")

    def test_assign_role_missing_account(self, web_ui, mock_api_client):
        """Test assigning role without selecting account."""
        result = web_ui.assign_role("", "CODING")[0]

        assert "❌" in result
        assert "select an account" in result

    def test_assign_role_missing_role(self, web_ui, mock_api_client):
        """Test assigning role without selecting role."""
        result = web_ui.assign_role("claude", "")[0]

        assert "❌" in result
        assert "select a role" in result

    def test_delete_provider_success(self, web_ui, mock_api_client):
        """Test deleting a provider successfully."""
        result = web_ui.delete_provider("claude", True)[0]

        assert "✓" in result
        assert "deleted" in result
        mock_api_client.delete_account.assert_called_once_with("claude")

    def test_delete_provider_requires_confirmation(self, web_ui, mock_api_client):
        """Test that deletion requires confirmation."""
        result = web_ui.delete_provider("claude", False)[0]

        # When not confirmed, deletion is cancelled
        assert "cancelled" in result.lower()
        mock_api_client.delete_account.assert_not_called()

    def test_delete_provider_error(self, web_ui, mock_api_client):
        """Test deleting provider when error occurs."""
        mock_api_client.delete_account.side_effect = Exception("Delete error")

        result = web_ui.delete_provider("claude", True)[0]

        assert "❌" in result
        assert "Error" in result

    def test_delete_provider_missing_account(self, web_ui, mock_api_client):
        """Test deleting provider without selecting account."""
        result = web_ui.delete_provider("", False)[0]

        assert "❌" in result
        assert "no provider selected" in result.lower()

    def test_attempt_merge_handles_commit_error(self, web_ui, git_repo, monkeypatch):
        """Attempting merge should surface commit errors without clearing state."""
        session_id = "merge-error"
        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = Path(git_repo / ".chad-worktrees" / session_id)
        session.worktree_path.mkdir(parents=True, exist_ok=True)
        session.worktree_branch = f"chad-task-{session_id}"
        session.worktree_base_commit = "deadbeef"

        def fake_merge(self, task_id, commit_message=None, target_branch=None):
            assert task_id == session_id
            return False, None, "Commit hook failed"

        monkeypatch.setattr(GitWorktreeManager, "merge_to_main", fake_merge)

        outputs = web_ui.attempt_merge(session_id, "msg", "main")

        merge_section_update = outputs[0]
        task_status = outputs[5]

        assert merge_section_update.get("visible") is True
        assert task_status["value"].startswith("❌ Commit hook failed")
        assert session.worktree_path is not None
        assert session.worktree_path.exists()

    def test_discard_keeps_task_description(self, web_ui, git_repo):
        """Discard should keep the task description so user can retry."""
        session_id = "discard-test"
        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = Path(git_repo / ".chad-worktrees" / session_id)
        session.worktree_path.mkdir(parents=True, exist_ok=True)
        session.worktree_branch = f"chad-task-{session_id}"
        session.task_description = "Test task to be preserved"

        outputs = web_ui.discard_worktree_changes(session_id)

        # Index 11 should be task_description update (no_change to preserve it)
        # Note: 14 outputs total (includes merge_section_header, diff_content)
        assert len(outputs) >= 14, "Discard should return 14 outputs"
        task_desc_update = outputs[11]
        assert task_desc_update.get("value") == session.task_description, (
            "Task description value should be preserved for retry"
        )
        assert task_desc_update.get("interactive") is False, "Task description should remain locked after task starts"

        # Verify header and diff cleared after discard
        header_text = outputs[12]
        diff_content = outputs[13]
        assert header_text == "", "Header should be cleared after discard"
        assert diff_content == "", "Diff content should be cleared after discard"

    def test_merge_clears_task_description_on_success(self, web_ui, git_repo, monkeypatch):
        """Successful merge should clear the task description input."""
        session_id = "merge-clear-test"
        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = Path(git_repo / ".chad-worktrees" / session_id)
        session.worktree_path.mkdir(parents=True, exist_ok=True)
        session.worktree_branch = f"chad-task-{session_id}"
        session.task_description = "Test task to be cleared after merge"

        def fake_merge(self, task_id, commit_message=None, target_branch=None):
            return True, None, None  # success, no conflicts, no error

        def fake_cleanup(self, task_id):
            pass

        monkeypatch.setattr(GitWorktreeManager, "merge_to_main", fake_merge)
        monkeypatch.setattr(GitWorktreeManager, "cleanup_after_merge", fake_cleanup)
        monkeypatch.setattr(GitWorktreeManager, "get_main_branch", lambda self: "main")

        outputs = web_ui.attempt_merge(session_id, "msg", "main")

        # Index 11 should be task_description update (direct value "" or gr.update)
        # Note: 14 outputs total (includes merge_section_header, diff_content)
        assert len(outputs) >= 14, "Merge should return 14 outputs"
        task_desc_update = outputs[11]
        # Handle both direct value "" and gr.update(value="")
        if isinstance(task_desc_update, str):
            assert task_desc_update == "", "Task description should be cleared"
        else:
            assert task_desc_update.get("value") == "", "Task description should be cleared"

        # Verify header and diff cleared after merge
        header_text = outputs[12]
        diff_content = outputs[13]
        assert header_text == "", "Header should be cleared after merge"
        assert diff_content == "", "Diff content should be cleared after merge"

    def test_merge_preserves_chatbot_and_followup(self, web_ui, git_repo, monkeypatch):
        """Successful merge should preserve chatbot and followup_row for follow-up conversations."""
        session_id = "merge-preserve-chat-test"
        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = Path(git_repo / ".chad-worktrees" / session_id)
        session.worktree_path.mkdir(parents=True, exist_ok=True)
        session.worktree_branch = f"chad-task-{session_id}"
        session.chat_history = [{"role": "user", "content": "Test message"}]

        def fake_merge(self, task_id, commit_message=None, target_branch=None):
            return True, None, None  # success, no conflicts, no error

        def fake_cleanup(self, task_id):
            pass

        monkeypatch.setattr(GitWorktreeManager, "merge_to_main", fake_merge)
        monkeypatch.setattr(GitWorktreeManager, "cleanup_after_merge", fake_cleanup)
        monkeypatch.setattr(GitWorktreeManager, "get_main_branch", lambda self: "main")

        outputs = web_ui.attempt_merge(session_id, "msg", "main")

        # Index 6 is chatbot - should NOT be cleared (use no_change)
        chatbot_update = outputs[6]
        assert not isinstance(chatbot_update, list), "Chatbot should not be cleared to empty list"

        # Index 10 is followup_row - should remain visible
        followup_update = outputs[10]
        assert followup_update.get("visible") is not False, "Followup row should remain visible"

    def test_discard_preserves_chatbot_and_followup(self, web_ui, git_repo):
        """Discard should preserve chatbot and followup_row for follow-up conversations."""
        session_id = "discard-preserve-chat-test"
        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = Path(git_repo / ".chad-worktrees" / session_id)
        session.worktree_path.mkdir(parents=True, exist_ok=True)
        session.worktree_branch = f"chad-task-{session_id}"
        session.chat_history = [{"role": "user", "content": "Test message"}]

        outputs = web_ui.discard_worktree_changes(session_id)

        # Index 6 is chatbot - should NOT be cleared (use no_change)
        chatbot_update = outputs[6]
        assert not isinstance(chatbot_update, list), "Chatbot should not be cleared to empty list"

        # Index 10 is followup_row - should remain visible
        followup_update = outputs[10]
        assert followup_update.get("visible") is not False, "Followup row should remain visible"

    def test_followup_after_discard_uses_clean_worktree(self, web_ui, git_repo, monkeypatch):
        """Follow-up after discard should still operate in a clean worktree."""
        session_id = "discard-followup-test"
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit
        session.active = True
        session.chat_history = [{"role": "user", "content": "**Task**\n\nModify BUGS.md"}]

        # Speed up the mock provider
        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)
        config = ModelConfig(provider="mock", model_name="default", account_name="claude")
        provider = MockProvider(config)
        provider.start_session(str(worktree_path))
        session.provider = provider
        session.coding_account = "claude"
        session.config = config

        # Simulate an initial coding turn to represent the first task run
        provider.send_message("Initial coding turn")
        provider.get_response()

        # Discard before sending a follow-up
        web_ui.discard_worktree_changes(session_id)

        # Send a follow-up; this should succeed and write BUGS.md in the worktree
        list(
            web_ui.send_followup(
                session_id,
                "Add follow-up entry",
                session.chat_history,
                coding_agent="claude",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        assert session.worktree_path is not None
        bugs_file = session.worktree_path / "BUGS.md"
        assert bugs_file.exists(), "BUGS.md should be present after follow-up"
        content = bugs_file.read_text()
        assert "follow-up" in content.lower(), "Follow-up marker should be added to BUGS.md"
        assert not any(
            "Error" in msg.get("content", "") for msg in session.chat_history if msg.get("role") == "assistant"
        ), "Follow-up response should not include errors"

    def test_set_reasoning_success(self, web_ui, mock_api_client):
        """Test setting reasoning level for an account."""
        result = web_ui.set_reasoning("claude", "high")[0]

        assert "✓" in result
        assert "high" in result
        mock_api_client.set_account_reasoning.assert_called_once_with("claude", "high")

    def test_add_provider_install_failure(self, web_ui, mock_api_client):
        """Installer failures should surface to the user."""
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(False, "Node missing"))
        mock_api_client.list_accounts.return_value = []

        result = web_ui.add_provider("", "openai")[0]

        assert "❌" in result
        assert "Node missing" in result
        mock_api_client.create_account.assert_not_called()

    def test_get_models_includes_stored_model(self, web_ui, mock_api_client, tmp_path):
        """Stored models should always be present in dropdown choices."""
        mock_api_client.list_accounts.return_value = [MockAccount(name="gpt", provider="openai")]
        mock_api_client.get_account_model.return_value = "gpt-5.1-codex-max"
        from chad.util.model_catalog import ModelCatalog

        web_ui.model_catalog = ModelCatalog(api_client=mock_api_client, home_dir=tmp_path, cache_ttl=0)
        models = web_ui.get_models_for_account("gpt")

        assert "gpt-5.1-codex-max" in models
        assert "default" in models

    def test_get_account_choices(self, web_ui, mock_api_client):
        """Test getting account choices for dropdowns."""
        choices = web_ui.get_account_choices()

        assert "claude" in choices
        assert "gpt" in choices

    def test_cancel_task(self, web_ui, mock_api_client):
        """Test cancelling a running task."""
        session = web_ui.create_session("test")
        mock_provider = Mock()
        session.provider = mock_provider

        result = web_ui.cancel_task(session.id)

        assert "🛑" in result
        assert "cancelled" in result.lower()
        assert session.cancel_requested is True
        mock_provider.stop_session.assert_called_once()

    def test_cancel_task_no_session(self, web_ui, mock_api_client):
        """Test cancelling when no session is running."""
        session = web_ui.create_session("test")
        result = web_ui.cancel_task(session.id)

        assert "🛑" in result
        assert session.cancel_requested is True

    def test_cancel_preserves_live_stream(self, monkeypatch, web_ui, git_repo):
        """Cancelling should not clear the live output panel."""

        live_html = "<pre>MOCK LIVE OUTPUT</pre>"
        cancel_gate = threading.Event()
        stream_ready = threading.Event()

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working", live_html))
            stream_ready.set()
            cancel_gate.wait(timeout=1.0)
            return False, "stopped", "server-session"

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = []

        def trigger_cancel():
            stream_ready.wait(timeout=1.0)
            time.sleep(0.05)
            session.cancel_requested = True
            cancel_gate.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        for update in web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"):
            updates.append(update)

        final_live_stream = updates[-1][1]
        assert "MOCK LIVE OUTPUT" in final_live_stream

    def test_cancel_preserves_plain_live_stream(self, monkeypatch, web_ui, git_repo):
        """Cancelling should keep plain text live output when no HTML chunk is provided."""

        live_text = "MOCK LIVE OUTPUT FROM TEXT"
        cancel_gate = threading.Event()
        stream_ready = threading.Event()

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", live_text))
            stream_ready.set()
            cancel_gate.wait(timeout=1.0)
            return False, "stopped", "server-session"

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = []

        def trigger_cancel():
            stream_ready.wait(timeout=1.0)
            time.sleep(0.05)
            session.cancel_requested = True
            cancel_gate.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        for update in web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"):
            updates.append(update)

        final_live_stream = updates[-1][1]
        assert "MOCK LIVE OUTPUT FROM TEXT" in final_live_stream


class TestLiveStreamPresentation:
    """Formatting and styling tests for the live activity stream."""

    def test_live_stream_spacing_removes_all_blank_lines(self):
        """Live stream should remove all blank lines for compact display."""
        from chad.ui.gradio import web_ui

        raw = "first line\n\n\nsecond block\n\n\n\nthird"
        normalized = web_ui.normalize_live_stream_spacing(raw)

        assert normalized == "first line\nsecond block\nthird"
        rendered = web_ui.build_live_stream_html(raw, "AI")

        assert "first line\nsecond block\nthird" in rendered
        assert "\n\n" not in rendered


class TestPortResolution:
    """Ensure the UI chooses a safe port when launching."""

    @pytest.fixture(autouse=True)
    def skip_when_sockets_blocked(self):
        """Skip port resolution tests when sockets are not permitted."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
                pass
        except PermissionError:
            pytest.skip("Socket operations not permitted in this environment")

    def test_resolve_port_keeps_requested_when_free(self):
        """Requested port should be used when it is available."""
        from chad.ui.gradio.web_ui import _resolve_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        port, ephemeral, conflicted = _resolve_port(free_port)

        assert port == free_port
        assert ephemeral is False
        assert conflicted is False

    def test_resolve_port_returns_ephemeral_when_in_use(self):
        """If the requested port is busy, fall back to an ephemeral choice."""
        from chad.ui.gradio.web_ui import _resolve_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            busy_port = s.getsockname()[1]
            s.listen(1)

            port, ephemeral, conflicted = _resolve_port(busy_port)

        assert port != busy_port
        assert ephemeral is True
        assert conflicted is True

    def test_resolve_port_supports_explicit_ephemeral(self):
        """Port zero should always yield an ephemeral assignment."""
        from chad.ui.gradio.web_ui import _resolve_port

        port, ephemeral, conflicted = _resolve_port(0)

        assert port > 0
        assert ephemeral is True
        assert conflicted is False

    def test_live_stream_inline_code_is_color_only(self):
        """Inline code should be colored text without a background box."""
        from chad.ui.gradio import web_ui

        code_css_match = re.search(
            r"#live-stream-box\s+code[^}]*\{([^}]*)\}",
            web_ui.PROVIDER_PANEL_CSS,
        )
        assert code_css_match, "Expected live stream code style block"

        code_block = code_css_match.group(1)
        assert "background: none" in code_block or "background: transparent" in code_block
        assert "padding: 0" in code_block

    def test_live_stream_box_visibility_not_forced_globally(self):
        """Live stream box should not be forced visible in normal UI CSS."""
        from chad.ui.gradio import web_ui

        css = web_ui.PROVIDER_PANEL_CSS
        start_idx = css.find("#live-stream-box")
        assert start_idx != -1, "Expected live stream box style block"
        brace_idx = css.find("{", start_idx)
        end_idx = css.find("}", brace_idx)
        assert brace_idx != -1 and end_idx != -1, "Expected live stream box style block"

        box_block = css[brace_idx + 1 : end_idx]
        assert "display:" not in box_block
        assert "visibility:" not in box_block


def test_live_stream_display_buffer_trims_to_tail():
    """Live stream display buffer should keep only the most recent content."""
    from chad.ui.gradio.web_ui import LiveStreamDisplayBuffer

    buffer = LiveStreamDisplayBuffer(max_chars=100)
    buffer.append("a" * 60)
    buffer.append("b" * 60)

    assert len(buffer.content) == 100
    assert buffer.content == ("a" * 40) + ("b" * 60)


def test_live_stream_render_state_resets_for_rerender():
    """Resetting render state should allow re-rendering the same output."""
    from chad.ui.gradio.web_ui import LiveStreamRenderState

    state = LiveStreamRenderState()
    rendered = "<div>output</div>"

    assert state.should_render(rendered) is True
    state.record(rendered)
    assert state.should_render(rendered) is False

    state.reset()
    assert state.should_render(rendered) is True


@pytest.mark.skip(reason="Task execution tests need update for API streaming - provider-based execution being replaced")
class TestChadWebUITaskExecution:
    """Test cases for task execution in ChadWebUI."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        mgr.list_role_assignments.return_value = {"CODING": "claude"}
        mgr.get_account_model.return_value = "default"
        mgr.get_account_reasoning.return_value = "default"
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance."""
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    def test_claude_remaining_usage_wrapper_requires_account(self, web_ui):
        """Public wrapper should pass account name through to provider UI."""
        with patch.object(web_ui.provider_ui, "_get_claude_remaining_usage", return_value=0.5) as mock_call:
            assert web_ui._get_claude_remaining_usage("claude-1") == 0.5
            mock_call.assert_called_once_with("claude-1")

    def test_start_task_missing_project(self, web_ui):
        """Test starting task without project path."""
        session = web_ui.create_session("test")
        results = list(web_ui.start_chad_task(session.id, "", "do something", "test-coding"))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get("value", "") if isinstance(status_header, dict) else str(status_header)
        assert "❌" in status_value
        assert "project path" in status_value.lower() or "task description" in status_value.lower()

    def test_start_task_missing_description(self, web_ui):
        """Test starting task without task description."""
        session = web_ui.create_session("test")
        results = list(web_ui.start_chad_task(session.id, "/tmp", "", "test-coding"))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get("value", "") if isinstance(status_header, dict) else str(status_header)
        assert "❌" in status_value

    def test_start_task_invalid_path(self, web_ui):
        """Test starting task with invalid project path."""
        session = web_ui.create_session("test")
        results = list(web_ui.start_chad_task(session.id, "/nonexistent/path/xyz", "do something", "test-coding"))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get("value", "") if isinstance(status_header, dict) else str(status_header)
        assert "❌" in status_value
        assert "Invalid project path" in status_value

    def test_start_task_missing_agents(self, mock_api_client):
        """Test starting task when agents are not selected."""
        from chad.ui.gradio.web_ui import ChadWebUI

        mock_api_client.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_api_client)
        session = web_ui.create_session("test")
        results = list(web_ui.start_chad_task(session.id, "/tmp", "do something", ""))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get("value", "") if isinstance(status_header, dict) else str(status_header)
        assert "❌" in status_value
        assert "Coding Agent" in status_value

    def test_verification_preferences_use_verification_agent(self, monkeypatch, tmp_path, git_repo):
        """Verification dropdowns should apply to verification agent without mutating coding prefs."""
        from chad.ui.gradio import web_ui

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="coder", provider="anthropic", role="CODING"), MockAccount(name="verifier", provider="openai")]
        api_client.list_role_assignments.return_value = {"CODING": "coder"}
        api_client.get_account_model.side_effect = lambda acct: {"coder": "claude-3", "verifier": "gpt-4"}[acct]
        api_client.get_account_reasoning.side_effect = lambda acct: {"coder": "medium", "verifier": "high"}[acct]
        api_client.assign_role = Mock()
        api_client.set_account_model = Mock()
        api_client.set_account_reasoning = Mock()

        captured = {}

        class StubProvider:
            def __init__(self, config):
                self.config = config
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        def fake_create_provider(config):
            return StubProvider(config)

        def fake_run_verification(
            project_path,
            coding_output,
            task_description,
            verification_account,
            on_activity=None,
            timeout=300.0,
            verification_model=None,
            verification_reasoning=None,
        ):
            captured["account"] = verification_account
            captured["model"] = verification_model
            captured["reasoning"] = verification_reasoning
            return True, "ok"

        monkeypatch.setattr(web_ui, "create_provider", fake_create_provider)
        ui = web_ui.ChadWebUI(api_client)
        monkeypatch.setattr(ui, "_run_verification", fake_run_verification)

        session = ui.create_session("test")
        list(
            ui.start_chad_task(
                session.id,
                str(git_repo),
                "do something",
                "coder",
                "verifier",
                "claude-3-opus",
                "medium",
                "gpt-4o",
                "max",
            )
        )

        assert captured["account"] == "verifier"
        assert captured["model"] == "gpt-4o"
        assert captured["reasoning"] == "max"

        model_calls = [call.args for call in api_client.set_account_model.call_args_list]
        assert ("coder", "gpt-4o") not in model_calls
        assert ("verifier", "gpt-4o") in model_calls
        reasoning_calls = [call.args for call in api_client.set_account_reasoning.call_args_list]
        assert ("coder", "max") not in reasoning_calls
        assert ("verifier", "max") in reasoning_calls

    def test_same_as_coding_uses_coding_preferences(self, mock_api_client):
        """Verification prefs must mirror coding selections when using same agent."""
        from chad.ui.gradio import web_ui

        mock_api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING"), MockAccount(name="gpt", provider="openai")]
        ui = web_ui.ChadWebUI(mock_api_client)

        verification_agent = ui.SAME_AS_CODING
        account, model, reasoning = ui._resolve_verification_preferences(
            "claude",
            "claude-3-opus",
            "high",
            verification_agent,
            "gpt-5.1-codex-max",
            "max",
        )

        assert account == "claude"
        assert model == "claude-3-opus"
        assert reasoning == "high"

    def test_verification_none_disables_verification(self, monkeypatch, tmp_path, git_repo):
        """Selecting None for verification should skip verification runs."""
        from chad.ui.gradio import web_ui

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="coder", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "coder"}
        api_client.get_account_model.return_value = "claude-3"
        api_client.get_account_reasoning.return_value = "medium"
        api_client.assign_role = Mock()
        api_client.set_account_model = Mock()
        api_client.set_account_reasoning = Mock()

        class StubProvider:
            def __init__(self, config):
                self.config = config
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        monkeypatch.setattr(web_ui, "create_provider", lambda config: StubProvider(config))

        verification_called = {"called": False}

        def fake_run_verification(*args, **kwargs):
            verification_called["called"] = True
            return True, "ok"

        ui = web_ui.ChadWebUI(api_client)
        monkeypatch.setattr(ui, "_run_verification", fake_run_verification)

        session = ui.create_session("test")
        list(
            ui.start_chad_task(
                session.id,
                str(git_repo),
                "do something",
                "coder",
                ui.VERIFICATION_NONE,
                "claude-3",
                "medium",
                None,
                None,
            )
        )

        assert verification_called["called"] is False

    def test_start_task_revision_runtime_error_handled(self, monkeypatch, tmp_path, git_repo):
        """Runtime errors during revision should be surfaced without crashing."""
        from chad.ui.gradio import web_ui

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "claude"}
        api_client.get_account_model.return_value = "default"
        api_client.get_account_reasoning.return_value = "default"
        api_client.assign_role = Mock()
        api_client.set_account_model = Mock()
        api_client.set_account_reasoning = Mock()

        class StubProvider:
            def __init__(self, config):
                self.config = config
                self.calls = 0
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    return "codex\nok"
                raise RuntimeError("timeout during revision")

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        monkeypatch.setattr(web_ui, "create_provider", lambda config: StubProvider(config))
        ui = web_ui.ChadWebUI(api_client)
        monkeypatch.setattr(ui, "_run_verification", lambda *args, **kwargs: (False, "issues"))

        session = ui.create_session("test")
        results = list(ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))
        last_history = results[-1][0]
        assert any("Error:" in msg.get("content", "") for msg in last_history)

    def test_verification_banner_yields_before_result(self, monkeypatch, tmp_path, git_repo):
        """Verification banner should render immediately before verification finishes."""
        from chad.ui.gradio import web_ui

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "claude"}
        api_client.get_account_model.return_value = "default"
        api_client.get_account_reasoning.return_value = "default"

        class StubProvider:
            def __init__(self):
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        monkeypatch.setattr(web_ui, "create_provider", lambda config: StubProvider())
        monkeypatch.setattr(web_ui.ChadWebUI, "_run_verification", lambda *args, **kwargs: (True, "verified"))

        ui = web_ui.ChadWebUI(api_client)

        session = ui.create_session("test")
        updates = []
        for update in ui.start_chad_task(session.id, str(git_repo), "do something", "claude"):
            history_snapshot = [msg.copy() if isinstance(msg, dict) else msg for msg in update[0]]
            updates.append((history_snapshot, *update[1:]))

        def find_first(predicate):
            for idx, update in enumerate(updates):
                history = update[0]
                contents = [msg.get("content", "") for msg in history if isinstance(msg, dict)]
                if any(predicate(content) for content in contents):
                    return idx
            return None

        def find_banner_without_result():
            for idx, update in enumerate(updates):
                history = update[0]
                contents = [msg.get("content", "") for msg in history if isinstance(msg, dict)]
                if any("VERIFICATION (Attempt 1)" in content for content in contents) and not any(
                    marker in content
                    for content in contents
                    for marker in ("VERIFICATION AI", "VERIFICATION PASSED", "VERIFICATION ERROR")
                ):
                    return idx
            return None

        banner_only_idx = find_banner_without_result()
        result_idx = find_first(lambda content: "VERIFICATION AI" in content or "VERIFICATION PASSED" in content)

        assert banner_only_idx is not None
        assert result_idx is not None
        assert banner_only_idx < result_idx

    def test_followup_revision_runtime_error_handled(self, monkeypatch, tmp_path):
        """Follow-up revisions should surface RuntimeError without crashing."""
        from chad.ui.gradio import web_ui
        from chad.util.providers import ModelConfig

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "claude"}
        api_client.get_account_model.return_value = "default"
        api_client.get_account_reasoning.return_value = "default"
        api_client.assign_role = Mock()
        api_client.set_account_model = Mock()
        api_client.set_account_reasoning = Mock()

        class StubProvider:
            def __init__(self):
                self.calls = 0
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    return "codex\nok"
                raise RuntimeError("timeout during revision")

            def is_alive(self):
                return not self.stopped

        ui = web_ui.ChadWebUI(api_client)
        session = ui.create_session("test")
        session.active = True
        session.provider = StubProvider()
        session.config = ModelConfig(
            provider="anthropic", model_name="default", account_name="claude", reasoning_effort=None
        )
        session.coding_account = "claude"
        session.project_path = str(tmp_path)
        session.chat_history = []
        monkeypatch.setattr(ui, "_run_verification", lambda *args, **kwargs: (False, "issues"))

        results = list(ui.send_followup(session.id, "follow up", [], "claude", web_ui.ChadWebUI.SAME_AS_CODING))
        last_history = results[-1][0]
        assert any("Error:" in msg.get("content", "") for msg in last_history)

    def test_followup_verification_banner_yields_before_result(self, monkeypatch, tmp_path, git_repo):
        """Follow-up verification banner should appear before results stream back."""
        from chad.ui.gradio import web_ui
        from chad.util.providers import ModelConfig

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "claude"}
        api_client.get_account_model.return_value = "default"
        api_client.get_account_reasoning.return_value = "default"

        class StubProvider:
            def __init__(self):
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        monkeypatch.setattr(web_ui.ChadWebUI, "_run_verification", lambda *args, **kwargs: (True, "verified"))

        ui = web_ui.ChadWebUI(api_client)

        session = ui.create_session("test")
        session.active = True
        session.provider = StubProvider()
        session.config = ModelConfig(
            provider="anthropic", model_name="default", account_name="claude", reasoning_effort=None
        )
        session.coding_account = "claude"
        session.project_path = str(git_repo)
        session.chat_history = []

        updates = []
        for update in ui.send_followup(
            session.id,
            "follow up",
            session.chat_history,
            coding_agent="claude",
            verification_agent=ui.SAME_AS_CODING,
        ):
            history_snapshot = [msg.copy() if isinstance(msg, dict) else msg for msg in update[0]]
            updates.append((history_snapshot, *update[1:]))

        def find_first(predicate):
            for idx, update in enumerate(updates):
                history = update[0]
                contents = [msg.get("content", "") for msg in history if isinstance(msg, dict)]
                if any(predicate(content) for content in contents):
                    return idx
            return None

        def find_banner_without_result():
            for idx, update in enumerate(updates):
                history = update[0]
                contents = [msg.get("content", "") for msg in history if isinstance(msg, dict)]
                if any("VERIFICATION (Attempt 1)" in content for content in contents) and not any(
                    marker in content
                    for content in contents
                    for marker in ("VERIFICATION AI", "VERIFICATION PASSED", "VERIFICATION ERROR")
                ):
                    return idx
            return None

        banner_only_idx = find_banner_without_result()
        result_idx = find_first(lambda content: "VERIFICATION AI" in content or "VERIFICATION PASSED" in content)

        assert banner_only_idx is not None
        assert result_idx is not None
        assert banner_only_idx < result_idx

    def test_followup_restarts_with_updated_preferences(self, tmp_path, monkeypatch, git_repo):
        """Follow-up should honor updated model/reasoning after task completion."""
        from chad.ui.gradio import web_ui

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING")]
        api_client.list_role_assignments.return_value = {"CODING": "claude"}
        api_client.get_account_model.return_value = "claude-3"
        api_client.get_account_reasoning.return_value = "medium"
        api_client.assign_role = Mock()
        api_client.set_account_model = Mock()
        api_client.set_account_reasoning = Mock()
        api_client.set_verification_agent = Mock()
        api_client.clear_role = Mock()

        created_configs = []

        class StubProvider:
            def __init__(self, config):
                self.config = config
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        def fake_create_provider(config):
            created_configs.append(config)
            return StubProvider(config)

        monkeypatch.setattr(web_ui, "create_provider", fake_create_provider)
        monkeypatch.setattr(web_ui.ChadWebUI, "_run_verification", lambda *args, **kwargs: (True, "ok"))

        ui = web_ui.ChadWebUI(api_client)

        session = ui.create_session("test")
        list(ui.start_chad_task(session.id, str(git_repo), "do something", "claude", ""))

        api_client.get_account_model.return_value = "claude-latest"
        api_client.get_account_reasoning.return_value = "high"

        list(
            ui.send_followup(
                session.id,
                "continue",
                session.chat_history,
                coding_agent="claude",
                verification_agent="",
                coding_model="claude-latest",
                coding_reasoning="high",
            )
        )

        assert len(created_configs) == 2
        assert created_configs[-1].model_name == "claude-latest"
        assert created_configs[-1].reasoning_effort == "high"

    def test_verification_dropdown_rejects_invalid_models_for_provider(self):
        """When verification is 'Same as Coding Agent', switching coding agents must validate model/reasoning."""
        from chad.ui.gradio.web_ui import ChadWebUI

        api_client = Mock()
        api_client.list_accounts.return_value = [MockAccount(name="codex-work", provider="openai"), MockAccount(name="claude-pro", provider="anthropic")]

        web_ui = ChadWebUI(api_client)

        # Mock get_models_for_account to return provider-specific models
        def mock_get_models(account):
            if account == "codex-work":
                return ["o3", "o3-mini", "gpt-4.1"]
            elif account == "claude-pro":
                return ["claude-sonnet-4-202", "claude-opus-4", "claude-haiku-4"]
            return ["default"]

        # Mock get_reasoning_choices to return provider-specific reasoning
        def mock_get_reasoning(provider_type, account):
            if provider_type == "openai":
                return ["low", "medium", "high"]
            elif provider_type == "anthropic":
                return ["default", "extended"]
            return ["default"]

        web_ui.get_models_for_account = mock_get_models
        web_ui.get_reasoning_choices = mock_get_reasoning

        # Scenario: User has codex selected with gpt-4.1 and high reasoning
        # Then switches to claude with verification still set to "Same as Coding Agent"
        state = web_ui._build_verification_dropdown_state(
            coding_agent="claude-pro",
            verification_agent=web_ui.SAME_AS_CODING,
            coding_model_value="gpt-4.1",  # Invalid for claude!
            coding_reasoning_value="high",  # Invalid for claude!
        )

        # BUG: Currently this test will FAIL because the code accepts invalid values
        # The model should be a valid claude model, not gpt-4.1
        assert state.model_value in mock_get_models(
            "claude-pro"
        ), f"Expected valid claude model, got {state.model_value}"
        assert state.reasoning_value in mock_get_reasoning(
            "anthropic", "claude-pro"
        ), f"Expected valid claude reasoning, got {state.reasoning_value}"


class TestChadWebUIInterface:
    """Test cases for Gradio interface creation."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = []
        mgr.list_role_assignments.return_value = {}
        mgr.load_config.return_value = {}
        mgr.load_preferences.return_value = {}
        mgr.get_cleanup_days.return_value = 3
        mgr.get_verification_agent.return_value = None
        return mgr

    @patch("chad.ui.gradio.web_ui.gr")
    def test_create_interface(self, mock_gr, mock_api_client):
        """Test that create_interface creates a Gradio Blocks interface."""
        from chad.ui.gradio.web_ui import ChadWebUI

        # Mock the Gradio components
        mock_blocks = MagicMock()
        mock_gr.Blocks.return_value.__enter__ = Mock(return_value=mock_blocks)
        mock_gr.Blocks.return_value.__exit__ = Mock(return_value=None)

        web_ui = ChadWebUI(mock_api_client)
        web_ui.create_interface()

        # Verify Blocks was called
        mock_gr.Blocks.assert_called_once()


class TestLaunchWebUI:
    """Test cases for launch_web_ui function."""

    @patch("chad.ui.gradio.web_ui._resolve_port", return_value=(7860, False, False))
    @patch("chad.ui.gradio.web_ui.ChadWebUI")
    @patch("chad.ui.client.APIClient")
    def test_launch_creates_api_client(self, mock_api_client_class, mock_webui_class, mock_resolve_port):
        """Test launch_web_ui creates an API client and ChadWebUI."""
        from chad.ui.gradio.web_ui import launch_web_ui

        mock_api_client = Mock()
        mock_api_client_class.return_value = mock_api_client

        mock_server = Mock()
        mock_server.server_port = 7860
        mock_app = Mock()
        mock_app.launch.return_value = (mock_server, "http://127.0.0.1:7860", None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui(api_base_url="http://localhost:8000")

        mock_api_client_class.assert_called_once_with("http://localhost:8000")
        mock_webui_class.assert_called_once_with(mock_api_client, dev_mode=False)
        mock_app.launch.assert_called_once()
        assert result == (None, 7860)

    @patch("chad.ui.gradio.web_ui._resolve_port", return_value=(43210, True, True))
    @patch("chad.ui.gradio.web_ui.ChadWebUI")
    @patch("chad.ui.client.APIClient")
    def test_launch_falls_back_when_port_busy(self, mock_api_client_class, mock_webui_class, mock_resolve_port):
        """New launches should fall back to an ephemeral port if the default is in use."""
        from chad.ui.gradio.web_ui import launch_web_ui

        mock_api_client = Mock()
        mock_api_client_class.return_value = mock_api_client

        mock_app = Mock()
        mock_app.launch.return_value = (Mock(server_port=43210), "http://127.0.0.1:43210", None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui(api_base_url="http://localhost:8000", port=7860)

        mock_resolve_port.assert_called_once_with(7860)
        mock_app.launch.assert_called_once()
        assert result == (None, 43210)

    @patch("chad.ui.gradio.web_ui._resolve_port", return_value=(7860, False, False))
    @patch("chad.ui.gradio.web_ui.ChadWebUI")
    @patch("chad.ui.client.APIClient")
    def test_launch_dev_mode(self, mock_api_client_class, mock_webui_class, mock_resolve_port):
        """Test launch_web_ui passes dev_mode parameter."""
        from chad.ui.gradio.web_ui import launch_web_ui

        mock_api_client = Mock()
        mock_api_client_class.return_value = mock_api_client

        mock_server = Mock()
        mock_server.server_port = 7860
        mock_app = Mock()
        mock_app.launch.return_value = (mock_server, "http://127.0.0.1:7860", None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui(api_base_url="http://localhost:8000", dev_mode=True)

        mock_webui_class.assert_called_once_with(mock_api_client, dev_mode=True)
        assert result == (None, 7860)


class TestGeminiUsage:
    """Test cases for Gemini usage stats parsing."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = [MockAccount(name="gemini", provider="gemini")]
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = "default"
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance."""
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    @patch("pathlib.Path.home")
    def test_gemini_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage when not logged in."""
        mock_home.return_value = tmp_path
        (tmp_path / ".gemini").mkdir()
        # No oauth_creds.json file

        result = web_ui._get_gemini_usage()

        assert "❌" in result
        assert "Not logged in" in result

    @patch("pathlib.Path.home")
    def test_gemini_logged_in_no_sessions(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage when logged in but no session data."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')
        # No tmp directory

        result = web_ui._get_gemini_usage()

        assert "✅" in result
        assert "Logged in" in result
        assert "Usage data unavailable" in result

    @patch("pathlib.Path.home")
    def test_gemini_usage_aggregates_models(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage aggregates token counts by model."""
        import json

        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        # Create session file with model usage data
        session_dir = gemini_dir / "tmp" / "project123" / "chats"
        session_dir.mkdir(parents=True)

        session_data = {
            "sessionId": "test-session",
            "messages": [
                {"type": "gemini", "model": "gemini-2.5-pro", "tokens": {"input": 1000, "output": 100, "cached": 500}},
                {"type": "gemini", "model": "gemini-2.5-pro", "tokens": {"input": 2000, "output": 200, "cached": 1000}},
                {"type": "gemini", "model": "gemini-2.5-flash", "tokens": {"input": 500, "output": 50, "cached": 200}},
                {"type": "user", "content": "test"},  # Should be ignored
            ],
        }
        (session_dir / "session-test.json").write_text(json.dumps(session_data))

        result = web_ui._get_gemini_usage()

        assert "✅" in result
        assert "Model Usage" in result
        assert "gemini-2.5-pro" in result
        assert "gemini-2.5-flash" in result
        assert "3,000" in result  # 1000 + 2000 input for pro
        assert "300" in result  # 100 + 200 output for pro
        assert "Cache savings" in result


class TestModelSelection:
    """Test cases for model selection functionality."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = [MockAccount(name="claude", provider="anthropic", role="CODING"), MockAccount(name="gpt", provider="openai")]
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = "default"
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance."""
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    def test_set_model_success(self, web_ui, mock_api_client):
        """Test setting model successfully."""
        result = web_ui.set_model("claude", "claude-opus-4-20250514")[0]

        assert "✓" in result
        assert "claude-opus-4-20250514" in result
        mock_api_client.set_account_model.assert_called_once_with("claude", "claude-opus-4-20250514")

    def test_set_model_missing_account(self, web_ui, mock_api_client):
        """Test setting model without selecting account."""
        result = web_ui.set_model("", "some-model")[0]

        assert "❌" in result
        assert "select an account" in result

    def test_set_model_missing_model(self, web_ui, mock_api_client):
        """Test setting model without selecting model."""
        result = web_ui.set_model("claude", "")[0]

        assert "❌" in result
        assert "select a model" in result

    def test_set_model_account_not_found(self, web_ui, mock_api_client):
        """Test setting model for non-existent account."""
        result = web_ui.set_model("nonexistent", "some-model")[0]

        assert "❌" in result
        assert "not found" in result

    def test_get_models_for_anthropic(self, web_ui, mock_api_client, monkeypatch):
        """Test getting models for anthropic provider."""
        # Mock the model catalog to return expected models
        mock_api_client.get_account.return_value = MockAccount(name="claude", provider="anthropic", model="default")
        monkeypatch.setattr(
            web_ui.model_catalog, "get_models",
            lambda provider, acct=None: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "default"]
        )

        models = web_ui.get_models_for_account("claude")

        assert "claude-sonnet-4-20250514" in models
        assert "claude-opus-4-20250514" in models
        assert "default" in models

    def test_get_models_for_openai(self, web_ui, mock_api_client, monkeypatch):
        """Test getting models for openai provider."""
        mock_api_client.get_account.return_value = MockAccount(name="gpt", provider="openai", model="default")
        monkeypatch.setattr(
            web_ui.model_catalog, "get_models",
            lambda provider, acct=None: ["default"]
        )

        models = web_ui.get_models_for_account("gpt")

        # Only 'default' is guaranteed - other models come from user's config/sessions
        assert "default" in models

    def test_get_models_for_unknown_account(self, web_ui, monkeypatch):
        """Test getting models for unknown account returns default."""
        monkeypatch.setattr(
            web_ui.model_catalog, "get_models",
            lambda provider, acct=None: ["default"]
        )

        models = web_ui.get_models_for_account("unknown")

        assert models == ["default"]

    def test_get_models_for_empty_account(self, web_ui):
        """Test getting models with empty account name."""
        models = web_ui.get_models_for_account("")

        assert models == ["default"]

    def test_provider_models_constant(self, web_ui):
        """Test that PROVIDER_MODELS includes expected providers."""
        from chad.ui.gradio.web_ui import ChadWebUI

        assert "anthropic" in ChadWebUI.SUPPORTED_PROVIDERS
        assert "openai" in ChadWebUI.SUPPORTED_PROVIDERS
        assert "gemini" in ChadWebUI.SUPPORTED_PROVIDERS


class TestUILayout:
    """Test cases for UI layout and CSS."""


class TestRemainingUsage:
    """Test cases for remaining_usage calculation and sorting."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        accounts = {
            "claude": MockAccount(name="claude", provider="anthropic", role="CODING"),
            "codex": MockAccount(name="codex", provider="openai"),
            "gemini": MockAccount(name="gemini", provider="gemini"),
        }
        mgr.list_accounts.return_value = list(accounts.values())
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = "default"

        def get_account(name):
            if name in accounts:
                return accounts[name]
            raise ValueError(f"Account {name} not found")

        mgr.get_account.side_effect = get_account
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance."""
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    def test_remaining_usage_unknown_account(self, web_ui):
        """Unknown account returns 0.0."""
        result = web_ui.get_remaining_usage("nonexistent")
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_gemini_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Gemini not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".gemini").mkdir()

        result = web_ui._get_gemini_remaining_usage()
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_gemini_remaining_usage_logged_in(self, mock_home, web_ui, tmp_path):
        """Gemini logged in returns low estimate (0.3)."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        result = web_ui._get_gemini_remaining_usage()
        assert result == 0.3

    @patch("pathlib.Path.home")
    def test_mistral_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".vibe").mkdir()

        result = web_ui._get_mistral_remaining_usage()
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_mistral_remaining_usage_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral logged in returns low estimate (0.3)."""
        mock_home.return_value = tmp_path
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / "config.toml").write_text('[general]\napi_key = "test"')

        result = web_ui._get_mistral_remaining_usage()
        assert result == 0.3

    @patch("pathlib.Path.home")
    def test_claude_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Claude not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".chad" / "claude-configs" / "claude-1").mkdir(parents=True)

        result = web_ui._get_claude_remaining_usage("claude-1")
        assert result == 0.0

    @patch("pathlib.Path.home")
    @patch("requests.get")
    def test_claude_remaining_usage_from_api(self, mock_get, mock_home, web_ui, tmp_path):
        """Claude calculates remaining from API utilization."""
        import json

        mock_home.return_value = tmp_path
        claude_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        claude_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "PRO"}}
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 25}}
        mock_get.return_value = mock_response

        result = web_ui._get_claude_remaining_usage("claude-1")
        assert result == 0.75  # 1.0 - 0.25

    @patch("pathlib.Path.home")
    def test_codex_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Codex not logged in returns 0.0."""
        mock_home.return_value = tmp_path

        result = web_ui._get_codex_remaining_usage("codex")
        assert result == 0.0


class TestClaudeMultiAccount:
    """Test cases for Claude multi-account support."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = [MockAccount(name="claude-1", provider="anthropic"), MockAccount(name="claude-2", provider="anthropic")]
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = "default"
        mgr.get_account_reasoning.return_value = "default"
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.ui.gradio.web_ui import ChadWebUI

        ui = ChadWebUI(mock_api_client)
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "claude"))
        return ui

    @patch("pathlib.Path.home")
    def test_get_claude_config_dir_returns_isolated_path(self, mock_home, web_ui, tmp_path):
        """Each Claude account gets its own config directory."""
        mock_home.return_value = tmp_path

        config_dir_1 = web_ui.provider_ui._get_claude_config_dir("claude-1")
        config_dir_2 = web_ui.provider_ui._get_claude_config_dir("claude-2")

        assert str(config_dir_1) == str(tmp_path / ".chad" / "claude-configs" / "claude-1")
        assert str(config_dir_2) == str(tmp_path / ".chad" / "claude-configs" / "claude-2")
        assert config_dir_1 != config_dir_2

    @patch("pathlib.Path.home")
    def test_setup_claude_account_creates_directory(self, mock_home, web_ui, tmp_path):
        """Setup creates the isolated config directory."""
        mock_home.return_value = tmp_path

        result = web_ui.provider_ui._setup_claude_account("test-account")

        assert str(result) == str(tmp_path / ".chad" / "claude-configs" / "test-account")
        assert (tmp_path / ".chad" / "claude-configs" / "test-account").exists()

    @patch("pathlib.Path.home")
    def test_claude_usage_reads_from_isolated_config(self, mock_home, web_ui, tmp_path):
        """Claude usage reads credentials from account-specific config dir."""
        import json

        mock_home.return_value = tmp_path

        # Setup isolated config directory for claude-1
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "", "subscriptionType": "PRO"}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        result = web_ui.provider_ui._get_claude_usage("claude-1")

        # Should report not logged in due to empty access token
        assert "Not logged in" in result

    @patch("pathlib.Path.home")
    @patch("requests.get")
    def test_claude_usage_with_valid_credentials(self, mock_get, mock_home, web_ui, tmp_path):
        """Claude usage fetches data when credentials are valid."""
        import json

        mock_home.return_value = tmp_path

        # Setup isolated config directory with valid credentials
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "PRO"}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 25}}
        mock_get.return_value = mock_response

        result = web_ui.provider_ui._get_claude_usage("claude-1")

        assert "Logged in" in result
        assert "PRO" in result

    @patch("pathlib.Path.home")
    def test_check_provider_login_uses_isolated_config(self, mock_home, web_ui, tmp_path):
        """Provider login check uses account-specific config directory."""
        import json

        mock_home.return_value = tmp_path

        # claude-1 has credentials, claude-2 does not
        config_dir_1 = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir_1.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token"}}
        (config_dir_1 / ".credentials.json").write_text(json.dumps(creds))

        # claude-2 has no credentials
        config_dir_2 = tmp_path / ".chad" / "claude-configs" / "claude-2"
        config_dir_2.mkdir(parents=True)

        logged_in_1, _ = web_ui.provider_ui._check_provider_login("anthropic", "claude-1")
        logged_in_2, _ = web_ui.provider_ui._check_provider_login("anthropic", "claude-2")

        assert logged_in_1 is True
        assert logged_in_2 is False

    def test_delete_provider_cleans_up_claude_config(self, mock_api_client, tmp_path):
        """Deleting Claude provider removes its config directory."""
        from chad.ui.gradio.web_ui import ChadWebUI

        # Setup mock to return the correct provider
        mock_api_client.get_account.return_value = MockAccount(name="claude-1", provider="anthropic")

        # Setup config directory for claude-1 before creating UI
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        (config_dir / ".credentials.json").write_text("{}")

        with patch("chad.ui.gradio.provider_ui.safe_home", return_value=tmp_path):
            ui = ChadWebUI(mock_api_client)
            ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "claude"))

            # Delete the provider
            ui.provider_ui.delete_provider("claude-1", confirmed=True, card_slots=4)

        # Config directory should be removed
        assert not config_dir.exists()

    @patch("pathlib.Path.home")
    def test_add_provider_claude_login_timeout(self, mock_home, web_ui, mock_api_client, tmp_path):
        """Adding Claude provider times out if OAuth not completed."""
        mock_home.return_value = tmp_path
        mock_api_client.list_accounts.return_value = []

        # Mock pexpect to simulate timeout (no credentials created)
        mock_child = Mock()
        mock_child.expect = Mock(return_value=0)
        mock_child.send = Mock()
        mock_child.close = Mock()

        if os.name == "nt":
            mock_process = Mock()
            mock_process.terminate = Mock()
            with patch("subprocess.Popen", return_value=mock_process):
                # Patch time to simulate timeout quickly
                with patch("time.time", side_effect=[0, 0, 200]):  # Instant timeout
                    with patch("time.sleep"):
                        result = web_ui.add_provider("my-claude", "anthropic")[0]
        else:
            with patch("pexpect.spawn", return_value=mock_child):
                # Patch time to simulate timeout quickly
                with patch("time.time", side_effect=[0, 0, 200]):  # Instant timeout
                    with patch("time.sleep"):
                        result = web_ui.add_provider("my-claude", "anthropic")[0]

        # Provider should NOT be stored (login timed out)
        mock_api_client.create_account.assert_not_called()

        # Should show timeout error
        assert "❌" in result
        assert "timed out" in result.lower()

        # Config directory should be cleaned up
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        assert not config_dir.exists()

    @patch("pathlib.Path.home")
    def test_add_provider_claude_login_success(self, mock_home, web_ui, mock_api_client, tmp_path):
        """Adding Claude provider succeeds when OAuth completes."""
        import json

        mock_home.return_value = tmp_path
        mock_api_client.list_accounts.return_value = []

        # Create the config directory and credentials file to simulate successful OAuth
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        config_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "pro"}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock pexpect - credentials already exist so pexpect won't be called
        # (the login check passes before pexpect is used)
        result = web_ui.add_provider("my-claude", "anthropic")[0]

        # Provider should be stored
        mock_api_client.create_account.assert_called_once_with("my-claude", "anthropic")

        # Should show success
        assert "✅" in result
        assert "logged in" in result.lower()

    @patch("pathlib.Path.home")
    @patch("requests.get")
    def test_add_provider_claude_already_logged_in(self, mock_get, mock_home, web_ui, mock_api_client, tmp_path):
        """Adding Claude provider when already logged in shows success."""
        import json

        mock_home.return_value = tmp_path
        mock_api_client.list_accounts.return_value = []

        # Pre-create credentials file (user already logged in)
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        config_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "pro"}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock successful API call
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 25}}
        mock_get.return_value = mock_response

        result = web_ui.add_provider("my-claude", "anthropic")[0]

        # Should show logged in status
        assert "✅" in result
        assert "logged in" in result.lower()
        mock_api_client.create_account.assert_called_once()


class TestCodingSummaryExtraction:
    """Test extraction of structured summaries from coding agent output."""

    def test_extract_coding_summary_from_json_block(self):
        """Extract summary from a ```json code block."""
        from chad.util.prompts import extract_coding_summary

        content = """Some thinking text here...

```json
{"change_summary": "Fixed the authentication bug in login flow"}
```
"""
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Fixed the authentication bug in login flow"

    def test_extract_coding_summary_from_raw_json(self):
        """Extract summary from raw JSON without code block."""
        from chad.util.prompts import extract_coding_summary

        content = 'Done! {"change_summary": "Added new feature"}'
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Added new feature"

    def test_extract_coding_summary_with_all_fields(self):
        """Extract summary with hypothesis and screenshots."""
        from chad.util.prompts import extract_coding_summary

        content = '''Done with the fix.

```json
{
  "change_summary": "Fixed login bug",
  "hypothesis": "Race condition in token refresh",
  "before_screenshot": "/tmp/before.png",
  "after_screenshot": "/tmp/after.png"
}
```
'''
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Fixed login bug"
        assert result.hypothesis == "Race condition in token refresh"
        assert result.before_screenshot == "/tmp/before.png"
        assert result.after_screenshot == "/tmp/after.png"

    def test_extract_coding_summary_partial_fields(self):
        """Extract summary with only some optional fields."""
        from chad.util.prompts import extract_coding_summary

        content = '''```json
{"change_summary": "Added feature", "hypothesis": "User needs this"}
```'''
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Added feature"
        assert result.hypothesis == "User needs this"
        assert result.before_screenshot is None
        assert result.after_screenshot is None

    def test_extract_coding_summary_returns_none_when_missing(self):
        """Return None when no change_summary found."""
        from chad.util.prompts import extract_coding_summary

        content = "Just some regular text without any JSON."
        result = extract_coding_summary(content)
        assert result is None

    def test_make_chat_message_uses_extracted_summary(self):
        """make_chat_message should prefer extracted JSON summary over heuristics."""
        from chad.ui.gradio.web_ui import make_chat_message

        # Content needs to be > 300 chars to trigger collapsible mode
        content = """I'm thinking about this task...

I'll also check that things are working correctly in the codebase.

More random text that shouldn't be the summary. This needs to be long enough
to trigger the collapsible mode which requires more than 300 characters total.
Here's some more filler text to ensure we hit that threshold.

```json
{"change_summary": "Updated the config parser to handle edge cases"}
```
"""
        message = make_chat_message("CODING AI", content)
        # The summary should be the JSON-extracted one
        summary_part = message["content"].split("<details>")[0]
        assert "Updated the config parser to handle edge cases" in summary_part
        # The heuristic match should NOT be in the summary part
        assert "I'll also check" not in summary_part

    def test_make_chat_message_falls_back_to_heuristic(self):
        """make_chat_message should use heuristics when no JSON summary."""
        from chad.ui.gradio.web_ui import make_chat_message

        content = (
            """Some thinking text...

I've updated the authentication module to fix the login issue.

More details here...
"""
            + "x" * 300
        )  # Make it long enough to trigger collapsible
        message = make_chat_message("CODING AI", content)
        # Should use heuristic extraction (starts with "I've updated...")
        assert "I've updated the authentication module" in message["content"]

    def test_make_chat_message_displays_hypothesis_and_screenshots(self, tmp_path):
        """make_chat_message should show hypothesis and inline screenshot images."""
        from chad.ui.gradio.web_ui import make_chat_message

        # Create minimal PNG files for testing
        before_png = tmp_path / "before.png"
        after_png = tmp_path / "after.png"
        # Minimal valid PNG (1x1 transparent pixel)
        png_bytes = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1 dimensions
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89,  # bit depth, color type, CRC
            0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41, 0x54,  # IDAT chunk
            0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01,  # compressed data
            0x0D, 0x0A, 0x2D, 0xB4,  # CRC
            0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44,  # IEND chunk
            0xAE, 0x42, 0x60, 0x82  # CRC
        ])
        before_png.write_bytes(png_bytes)
        after_png.write_bytes(png_bytes)

        content = '''Working on the task...
''' + "x" * 300 + f'''
```json
{{
  "change_summary": "Fixed the display issue",
  "hypothesis": "CSS z-index conflict",
  "before_screenshot": "{before_png}",
  "after_screenshot": "{after_png}"
}}
```
'''
        message = make_chat_message("CODING AI", content)
        content_str = message["content"]
        summary_part = content_str.split("<details>")[0]
        assert "Fixed the display issue" in summary_part
        assert "Hypothesis:" in summary_part
        assert "CSS z-index conflict" in summary_part
        # Screenshots are now inline images, not links
        assert "screenshot-comparison" in summary_part
        assert "screenshot-label" in summary_part
        assert "Before" in summary_part
        assert "After" in summary_part
        assert "data:image/png;base64," in summary_part


class TestVerificationPrompt:
    """Ensure verification prompts include task context and summaries."""

    def test_get_verification_prompt_includes_task_and_summary(self):
        """Task and change summary should be prefixed for the verifier."""
        from chad.util.prompts import get_verification_prompt

        prompt = get_verification_prompt("Full response content", "Do the thing", "Did the thing")
        assert "Do the thing" in prompt
        assert "Summary from coding agent: Did the thing" in prompt
        assert "Full response:" in prompt
        assert "Full response content" in prompt

    def test_truncation_keeps_indicator_and_fits_limit(self):
        """Verification payloads should be compact and annotated when truncated."""
        from chad.ui.gradio.web_ui import _truncate_verification_output, MAX_VERIFICATION_PROMPT_CHARS

        long_text = "a" * (MAX_VERIFICATION_PROMPT_CHARS + 500)
        truncated = _truncate_verification_output(long_text)
        assert "[truncated" in truncated
        assert len(truncated) <= MAX_VERIFICATION_PROMPT_CHARS + 20

    def test_run_verification_aborts_without_required_inputs(self, monkeypatch, tmp_path):
        """Verification should abort before contacting providers when inputs are missing."""
        from chad.ui.gradio.web_ui import ChadWebUI

        class DummyAPIClient:
            def list_accounts(self):
                return [MockAccount(name="verifier", provider="anthropic")]

            def get_account(self, name):
                return MockAccount(name=name, provider="anthropic")

            def get_verification_agent(self):
                return None

        # Fail fast if provider creation is attempted
        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("create_provider should not be called when inputs are missing")

        monkeypatch.setattr("chad.ui.gradio.web_ui.create_provider", _fail_if_called)

        web_ui = ChadWebUI(DummyAPIClient())

        verified, feedback = web_ui._run_verification(
            str(tmp_path), "output present", "", "verifier"
        )
        assert verified is None
        assert "missing task description" in feedback

        verified, feedback = web_ui._run_verification(
            str(tmp_path), "", "Task here", "verifier"
        )
        assert verified is None
        assert "coding agent output was empty" in feedback

    def test_run_verification_returns_rich_feedback(self, monkeypatch, tmp_path):
        """Verification failures should include lint details (tests no longer run)."""
        from chad.ui.gradio.web_ui import ChadWebUI
        import chad.ui.gradio.web_ui as web_ui
        import chad.ui.gradio.verification.tools as verification_tools

        class DummyAPIClient:
            def list_accounts(self):
                # Use anthropic (non-mock) to trigger automated verification
                return [MockAccount(name="verifier", provider="anthropic")]

            def get_account(self, name):
                return MockAccount(name=name, provider="anthropic")

            def get_verification_agent(self):
                return None

        # Patch verification tool to avoid running real lint
        # Note: verification now only runs lint (lint_only=True)
        def fake_verify(project_root=None, lint_only=False):
            return {
                "success": False,
                "message": "Lint failed",
                "phases": {
                    "lint": {"success": False, "issues": ["E123 line 5: bad import"]},
                },
            }

        class DummyVerifier:
            def set_activity_callback(self, _callback):
                return None

            def start_session(self, _project_path, _system_prompt):
                return True

            def send_message(self, _message):
                return None

            def get_response(self, timeout=None):
                return '```json\n{"passed": true, "summary": "Looks good"}\n```'

            def stop_session(self):
                return None

        monkeypatch.setattr(web_ui, "create_provider", lambda *_args, **_kwargs: DummyVerifier())
        monkeypatch.setattr(web_ui, "check_verification_mentioned", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(verification_tools, "verify", fake_verify)

        web_ui = ChadWebUI(DummyAPIClient())
        verified, feedback = web_ui._run_verification(str(tmp_path), "output", "Task", "verifier")

        assert verified is False
        assert "Verification failed" in feedback
        assert "Flake8 errors" in feedback
        assert "E123 line 5" in feedback


class TestAnsiToHtml:
    """Test that ANSI escape codes are properly converted to HTML spans."""

    def test_converts_basic_color_codes_to_html(self):
        """Basic SGR color codes should be converted to HTML spans."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Purple/magenta color code
        text = "\x1b[35mPurple text\x1b[0m"
        result = ansi_to_html(text)
        assert '<span style="color:rgb(' in result
        assert "Purple text" in result
        assert "</span>" in result
        assert "\x1b" not in result

    def test_converts_256_color_codes(self):
        """256-color codes should be converted to HTML spans."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # 256-color purple
        text = "\x1b[38;5;141mColored\x1b[0m"
        result = ansi_to_html(text)
        assert "Colored" in result
        assert '<span style="color:rgb(' in result

    def test_converts_rgb_color_codes(self):
        """RGB true-color codes should be converted to HTML spans."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # RGB purple
        text = "\x1b[38;2;198;120;221mRGB color\x1b[0m"
        result = ansi_to_html(text)
        assert "RGB color" in result
        assert '<span style="color:rgb(' in result

    def test_strips_cursor_codes(self):
        """Cursor control sequences with ? should be stripped."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Show/hide cursor - these use different ending chars, should be skipped
        text = "\x1b[?25hVisible\x1b[?25l"
        result = ansi_to_html(text)
        assert "Visible" in result

    def test_strips_osc_sequences(self):
        """OSC sequences (like terminal title) should be stripped."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Set terminal title - uses different format, should be skipped
        text = "\x1b]0;My Title\x07Content here"
        result = ansi_to_html(text)
        assert "Content here" in result

    def test_preserves_newlines(self):
        """Newlines should be preserved."""
        from chad.ui.gradio.web_ui import ansi_to_html

        text = "Line 1\n\nLine 3"
        result = ansi_to_html(text)
        assert result == "Line 1\n\nLine 3"

    def test_escapes_html_entities(self):
        """HTML entities should be escaped."""
        from chad.ui.gradio.web_ui import ansi_to_html

        text = "<script>alert('xss')</script>"
        result = ansi_to_html(text)
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_converts_unclosed_color_codes(self):
        """Unclosed color codes should generate HTML span that closes at end."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Color without reset
        text = "\x1b[35mPurple start\n\nText after blank line"
        result = ansi_to_html(text)
        assert '<span style="color:rgb(' in result
        assert "Purple start" in result
        assert "Text after blank line" in result
        # Span should be auto-closed at end
        assert result.endswith("</span>")
        assert "\x1b" not in result

    def test_handles_stray_escape_characters(self):
        """Stray escape characters in non-m sequences should be handled."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Stray escape that doesn't match known patterns - skipped
        text = "Before\x1b[999zAfter"
        result = ansi_to_html(text)
        # The content before and after should be present
        assert "Before" in result
        assert "After" in result

    def test_strips_background_colors(self):
        """Background colors (40-47) should be stripped to prevent white-on-dark issues."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # White background (47) - would make text unreadable on dark theme
        text = "\x1b[47mWhite bg text\x1b[0m"
        result = ansi_to_html(text)
        assert "White bg text" in result
        assert "background" not in result
        assert "\x1b" not in result

        # Extended background (48;5;N) should also be stripped
        text = "\x1b[48;5;231mBright bg\x1b[0m"
        result = ansi_to_html(text)
        assert "Bright bg" in result
        assert "background" not in result

        # Extended RGB background (48;2;R;G;B) should also be stripped
        text = "\x1b[48;2;255;255;255mRGB white bg\x1b[0m"
        result = ansi_to_html(text)
        assert "RGB white bg" in result
        assert "background" not in result


class TestBuildInlineLiveHtml:
    """Tests for build_inline_live_html function - critical for live view streaming."""

    def test_empty_content_creates_container_with_live_id(self):
        """Empty content must still create a container with data-live-id.

        This is critical: without the data-live-id container, JS patching
        can't find the element to update, and live view never works.
        This was the root cause of the live view regression.
        """
        from chad.ui.gradio.web_ui import build_inline_live_html

        result = build_inline_live_html("", "CODING AI", live_id="test-123")

        # Must contain data-live-id for JS to find and patch
        assert 'data-live-id="test-123"' in result
        # Must have the live content container
        assert 'class="inline-live-content"' in result
        # Must have the header
        assert 'inline-live-header' in result
        assert "CODING AI (Live)" in result
        # Should show working placeholder
        assert "Working" in result

    def test_content_creates_container_with_live_id(self):
        """Content should create a container with data-live-id."""
        from chad.ui.gradio.web_ui import build_inline_live_html

        result = build_inline_live_html("Test output", "CODING AI", live_id="abc-456")

        assert 'data-live-id="abc-456"' in result
        assert 'class="inline-live-content"' in result
        assert "Test output" in result

    def test_no_live_id_still_creates_container(self):
        """Without live_id, container should still be created (just without data attribute)."""
        from chad.ui.gradio.web_ui import build_inline_live_html

        result = build_inline_live_html("Some output", "CODING AI", live_id=None)

        assert 'class="inline-live-content"' in result
        assert "data-live-id" not in result
        assert "Some output" in result

    def test_empty_content_without_live_id_still_creates_structure(self):
        """Even empty content without live_id should create proper HTML structure."""
        from chad.ui.gradio.web_ui import build_inline_live_html

        result = build_inline_live_html("", "TEST AI", live_id=None)

        assert 'class="inline-live-content"' in result
        assert 'inline-live-header' in result
        assert "TEST AI (Live)" in result
        assert "Working" in result

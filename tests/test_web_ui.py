"""Tests for web UI module."""

import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import ANY, Mock, patch, MagicMock
import pytest

import pexpect

from chad.util.git_worktree import GitWorktreeManager
from chad.util.providers import ModelConfig, MockProvider
from chad.ui.gradio.provider_ui import ProviderUIManager


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
        client.get_milestones.return_value = []
        client.get_preferences.return_value = Mock(last_project_path=None, dark_mode=True, ui_mode="gradio")
        client.get_cleanup_settings.return_value = Mock(retention_days=7, auto_cleanup=True)
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client, monkeypatch, tmp_path):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.ui.gradio.web_ui import ChadWebUI

        monkeypatch.setenv("CHAD_CONFIG", str(tmp_path / "test_chad.conf"))
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

    def test_build_coding_dropdown_state_uses_selected_provider_models(self, web_ui, mock_api_client, monkeypatch):
        """Coding model dropdown must never keep a model outside the selected provider catalog."""
        mock_account = MockAccount(
            name="testy",
            provider="mock",
            model="gemini-2.5-flash",
            reasoning="default",
            role="CODING",
        )
        mock_api_client.list_accounts.return_value = [mock_account]
        mock_api_client.get_account.side_effect = lambda name: mock_account if name == "testy" else None
        monkeypatch.setattr(web_ui.model_catalog, "get_models", lambda provider, acct=None: ["default"])

        state = web_ui._build_coding_dropdown_state("testy", accounts=[mock_account])

        assert state.model_choices == ["default"]
        assert state.model_value == "default"
        assert state.interactive is True

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
        provider._get_remaining_usage = lambda: 0.5
        provider._decrement_usage = lambda amount=None: None
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

    def test_followup_reconnects_when_provider_released(self, web_ui, git_repo, monkeypatch):
        """Follow-up should reconnect to provider after API-based execution releases it."""
        session_id = "reconnect-test"
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit

        # Simulate state after API-based execution:
        # - session.active = True (task succeeded)
        # - session.provider = None (released after API execution)
        # - session.coding_account set to the account name
        session.active = True
        session.provider = None  # This is the key condition we're testing
        session.coding_account = "mock-claude"
        session.config = ModelConfig(provider="mock", model_name="default", account_name="mock-claude")
        session.chat_history = [{"role": "user", "content": "**Task**\n\nInitial task"}]

        # Set up mock account with "mock" provider so create_provider uses MockProvider
        mock_account = MockAccount(name="mock-claude", provider="mock", role="CODING")
        web_ui.api_client.list_accounts.return_value = [mock_account]
        web_ui.api_client.get_account.return_value = mock_account

        # Speed up the mock provider
        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)

        # Send a follow-up; this should reconnect and succeed, not show "Session expired"
        list(
            web_ui.send_followup(
                session_id,
                "Follow-up after API execution",
                session.chat_history,
                coding_agent="mock-claude",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        # Check that no "Session expired" message was added
        all_messages = [msg.get("content", "") for msg in session.chat_history]
        assert not any("Session expired" in msg for msg in all_messages), (
            "Follow-up should reconnect, not show 'Session expired'"
        )

        # Check that the follow-up was processed (BUGS.md should exist from mock provider)
        assert session.worktree_path is not None
        bugs_file = session.worktree_path / "BUGS.md"
        assert bugs_file.exists(), "BUGS.md should be created by follow-up"

    def test_followup_restarts_session_after_completion(self, web_ui, git_repo, monkeypatch):
        """Follow-up after session completion should restart provider, not show 'Session expired'."""
        session_id = "restart-test"
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit

        # Simulate state after task completion / rate limit:
        # - session.active = False (task ended)
        # - session.provider = None (CLI exited)
        # - session.coding_account set from previous task
        session.active = False
        session.provider = None
        session.coding_account = "mock-claude"
        session.config = None  # Cleared when session is inactive
        session.chat_history = [{"role": "user", "content": "**Task**\n\nInitial task"}]

        mock_account = MockAccount(name="mock-claude", provider="mock", role="CODING")
        web_ui.api_client.list_accounts.return_value = [mock_account]
        web_ui.api_client.get_account.return_value = mock_account

        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)

        list(
            web_ui.send_followup(
                session_id,
                "Continue after rate limit",
                session.chat_history,
                coding_agent="mock-claude",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        all_messages = [msg.get("content", "") for msg in session.chat_history]
        assert not any("Session expired" in msg for msg in all_messages), (
            "Follow-up after completion should restart, not show 'Session expired'"
        )
        assert session.active or session.provider is not None, (
            "Session should be reactivated after restart"
        )

    def test_followup_account_fallback_from_session(self, web_ui, git_repo, monkeypatch):
        """When no agent is explicitly selected, reuse the session's previous coding_account."""
        session_id = "fallback-test"
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit

        session.active = False
        session.provider = None
        session.coding_account = "mock-claude"
        session.config = None
        session.chat_history = [{"role": "user", "content": "**Task**\n\nInitial task"}]

        mock_account = MockAccount(name="mock-claude", provider="mock", role="CODING")
        web_ui.api_client.list_accounts.return_value = [mock_account]
        web_ui.api_client.get_account.return_value = mock_account

        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)

        # Send follow-up with NO coding_agent (empty string / None) — should fall back
        list(
            web_ui.send_followup(
                session_id,
                "Continue with previous agent",
                session.chat_history,
                coding_agent="",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        all_messages = [msg.get("content", "") for msg in session.chat_history]
        assert not any("Session expired" in msg for msg in all_messages), (
            "Should fall back to session's coding_account, not expire"
        )

    def test_followup_no_account_shows_expired(self, web_ui, git_repo):
        """When no account is configured and no previous account exists, show 'Session expired'."""
        session_id = "no-account-test"

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.active = False
        session.provider = None
        session.coding_account = None
        session.config = None
        session.chat_history = []

        web_ui.api_client.list_accounts.return_value = []

        list(
            web_ui.send_followup(
                session_id,
                "Try to continue",
                session.chat_history,
                coding_agent="",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        all_messages = [msg.get("content", "") for msg in session.chat_history]
        assert any("Session expired" in msg for msg in all_messages), (
            "Should show 'Session expired' when no account is available"
        )

    def test_followup_provider_change_after_completion(self, web_ui, git_repo, monkeypatch):
        """Switching provider on follow-up after completion should show handoff message."""
        session_id = "switch-test"
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit

        session.active = False
        session.provider = None
        session.coding_account = "old-agent"
        session.config = None
        session.chat_history = [{"role": "user", "content": "**Task**\n\nInitial task"}]

        mock_old = MockAccount(name="old-agent", provider="mock", role="CODING")
        mock_new = MockAccount(name="new-agent", provider="mock", role="CODING")
        web_ui.api_client.list_accounts.return_value = [mock_old, mock_new]
        web_ui.api_client.get_account.return_value = mock_new

        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)

        list(
            web_ui.send_followup(
                session_id,
                "Continue with different agent",
                session.chat_history,
                coding_agent="new-agent",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        all_messages = [msg.get("content", "") for msg in session.chat_history]
        assert not any("Session expired" in msg for msg in all_messages), (
            "Should not show 'Session expired' when switching provider"
        )
        assert any("PROVIDER HANDOFF" in msg for msg in all_messages), (
            "Should show handoff message when switching provider after completion"
        )

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

    @patch("pexpect.spawn")
    def test_add_provider_mistral_runs_setup_before_account_creation(self, mock_spawn, web_ui, mock_api_client):
        """Mistral add flow should run setup via PTY and then create the account."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/vibe"))

        child = MagicMock()
        child.isalive.side_effect = [True, False]
        child.read_nonblocking.side_effect = pexpect.TIMEOUT("timeout")
        mock_spawn.return_value = child

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(
                web_ui.provider_ui,
                "_check_provider_login",
                side_effect=[(False, "Not logged in"), (True, "Logged in")],
            ),
        ):
            result = web_ui.add_provider("mistral-1", "mistral")[0]

        assert "✅" in result
        assert "mistral-1" in result
        mock_spawn.assert_called_once_with(
            "/tmp/vibe",
            ["--setup"],
            timeout=ANY,
            encoding="utf-8",
            env=ANY,
            dimensions=(50, 120),
            cwd=ANY,
        )
        mock_api_client.create_account.assert_called_once_with("mistral-1", "mistral")

    @patch("pexpect.spawn")
    def test_add_provider_mistral_setup_failure_blocks_account_creation(self, mock_spawn, web_ui, mock_api_client):
        """Mistral add flow should fail cleanly when setup does not authenticate."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/vibe"))

        child = MagicMock()
        child.isalive.return_value = False
        child.read_nonblocking.side_effect = pexpect.TIMEOUT("timeout")
        mock_spawn.return_value = child

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(
                web_ui.provider_ui,
                "_check_provider_login",
                side_effect=[(False, "Not logged in"), (False, "Not logged in")],
            ),
        ):
            result = web_ui.add_provider("mistral-1", "mistral")[0]

        assert "❌" in result
        assert "Login failed" in result
        mock_spawn.assert_called_once()
        mock_api_client.create_account.assert_not_called()

    @patch("chad.ui.gradio.provider_ui.safe_home")
    @patch("pexpect.spawn")
    def test_add_provider_mistral_trust_prompt_is_auto_answered(self, mock_spawn, mock_safe_home, web_ui, mock_api_client, tmp_path):
        """Trust prompts during setup should be acknowledged automatically so OAuth can continue."""

        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/vibe"))
        mock_safe_home.return_value = tmp_path

        # Simulate a trust prompt followed by process exit
        class DummyChild:
            def __init__(self):
                self.sent = []
                self._alive = True
                self._chunks = [
                    "Do you trust the folder /home/miles/chad? [y/N]",
                    "Redirecting to browser...",
                ]
                self._idx = 0

            def isalive(self):
                return self._alive

            def read_nonblocking(self, size=1, timeout=0.1):
                if self._idx < len(self._chunks):
                    chunk = self._chunks[self._idx]
                    self._idx += 1
                    if self._idx >= len(self._chunks):
                        # Next iteration will see EOF
                        self._alive = False
                    return chunk
                raise pexpect.EOF("done")

            def sendline(self, data):
                self.sent.append(data)

            def send(self, data):
                self.sent.append(data)

            def close(self, force=False):
                self._alive = False

        dummy_child = DummyChild()
        mock_spawn.return_value = dummy_child

        vibe_config = tmp_path / ".vibe" / "config.toml"
        vibe_config.parent.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(web_ui.provider_ui, "_is_windows", return_value=False),
            patch.object(
                web_ui.provider_ui,
                "_check_provider_login",
                side_effect=[(False, "Not logged in"), (True, "Logged in")],
            ),
        ):
            result = web_ui.add_provider("mistral-1", "mistral")[0]

        assert "✅" in result
        assert any(ans.strip().lower() == "y" for ans in dummy_child.sent)
        mock_api_client.create_account.assert_called_once_with("mistral-1", "mistral")

    def test_add_provider_kimi_login_flow_failure(self, web_ui, mock_api_client):
        """Kimi login flow shows error on failure (no creds written)."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/kimi"))
        web_ui.provider_ui.installer.find_tool_path = Mock(return_value="/tmp/kimi")

        # Mock subprocess.Popen to simulate a failed login (no success event)
        mock_proc = Mock()
        mock_proc.stdout.readline.return_value = b""
        mock_proc.poll.return_value = 0  # Process exited immediately
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock()

        import subprocess as _subprocess
        with patch.object(_subprocess, "Popen", return_value=mock_proc):
            with patch("chad.ui.gradio.provider_ui.shutil.which", return_value="/tmp/kimi"):
                result = web_ui.add_provider("kimi-1", "kimi")[0]

        assert "❌" in result
        mock_api_client.create_account.assert_not_called()

    def test_add_provider_kimi_missing_cli_suggests_pip_install(self, web_ui, mock_api_client):
        """Kimi missing CLI message should match the managed pip install path."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/kimi"))
        web_ui.provider_ui.installer.resolve_tool_path = Mock(return_value=None)

        with patch("chad.ui.gradio.provider_ui.shutil.which", return_value=None):
            result = web_ui.add_provider("kimi-1", "kimi")[0]

        assert "❌" in result
        assert "pip install kimi-cli" in result
        mock_api_client.create_account.assert_not_called()

    def test_add_provider_kimi_creds_saved_but_error_event(self, web_ui, mock_api_client, tmp_path):
        """Kimi login should succeed when CLI saves creds but emits error (model listing fails)."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/kimi"))
        web_ui.provider_ui.installer.find_tool_path = Mock(return_value="/tmp/kimi")

        # The CLI emits: verification_url → waiting → error (model listing 401)
        # BUT it saves credentials before trying to list models.
        events = [
            b'{"type": "verification_url", "data": {"verification_url": "https://example.com"}}\n',
            b'{"type": "error", "message": "Failed to get models: 401"}\n',
        ]
        event_iter = iter(events)

        mock_proc = Mock()
        mock_proc.stdout.readline.side_effect = lambda: next(event_iter, b"")
        mock_proc.poll.side_effect = [None, None, 0]
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock()

        def fake_popen(cmd, **kwargs):
            # Simulate the CLI writing creds (it saves before model listing)
            kimi_home = Path(kwargs["env"]["HOME"])
            creds_dir = kimi_home / ".kimi" / "credentials"
            creds_dir.mkdir(parents=True, exist_ok=True)
            (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
            return mock_proc

        import subprocess as _subprocess
        with patch.object(_subprocess, "Popen", side_effect=fake_popen):
            with patch("chad.ui.gradio.provider_ui.shutil.which", return_value="/tmp/kimi"):
                with patch("chad.ui.gradio.provider_ui.safe_home", return_value=str(tmp_path)):
                    result = web_ui.add_provider("kimi-1", "kimi")[0]

        assert "✅" in result
        mock_api_client.create_account.assert_called_once()
        # Config should be written by Chad since CLI didn't write it
        config_file = tmp_path / ".chad" / "kimi-homes" / "kimi-1" / ".kimi" / "config.toml"
        assert config_file.exists()
        assert "[models." in config_file.read_text()

    def test_get_models_includes_stored_model(self, web_ui, mock_api_client, tmp_path):
        """Stored models should always be present in dropdown choices."""
        mock_api_client.list_accounts.return_value = [MockAccount(name="gpt", provider="openai")]
        mock_api_client.get_account_model.return_value = "gpt-5.1-codex-max"
        from chad.util.model_catalog import ModelCatalog

        web_ui.model_catalog = ModelCatalog(api_client=mock_api_client, home_dir=tmp_path, cache_ttl=0)
        models = web_ui.get_models_for_account("gpt")

        assert "gpt-5.1-codex-max" in models
        assert "default" in models

    def test_get_models_for_mock_excludes_foreign_stored_model(self, web_ui, mock_api_client, tmp_path):
        """Mock coding agent should not expose models from other providers."""
        mock_account = MockAccount(
            name="testy",
            provider="mock",
            role="CODING",
            model="claude-sonnet-4-20250514",
        )
        mock_api_client.list_accounts.return_value = [mock_account]
        mock_api_client.get_account.side_effect = lambda _: mock_account
        mock_api_client.get_account_model.return_value = "claude-sonnet-4-20250514"

        from chad.util.model_catalog import ModelCatalog

        web_ui.model_catalog = ModelCatalog(api_client=mock_api_client, home_dir=tmp_path, cache_ttl=0)
        models = web_ui.get_models_for_account("testy")

        assert models == ["default"]

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

        # Result is a tuple of gr.update dicts
        # Order: live_stream, chatbot, task_status, project_path, task_description,
        #        start_btn, cancel_btn, followup_row, merge_section_group
        assert isinstance(result, tuple)
        live_stream_update = result[0]
        assert "🛑" in live_stream_update.get("value", "")
        assert "cancelled" in live_stream_update.get("value", "").lower()
        assert session.cancel_requested is True
        mock_provider.stop_session.assert_called_once()
        # Verify button states - start should be enabled, cancel should be disabled
        start_btn_update = result[5]
        cancel_btn_update = result[6]
        assert start_btn_update.get("interactive") is True, "Start button should be enabled after cancel"
        assert cancel_btn_update.get("interactive") is False, "Cancel button should be disabled after cancel"

    def test_cancel_task_no_session(self, web_ui, mock_api_client):
        """Test cancelling when no session is running."""
        session = web_ui.create_session("test")
        result = web_ui.cancel_task(session.id)

        # Result is a tuple of gr.update dicts - live_stream is at index 0
        assert isinstance(result, tuple)
        live_stream_update = result[0]
        assert "🛑" in live_stream_update.get("value", "")
        assert session.cancel_requested is True

    def test_start_task_rejects_when_server_session_is_still_active(self, web_ui, git_repo):
        """Starting a new task with an active server task should return one full Gradio update tuple."""
        session = web_ui.create_session("test")
        session.server_session_id = "server-session-1"
        web_ui.api_client.get_session.return_value = Mock(active=True)

        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        assert len(updates) == 1
        update = updates[0]
        # 22 elements: 18 base + 4 prompt outputs (coding + verification)
        assert len(update) == 22
        assert "already running" in update[2].get("value", "").lower()
        assert update[5].get("interactive") is True
        assert update[6].get("interactive") is False

    def test_start_task_after_cancel_requests_server_cancel_and_continues(self, web_ui, git_repo, monkeypatch):
        """Restart right after cancel should request server cancel and continue once inactive."""
        session = web_ui.create_session("test")
        session.server_session_id = "server-session-1"
        session.cancel_requested = True

        calls = {"count": 0}

        def get_session_side_effect(_session_id):
            calls["count"] += 1
            if calls["count"] == 1:
                return Mock(active=True)
            return Mock(active=False)

        web_ui.api_client.get_session.side_effect = get_session_side_effect

        started = {"value": False}

        def fake_run_task_via_api(
            session_id,
            project_path,
            task_description,
            coding_account,
            message_queue,
            **kwargs,
        ):
            started["value"] = True
            message_queue.put(("message_complete", "CODING AI", "done"))
            return True, "done", "server-session-2", {
                "files_modified": [],
                "files_created": [],
                "commands_run": [],
                "total_tool_calls": 0,
            }

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        updates = list(
            web_ui.start_chad_task(
                session.id,
                str(git_repo),
                "do something",
                "claude",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        assert started["value"] is True
        web_ui.api_client.cancel_session.assert_called_once_with("server-session-1")
        assert all("already running" not in (u[2].get("value", "").lower()) for u in updates)

    def test_start_task_after_cancel_detaches_stale_server_session_when_cancel_wait_fails(
        self,
        web_ui,
        git_repo,
        monkeypatch,
    ):
        """Restart after cancel should continue even if stale session still reports active."""
        session = web_ui.create_session("test")
        session.server_session_id = "server-session-1"
        session.cancel_requested = True
        web_ui.api_client.get_session.return_value = Mock(active=True)

        cancel_attempts = {"count": 0}

        def fake_request_server_cancel(_server_session_id, timeout_seconds=3.0):  # noqa: ARG001
            cancel_attempts["count"] += 1
            return False

        monkeypatch.setattr(web_ui, "_request_server_cancel", fake_request_server_cancel)

        started = {"value": False}

        def fake_run_task_via_api(
            session_id,
            project_path,
            task_description,
            coding_account,
            message_queue,
            **kwargs,
        ):
            started["value"] = True
            message_queue.put(("message_complete", "CODING AI", "done"))
            return True, "done", "server-session-2", {
                "files_modified": [],
                "files_created": [],
                "commands_run": [],
                "total_tool_calls": 0,
            }

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        updates = list(
            web_ui.start_chad_task(
                session.id,
                str(git_repo),
                "do something",
                "claude",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        assert cancel_attempts["count"] == 1
        assert started["value"] is True
        assert session.server_session_id == "server-session-2"
        assert all("already running" not in (u[2].get("value", "").lower()) for u in updates)

    def test_workspace_label_prefers_worktree_over_project(self, web_ui):
        """Workspace label should show active worktree path when available."""
        session = web_ui.create_session("workspace")
        session.project_path = "/repo"
        session.worktree_path = Path("/repo/.chad-worktrees/abcd1234")

        label = web_ui._workspace_label(session)

        assert "Workspace:" in label
        assert "/repo/.chad-worktrees/abcd1234" in label

    def test_workspace_html_escapes_label_and_sets_tooltip(self, web_ui):
        """Workspace HTML should escape path text and include it in tooltip."""
        session = web_ui.create_session("workspace-html")
        session.project_path = '/tmp/repo/<unsafe>"path'

        html_output = web_ui._workspace_html(session)

        assert 'title="/tmp/repo/&lt;unsafe&gt;&quot;path"' in html_output
        assert "Workspace: /tmp/repo/&lt;unsafe&gt;&quot;path" in html_output

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
            return False, "stopped", "server-session", None

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

        final_live_stream = updates[-1][0]  # live_stream is at index 0
        # Handle both plain string and gr.update dict
        if isinstance(final_live_stream, dict):
            final_live_stream = final_live_stream.get("value", "")
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
            return False, "stopped", "server-session", None

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

        final_live_stream = updates[-1][0]  # live_stream is at index 0
        # Handle both plain string and gr.update dict
        if isinstance(final_live_stream, dict):
            final_live_stream = final_live_stream.get("value", "")
        assert "MOCK LIVE OUTPUT FROM TEXT" in final_live_stream

    def test_cancel_enables_start_button_in_final_yield(self, monkeypatch, web_ui, git_repo):
        """After cancel, start_task's final yield should enable start button and disable cancel."""
        cancel_gate = threading.Event()

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working"))
            cancel_gate.wait(timeout=1.0)
            return False, "cancelled", "server-session"

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = []

        def trigger_cancel():
            time.sleep(0.05)
            session.cancel_requested = True
            cancel_gate.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        for update in web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"):
            updates.append(update)

        # Final yield's button states (indices: 5=start_btn, 6=cancel_btn)
        final_update = updates[-1]
        start_btn_update = final_update[5]
        cancel_btn_update = final_update[6]

        # Extract interactive value - handle both dict and gr.update
        def get_interactive(update):
            if isinstance(update, dict):
                return update.get("interactive")
            return getattr(update, "interactive", None) if hasattr(update, "interactive") else None

        start_interactive = get_interactive(start_btn_update)
        cancel_interactive = get_interactive(cancel_btn_update)

        assert start_interactive is True, f"Start button should be enabled after cancel, got {start_btn_update}"
        assert cancel_interactive is False, f"Cancel button should be disabled after cancel, got {cancel_btn_update}"

    def test_progress_emission_preserves_live_stream(self, monkeypatch, web_ui, git_repo):
        """Progress detection should not clear the live output panel.

        Regression test: Previously, emitting a progress update would yield ""
        for the live stream, causing the live output to disappear.
        """

        live_html = "<pre>INITIAL LIVE OUTPUT</pre>"
        stream_complete = threading.Event()

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            # Stream initial content that will build up the live view
            message_queue.put(("stream", "working on task\n", live_html))
            time.sleep(0.02)  # Small delay to ensure processing
            # Stream content with progress JSON block
            progress_json = '```json\n{"change_summary": "Fix bug", "location": "src/main.py:42"}\n```'
            message_queue.put(("stream", progress_json))
            time.sleep(0.02)
            # Stream more content after progress
            message_queue.put(("stream", "continuing work\n"))
            message_queue.put(("message_complete", "CODING AI", "Task done"))
            stream_complete.set()
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = []

        for update in web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"):
            updates.append(update)

        # After progress emission, live stream should not be empty
        # Find the update that shows progress (has progress in chatbot)
        progress_updates = []
        for update in updates:
            live_stream = update[0]  # live_stream is at index 0
            if isinstance(live_stream, dict):
                live_stream = live_stream.get("value", "")
            # Track updates that occurred - none should be completely empty
            # during the task execution (before final completion)
            progress_updates.append(live_stream)

        # At least one update after streaming started should have content
        # Check for various content indicators - either actual output or waiting placeholder
        content_updates = [u for u in progress_updates if u and (
            "LIVE OUTPUT" in u or
            "Waiting" in u or
            "working" in u or
            "CODING AI" in u
        )]
        assert len(content_updates) > 0, f"Live stream should have content during streaming. Got updates: {progress_updates[:5]}"

    def test_coding_milestone_inserted_at_start(self, monkeypatch, web_ui, git_repo):
        """Coding milestone appears at the start of a task, before any streaming."""

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working...\n"))
            message_queue.put(("message_complete", "CODING AI", "Task done"))
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        # Collect all chat history snapshots
        all_histories = [u[1] for u in updates if isinstance(u[1], list)]
        # The final chat history should contain exactly one Coding milestone
        final_history = all_histories[-1] if all_histories else []
        coding_milestones = [
            msg for msg in final_history
            if isinstance(msg, dict) and "**Coding:**" in msg.get("content", "")
        ]
        assert len(coding_milestones) == 1, (
            f"Expected exactly 1 Coding milestone, found {len(coding_milestones)}. "
            f"History contents: {[m.get('content', '')[:60] for m in final_history if isinstance(m, dict)]}"
        )

    def test_coding_milestone_not_duplicated_with_progress_json(self, monkeypatch, web_ui, git_repo):
        """Coding milestone is not duplicated when progress JSON appears in stream."""

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "exploring...\n"))
            time.sleep(0.02)
            # Progress JSON in stream
            progress_json = '{"type": "progress", "summary": "Found bug", "location": "x.py:1", "next_step": "Fix"}'
            message_queue.put(("stream", progress_json))
            time.sleep(0.02)
            message_queue.put(("stream", "fixing...\n"))
            message_queue.put(("message_complete", "CODING AI", "Task done"))
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        all_histories = [u[1] for u in updates if isinstance(u[1], list)]
        final_history = all_histories[-1] if all_histories else []
        coding_milestones = [
            msg for msg in final_history
            if isinstance(msg, dict) and "**Coding:**" in msg.get("content", "")
        ]
        assert len(coding_milestones) == 1, (
            f"Expected exactly 1 Coding milestone even with progress JSON, found {len(coding_milestones)}"
        )

    def test_structured_progress_renders_after_coding_milestone(self, monkeypatch, web_ui, git_repo):
        """Structured progress events should appear after the initial coding milestone."""
        from chad.util.prompts import ProgressUpdate

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "exploring...\n"))
            time.sleep(0.02)
            message_queue.put(("progress", ProgressUpdate(
                summary="Found bug",
                location="x.py:1",
                next_step="Fix",
            )))
            time.sleep(0.02)
            # Delayed echoed JSON should not create a second progress bubble.
            message_queue.put((
                "stream",
                '{"type": "progress", "summary": "Found bug", "location": "x.py:1", "next_step": "Fix"}',
            ))
            message_queue.put(("stream", "implementing...\n"))
            time.sleep(0.02)
            message_queue.put(("message_complete", "CODING AI", "Task done"))
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        all_histories = [u[1] for u in updates if isinstance(u[1], list)]
        final_history = all_histories[-1] if all_histories else []

        progress_indices = [
            idx for idx, msg in enumerate(final_history)
            if isinstance(msg, dict) and "*(Progress)*" in msg.get("content", "")
        ]
        coding_indices = [
            idx for idx, msg in enumerate(final_history)
            if isinstance(msg, dict) and "**Coding:**" in msg.get("content", "")
        ]

        assert len(progress_indices) == 1, (
            f"Expected exactly one progress bubble, got {len(progress_indices)} "
            f"with history: {[m.get('content', '')[:80] for m in final_history if isinstance(m, dict)]}"
        )
        assert len(coding_indices) == 1, (
            f"Expected exactly one coding milestone, got {len(coding_indices)} "
            f"with history: {[m.get('content', '')[:80] for m in final_history if isinstance(m, dict)]}"
        )
        assert coding_indices[0] < progress_indices[0], (
            "Coding milestone should appear first since it is inserted at task start"
        )

    def test_exploration_milestones_appear_in_chat_history(self, monkeypatch, web_ui, git_repo):
        """Server-side exploration milestones should appear as Discovery chat bubbles."""

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "exploring...\n"))
            time.sleep(0.02)
            # Server-side milestones flow through SSE as ("milestone", type, title, summary)
            message_queue.put(("milestone", "exploration", "Discovery", "Found auth logic in src/auth.py"))
            message_queue.put(("milestone", "exploration", "Discovery", "Config is loaded from ~/.app/config"))
            time.sleep(0.02)
            message_queue.put(("stream", "implementing...\n"))
            message_queue.put(("message_complete", "CODING AI", "Task done"))
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        all_histories = [u[1] for u in updates if isinstance(u[1], list)]
        final_history = all_histories[-1] if all_histories else []

        discovery_msgs = [
            msg for msg in final_history
            if isinstance(msg, dict) and "**Discovery:**" in msg.get("content", "")
        ]
        assert len(discovery_msgs) == 2, (
            f"Expected 2 discovery milestones, found {len(discovery_msgs)}. "
            f"History: {[m.get('content', '')[:60] for m in final_history if isinstance(m, dict)]}"
        )
        assert "src/auth.py" in discovery_msgs[0]["content"]
        assert "config" in discovery_msgs[1]["content"].lower()

    def test_session_limit_milestone_appears_in_chat_history(self, monkeypatch, web_ui, git_repo):
        """Session limit milestones should appear as Session Limit chat bubbles and improve failure message."""

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working on task...\n"))
            time.sleep(0.02)
            message_queue.put(("milestone", "session_limit_reached", "Session Limit", "Session limit reached - resets 4pm (Australia/Melbourne)"))
            time.sleep(0.02)
            message_queue.put(("status", "❌ Agent exited with code 1"))
            message_queue.put(("message_complete", "CODING AI", "partial output"))
            return False, "Agent exited with code 1", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        updates = list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        all_histories = [u[1] for u in updates if isinstance(u[1], list)]
        final_history = all_histories[-1] if all_histories else []

        # Check session limit milestone appeared
        limit_msgs = [
            msg for msg in final_history
            if isinstance(msg, dict) and "**Session Limit:**" in msg.get("content", "")
        ]
        assert len(limit_msgs) == 1, (
            f"Expected 1 session limit milestone, found {len(limit_msgs)}. "
            f"History: {[m.get('content', '')[:80] for m in final_history if isinstance(m, dict)]}"
        )
        assert "resets 4pm" in limit_msgs[0]["content"]

        # Check failure message is specific (not generic)
        failure_msgs = [
            msg for msg in final_history
            if isinstance(msg, dict) and "SESSION LIMIT" in msg.get("content", "")
        ]
        assert len(failure_msgs) == 1, (
            f"Expected session limit failure banner, found {len(failure_msgs)}. "
            f"History: {[m.get('content', '')[:80] for m in final_history if isinstance(m, dict)]}"
        )

    def test_session_limit_replaces_generic_revision_failure(self, monkeypatch, web_ui, git_repo):
        """When a revision fails due to session limit, the failure message should be specific."""

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("stream", "working...\n"))
            # Emit session limit milestone
            message_queue.put(("milestone", "session_limit_reached", "Session Limit", "Session limit reached - resets 4pm (Australia/Melbourne)"))
            message_queue.put(("message_complete", "CODING AI", "partial output"))
            return False, "Agent exited with code 1", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("test")
        list(web_ui.start_chad_task(session.id, str(git_repo), "do something", "claude"))

        # After the task, session should have the limit summary stored
        assert session.session_limit_summary is not None
        assert "resets 4pm" in session.session_limit_summary

    def test_session_limit_summary_reset_between_tasks(self, monkeypatch, web_ui, git_repo):
        """session_limit_summary should be reset at start of each new task."""
        session = web_ui.create_session("test")
        session.session_limit_summary = "old limit"

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("message_complete", "CODING AI", "done"))
            return True, "completed", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)
        list(web_ui.start_chad_task(session.id, str(git_repo), "new task", "claude"))

        assert session.session_limit_summary is None

    def test_resolve_override_prompt_ignores_stale_generated_prompt(self, web_ui, git_repo):
        """Generated prompt text from a previous task should not become an override."""
        from chad.util.prompts import build_prompt

        project_path = str(git_repo)
        old_task = "Investigate Mistral startup behavior"
        new_task = "Raise verification bar for second attempt"

        stale_prompt = build_prompt(old_task, web_ui._read_project_docs(git_repo), project_path)

        override = web_ui._resolve_override_prompt(
            project_path=project_path,
            task_description=new_task,
            coding_prompt_value=stale_prompt,
        )

        assert override is None

    def test_resolve_override_prompt_keeps_user_template_edits(self, web_ui, git_repo):
        """Manual prompt edits should still be sent as override for the new task."""
        from chad.util.prompts import build_prompt_previews

        project_path = str(git_repo)
        task = "Improve verification structure"
        edited_template = build_prompt_previews(project_path).coding.replace(
            "## Instructions",
            "## Instructions\n- Add a dedicated section called Root Cause Analysis.",
        )

        override = web_ui._resolve_override_prompt(
            project_path=project_path,
            task_description=task,
            coding_prompt_value=edited_template,
        )

        assert override is not None
        assert "{task}" not in override
        assert task in override
        assert "Root Cause Analysis" in override


class TestClaudeJsonParsingIntegration:
    """Tests for Claude stream-json parsing in the web UI."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client."""
        client = Mock()
        client.list_accounts.return_value = []
        client.list_role_assignments.return_value = {}
        client.list_providers.return_value = ["anthropic", "openai"]
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.ui.gradio.web_ui import ChadWebUI

        ui = ChadWebUI(mock_api_client)
        return ui

    def test_run_task_parses_claude_json_for_anthropic_provider(self, web_ui, mock_api_client, git_repo):
        """run_task_via_api should parse Claude stream-json output for anthropic accounts."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        # Configure mock to return anthropic account
        mock_api_client.list_accounts.return_value = [
            Account(name="claude", provider="anthropic", model=None, reasoning=None, role="CODING", ready=True)
        ]

        # Mock create_session
        mock_session = Mock()
        mock_session.id = "server-sess-123"
        mock_api_client.create_session.return_value = mock_session

        # Simulate Claude stream-json output
        # Tool uses are placed BEFORE text so they get summarized when text arrives
        # (tool uses after text won't be summarized since no text follows)
        claude_json_lines = (
            b'{"type":"system","subtype":"init","cwd":"/test"}' + b'\n'
            b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/src/main.py"}}]}}' + b'\n'
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"Hello from Claude!"}]}}' + b'\n'
            b'{"type":"result","result":"done"}' + b'\n'
        )

        # Mock stream client to return terminal events with Claude JSON
        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(claude_json_lines).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        # Run the task
        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="claude",
            message_queue=message_queue,
        )

        # Collect stream messages
        stream_messages = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "stream":
                stream_messages.append(msg[1])

        # Verify JSON was parsed to human-readable text
        combined = "\n".join(stream_messages)
        assert "Hello from Claude!" in combined, "Assistant text should be extracted"
        # Tool uses are summarized when text follows them
        assert "1 file read" in combined, "Tool summary should be included before text"
        assert '{"type":"system"' not in combined, "Raw JSON should not appear"
        assert '{"type":"result"' not in combined, "Result events should be filtered"

    def test_run_task_parses_gemini_json_for_gemini_provider(self, web_ui, mock_api_client, git_repo):
        """run_task_via_api should parse Gemini stream-json output for gemini accounts."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="gem", provider="gemini", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-123"
        mock_api_client.create_session.return_value = mock_session

        gemini_json_lines = (
            b'{"type":"init","model":"gemini-2.5-pro","session_id":"abc123"}' + b'\n'
            b'{"type":"message","role":"assistant","content":"Hello from Gemini!"}' + b'\n'
            b'{"type":"result","stats":{"input_tokens":10,"output_tokens":20}}' + b'\n'
        )

        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(gemini_json_lines).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="gem",
            message_queue=message_queue,
        )

        stream_messages = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "stream":
                stream_messages.append(msg[1])

        combined = "\n".join(stream_messages)
        assert success is True
        assert "Hello from Gemini!" in combined
        assert '{"type":"message"' not in combined
        assert '{"type":"result"' not in combined

    def test_run_task_emits_progress_messages_from_structured_events(self, web_ui, mock_api_client, git_repo):
        """run_task_via_api should convert structured progress events into queue progress messages."""
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account
        from chad.util.prompts import ProgressUpdate

        mock_api_client.list_accounts.return_value = [
            Account(name="claude", provider="anthropic", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-123"
        mock_api_client.create_session.return_value = mock_session

        progress_event = Mock()
        progress_event.event_type = "event"
        progress_event.data = {
            "type": "progress",
            "summary": "Found workspace CSS issue",
            "location": "src/chad/ui/gradio/web_ui.py:1066",
            "next_step": "Apply CSS fix",
        }

        status_event = Mock()
        status_event.event_type = "event"
        status_event.data = {"type": "status", "status": "Phase 2: Implementing changes..."}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([progress_event, status_event, complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="claude",
            message_queue=message_queue,
        )

        assert success is True
        assert output == ""

        queued_messages = []
        while not message_queue.empty():
            queued_messages.append(message_queue.get())

        progress_messages = [msg for msg in queued_messages if msg[0] == "progress"]
        assert len(progress_messages) == 1, f"Expected one progress message, got: {queued_messages}"

        progress_payload = progress_messages[0][1]
        assert isinstance(progress_payload, ProgressUpdate)
        assert progress_payload.summary == "Found workspace CSS issue"
        assert progress_payload.location == "src/chad/ui/gradio/web_ui.py:1066"
        assert progress_payload.next_step == "Apply CSS fix"

    def test_run_task_passes_through_raw_for_non_anthropic(self, web_ui, mock_api_client, git_repo):
        """run_task_via_api should pass through raw output for non-anthropic providers."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        # Configure mock to return non-anthropic account
        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True)
        ]

        # Mock create_session
        mock_session = Mock()
        mock_session.id = "server-sess-456"
        mock_api_client.create_session.return_value = mock_session

        # Realistic Codex output with prompt echo
        # Codex outputs: banner, --------, header, --------, user (colored), prompt, mcp startup:
        raw_output = b"OpenAI Codex v0.92.0\n--------\nworkdir: /tmp\nmodel: gpt-5\n--------\n\x1b[36muser\x1b[0m\nTask prompt here\n\x1b[36mmcp startup:\x1b[0m 0 servers\nWorking on task...\nDone!"

        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(raw_output).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        # Run the task
        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            message_queue=message_queue,
        )

        # Collect stream messages
        stream_messages = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "stream":
                stream_messages.append(msg[1])

        # Verify raw output was passed through
        combined = "".join(stream_messages)
        assert "Working on task" in combined
        assert "Done!" in combined

    def test_codex_prompt_echo_keeps_content_before_user_marker(self, web_ui, mock_api_client, git_repo):
        """Codex filter should keep content BEFORE '-------- user' marker."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        # Configure mock to return non-anthropic account
        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True)
        ]

        # Mock create_session
        mock_session = Mock()
        mock_session.id = "server-sess-789"
        mock_api_client.create_session.return_value = mock_session

        # Realistic Codex output with header info preserved, prompt filtered
        # Structure: banner, --------, header (keep), --------, user, prompt (filter), mcp startup, agent work (keep)
        raw_output = b"OpenAI Codex v0.92.0\n--------\nworkdir: /home/user/project\nmodel: gpt-5.1-codex\n--------\n\x1b[36muser\x1b[0m\nTask prompt here\n\x1b[36mmcp startup:\x1b[0m 0 servers\nActual agent output\nDone!"

        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(raw_output).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        # Run the task
        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            message_queue=message_queue,
        )

        # Collect stream messages
        stream_messages = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "stream":
                stream_messages.append(msg[1])

        combined = "".join(stream_messages)
        # Header info (before "user" marker) should be kept
        assert "OpenAI Codex" in combined
        assert "workdir:" in combined
        # Content between "user" and "mcp startup:" should be filtered
        assert "Task prompt here" not in combined
        # Content after "mcp startup:" should be shown
        assert "Actual agent output" in combined
        assert "Done!" in combined

    def test_codex_prompt_echo_filtered_after_phase_restart(self, web_ui, mock_api_client, git_repo):
        """Codex prompt echo filtering should restart for implementation phase output."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-phase"
        mock_api_client.create_session.return_value = mock_session

        phase_one = (
            b"OpenAI Codex v0.92.0\n--------\nworkdir: /tmp\nmodel: gpt-5\n--------\n"
            b"\x1b[36muser\x1b[0m\nExploration prompt should be hidden\n"
            b"\x1b[36mmcp startup:\x1b[0m 0 servers\nExploration output kept\n"
        )
        phase_two = (
            b"OpenAI Codex v0.92.0\n--------\nworkdir: /tmp\nmodel: gpt-5\n--------\n"
            b"\x1b[36muser\x1b[0m\nImplementation prompt should be hidden\n"
            b"\x1b[36mmcp startup:\x1b[0m 0 servers\nImplementation output kept\nDone!\n"
        )

        phase_one_event = Mock()
        phase_one_event.event_type = "terminal"
        phase_one_event.data = {"data": base64.b64encode(phase_one).decode()}

        phase_two_event = Mock()
        phase_two_event.event_type = "terminal"
        phase_two_event.data = {"data": base64.b64encode(phase_two).decode()}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([phase_one_event, phase_two_event, complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            message_queue=message_queue,
        )

        assert success
        assert "Exploration prompt should be hidden" not in output
        assert "Implementation prompt should be hidden" not in output
        assert "Exploration output kept" in output
        assert "Implementation output kept" in output
        assert "Done!" in output

    def test_long_codex_output_fully_captured(self, web_ui, mock_api_client, git_repo):
        """Long Codex output should be fully captured in final_output, not just last screenful."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-long"
        mock_api_client.create_session.return_value = mock_session

        # Header + prompt echo + mcp startup
        header = b"OpenAI Codex v0.92.0\n--------\nworkdir: /tmp\nmodel: gpt-5\n--------\n\x1b[36muser\x1b[0m\nTask prompt\n\x1b[36mmcp startup:\x1b[0m 0 servers\n"
        # Simulate long agent output (many lines that would overflow terminal screen buffer)
        long_lines = "\n".join(f"Agent output line {i}" for i in range(200))
        raw_output = header + long_lines.encode("utf-8")

        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(raw_output).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            message_queue=message_queue,
        )

        assert success
        # Final output must contain ALL lines, not just the last screenful
        assert "Agent output line 0" in output
        assert "Agent output line 100" in output
        assert "Agent output line 199" in output

    def test_codex_output_buffer_flushed_on_completion(self, web_ui, mock_api_client, git_repo):
        """Codex output buffer should be flushed when stream completes, even without mcp marker."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-flush"
        mock_api_client.create_session.return_value = mock_session

        # Output without mcp startup marker - buffer won't be drained by normal filtering
        # This simulates incomplete Codex output (e.g., early termination)
        raw_output = b"Some initial output without markers\nImportant content here"

        mock_stream_event = Mock()
        mock_stream_event.event_type = "terminal"
        mock_stream_event.data = {"data": base64.b64encode(raw_output).decode()}

        mock_complete_event = Mock()
        mock_complete_event.event_type = "complete"
        mock_complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([mock_stream_event, mock_complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            message_queue=message_queue,
        )

        assert success
        # The buffered content should be flushed and included in final output
        assert "Important content here" in output

    def test_run_task_forwards_phase_status_and_strips_ansi(self, web_ui, mock_api_client, git_repo):
        """Structured status events should reach UI and final output should be ANSI-clean."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="mock-coding", provider="mock", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-status"
        mock_api_client.create_session.return_value = mock_session

        raw_output = b"\x1b[1;34mMock Agent v1.0\x1b[0m\n\x1b[33m> Analyzing task...\x1b[0m\n"

        terminal_event = Mock()
        terminal_event.event_type = "terminal"
        terminal_event.data = {"data": base64.b64encode(raw_output).decode()}

        phase_event = Mock()
        phase_event.event_type = "event"
        phase_event.data = {"type": "status", "status": "Phase 2: Implementing changes..."}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([terminal_event, phase_event, complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="mock-coding",
            message_queue=message_queue,
        )

        statuses = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "status":
                statuses.append(msg[1])

        assert success
        assert any("Phase 2: Implementing changes..." in s for s in statuses), statuses
        assert "Mock Agent v1.0" in output
        assert "\x1b" not in output

    def test_run_task_emits_single_coding_message(self, web_ui, mock_api_client, git_repo):
        """Combined coding phase should emit a single message_complete event."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="mock-coding", provider="mock", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-combined"
        mock_api_client.create_session.return_value = mock_session

        coding_output = b"Exploring files...\nApplying fix...\n"

        terminal_event = Mock()
        terminal_event.event_type = "terminal"
        terminal_event.data = {"data": base64.b64encode(coding_output).decode()}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter(
            [terminal_event, complete_event]
        )
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="mock-coding",
            message_queue=message_queue,
        )

        assert success
        assert "Exploring files" in output

        completes = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "message_complete":
                completes.append(msg[2])

        # Combined model: single message_complete event
        assert len(completes) == 1
        assert "Exploring files" in completes[0]

    def test_run_task_emits_milestone_events_from_api(self, web_ui, mock_api_client, git_repo):
        """Milestones should be polled from the dedicated API endpoint."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="mock-coding", provider="mock", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-milestone"
        mock_api_client.create_session.return_value = mock_session
        mock_api_client.get_milestones.side_effect = [
            [
                {
                    "seq": 1,
                    "milestone_type": "exploration",
                    "title": "Discovery",
                    "summary": "Found the bug in main.py",
                }
            ],
            [],
        ]

        terminal_event = Mock()
        terminal_event.event_type = "terminal"
        terminal_event.data = {"data": base64.b64encode(b"working...\n").decode()}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter(
            [terminal_event, complete_event]
        )
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="mock-coding",
            message_queue=message_queue,
        )

        assert success

        milestones = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "milestone":
                milestones.append(msg)

        assert len(milestones) == 1
        assert milestones[0][1] == "exploration"
        assert milestones[0][2] == "Discovery"
        assert milestones[0][3] == "Found the bug in main.py"
        assert mock_api_client.get_milestones.call_count >= 1

    def test_run_task_switches_to_verification_parser_for_qwen(self, web_ui, mock_api_client, git_repo):
        """Verification phase should parse Qwen stream-json even when coding provider differs."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True),
            Account(name="qwen-verifier", provider="qwen", model=None, reasoning=None, role="VERIFICATION", ready=True),
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-verify-qwen"
        mock_api_client.create_session.return_value = mock_session
        mock_api_client.get_milestones.side_effect = [
            [
                {
                    "seq": 1,
                    "milestone_type": "verification_started",
                    "title": "Verification",
                    "summary": "Attempt 1",
                }
            ],
            [],
        ]

        qwen_json_lines = (
            b'{"type":"system","subtype":"init","session_id":"abc"}\n'
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"Verification phase output"}]}}\n'
        )

        terminal_event = Mock()
        terminal_event.event_type = "terminal"
        terminal_event.data = {"data": base64.b64encode(qwen_json_lines).decode()}

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([terminal_event, complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            verification_account="qwen-verifier",
            message_queue=message_queue,
        )

        assert success is True
        combined_stream = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "stream":
                combined_stream.append(msg[1])

        rendered = "\n".join(combined_stream)
        assert "Verification phase output" in rendered
        assert '{"type":"system"' not in rendered
        assert '{"type":"assistant"' not in rendered

    def test_run_task_emits_ai_switch_for_verification_milestone(self, web_ui, mock_api_client, git_repo):
        """verification_started milestone should emit ai_switch so live header updates immediately."""
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="codex", provider="openai", model=None, reasoning=None, role="CODING", ready=True),
            Account(name="qwen-verifier", provider="qwen", model=None, reasoning=None, role="VERIFICATION", ready=True),
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-ai-switch"
        mock_api_client.create_session.return_value = mock_session
        mock_api_client.get_milestones.side_effect = [
            [
                {
                    "seq": 1,
                    "milestone_type": "verification_started",
                    "title": "Verification",
                    "summary": "Attempt 1",
                }
            ],
            [],
        ]

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, _, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="codex",
            verification_account="qwen-verifier",
            message_queue=message_queue,
        )

        assert success is True
        switches = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "ai_switch":
                switches.append(msg[1])

        assert "VERIFICATION AI" in switches

    def test_run_task_ignores_milestone_events_from_sse(self, web_ui, mock_api_client, git_repo):
        """SSE milestone events should be ignored in favor of the dedicated endpoint."""
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="mock-coding", provider="mock", model=None, reasoning=None, role="CODING", ready=True)
        ]

        mock_session = Mock()
        mock_session.id = "server-sess-milestone"
        mock_api_client.create_session.return_value = mock_session
        mock_api_client.get_milestones.return_value = []

        milestone_event = Mock()
        milestone_event.event_type = "event"
        milestone_event.data = {
            "type": "milestone",
            "milestone_type": "exploration",
            "title": "Discovery",
            "summary": "Should be ignored",
        }

        complete_event = Mock()
        complete_event.event_type = "complete"
        complete_event.data = {"exit_code": 0}

        mock_stream_client = Mock()
        mock_stream_client.stream_events.return_value = iter([milestone_event, complete_event])
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, _, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="mock-coding",
            message_queue=message_queue,
        )

        assert success
        milestones = []
        while not message_queue.empty():
            msg = message_queue.get()
            if msg[0] == "milestone":
                milestones.append(msg)

        assert milestones == []

    def test_run_task_reused_session_streams_only_new_events(self, web_ui, mock_api_client, git_repo):
        """Reused server sessions should stream from latest sequence, not from the beginning."""
        import base64
        import queue
        from unittest.mock import Mock
        from chad.ui.client.api_client import Account

        mock_api_client.list_accounts.return_value = [
            Account(name="mock-coding", provider="mock", model=None, reasoning=None, role="CODING", ready=True)
        ]
        mock_api_client.get_session_latest_seq.return_value = 9

        stale_complete = Mock()
        stale_complete.event_type = "complete"
        stale_complete.data = {"exit_code": None}

        terminal_event = Mock()
        terminal_event.event_type = "terminal"
        terminal_event.data = {"data": base64.b64encode(b"new run output\n").decode()}

        fresh_complete = Mock()
        fresh_complete.event_type = "complete"
        fresh_complete.data = {"exit_code": 0}

        def stream_events(session_id, since_seq=0, include_terminal=True):
            if since_seq == 0:
                return iter([stale_complete])
            return iter([terminal_event, fresh_complete])

        mock_stream_client = Mock()
        mock_stream_client.stream_events.side_effect = stream_events
        web_ui._stream_client = mock_stream_client

        message_queue = queue.Queue()
        success, output, _, _ = web_ui.run_task_via_api(
            session_id="test",
            project_path=str(git_repo),
            task_description="test task",
            coding_account="mock-coding",
            message_queue=message_queue,
            server_session_id="server-sess-reuse",
        )

        assert success
        assert "new run output" in output
        mock_api_client.get_session_latest_seq.assert_called_once_with("server-sess-reuse")


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


class TestVerificationApiForwarding:
    """Ensure start_chad_task runs verification through API execution path."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client."""
        client = Mock()
        claude_account = MockAccount(name="claude", provider="anthropic", role="CODING")
        qwen_account = MockAccount(name="qwen-verifier", provider="qwen", role="VERIFICATION")
        client.list_accounts.return_value = [claude_account, qwen_account]
        client.get_account.side_effect = lambda name: {
            "claude": claude_account,
            "qwen-verifier": qwen_account,
        }.get(name, Mock(name=name, provider="unknown", model="default", reasoning="default", role=None))
        client.get_verification_agent.return_value = None
        client.get_milestones.return_value = []
        client.get_preferences.return_value = Mock(last_project_path=None, dark_mode=True, ui_mode="gradio")
        client.get_cleanup_settings.return_value = Mock(retention_days=7, auto_cleanup=True)
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client, monkeypatch, tmp_path):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.ui.gradio.web_ui import ChadWebUI

        monkeypatch.setenv("CHAD_CONFIG", str(tmp_path / "test_chad.conf"))
        ui = ChadWebUI(mock_api_client)
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/codex"))
        return ui

    def test_start_task_forwards_verification_settings_to_api_run(self, monkeypatch, web_ui, git_repo):
        """Regression: verification config must be passed to run_task_via_api."""
        captured_kwargs = {}

        def fake_run_task_via_api(session_id, project_path, task_description, coding_account, message_queue, **kwargs):
            captured_kwargs.update(kwargs)
            message_queue.put(("ai_switch", "CODING AI"))
            message_queue.put(("message_start", "CODING AI"))
            message_queue.put(("message_complete", "CODING AI", "Done"))
            return True, "Done", "server-session", None

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("verification-forwarding")
        list(
            web_ui.start_chad_task(
                session.id,
                str(git_repo),
                "do something",
                "claude",
                verification_agent="qwen-verifier",
                verification_model="qwen3-coder",
                verification_reasoning="high",
            )
        )

        assert captured_kwargs.get("verification_account") == "qwen-verifier"
        assert captured_kwargs.get("verification_model") == "qwen3-coder"
        assert captured_kwargs.get("verification_reasoning") == "high"


class TestLiveStreamSearch:
    """Verify that live stream HTML includes the search bar."""

    def test_live_stream_html_contains_search_bar(self):
        """build_live_stream_html should include search input in header."""
        from chad.ui.gradio import web_ui

        rendered = web_ui.build_live_stream_html("some content", "AI")
        assert 'class="live-search-input"' in rendered
        assert 'class="live-search-bar"' in rendered
        assert 'class="live-search-count"' in rendered
        assert 'class="live-search-nav"' in rendered
        assert 'placeholder="Search..."' in rendered

    def test_live_stream_pyte_contains_search_bar(self):
        """build_live_stream_html_from_pyte should include search input in header."""
        from chad.ui.gradio import web_ui

        rendered = web_ui.build_live_stream_html_from_pyte("<p>content</p>", "AI")
        assert 'class="live-search-input"' in rendered
        assert 'class="live-search-bar"' in rendered
        assert 'class="live-search-count"' in rendered
        assert 'class="live-search-nav"' in rendered

    def test_search_bar_inside_header(self):
        """Search bar should be inside the live-output-header div."""
        from chad.ui.gradio import web_ui

        rendered = web_ui.build_live_stream_html("test", "AI")
        # Header should contain the title and search bar
        assert 'class="live-header-title"' in rendered
        # Ensure the header still shows the AI name
        assert "AI (Live Stream)" in rendered


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


def test_live_stream_display_buffer_keeps_all_content():
    """Live stream display buffer should keep all content for infinite history."""
    from chad.ui.gradio.web_ui import LiveStreamDisplayBuffer

    buffer = LiveStreamDisplayBuffer()
    buffer.append("a" * 60)
    buffer.append("b" * 60)

    # Should keep all content without truncation
    assert len(buffer.content) == 120
    assert buffer.content == ("a" * 60) + ("b" * 60)


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


def test_workspace_display_allows_full_path():
    """Workspace display should allow full path without ellipsis truncation."""
    from chad.ui.gradio import web_ui

    # Check that workspace display CSS doesn't cause text truncation
    css = web_ui.PROVIDER_PANEL_CSS

    # Look for workspace-inline style block that controls text display
    workspace_inline_pattern = r"\.workspace-inline[^}]*\{([^}]*)\}"
    matches = re.findall(workspace_inline_pattern, css, re.MULTILINE | re.DOTALL)

    assert matches, "Expected workspace-inline CSS style block"

    workspace_inline_css = matches[0]

    # The workspace should not truncate text with ellipsis - it should allow full paths to be shown
    # This test will fail if ellipsis truncation is present, requiring the fix
    assert not ("text-overflow: ellipsis" in workspace_inline_css and "overflow: hidden" in workspace_inline_css), \
        f"Workspace inline should not use ellipsis truncation to allow full path display: {workspace_inline_css}"


def test_workspace_font_color_is_black():
    """Workspace font should be black (#000000) for better readability."""
    from chad.ui.gradio import web_ui

    # Check that workspace display CSS uses black font color
    css = web_ui.PROVIDER_PANEL_CSS

    # Look for workspace-inline style block that controls text color
    workspace_inline_pattern = r"\.workspace-inline[^}]*\{([^}]*)\}"
    matches = re.findall(workspace_inline_pattern, css, re.MULTILINE | re.DOTALL)

    assert matches, "Expected workspace-inline CSS style block"

    workspace_inline_css = matches[0]

    # The workspace font should be black for better readability
    # This test ensures the color is #000000 (black) instead of light gray
    assert "color: #000000" in workspace_inline_css, \
        f"Workspace font should be black (#000000), found: {workspace_inline_css}"


def test_cancel_and_save_buttons_same_size():
    """Cancel and Save buttons should have consistent size properties."""
    from chad.ui.gradio import web_ui

    # Read the web UI source to check button properties
    import inspect
    source = inspect.getsource(web_ui.ChadWebUI)

    # Find both button definitions
    cancel_btn_pattern = r'cancel_btn = gr\.Button\(\s*"Cancel"[^)]+\)'
    save_btn_pattern = r'project_save_btn = gr\.Button\(\s*"Save"[^)]+\)'

    cancel_match = re.search(cancel_btn_pattern, source, re.MULTILINE | re.DOTALL)
    save_match = re.search(save_btn_pattern, source, re.MULTILINE | re.DOTALL)

    assert cancel_match, "Could not find Cancel button definition"
    assert save_match, "Could not find Save button definition"

    cancel_def = cancel_match.group(0)
    save_def = save_match.group(0)

    # Both buttons should have size="sm" for consistency
    assert 'size="sm"' in cancel_def, f"Cancel button should have size='sm': {cancel_def}"
    assert 'size="sm"' in save_def, f"Save button should have size='sm': {save_def}"

    # Both buttons should have same min_width and scale for consistent sizing
    assert 'min_width=80' in cancel_def, f"Cancel button should have min_width=80: {cancel_def}"
    assert 'min_width=80' in save_def, f"Save button should have min_width=80: {save_def}"

    assert 'scale=0' in cancel_def, f"Cancel button should have scale=0: {cancel_def}"
    assert 'scale=0' in save_def, f"Save button should have scale=0: {save_def}"


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
        mgr.get_action_settings.return_value = [
            {"event": "session_usage", "threshold": 90, "action": "notify"},
            {"event": "weekly_usage", "threshold": 90, "action": "notify"},
            {"event": "context_usage", "threshold": 90, "action": "notify"},
        ]
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

    @patch("chad.ui.gradio.web_ui.gr")
    def test_create_interface_uses_multimodal_textbox_for_task_input(self, mock_gr, mock_api_client):
        """Task input should use MultimodalTextbox for combined text and image drag-drop."""
        from chad.ui.gradio.web_ui import ChadWebUI

        mock_blocks = MagicMock()
        mock_gr.Blocks.return_value.__enter__ = Mock(return_value=mock_blocks)
        mock_gr.Blocks.return_value.__exit__ = Mock(return_value=None)

        web_ui = ChadWebUI(mock_api_client)
        web_ui.create_interface()

        # Check that MultimodalTextbox is used for task description
        multimodal_calls = [
            call.kwargs
            for call in mock_gr.MultimodalTextbox.call_args_list
            if "elem_classes" in call.kwargs and "task-desc-input" in call.kwargs["elem_classes"]
        ]

        assert multimodal_calls, "Task input should use MultimodalTextbox"
        # Should support image uploads
        assert all(kwargs.get("file_types") == ["image"] for kwargs in multimodal_calls)
        assert all(kwargs.get("file_count") == "multiple" for kwargs in multimodal_calls)

    @patch("chad.ui.gradio.web_ui.gr")
    def test_create_interface_does_not_pass_scale_to_html(self, mock_gr, mock_api_client):
        """gr.HTML does not support scale; passing it causes startup failure."""
        from chad.ui.gradio.web_ui import ChadWebUI

        mock_blocks = MagicMock()
        mock_gr.Blocks.return_value.__enter__ = Mock(return_value=mock_blocks)
        mock_gr.Blocks.return_value.__exit__ = Mock(return_value=None)

        web_ui = ChadWebUI(mock_api_client)
        web_ui.create_interface()

        assert mock_gr.HTML.call_count > 0
        for call in mock_gr.HTML.call_args_list:
            assert "scale" not in call.kwargs

    @patch("chad.ui.gradio.web_ui.gr")
    def test_create_interface_disables_chat_message_grouping(self, mock_gr, mock_api_client):
        """Milestones chatbot should not merge consecutive same-role messages."""
        from chad.ui.gradio.web_ui import ChadWebUI

        mock_blocks = MagicMock()
        mock_gr.Blocks.return_value.__enter__ = Mock(return_value=mock_blocks)
        mock_gr.Blocks.return_value.__exit__ = Mock(return_value=None)

        web_ui = ChadWebUI(mock_api_client)
        web_ui.create_interface()

        chatbot_calls = [c.kwargs for c in mock_gr.Chatbot.call_args_list if c.kwargs.get("label") == "Milestones"]
        assert chatbot_calls, "Expected Milestones chatbot to be created"
        assert all(c.get("group_consecutive_messages") is False for c in chatbot_calls)

    def test_action_rules_ui_exists_in_source(self):
        """Action rules UI should exist in the web UI source."""
        import inspect

        import chad.ui.gradio.web_ui as web_ui

        source = inspect.getsource(web_ui)
        assert "action_settings" in source
        assert "Action Rules" in source

    def test_action_rules_ui_creates_with_real_gradio(self):
        """Action Rules section must create without Gradio component errors."""
        import gradio as gr

        action_settings_data = [
            {"event": "session_usage", "threshold": 90, "action": "notify"},
        ]
        account_names = ["test-account"]
        _MAX = 6
        all_event_choices = ["session_usage", "weekly_usage", "context_usage"]
        all_action_choices = ["notify", "switch_provider", "await_reset"]

        with gr.Blocks():
            gr.Markdown("### Action Rules")
            with gr.Row():
                with gr.Column(scale=3):
                    gr.Markdown("**Rule**")
                with gr.Column(scale=3):
                    gr.Markdown("**Action**")
                with gr.Column(scale=0, min_width=60):
                    gr.Markdown("")
            for i in range(_MAX):
                has_data = i < len(action_settings_data)
                current = action_settings_data[i] if has_data else {}
                with gr.Row(visible=has_data):
                    with gr.Column(scale=3):
                        with gr.Row():
                            gr.Dropdown(choices=all_event_choices, value=current.get("event", "session_usage"), show_label=False)
                            gr.Slider(minimum=0, maximum=100, step=5, value=current.get("threshold", 90), show_label=False)
                    with gr.Column(scale=3):
                        with gr.Row():
                            gr.Dropdown(choices=all_action_choices, value=current.get("action", "notify"), show_label=False)
                            gr.Dropdown(choices=[""] + account_names, value="", show_label=False, visible=False)
                    gr.Button("✕", scale=0, min_width=60, size="sm")
            gr.Button("+ Add Rule", size="sm")


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
    def test_gemini_logged_in_no_usage(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage when logged in but no usage data."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        with patch("chad.util.providers._read_gemini_usage", return_value=[]):
            result = web_ui._get_gemini_usage()

        assert "✅" in result
        assert "Logged in" in result
        assert "No usage data yet" in result

    @patch("pathlib.Path.home")
    def test_gemini_usage_aggregates_models(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage aggregates token counts by model from JSONL."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        records = [
            {"timestamp": "2026-01-01T12:00:00+00:00", "model": "gemini-2.5-pro",
             "input_tokens": 1000, "output_tokens": 100, "cached_tokens": 500},
            {"timestamp": "2026-01-01T13:00:00+00:00", "model": "gemini-2.5-pro",
             "input_tokens": 2000, "output_tokens": 200, "cached_tokens": 1000},
            {"timestamp": "2026-01-01T14:00:00+00:00", "model": "gemini-2.5-flash",
             "input_tokens": 500, "output_tokens": 50, "cached_tokens": 200},
        ]

        with patch("chad.util.providers._read_gemini_usage", return_value=records):
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
        """Gemini logged in with no usage returns 1.0 (full capacity)."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        with patch("chad.util.providers._read_gemini_usage", return_value=[]):
            result = web_ui._get_gemini_remaining_usage()
        assert result == 1.0  # Logged in, no usage = full capacity

    @patch("pathlib.Path.home")
    def test_mistral_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".vibe").mkdir()

        result = web_ui._get_mistral_remaining_usage()
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_mistral_remaining_usage_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral logged in with no usage returns 1.0 (full capacity)."""
        mock_home.return_value = tmp_path
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / ".env").write_text("MISTRAL_API_KEY=test-key\n")

        result = web_ui._get_mistral_remaining_usage()
        assert result == 1.0  # Logged in, no sessions = full capacity

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
    @patch("requests.get")
    def test_claude_remaining_usage_fractional_utilization(self, mock_get, mock_home, web_ui, tmp_path):
        """Fractional utilization values (0-1) should be treated as percentages."""
        import json

        mock_home.return_value = tmp_path
        claude_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        claude_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "PRO"}}
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"five_hour": {"utilization": 0.25}}
        mock_get.return_value = mock_response

        result = web_ui._get_claude_remaining_usage("claude-1")
        assert result == 0.75  # 25% used -> 75% remaining

    @patch("pathlib.Path.home")
    def test_codex_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Codex not logged in returns 0.0."""
        mock_home.return_value = tmp_path

        result = web_ui._get_codex_remaining_usage("codex")
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_gemini_remaining_usage_with_records(self, mock_home, web_ui, tmp_path):
        """Gemini calculates remaining usage from today's JSONL records."""
        from datetime import datetime, timezone

        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        today = datetime.now(timezone.utc).isoformat()
        # 10 requests = 10% of 100 limit
        records = [{"timestamp": today, "model": "gemini-pro"} for _ in range(10)]

        with patch("chad.util.providers._read_gemini_usage", return_value=records):
            result = web_ui._get_gemini_remaining_usage()
        assert result == pytest.approx(0.9, abs=0.01)  # 90% remaining

    @patch("pathlib.Path.home")
    def test_qwen_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Qwen not logged in returns 0.0."""
        mock_home.return_value = tmp_path

        result = web_ui._get_qwen_remaining_usage()
        assert result == 0.0

    @patch("pathlib.Path.home")
    def test_qwen_remaining_usage_logged_in_no_sessions(self, mock_home, web_ui, tmp_path):
        """Qwen logged in with no sessions returns 1.0 (full capacity)."""
        mock_home.return_value = tmp_path
        qwen_dir = tmp_path / ".qwen"
        qwen_dir.mkdir()
        (qwen_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        result = web_ui._get_qwen_remaining_usage()
        assert result == 1.0

    @patch("pathlib.Path.home")
    def test_qwen_remaining_usage_with_sessions(self, mock_home, web_ui, tmp_path):
        """Qwen calculates remaining usage from today's requests."""
        import json
        from datetime import datetime, timezone

        mock_home.return_value = tmp_path
        qwen_dir = tmp_path / ".qwen"
        qwen_dir.mkdir()
        (qwen_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        session_dir = qwen_dir / "projects" / "project1" / "chats"
        session_dir.mkdir(parents=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # 400 requests = 20% of 2000 limit
        lines = [json.dumps({"type": "assistant", "timestamp": today}) for _ in range(400)]
        (session_dir / "session.jsonl").write_text("\n".join(lines))

        result = web_ui._get_qwen_remaining_usage()
        assert result == pytest.approx(0.8, abs=0.01)  # 80% remaining

    @patch("pathlib.Path.home")
    def test_mistral_remaining_usage_with_sessions(self, mock_home, web_ui, tmp_path):
        """Mistral calculates remaining usage from today's sessions."""
        import json

        mock_home.return_value = tmp_path
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / ".env").write_text("MISTRAL_API_KEY=test-key\n")

        session_dir = vibe_dir / "logs" / "session"
        session_dir.mkdir(parents=True)

        # 100 requests = 10% of 1000 limit
        session_data = {"metadata": {"stats": {"prompt_count": 100}}}
        (session_dir / "session_today.json").write_text(json.dumps(session_data))

        result = web_ui._get_mistral_remaining_usage()
        assert result == pytest.approx(0.9, abs=0.01)  # 90% remaining


class TestUsageBasedProviderSwitch:
    """Test cases for proactive usage-based provider switching."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client with mock provider support."""
        mgr = Mock()
        accounts = {
            "primary-mock": MockAccount(name="primary-mock", provider="mock", role="CODING"),
            "fallback-mock": MockAccount(name="fallback-mock", provider="mock"),
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

    def test_check_usage_and_switch_no_switch_when_no_switch_action(self, web_ui, mock_api_client):
        """No switch should occur when action_settings has only notify actions."""
        mock_api_client.get_action_settings.return_value = [
            {"event": "session_usage", "threshold": 90, "action": "notify"},
        ]

        account, switched_from = web_ui._check_usage_and_switch("primary-mock")

        assert account == "primary-mock"
        assert switched_from is None

    def test_check_usage_and_switch_triggers_switch(self, web_ui, mock_api_client):
        """Switch should occur when action_settings has a switch_provider action."""
        mock_api_client.get_action_settings.return_value = [
            {"event": "session_usage", "threshold": 90, "action": "switch_provider", "target_account": "fallback-mock"},
        ]

        account, switched_from = web_ui._check_usage_and_switch("primary-mock")

        assert account == "fallback-mock"
        assert switched_from == "primary-mock"

    def test_check_usage_and_switch_no_switch_on_api_error(self, web_ui, mock_api_client):
        """No switch when API call fails."""
        mock_api_client.get_action_settings.side_effect = Exception("API error")

        account, switched_from = web_ui._check_usage_and_switch("primary-mock")

        assert account == "primary-mock"
        assert switched_from is None

    def test_start_task_uses_selected_coding_agent_for_initial_run(
        self, web_ui, mock_api_client, git_repo, monkeypatch
    ):
        """Initial coding run should honor the selected coding agent."""
        mock_api_client.get_action_settings.return_value = [
            {"event": "session_usage", "threshold": 50, "action": "switch_provider", "target_account": "fallback-mock"},
        ]
        mock_api_client.get_worktree_status.return_value = Mock(exists=False)

        captured = {"coding_account": None}

        def fake_run_task_via_api(
            session_id,
            project_path,
            task_description,
            coding_account,
            message_queue,
            **kwargs,
        ):
            captured["coding_account"] = coding_account
            message_queue.put(("status", "Phase 1: Exploring codebase..."))
            message_queue.put(("status", "Phase 2: Implementing changes..."))
            message_queue.put(("message_complete", "CODING AI", "done"))
            return True, "done", "server-session", {
                "files_modified": ["src/example.py"],
                "files_created": [],
                "commands_run": [],
                "total_tool_calls": 1,
            }

        monkeypatch.setattr(web_ui, "run_task_via_api", fake_run_task_via_api)

        session = web_ui.create_session("selected-agent")
        list(
            web_ui.start_chad_task(
                session.id,
                str(git_repo),
                "test",
                coding_agent="primary-mock",
                verification_agent=web_ui.VERIFICATION_NONE,
            )
        )

        assert captured["coding_account"] == "primary-mock"


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
    @patch("requests.get")
    def test_claude_usage_extra_credits_are_displayed_as_dollars(self, mock_get, mock_home, web_ui, tmp_path):
        """Claude extra credit values are cents and should render as dollars."""
        import json

        mock_home.return_value = tmp_path

        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        creds = {"claudeAiOauth": {"accessToken": "test-token", "subscriptionType": "PRO"}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "extra_usage": {
                "is_enabled": True,
                "used_credits": 1499,
                "monthly_limit": 4000,
                "utilization": 37.5,
            }
        }
        mock_get.return_value = mock_response

        result = web_ui.provider_ui._get_claude_usage("claude-1")

        assert "$14.99 / $40.00 (37.5%)" in result

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

    def test_opencode_login_check_detects_auth_file(self, web_ui, monkeypatch, tmp_path):
        """OpenCode login check succeeds when OAuth auth.json exists."""
        import json
        monkeypatch.setattr("chad.ui.gradio.provider_ui.safe_home", lambda: tmp_path)
        auth_dir = tmp_path / ".local" / "share" / "opencode"
        auth_dir.mkdir(parents=True)
        (auth_dir / "auth.json").write_text(json.dumps({"token": "test-token"}))
        logged_in, msg = web_ui.provider_ui._check_provider_login("opencode", "oc-test")
        assert logged_in is True
        assert "Logged in" in msg

    def test_opencode_login_check_fails_without_auth(self, web_ui, monkeypatch, tmp_path):
        """OpenCode login check fails when no OAuth credentials exist."""
        monkeypatch.setattr("chad.ui.gradio.provider_ui.safe_home", lambda: tmp_path)
        logged_in, msg = web_ui.provider_ui._check_provider_login("opencode", "oc-test")
        assert logged_in is False
        assert "Not logged in" in msg

    def test_mistral_login_check_requires_api_key(self, web_ui, monkeypatch, tmp_path):
        """Mistral login should not pass with config.toml only."""
        monkeypatch.setattr("chad.ui.gradio.provider_ui.safe_home", lambda: tmp_path)
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir(parents=True)
        (vibe_dir / "config.toml").write_text("active_model = \"devstral-2\"\n")

        logged_in, msg = web_ui.provider_ui._check_provider_login("mistral", "mistral-test")

        assert logged_in is False
        assert "Not logged in" in msg

    def test_mistral_login_check_reads_env_file(self, web_ui, monkeypatch, tmp_path):
        """Mistral login should pass when ~/.vibe/.env has MISTRAL_API_KEY."""
        monkeypatch.setattr("chad.ui.gradio.provider_ui.safe_home", lambda: tmp_path)
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir(parents=True)
        (vibe_dir / ".env").write_text("MISTRAL_API_KEY=test-key\n")

        logged_in, msg = web_ui.provider_ui._check_provider_login("mistral", "mistral-test")

        assert logged_in is True
        assert "Logged in" in msg

    def test_kimi_add_provider_no_shutil_error(self, web_ui, mock_api_client):
        """Kimi add_provider should not raise UnboundLocalError for shutil."""
        mock_api_client.list_accounts.return_value = []
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/kimi"))
        web_ui.provider_ui.installer.find_tool_path = Mock(return_value="/tmp/kimi")

        # Mock subprocess.Popen to simulate a failed login (no creds written)
        mock_proc = Mock()
        mock_proc.stdout.readline.return_value = b""
        mock_proc.poll.return_value = 0
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock()

        import subprocess as _subprocess
        with patch.object(_subprocess, "Popen", return_value=mock_proc):
            with patch("chad.ui.gradio.provider_ui.shutil.which", return_value="/tmp/kimi"):
                # This should NOT raise UnboundLocalError: local variable 'shutil'
                result = web_ui.add_provider("kimi-test", "kimi")[0]
        assert "❌" in result

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


class TestPexpectLoginDrain:
    """Test that pexpect-based login flows drain PTY output to prevent buffer deadlocks."""

    @pytest.fixture
    def mock_api_client(self):
        mgr = Mock()
        mgr.list_accounts.return_value = []
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = "default"
        mgr.get_account_reasoning.return_value = "default"
        return mgr

    @pytest.fixture
    def web_ui(self, mock_api_client):
        from chad.ui.gradio.web_ui import ChadWebUI

        ui = ChadWebUI(mock_api_client)
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "gemini"))
        return ui

    @patch("pathlib.Path.home")
    def test_gemini_login_creates_settings_json(self, mock_home, web_ui, mock_api_client, tmp_path):
        """Gemini login pre-creates settings.json so the CLI skips the auth dialog."""
        import json
        import pexpect

        mock_home.return_value = tmp_path

        mock_child = Mock()
        mock_child.isalive.return_value = True
        mock_child.close = Mock()

        def fake_drain(size=10000, timeout=0.1):
            raise pexpect.TIMEOUT("no data")

        mock_child.read_nonblocking = fake_drain

        poll_count = [0]
        original_exists = Path.exists

        def mock_exists(path):
            if "oauth_creds.json" in str(path) and ".gemini" in str(path):
                poll_count[0] += 1
                # _check_provider_login calls once, then loop polls
                return poll_count[0] >= 3
            return original_exists(path)

        with patch("pexpect.spawn", return_value=mock_child):
            with patch("time.sleep"):
                with patch.object(Path, "exists", mock_exists):
                    result = web_ui.add_provider("my-gemini", "gemini")[0]

        # settings.json must be created before the CLI is spawned
        settings_path = tmp_path / ".gemini" / "settings.json"
        assert settings_path.exists(), "settings.json not pre-created"
        settings = json.loads(settings_path.read_text())
        assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
        assert "✅" in result

    @patch("pathlib.Path.home")
    def test_gemini_login_drains_pty_output(self, mock_home, web_ui, mock_api_client, tmp_path):
        """Gemini login flow must drain PTY output to prevent buffer deadlock.

        Without draining, the Gemini CLI blocks when its PTY output buffer fills,
        causing the polling loop to hang indefinitely since child.isalive() stays True.
        """
        import pexpect

        mock_home.return_value = tmp_path

        # Track read_nonblocking calls to verify draining happens
        drain_calls = []

        mock_child = Mock()
        mock_child.isalive.return_value = True
        mock_child.close = Mock()

        def track_drain(size=10000, timeout=0.1):
            drain_calls.append((size, timeout))
            raise pexpect.TIMEOUT("no data")

        mock_child.read_nonblocking = track_drain

        poll_count = [0]
        original_exists = Path.exists

        def mock_exists(path):
            if "oauth_creds.json" in str(path) and ".gemini" in str(path):
                poll_count[0] += 1
                # _check_provider_login calls exists() once (poll 1),
                # then the loop polls: poll 2, 3, 4 return False, poll 5 succeeds.
                # This gives 3 drain calls in the loop.
                return poll_count[0] >= 5
            return original_exists(path)

        with patch("pexpect.spawn", return_value=mock_child):
            with patch("time.sleep"):
                with patch.object(Path, "exists", mock_exists):
                    result = web_ui.add_provider("my-gemini", "gemini")[0]

        # Verify draining happened during polling
        assert len(drain_calls) >= 2, (
            f"Expected read_nonblocking to be called during polling, got {len(drain_calls)} calls. "
            "Without draining, the PTY buffer fills and the CLI deadlocks."
        )
        assert "✅" in result
        mock_api_client.create_account.assert_called_once_with("my-gemini", "gemini")

    @patch("pathlib.Path.home")
    def test_gemini_login_does_not_overwrite_existing_settings(self, mock_home, web_ui, mock_api_client, tmp_path):
        """If settings.json already exists, don't overwrite the user's config."""
        import json
        import pexpect

        mock_home.return_value = tmp_path

        # Pre-create settings with custom content
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir(parents=True)
        custom = {"security": {"auth": {"selectedType": "gemini-api-key"}}, "custom": True}
        (gemini_dir / "settings.json").write_text(json.dumps(custom))

        mock_child = Mock()
        mock_child.isalive.return_value = True
        mock_child.close = Mock()
        mock_child.read_nonblocking = Mock(side_effect=pexpect.TIMEOUT("no data"))

        poll_count = [0]
        original_exists = Path.exists

        def mock_exists(path):
            if "oauth_creds.json" in str(path) and ".gemini" in str(path):
                poll_count[0] += 1
                return poll_count[0] >= 3
            return original_exists(path)

        with patch("pexpect.spawn", return_value=mock_child):
            with patch("time.sleep"):
                with patch.object(Path, "exists", mock_exists):
                    web_ui.add_provider("my-gemini", "gemini")

        # Original settings must be preserved
        settings = json.loads((gemini_dir / "settings.json").read_text())
        assert settings["custom"] is True
        assert settings["security"]["auth"]["selectedType"] == "gemini-api-key"

    @patch("pathlib.Path.home")
    def test_qwen_login_drains_pty_output(self, mock_home, web_ui, mock_api_client, tmp_path):
        """Qwen login flow drains PTY output (regression guard)."""
        import pexpect

        mock_home.return_value = tmp_path
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "qwen"))

        drain_calls = []

        mock_child = Mock()
        mock_child.isalive.return_value = True
        mock_child.send = Mock()
        mock_child.close = Mock()

        def track_drain(size=10000, timeout=0.1):
            drain_calls.append((size, timeout))
            raise pexpect.TIMEOUT("no data")

        mock_child.read_nonblocking = track_drain

        poll_count = [0]
        original_exists = Path.exists

        def mock_exists(path):
            if "oauth_creds.json" in str(path) and ".qwen" in str(path):
                poll_count[0] += 1
                # _check_provider_login calls exists() once (poll 1),
                # then the loop polls: poll 2, 3, 4 return False, poll 5 succeeds.
                return poll_count[0] >= 5
            return original_exists(path)

        with patch("pexpect.spawn", return_value=mock_child):
            with patch("time.sleep"):
                with patch.object(Path, "exists", mock_exists):
                    result = web_ui.add_provider("my-qwen", "qwen")[0]

        assert len(drain_calls) >= 2, (
            f"Expected read_nonblocking to be called during polling, got {len(drain_calls)} calls."
        )
        assert "✅" in result
        mock_api_client.create_account.assert_called_once_with("my-qwen", "qwen")


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

    def test_make_chat_message_prefers_prompt_line_for_terminal_output(self):
        """Collapsed terminal summaries should prioritize prompt/phase markers over file paths."""
        from chad.ui.gradio.web_ui import make_chat_message

        content = (
            "Mock Agent v1.0\n"
            "Working in: /tmp/work\n"
            "Prompt: Exploration\n\n"
            "> Analyzing task...\n"
            "Tool: Read BUGS.md\n"
            "✓ Read 15 lines\n"
            "Tool: Glob src/**/*.py\n"
            "✓ Found 8 files\n"
            + ("[tick] provider context stream\n" * 30)
        )

        message = make_chat_message("CODING AI", content)
        summary_part = message["content"].split("<details>")[0]

        assert "Prompt: Exploration" in summary_part
        assert "Tool: Read BUGS.md" not in summary_part

    def test_make_chat_message_strips_visible_escape_symbol_sequences(self):
        """Visible Unicode escape symbol sequences should be sanitized from chat output."""
        from chad.ui.gradio.web_ui import make_chat_message

        content = "␛[1;34mMock Agent v1.0␛[0m\n␛[36mPrompt: Exploration␛[0m"
        message = make_chat_message("CODING AI", content, collapsible=False)
        rendered = message["content"]

        assert "␛[" not in rendered
        assert "Mock Agent v1.0" in rendered
        assert "Prompt: Exploration" in rendered

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

    def test_extract_coding_summary_with_files_changed_list(self):
        """Extract files_changed as a list of paths."""
        from chad.util.prompts import extract_coding_summary

        content = '''```json
{
  "change_summary": "Fixed the bug",
  "files_changed": ["src/auth.py", "tests/test_auth.py"],
  "completion_status": "success"
}
```'''
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Fixed the bug"
        assert result.files_changed == ["src/auth.py", "tests/test_auth.py"]
        assert result.completion_status == "success"

    def test_extract_coding_summary_with_files_changed_info_only(self):
        """Extract files_changed as 'info_only' string."""
        from chad.util.prompts import extract_coding_summary

        content = '''```json
{
  "change_summary": "Explained the codebase structure",
  "files_changed": "info_only",
  "completion_status": "success"
}
```'''
        result = extract_coding_summary(content)
        assert result is not None
        assert result.change_summary == "Explained the codebase structure"
        assert result.files_changed == "info_only"
        assert result.completion_status == "success"

    def test_extract_coding_summary_with_completion_status_partial(self):
        """Extract completion_status with partial value."""
        from chad.util.prompts import extract_coding_summary

        content = '''```json
{
  "change_summary": "Started implementing feature",
  "files_changed": ["src/feature.py"],
  "completion_status": "partial"
}
```'''
        result = extract_coding_summary(content)
        assert result is not None
        assert result.completion_status == "partial"

    def test_get_summary_completion_prompt_returns_none_when_complete(self):
        """get_summary_completion_prompt returns None when all fields present."""
        from chad.util.prompts import extract_coding_summary, get_summary_completion_prompt

        content = '''```json
{
  "change_summary": "Fixed the bug",
  "files_changed": ["src/auth.py"],
  "completion_status": "success"
}
```'''
        result = extract_coding_summary(content)
        prompt = get_summary_completion_prompt(result)
        assert prompt is None

    def test_get_summary_completion_prompt_returns_prompt_when_no_summary(self):
        """get_summary_completion_prompt returns prompt when no summary extracted."""
        from chad.util.prompts import get_summary_completion_prompt

        prompt = get_summary_completion_prompt(None)
        assert prompt is not None
        assert "files_changed" in prompt
        assert "completion_status" in prompt

    def test_get_summary_completion_prompt_returns_prompt_when_missing_files_changed(self):
        """get_summary_completion_prompt returns prompt when files_changed missing."""
        from chad.util.prompts import extract_coding_summary, get_summary_completion_prompt

        content = '''```json
{"change_summary": "Fixed the bug"}
```'''
        result = extract_coding_summary(content)
        prompt = get_summary_completion_prompt(result)
        assert prompt is not None
        assert "files_changed" in prompt

    def test_get_summary_completion_prompt_returns_prompt_when_missing_completion_status(self):
        """get_summary_completion_prompt returns prompt when completion_status missing."""
        from chad.util.prompts import CodingSummary, get_summary_completion_prompt

        summary = CodingSummary(
            change_summary="Fixed the bug",
            files_changed=["src/auth.py"],
            completion_status=None,
        )
        prompt = get_summary_completion_prompt(summary)
        assert prompt is not None
        assert "completion_status" in prompt


class TestProgressUpdateExtraction:
    """Test progress update extraction with placeholder filtering.

    Supports two formats:
    1. Markdown (preferred) - avoids Codex CLI early exit with JSON
    2. JSON (legacy fallback)
    """

    # =========================================================================
    # Markdown format tests (preferred format)
    # =========================================================================

    def test_extract_progress_update_from_markdown_block(self):
        """Extract progress from markdown code block format."""
        from chad.util.prompts import extract_progress_update

        content = '''
Some thinking text...

```
**Progress:** Fixing authentication bug in login flow
**Location:** src/auth.py:45
**Next:** Writing tests
```
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Fixing authentication bug in login flow"
        assert result.location == "src/auth.py:45"
        assert result.next_step == "Writing tests"

    def test_extract_progress_update_markdown_without_code_block(self):
        """Extract progress from markdown without code block wrapper."""
        from chad.util.prompts import extract_progress_update

        content = '''
**Progress:** Found the config manager in settings module
**Location:** src/config.py:42
**Next:** Adding validation logic
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Found the config manager in settings module"
        assert result.location == "src/config.py:42"
        assert result.next_step == "Adding validation logic"

    def test_extract_progress_update_markdown_next_step_optional(self):
        """Markdown progress should work without Next field."""
        from chad.util.prompts import extract_progress_update

        content = '''
**Progress:** Investigating the issue
**Location:** src/app.py:10
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Investigating the issue"
        assert result.location == "src/app.py:10"
        assert result.next_step is None

    def test_extract_progress_update_markdown_filters_placeholder(self):
        """Markdown progress with placeholder text should be filtered."""
        from chad.util.prompts import extract_progress_update

        # The exact example from the prompt should be filtered
        content = '''
**Progress:** Adding retry logic to handle API rate limits
**Location:** src/api/client.py:45
**Next:** Writing tests to verify the retry behavior
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_markdown_case_insensitive(self):
        """Markdown field names should be case-insensitive."""
        from chad.util.prompts import extract_progress_update

        content = '''
**progress:** Found authentication handler
**LOCATION:** src/auth.py:45
**next:** Adding tests
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Found authentication handler"

    # =========================================================================
    # JSON format tests (legacy fallback)
    # =========================================================================

    def test_extract_progress_update_from_json_block(self):
        """Extract progress from code-fenced JSON (legacy format)."""
        from chad.util.prompts import extract_progress_update

        content = '''
Some thinking text...

```json
{"type": "progress", "summary": "Fixing authentication bug in login flow", "location": "src/auth.py:45"}
```
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Fixing authentication bug in login flow"
        assert result.location == "src/auth.py:45"

    def test_extract_progress_update_filters_placeholder_text(self):
        """Progress with placeholder text should be filtered out."""
        from chad.util.prompts import extract_progress_update

        # Test the exact placeholder from the old prompt
        content = '''
```json
{"type": "progress", "summary": "One line describing the issue/feature", "location": "src/file.py:123"}
```
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_filters_brief_description_placeholder(self):
        """Progress with 'brief description of' should be filtered."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "Brief description of what needs to change", "location": "src/foo.py:1"}
```
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_filters_example_path(self):
        """Progress containing example path patterns should be filtered."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "Working on src/file.py:123 changes", "location": "src/foo.py:1"}
```
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_allows_real_summaries(self):
        """Real task descriptions should pass through."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "Found authentication handler in login module", "location": "src/auth/login.py:45"}
```
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Found authentication handler in login module"

    def test_extract_progress_update_filters_prompt_example(self):
        """The exact example from the prompt should be filtered."""
        from chad.util.prompts import extract_progress_update

        # This is the exact example in the coding agent prompt - should be filtered
        content = '''
```json
{"type": "progress", "summary": "Adding retry logic to handle API rate limits", "location": "src/api/client.py:45"}
```
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_filters_empty_summary(self):
        """Empty summary should be filtered."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "", "location": "src/foo.py:1"}
```
'''
        result = extract_progress_update(content)
        assert result is None

    def test_extract_progress_update_handles_newlines_in_summary(self):
        """Progress JSON with accidental newlines should still parse."""
        from chad.util.prompts import extract_progress_update

        content = '''
{"type":"progress","summary":"Line 1 of summary
 Line 2 continuing","location":"src/app.py:10"}
'''
        result = extract_progress_update(content)
        assert result is not None
        assert "Line 1 of summary" in result.summary
        assert "Line 2 continuing" in result.summary
        assert result.location == "src/app.py:10"

    def test_extract_progress_update_includes_next_step(self):
        """Progress update should include next_step field."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "Found the config manager", "location": "src/config.py:42", "next_step": "Adding the new option to the config schema"}
```
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.summary == "Found the config manager"
        assert result.location == "src/config.py:42"
        assert result.next_step == "Adding the new option to the config schema"

    def test_extract_progress_update_next_step_optional(self):
        """Progress update should work without next_step field."""
        from chad.util.prompts import extract_progress_update

        content = '''
```json
{"type": "progress", "summary": "Investigating issue", "location": "src/app.py:10"}
```
'''
        result = extract_progress_update(content)
        assert result is not None
        assert result.next_step is None

    def test_extract_progress_update_filters_placeholder_next_step(self):
        """Progress with placeholder next_step should be filtered."""
        from chad.util.prompts import extract_progress_update

        # The example from the prompt should be filtered
        content = '''
```json
{"type": "progress", "summary": "Adding retry logic to handle API rate limits", "location": "src/api/client.py:45", "next_step": "Writing tests to verify the retry behavior"}
```
'''
        result = extract_progress_update(content)
        assert result is None


class TestMakeProgressMessage:
    """Test progress message formatting."""

    def test_make_progress_message_with_next_step(self):
        """Progress message should display next step."""
        from chad.ui.gradio.web_ui import make_progress_message
        from chad.util.prompts import ProgressUpdate

        progress = ProgressUpdate(
            summary="Found the config module",
            location="src/config.py:42",
            next_step="Adding the new option",
        )
        result = make_progress_message(progress)

        assert result["role"] == "assistant"
        content = result["content"]
        assert "Found the config module" in content
        assert "`src/config.py:42`" in content
        assert "**Next:** Adding the new option" in content

    def test_make_progress_message_without_next_step(self):
        """Progress message should work without next step."""
        from chad.ui.gradio.web_ui import make_progress_message
        from chad.util.prompts import ProgressUpdate

        progress = ProgressUpdate(
            summary="Investigating issue",
            location="src/app.py:10",
        )
        result = make_progress_message(progress)

        content = result["content"]
        assert "Investigating issue" in content
        assert "**Next:**" not in content


class TestLivePatchScrollPreservation:
    """Ensure live patching does not reset scroll by avoiding value updates."""

    @pytest.fixture
    def mock_api_client(self):
        from unittest.mock import Mock

        client = Mock()
        client.list_accounts.return_value = []
        client.list_role_assignments.return_value = {}
        client.list_providers.return_value = ["anthropic", "openai"]
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client):
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    def test_live_patch_returns_update_without_value(self, web_ui):
        """When live_patch is used, live_stream update should not include a value (prevents scroll reset)."""
        session = web_ui.create_session("scroll-test")
        session.has_initial_live_render = True  # Simulate after first render
        live_html = '<div data-live-id="live-stream-box"><div class="live-output-content">line 1</div></div>'

        display_stream, live_patch, flag = web_ui._compute_live_stream_updates(
            live_html, None, session, live_stream_id="live-stream-box", task_ended=False
        )
        assert display_stream is None  # use live_patch path
        assert live_patch is not None
        assert flag is True  # remains set

    def test_initial_render_sets_value(self, web_ui):
        """On first render (no initial live render yet), value should be populated so content appears."""
        session = web_ui.create_session("scroll-first")
        session.has_initial_live_render = False
        live_html = '<div data-live-id="live-stream-box"><div class="live-output-content">init</div></div>'

        display_stream, live_patch, flag = web_ui._compute_live_stream_updates(
            live_html, None, session, live_stream_id="live-stream-box", task_ended=False
        )
        assert display_stream == live_html
        assert live_patch is None
        assert flag is True  # should flip on first render

    def test_empty_content_resets_initial_flag(self, web_ui):
        """Clearing live_stream must reset has_initial so next content does a full render.

        Reproduces the race condition where:
        1. First render sets has_initial = True
        2. Empty content clears the DOM but has_initial stays True
        3. Next content tries JS patching on a cleared DOM and fails silently
        """
        session = web_ui.create_session("clear-reset")
        live_stream_id = "live-stream-box"
        live_html = (
            f'<div data-live-id="{live_stream_id}">'
            '<div class="live-output-content">content</div></div>'
        )

        # Step 1: initial render — sets has_initial = True
        _, _, flag = web_ui._compute_live_stream_updates(
            live_html, None, session, live_stream_id=live_stream_id,
            task_ended=False,
        )
        assert flag is True

        # Step 2: empty content arrives (e.g. status event clears live stream)
        session.has_initial_live_render = flag
        _, _, flag = web_ui._compute_live_stream_updates(
            "", None, session, live_stream_id=live_stream_id,
            task_ended=False,
        )
        assert flag is False, "has_initial must reset when content is cleared"

        # Step 3: new content arrives — must do a full Gradio render (not JS patch)
        session.has_initial_live_render = flag
        display_stream, live_patch, flag = web_ui._compute_live_stream_updates(
            live_html, None, session, live_stream_id=live_stream_id,
            task_ended=False,
        )
        assert display_stream == live_html, (
            "After clearing, next content must use full Gradio render"
        )
        assert live_patch is None
        assert flag is True


class TestPhaseMilestones:
    """Test phase milestone messages inserted into chat history."""

    def test_make_phase_milestone_with_metrics(self):
        """make_phase_milestone should format phase name, account, model, and metrics."""
        from chad.ui.gradio.web_ui import make_phase_milestone

        msg = make_phase_milestone("Exploration", "claude-1", "claude-sonnet-4-20250514", 85)
        assert msg["role"] == "user"
        assert "**Exploration:**" in msg["content"]
        assert "claude-1" in msg["content"]
        assert "claude-sonnet-4-20250514" in msg["content"]
        assert "Usage: 85%" in msg["content"]

    def test_make_phase_milestone_without_metrics(self):
        """make_phase_milestone should omit metrics when not provided."""
        from chad.ui.gradio.web_ui import make_phase_milestone

        msg = make_phase_milestone("Verification", "verifier", "gpt-4o")
        assert msg["role"] == "user"
        assert "**Verification:**" in msg["content"]
        assert "verifier" in msg["content"]
        assert "gpt-4o" in msg["content"]
        assert "Usage:" not in msg["content"]

    def test_make_phase_milestone_different_phases(self):
        """make_phase_milestone should work with various phase names."""
        from chad.ui.gradio.web_ui import make_phase_milestone

        for phase in ("Coding", "Re-coding", "Re-verification"):
            msg = make_phase_milestone(phase, "acct", "model-x", 50)
            assert f"**{phase}:**" in msg["content"]

    def test_make_phase_milestone_with_weekly_usage(self):
        """make_phase_milestone should include weekly usage when provided."""
        from chad.ui.gradio.web_ui import make_phase_milestone

        msg = make_phase_milestone("Coding", "claude-1", "claude-opus-4", 18, weekly_usage_pct=85)
        assert msg["role"] == "user"
        assert "Usage: 18%" in msg["content"]
        assert "Weekly: 85%" in msg["content"]

    def test_make_phase_milestone_weekly_without_session(self):
        """make_phase_milestone should show weekly even without session usage."""
        from chad.ui.gradio.web_ui import make_phase_milestone

        msg = make_phase_milestone("Coding", "claude-1", "claude-opus-4", weekly_usage_pct=100)
        assert "Weekly: 100%" in msg["content"]
        assert "Usage:" not in msg["content"].split("Weekly")[0]

    def test_idle_status_shows_ready_with_model(self):
        """Idle status should show Ready with coding model info (get_role_config_status still works)."""
        from chad.ui.gradio.provider_ui import ProviderUIManager

        class MockAPIClient:
            def list_accounts(self):
                return [MockAccount(name="claude-main", provider="anthropic", model="sonnet-4", role="CODING")]

        ui = ProviderUIManager(MockAPIClient())
        ready, status = ui.get_role_config_status()
        assert ready is True
        assert "Ready" in status
        assert "Coding" in status
        assert "claude-main" in status

    def test_format_usage_metrics_returns_percentages(self, monkeypatch):
        """_format_usage_metrics should return formatted usage percentage."""

        class MockAPIClient:
            def __init__(self):
                self._mock_usage = {"test-account": 0.75}

            def list_accounts(self):
                return [MockAccount(name="test-account", provider="mock", role="CODING")]

            def get_account(self, name):
                return MockAccount(name=name, provider="mock", role="CODING")

            def get_mock_remaining_usage(self, name):
                return self._mock_usage.get(name, 0.5)

        ui = ProviderUIManager(MockAPIClient())

        # Mock get_weekly_remaining_usage for this test
        monkeypatch.setattr(ui, "get_weekly_remaining_usage", lambda name: 0.85)

        metrics = ui._format_usage_metrics("test-account")
        assert "session usage: 75%" in metrics
        assert "weekly usage: 85%" in metrics

    def test_format_usage_metrics_returns_only_session_if_no_weekly(self, monkeypatch):
        """_format_usage_metrics should return only session usage if no weekly data."""
        class MockAPIClient:
            def __init__(self):
                self._mock_usage = {"test-account": 0.75}

            def list_accounts(self):
                return [MockAccount(name="test-account", provider="mock", role="CODING")]

            def get_account(self, name):
                return MockAccount(name=name, provider="mock", role="CODING")

            def get_mock_remaining_usage(self, name):
                return self._mock_usage.get(name, 0.5)

        ui = ProviderUIManager(MockAPIClient())

        # Mock get_weekly_remaining_usage to return None
        monkeypatch.setattr(ui, "get_weekly_remaining_usage", lambda name: None)

        metrics = ui._format_usage_metrics("test-account")
        assert "session usage: 75%" in metrics
        assert "weekly usage" not in metrics

    def test_ready_status_includes_usage_metrics(self):
        """Ready status should include usage metrics when available."""
        from chad.ui.gradio.provider_ui import ProviderUIManager

        class MockAPIClient:
            def __init__(self):
                self._mock_usage = {"test-account": 0.8}

            def list_accounts(self):
                return [MockAccount(name="test-account", provider="mock", role="CODING")]

            def get_account(self, name):
                return MockAccount(name=name, provider="mock", role="CODING")

            def get_mock_remaining_usage(self, name):
                return self._mock_usage.get(name, 0.5)

        ui = ProviderUIManager(MockAPIClient())
        ready, status = ui.get_role_config_status()
        assert ready is True
        assert "Ready" in status
        assert "session usage: 80%" in status


class TestMockProviderCardControls:
    """Tests for mock-specific provider card controls."""

    def test_provider_state_includes_duration_sliders_for_mock(self):
        """Mock provider cards expose usage and duration controls."""
        from chad.ui.gradio.provider_ui import ProviderUIManager

        class MockAPIClient:
            def list_accounts(self):
                return [MockAccount(name="mock-coding", provider="mock", role="CODING")]

            def get_mock_remaining_usage(self, name):
                return 0.4

            def get_mock_run_duration_seconds(self, name):
                return 60

        ui = ProviderUIManager(MockAPIClient(), dev_mode=True)
        state = ui.provider_state(card_slots=1)

        # Per-card tuple shape:
        # column, group, header, account_name, usage_box, usage_slider, duration_slider, delete_btn
        assert len(state) == 8
        assert state[3] == "mock-coding"
        assert state[4]["visible"] is False
        assert state[5]["visible"] is True and state[5]["value"] == 60
        assert state[6]["visible"] is True and state[6]["value"] == 60


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

    def test_parse_verification_response_handles_timeout_error(self):
        """Timeout errors should be converted to structured failure, not raise exception."""
        from chad.util.prompts import parse_verification_response

        # Test Qwen-style timeout error
        response = "Error: Qwen execution timed out (30 minutes)"
        passed, summary, issues = parse_verification_response(response)
        assert passed is False
        assert "timed out" in summary.lower()
        assert len(issues) == 1

    def test_parse_verification_response_handles_stall_error(self):
        """Stall errors should be converted to structured failure."""
        from chad.util.prompts import parse_verification_response

        response = "Error: Qwen execution stalled (no output for 1800s)"
        passed, summary, issues = parse_verification_response(response)
        assert passed is False
        assert "stalled" in summary.lower()

    def test_parse_verification_response_handles_cli_not_found(self):
        """CLI not found errors should be converted to structured failure."""
        from chad.util.prompts import parse_verification_response

        response = "Failed to run Qwen Code: command not found"
        passed, summary, issues = parse_verification_response(response)
        assert passed is False
        assert "not installed" in summary.lower()

    def test_parse_verification_response_handles_no_response(self):
        """No response errors should be converted to structured failure."""
        from chad.util.prompts import parse_verification_response

        response = "No response from Qwen Code"
        passed, summary, issues = parse_verification_response(response)
        assert passed is False
        assert "no response" in summary.lower()

    def test_parse_verification_response_still_parses_json(self):
        """Valid JSON should still be parsed normally."""
        from chad.util.prompts import parse_verification_response

        response = '```json\n{"passed": true, "summary": "All good!"}\n```'
        passed, summary, issues = parse_verification_response(response)
        assert passed is True
        assert summary == "All good!"
        assert issues == []

    def test_parse_verification_response_prefers_json_with_passed(self):
        """When multiple JSON blocks exist, parser should choose one with `passed`."""
        from chad.util.prompts import parse_verification_response

        response = """
        ```json
        {"change_summary": "Coding-style summary", "completion_status": "success"}
        ```

        ```json
        {"passed": false, "summary": "Needs fixes", "issues": ["Missing test"]}
        ```
        """
        passed, summary, issues = parse_verification_response(response)
        assert passed is False
        assert summary == "Needs fixes"
        assert issues == ["Missing test"]

    def test_run_verification_emits_parse_failure_status(self, monkeypatch, tmp_path):
        """Verification should surface parse failures while retrying."""
        from chad.ui.gradio.web_ui import ChadWebUI
        import chad.ui.gradio.web_ui as web_ui

        class DummyAPIClient:
            def get_account(self, name):
                return MockAccount(name=name, provider="anthropic")

        class DummyVerifier:
            def __init__(self):
                self._responses = iter([
                    '```json\n{"change_summary": "wrong schema"}\n```',
                    '```json\n{"change_summary": "still wrong"}\n```',
                ])

            def set_activity_callback(self, _callback):
                return None

            def start_session(self, _project_path, _system_prompt):
                return True

            def send_message(self, _message):
                return None

            def get_response(self, timeout=None):
                return next(self._responses, None)

            def stop_session(self):
                return None

        monkeypatch.setattr(web_ui, "create_provider", lambda *_args, **_kwargs: DummyVerifier())

        activities = []

        def on_activity(kind, detail):
            activities.append((kind, detail))

        ui = ChadWebUI(DummyAPIClient())
        verified, feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "verifier", on_activity=on_activity
        )

        assert verified is False
        assert feedback.startswith("Verification failed:")
        assert "Missing required field 'passed'" in feedback
        parse_status = [d for k, d in activities if k == "system" and "Verification parse failed" in d]
        assert parse_status, "Expected parse-failure status updates during retries"

    def test_run_verification_mock_provider_two_phase_fail_then_pass(self, monkeypatch, tmp_path):
        """Mock provider should fail first verification attempt, then pass after revision."""
        from chad.ui.gradio.web_ui import ChadWebUI
        from chad.util.config_manager import ConfigManager
        from chad.util.providers import MockProvider

        class DummyAPIClient:
            def get_account(self, name):
                return MockAccount(name=name, provider="mock")

        # Keep this test fast and isolated.
        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)
        monkeypatch.setenv("CHAD_CONFIG", str(tmp_path / "test_chad.conf"))
        config_mgr = ConfigManager()
        config_mgr.save_config({"mock_remaining_usage": {"mock-verifier": 1.0}})
        MockProvider._verification_counts.clear()

        ui = ChadWebUI(DummyAPIClient())
        first_verified, first_feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "mock-verifier"
        )
        second_verified, second_feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "mock-verifier"
        )

        assert first_verified is False
        assert "timestamp" in first_feedback.lower()
        assert second_verified is True
        assert "verified that bugs.md was updated correctly" in second_feedback.lower()

    def test_run_verification_recovers_after_one_parse_failure(self, monkeypatch, tmp_path):
        """Verifier should retry once and succeed when second response is valid."""
        from chad.ui.gradio.web_ui import ChadWebUI
        import chad.ui.gradio.web_ui as web_ui

        class DummyAPIClient:
            def get_account(self, name):
                return MockAccount(name=name, provider="anthropic")

        class DummyVerifier:
            def __init__(self, response):
                self._response = response

            def set_activity_callback(self, _callback):
                return None

            def start_session(self, _project_path, _system_prompt):
                return True

            def send_message(self, _message):
                return None

            def get_response(self, timeout=None):
                return self._response

            def stop_session(self):
                return None

        responses = [
            '```json\n{"change_summary": "wrong schema"}\n```',
            '```json\n{"passed": true, "summary": "Looks good"}\n```',
        ]
        create_calls = {"count": 0}

        def create_provider_stub(*_args, **_kwargs):
            idx = create_calls["count"]
            create_calls["count"] += 1
            return DummyVerifier(responses[min(idx, len(responses) - 1)])

        monkeypatch.setattr(web_ui, "create_provider", create_provider_stub)
        monkeypatch.setattr(web_ui, "check_verification_mentioned", lambda *_args, **_kwargs: True)

        ui = ChadWebUI(DummyAPIClient())
        verified, feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "verifier"
        )

        assert verified is True
        assert feedback == "Looks good"
        assert create_calls["count"] == 2

    def test_run_verification_retry_uses_fresh_provider_session(self, monkeypatch, tmp_path):
        """Retry should create a new verifier instance instead of reusing prior session state."""
        from chad.ui.gradio.web_ui import ChadWebUI
        import chad.ui.gradio.web_ui as web_ui

        class DummyAPIClient:
            def get_account(self, name):
                return MockAccount(name=name, provider="openai")

        class DummyVerifier:
            def __init__(self, response):
                self._response = response

            def set_activity_callback(self, _callback):
                return None

            def start_session(self, _project_path, _system_prompt):
                return True

            def send_message(self, _message):
                return None

            def get_response(self, timeout=None):
                return self._response

            def stop_session(self):
                return None

        responses = [
            '```json\n{"change_summary": "wrong schema"}\n```',
            '```json\n{"passed": true, "summary": "Looks good"}\n```',
        ]
        create_calls = {"count": 0}

        def create_provider_stub(*_args, **_kwargs):
            idx = create_calls["count"]
            create_calls["count"] += 1
            return DummyVerifier(responses[min(idx, len(responses) - 1)])

        monkeypatch.setattr(web_ui, "create_provider", create_provider_stub)
        monkeypatch.setattr(web_ui, "check_verification_mentioned", lambda *_args, **_kwargs: True)

        ui = ChadWebUI(DummyAPIClient())
        verified, feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "verifier"
        )

        assert verified is True
        assert feedback == "Looks good"
        assert create_calls["count"] == 2

    def test_run_verification_uses_two_phase_prompts(self, monkeypatch, tmp_path):
        """Verification should run exploration first, then a strict conclusion JSON prompt."""
        from chad.ui.gradio.web_ui import ChadWebUI
        import chad.ui.gradio.web_ui as web_ui

        class DummyAPIClient:
            def get_account(self, name):
                return MockAccount(name=name, provider="openai")

        sent_messages = []

        class DummyVerifier:
            def __init__(self):
                self._responses = iter(
                    [
                        "Exploration complete. I reviewed the changes.",
                        '```json\n{"passed": true, "summary": "Looks good"}\n```',
                    ]
                )

            def set_activity_callback(self, _callback):
                return None

            def start_session(self, _project_path, _system_prompt):
                return True

            def send_message(self, message):
                sent_messages.append(message)

            def get_response(self, timeout=None):
                return next(self._responses, None)

            def stop_session(self):
                return None

        monkeypatch.setattr(web_ui, "create_provider", lambda *_args, **_kwargs: DummyVerifier())
        monkeypatch.setattr(web_ui, "check_verification_mentioned", lambda *_args, **_kwargs: True)

        ui = ChadWebUI(DummyAPIClient())
        verified, feedback = ui._run_verification(
            str(tmp_path), "coding output", "Task", "verifier"
        )

        assert verified is True
        assert feedback == "Looks good"
        assert len(sent_messages) == 2


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


class TestPreferredVerificationModel:
    """Tests for preferred verification model functionality."""

    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client with verification model support."""
        client = Mock()
        client.list_accounts.return_value = [
            MockAccount(name="claude", provider="anthropic", role="CODING", model="claude-sonnet-4-20250514"),
            MockAccount(name="claude-verifier", provider="anthropic", role="VERIFICATION", model="default"),
        ]
        client.get_account.return_value = MockAccount(
            name="claude-verifier", provider="anthropic", model="default", reasoning="default"
        )
        client.list_role_assignments.return_value = {}
        client.get_preferred_verification_model.return_value = None
        client.set_preferred_verification_model.return_value = None
        return client

    @pytest.fixture
    def web_ui(self, mock_api_client):
        """Create a ChadWebUI instance."""
        from chad.ui.gradio.web_ui import ChadWebUI

        return ChadWebUI(mock_api_client)

    def test_build_verification_dropdown_uses_stored_model(self, web_ui, mock_api_client, monkeypatch):
        """Test that _build_verification_dropdown_state uses the stored preferred model."""
        # Mock model catalog to return model choices
        monkeypatch.setattr(
            web_ui.model_catalog, "get_models",
            lambda provider, acct=None: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "default"]
        )

        # Build dropdown state with a stored preferred model
        state = web_ui._build_verification_dropdown_state(
            coding_agent="claude",
            verification_agent="claude-verifier",
            coding_model_value="claude-sonnet-4-20250514",
            coding_reasoning_value="default",
            current_verification_model="claude-opus-4-20250514",
        )

        # The model value should be the stored preferred model
        assert state.model_value == "claude-opus-4-20250514"

    def test_build_verification_dropdown_falls_back_to_account_model(self, web_ui, mock_api_client, monkeypatch):
        """Test fallback to account model when no preferred model is stored."""
        # Set a specific model on the account
        mock_api_client.get_account.return_value = MockAccount(
            name="claude-verifier", provider="anthropic", model="claude-sonnet-4-20250514", reasoning="default"
        )
        monkeypatch.setattr(
            web_ui.model_catalog, "get_models",
            lambda provider, acct=None: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "default"]
        )

        # Build dropdown state without a preferred model
        state = web_ui._build_verification_dropdown_state(
            coding_agent="claude",
            verification_agent="claude-verifier",
            coding_model_value="claude-sonnet-4-20250514",
            coding_reasoning_value="default",
            current_verification_model=None,
        )

        # Should fall back to the account's stored model
        assert state.model_value == "claude-sonnet-4-20250514"

    def test_build_verification_dropdown_same_as_mock_filters_foreign_model(self, web_ui, mock_api_client):
        """SAME_AS_CODING should not retain a model that belongs to a different provider."""
        mock_account = MockAccount(
            name="testy",
            provider="mock",
            role="CODING",
            model="claude-sonnet-4-20250514",
            reasoning="default",
        )
        mock_api_client.list_accounts.return_value = [mock_account]
        mock_api_client.get_account.side_effect = lambda _: mock_account

        state = web_ui._build_verification_dropdown_state(
            coding_agent="testy",
            verification_agent=web_ui.SAME_AS_CODING,
            coding_model_value="claude-sonnet-4-20250514",
            coding_reasoning_value="default",
        )

        assert state.model_choices == ["default"]
        assert state.model_value == "default"

    def test_resolve_verification_preferences_persists_model(self, web_ui, mock_api_client):
        """Test that _resolve_verification_preferences persists the verification model to config."""
        # Call with an explicit verification model
        web_ui._resolve_verification_preferences(
            coding_account="claude",
            coding_model="claude-sonnet-4-20250514",
            coding_reasoning="default",
            verification_agent="claude-verifier",
            verification_model="claude-opus-4-20250514",
            verification_reasoning="default",
        )

        # Should persist to both account and global config
        mock_api_client.set_account_model.assert_called_once_with("claude-verifier", "claude-opus-4-20250514")
        mock_api_client.set_preferred_verification_model.assert_called_once_with("claude-opus-4-20250514")

    def test_resolve_verification_preferences_skips_same_as_coding(self, web_ui, mock_api_client):
        """Test that SAME_AS_CODING model value doesn't persist to config."""
        # Call with SAME_AS_CODING as the model value
        web_ui._resolve_verification_preferences(
            coding_account="claude",
            coding_model="claude-sonnet-4-20250514",
            coding_reasoning="default",
            verification_agent="claude-verifier",
            verification_model=web_ui.SAME_AS_CODING,
            verification_reasoning="default",
        )

        # Should not persist to account or global config
        mock_api_client.set_account_model.assert_not_called()
        mock_api_client.set_preferred_verification_model.assert_not_called()


class TestPromptPreviews:
    """Tests for build_prompt_previews which pre-fills prompts with project docs."""

    def test_previews_without_project_path(self):
        """Previews with no project path should have {task} placeholder and no docs."""
        from chad.util.prompts import build_prompt_previews

        previews = build_prompt_previews(None)
        assert "{task}" in previews.coding
        assert "{task}" in previews.verification
        assert "EXPLORATION_RESULT:" in previews.coding
        assert "Project Documentation" not in previews.coding

    def test_previews_with_project_path(self, tmp_path):
        """Previews with a project path should include docs and verification instructions."""
        from chad.util.prompts import build_prompt_previews

        # Create a project with an AGENTS.md doc
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# My Project\nDo things.")

        previews = build_prompt_previews(tmp_path)
        # Should still have {task} placeholder
        assert "{task}" in previews.coding
        # Should have project docs filled in (reference to AGENTS.md)
        assert "AGENTS.md" in previews.coding

    def test_previews_legacy_aliases(self):
        """Legacy exploration/implementation aliases should map to coding prompt."""
        from chad.util.prompts import build_prompt_previews

        previews = build_prompt_previews(None)
        assert previews.exploration == previews.coding
        assert previews.implementation == previews.coding

    def test_previews_verification_has_coding_output_placeholder(self):
        """Verification preview should keep {coding_output} as placeholder."""
        from chad.util.prompts import build_prompt_previews

        previews = build_prompt_previews(None)
        assert "{coding_output}" in previews.verification


class TestScreenshotUpload:
    """Tests for screenshot upload functionality."""

    def test_coding_prompt_includes_screenshot_paths(self, tmp_path):
        """build_prompt should include screenshot file paths when provided."""
        from chad.util.prompts import build_prompt

        # Create test screenshot files
        screenshot1 = tmp_path / "screenshot1.png"
        screenshot2 = tmp_path / "screenshot2.png"
        screenshot1.write_bytes(b"PNG mock data 1")
        screenshot2.write_bytes(b"PNG mock data 2")

        prompt = build_prompt(
            task="Fix the UI layout",
            screenshots=[str(screenshot1), str(screenshot2)],
        )

        # Screenshot paths should be included in the prompt
        assert str(screenshot1) in prompt
        assert str(screenshot2) in prompt
        # Should have a screenshots section
        assert "Screenshot" in prompt or "screenshot" in prompt

    def test_coding_prompt_works_without_screenshots(self):
        """build_prompt should work without screenshots."""
        from chad.util.prompts import build_prompt

        prompt = build_prompt(task="Simple task")

        assert "Simple task" in prompt
        # No screenshot references should be present
        assert "Screenshot" not in prompt

    def test_coding_prompt_includes_exploration_markers(self):
        """Coding prompt should include EXPLORATION_RESULT markers and completion JSON requirement."""
        from chad.util.prompts import build_prompt

        prompt = build_prompt(task="Do thing")
        # Should have EXPLORATION_RESULT marker instruction
        assert "EXPLORATION_RESULT:" in prompt
        # Completion JSON requirement should be present
        assert "change_summary" in prompt

    def test_progress_is_extracted_correctly(self):
        """Progress JSON should be correctly extracted from agent output.

        The progress update allows users to see what the agent found during
        initial exploration before it continues with the remaining steps.
        """
        from chad.util.prompts import extract_progress_update

        # Verify progress is correctly extracted from agent output
        agent_output = """
        I've explored the codebase and found the relevant files.
        ```json
        {"type": "progress", "summary": "Found main entry point", "location": "src/main.py:42"}
        ```
        """
        progress = extract_progress_update(agent_output)
        assert progress is not None
        assert progress.summary == "Found main entry point"
        assert progress.location == "src/main.py:42"

    def test_task_create_schema_accepts_screenshots(self):
        """TaskCreate schema should accept an optional screenshots field."""
        from chad.server.api.schemas.task import TaskCreate

        # Without screenshots
        task = TaskCreate(
            project_path="/tmp/project",
            task_description="Fix bug",
            coding_agent="claude",
        )
        assert task.screenshots is None or task.screenshots == []

        # With screenshots
        task_with_screenshots = TaskCreate(
            project_path="/tmp/project",
            task_description="Fix UI issue",
            coding_agent="claude",
            screenshots=["/tmp/screenshot1.png", "/tmp/screenshot2.png"],
        )
        assert task_with_screenshots.screenshots == ["/tmp/screenshot1.png", "/tmp/screenshot2.png"]

    def test_task_create_schema_accepts_override_prompts(self):
        """TaskCreate schema should accept optional override prompt fields."""
        from chad.server.api.schemas.task import TaskCreate

        # Without overrides
        task = TaskCreate(
            project_path="/tmp/project",
            task_description="Fix bug",
            coding_agent="claude",
        )
        assert task.override_exploration_prompt is None
        assert task.override_implementation_prompt is None

        # With overrides
        task_with_overrides = TaskCreate(
            project_path="/tmp/project",
            task_description="Fix bug",
            coding_agent="claude",
            override_exploration_prompt="Custom exploration",
            override_implementation_prompt="Custom implementation",
        )
        assert task_with_overrides.override_exploration_prompt == "Custom exploration"
        assert task_with_overrides.override_implementation_prompt == "Custom implementation"

    def test_api_client_start_task_accepts_screenshots(self):
        """APIClient.start_task should accept screenshots parameter."""
        from chad.ui.client.api_client import APIClient
        import httpx

        # Mock the HTTP client
        mock_response = Mock()
        mock_response.json.return_value = {
            "task_id": "test-task-123",
            "session_id": "test-session",
            "status": "running",
        }
        mock_response.raise_for_status = Mock()

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            client = APIClient("http://localhost:8000")
            client.start_task(
                session_id="test-session",
                project_path="/tmp/project",
                task_description="Fix UI",
                coding_agent="claude",
                screenshots=["/tmp/screenshot1.png"],
            )

            # Verify screenshots were included in the request
            call_args = mock_post.call_args
            request_data = call_args.kwargs.get("json", {})
            assert "screenshots" in request_data
            assert request_data["screenshots"] == ["/tmp/screenshot1.png"]


class TestFollowupEventLogging:
    """Test that send_followup() logs events to the session event log."""

    @pytest.fixture
    def web_ui(self):
        from chad.ui.gradio.web_ui import ChadWebUI
        client = Mock()
        claude_account = MockAccount(name="claude", provider="mock", role="CODING")
        client.list_accounts.return_value = [claude_account]
        client.get_account.return_value = claude_account
        client.get_verification_agent.return_value = None
        client.get_preferences.return_value = Mock(last_project_path=None, dark_mode=True, ui_mode="gradio")
        client.get_cleanup_settings.return_value = Mock(retention_days=7, auto_cleanup=True)
        client.get_max_verification_attempts.return_value = 3
        ui = ChadWebUI(client)
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/codex"))
        return ui

    def _setup_session(self, web_ui, session_id, git_repo, monkeypatch):
        """Set up a session with a MockProvider and EventLog."""
        from chad.util.event_log import EventLog
        git_mgr = GitWorktreeManager(git_repo)
        worktree_path, base_commit = git_mgr.create_worktree(session_id)

        session = web_ui.get_session(session_id)
        session.project_path = str(git_repo)
        session.worktree_path = worktree_path
        session.worktree_branch = git_mgr._branch_name(session_id)
        session.worktree_base_commit = base_commit
        session.active = True
        session.chat_history = [{"role": "user", "content": "**Task**\n\nInitial task"}]
        session.task_description = "Initial task"

        monkeypatch.setattr(MockProvider, "_simulate_delay", lambda *args, **kwargs: None)
        config = ModelConfig(provider="mock", model_name="default", account_name="claude")
        provider = MockProvider(config)
        provider._get_remaining_usage = lambda: 0.5
        provider._decrement_usage = lambda amount=None: None
        provider.start_session(str(worktree_path))
        session.provider = provider
        session.coding_account = "claude"
        session.config = config

        # Set up event log (remove any stale log from previous runs)
        session.event_log = EventLog(session_id)
        if session.event_log.log_path.exists():
            session.event_log.log_path.unlink()
            session.event_log = EventLog(session_id)
        session.event_log.start_turn()
        return session

    def _get_events(self, session):
        """Read all events from the session's event log."""
        import json
        events = []
        log_path = session.event_log.log_path
        if log_path.exists():
            for line in log_path.read_text().splitlines():
                if line.strip():
                    events.append(json.loads(line))
        return events

    def test_followup_logs_user_and_assistant_events(self, web_ui, git_repo, monkeypatch):
        """Follow-up should log UserMessageEvent and AssistantMessageEvent."""
        session = self._setup_session(web_ui, "event-log-basic", git_repo, monkeypatch)

        list(web_ui.send_followup(
            "event-log-basic",
            "Fix the button color",
            session.chat_history,
            coding_agent="claude",
            verification_agent=web_ui.VERIFICATION_NONE,
        ))

        events = self._get_events(session)
        event_types = [e["type"] for e in events]
        assert "user_message" in event_types, f"Expected user_message event, got: {event_types}"
        assert "assistant_message" in event_types, f"Expected assistant_message event, got: {event_types}"

        # Check sequence numbers are monotonically increasing
        sequences = [e["seq"] for e in events if e["type"] in ("user_message", "assistant_message")]
        assert sequences == sorted(sequences), f"Sequences not monotonic: {sequences}"

    def test_followup_logs_raw_message_not_resume_prompt(self, web_ui, git_repo, monkeypatch):
        """Logged UserMessageEvent should contain the raw user message, not modified versions."""
        session = self._setup_session(web_ui, "event-log-raw", git_repo, monkeypatch)

        raw_message = "Please change the font size"
        list(web_ui.send_followup(
            "event-log-raw",
            raw_message,
            session.chat_history,
            coding_agent="claude",
            verification_agent=web_ui.VERIFICATION_NONE,
            screenshots=["/tmp/fake_screenshot.png"],
        ))

        events = self._get_events(session)
        user_events = [e for e in events if e["type"] == "user_message"]
        assert len(user_events) >= 1
        # The logged content should be the raw message, not the screenshot-appended version
        assert user_events[0]["content"] == raw_message

    def test_followup_with_verification_logs_verification_events(self, web_ui, git_repo, monkeypatch):
        """Follow-up with verification should log VerificationAttemptEvent."""
        session = self._setup_session(web_ui, "event-log-verify", git_repo, monkeypatch)

        # Mock _run_verification to return success
        monkeypatch.setattr(
            web_ui, "_run_verification",
            lambda *args, **kwargs: (True, "All checks passed"),
        )

        list(web_ui.send_followup(
            "event-log-verify",
            "Add a test",
            session.chat_history,
            coding_agent="claude",
            verification_agent="claude",
        ))

        events = self._get_events(session)
        event_types = [e["type"] for e in events]
        assert "user_message" in event_types
        assert "assistant_message" in event_types
        assert "verification_attempt" in event_types

        verify_events = [e for e in events if e["type"] == "verification_attempt"]
        assert verify_events[0]["passed"] is True

    def test_followup_verification_revision_logs_all_events(self, web_ui, git_repo, monkeypatch):
        """Verification fail + revision should log the full event sequence."""
        session = self._setup_session(web_ui, "event-log-revision", git_repo, monkeypatch)

        # First call fails verification, second passes
        verification_calls = {"count": 0}

        def mock_verification(*args, **kwargs):
            verification_calls["count"] += 1
            if verification_calls["count"] == 1:
                return (False, "Tests are failing")
            return (True, "All good now")

        monkeypatch.setattr(web_ui, "_run_verification", mock_verification)

        list(web_ui.send_followup(
            "event-log-revision",
            "Fix the layout",
            session.chat_history,
            coding_agent="claude",
            verification_agent="claude",
        ))

        events = self._get_events(session)
        event_types = [e["type"] for e in events]

        # Should have: user_message (followup), assistant_message (coding),
        # verification_attempt (fail), user_message (revision), assistant_message (revision),
        # verification_attempt (pass)
        assert event_types.count("user_message") >= 2, f"Expected >=2 user_message events, got: {event_types}"
        assert event_types.count("assistant_message") >= 2, f"Expected >=2 assistant_message events, got: {event_types}"
        assert event_types.count("verification_attempt") == 2, f"Expected 2 verification_attempt events, got: {event_types}"

        # First verification should be failed, second should pass
        verify_events = [e for e in events if e["type"] == "verification_attempt"]
        assert verify_events[0]["passed"] is False
        assert verify_events[1]["passed"] is True

    def test_followup_verification_uses_followup_message_as_task_description(
        self, web_ui, git_repo, monkeypatch
    ):
        """Follow-up after merge (empty task_description) should use the follow-up message for verification."""
        session = self._setup_session(web_ui, "event-log-empty-desc", git_repo, monkeypatch)

        # Simulate post-merge state: task_description cleared
        session.task_description = ""

        # Track what task_description is passed to _run_verification
        captured_args = {}

        def mock_verification(*args, **kwargs):
            # _run_verification(path, coding_output, task_description, ...)
            captured_args["task_description"] = args[2] if len(args) > 2 else kwargs.get("task_description")
            return (True, "All checks passed")

        monkeypatch.setattr(web_ui, "_run_verification", mock_verification)

        followup_msg = "Refactor the database layer"
        list(web_ui.send_followup(
            "event-log-empty-desc",
            followup_msg,
            session.chat_history,
            coding_agent="claude",
            verification_agent="claude",
        ))

        # Verification must receive the follow-up message, not an empty string
        assert captured_args.get("task_description") == followup_msg, (
            f"Expected task_description='{followup_msg}', got '{captured_args.get('task_description')}'"
        )
        # session.task_description should also be updated
        assert session.task_description == followup_msg

    def test_followup_after_session_end_displays_raw_message_not_resume_prompt(
        self, web_ui, git_repo, monkeypatch
    ):
        """When session restarts after completion, chat should show the raw user
        message, not the internal <previous_session> resume prompt XML."""
        session = self._setup_session(web_ui, "event-log-display", git_repo, monkeypatch)

        # Log some initial conversation so build_resume_prompt has content
        from chad.util.event_log import UserMessageEvent, AssistantMessageEvent
        session.event_log.log(UserMessageEvent(content="Initial task"))
        session.event_log.log(
            AssistantMessageEvent(blocks=[{"kind": "text", "content": "Done with initial task."}])
        )

        # Simulate session ended (e.g. rate limit or task completed)
        session.active = False
        session.provider = None

        followup_msg = "Now fix the tests"
        list(web_ui.send_followup(
            "event-log-display",
            followup_msg,
            session.chat_history,
            coding_agent="claude",
            verification_agent=web_ui.VERIFICATION_NONE,
        ))

        # Find the user message that was added to chat history
        user_messages = [
            msg for msg in session.chat_history
            if msg.get("role") == "user" and "Follow-up" in msg.get("content", "")
        ]
        assert user_messages, "Expected a follow-up user message in chat history"

        last_followup = user_messages[-1]["content"]
        # The raw message should be displayed, not the resume prompt
        assert followup_msg in last_followup, (
            f"Expected raw message '{followup_msg}' in display, got: {last_followup[:200]}"
        )
        assert "<previous_session>" not in last_followup, (
            f"Resume prompt XML leaked into chat display: {last_followup[:200]}"
        )

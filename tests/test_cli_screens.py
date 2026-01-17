"""Tests for Chad simple CLI."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from chad.ui.client.api_client import Preferences, CleanupSettings, Account


@dataclass
class MockAccount:
    """Mock account for testing."""
    name: str
    provider: str
    model: str | None = None
    reasoning: str | None = None
    role: str | None = None
    ready: bool = True


class TestCLIHelpers:
    """Tests for CLI helper functions."""

    def test_select_from_list_returns_default_on_enter(self, monkeypatch):
        """select_from_list returns default when Enter is pressed."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda *args: "")
        options = [("Option A", "a"), ("Option B", "b"), ("Option C", "c")]

        result = select_from_list("Choose:", options, default_idx=1)
        assert result == "b"

    def test_select_from_list_returns_selected_value(self, monkeypatch):
        """select_from_list returns the value for the selected option."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda *args: "2")
        options = [("Option A", "a"), ("Option B", "b"), ("Option C", "c")]

        result = select_from_list("Choose:", options, default_idx=0)
        assert result == "b"

    def test_select_from_list_returns_none_on_quit(self, monkeypatch):
        """select_from_list returns None when 'q' is entered."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda *args: "q")
        options = [("Option A", "a"), ("Option B", "b")]

        result = select_from_list("Choose:", options, default_idx=0)
        assert result is None

    def test_select_from_list_empty_options(self, capsys):
        """select_from_list handles empty options list."""
        from chad.ui.cli.app import select_from_list

        result = select_from_list("Choose:", [], default_idx=0)
        assert result is None

        captured = capsys.readouterr()
        assert "No options available" in captured.out

    def test_launch_cli_ui_connects_to_server(self, monkeypatch):
        """launch_cli_ui connects to API server."""
        from chad.ui.cli.app import launch_cli_ui

        mock_client = MagicMock()
        mock_client.get_status.return_value = {"version": "0.1.0", "status": "healthy"}
        mock_client.list_accounts.return_value = []

        with patch("chad.ui.cli.app.APIClient", return_value=mock_client):
            with patch("chad.ui.cli.app.run_cli"):
                launch_cli_ui(api_base_url="http://localhost:8000")

        mock_client.get_status.assert_called_once()


class TestCLIFlow:
    """Tests for CLI menu flow."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock API client."""
        client = MagicMock()
        client.get_status.return_value = {"version": "0.1.0", "status": "healthy"}
        client.list_accounts.return_value = [
            MockAccount(name="test-agent", provider="mock", role="CODING")
        ]
        client.get_preferences.return_value = Preferences(
            last_project_path="",
            dark_mode=True,
            ui_mode="cli",
        )
        client.get_cleanup_settings.return_value = CleanupSettings(
            retention_days=7,
            auto_cleanup=True,
        )
        return client

    def test_cli_exits_on_q(self, mock_client, monkeypatch, capsys):
        """CLI exits when 'q' is entered."""
        from chad.ui.cli.app import run_cli

        inputs = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(mock_client)
        # Should exit without error

    def test_cli_shows_no_accounts_message(self, mock_client, monkeypatch, capsys):
        """CLI shows message when no accounts are configured."""
        from chad.ui.cli.app import run_cli

        mock_client.list_accounts.return_value = []

        inputs = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(mock_client)

        captured = capsys.readouterr()
        assert "No accounts configured" in captured.out
        assert "Press [s] to open settings" in captured.out

    def test_cli_change_project_path(self, mock_client, monkeypatch, tmp_path):
        """CLI can change project path."""
        from chad.ui.cli.app import run_cli

        test_path = str(tmp_path)
        inputs = iter(["2", test_path, "", "q"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(mock_client)

        # Check that preferences were updated via API
        mock_client.set_preferences.assert_called_with(last_project_path=test_path)

    def test_cli_change_agent(self, mock_client, monkeypatch):
        """CLI can change coding agent."""
        from chad.ui.cli.app import run_cli

        mock_client.list_accounts.return_value = [
            MockAccount(name="agent-1", provider="mock", role="CODING"),
            MockAccount(name="agent-2", provider="anthropic"),
        ]

        # Select option 3 (change agent), then option 2 (agent-2), then quit
        inputs = iter(["3", "2", "", "q"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(mock_client)

        # Check that role was updated via API
        mock_client.set_account_role.assert_called_with("agent-2", "CODING")


class TestCLITaskFlow:
    """Integration tests for CLI task execution flow using API streaming."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock API client."""
        client = MagicMock()
        client.get_status.return_value = {"version": "0.1.0", "status": "healthy"}
        client.base_url = "http://localhost:8000"
        client.list_accounts.return_value = [
            MockAccount(name="test-agent", provider="mock", role="CODING")
        ]
        client.get_preferences.return_value = Preferences(
            last_project_path="",
            dark_mode=True,
            ui_mode="cli",
        )
        client.get_cleanup_settings.return_value = CleanupSettings(
            retention_days=7,
            auto_cleanup=True,
        )
        return client

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a temporary git repository."""
        import subprocess

        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True)

        # Create initial commit
        (repo_path / "README.md").write_text("# Test Project")
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True)

        return repo_path

    def test_cli_task_creates_session_and_runs(self, mock_client, git_repo, monkeypatch):
        """CLI task flow creates session and runs task via API."""
        from chad.ui.cli.app import run_cli
        from chad.ui.client.api_client import Session, WorktreeStatus
        from datetime import datetime

        mock_client.get_preferences.return_value = Preferences(
            last_project_path=str(git_repo),
            dark_mode=True,
            ui_mode="cli",
        )

        # Mock session creation
        mock_session = Session(
            id="test-session-123",
            name="Test task",
            project_path=str(git_repo),
            active=True,
            has_worktree=False,
            has_changes=False,
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        mock_client.create_session.return_value = mock_session

        # Mock worktree status (no changes)
        mock_client.get_worktree_status.return_value = WorktreeStatus(
            exists=True,
            path=str(git_repo / ".chad-worktrees" / "test"),
            branch="chad/test-session-123",
            base_commit="abc123",
            has_changes=False,
        )

        # Simulate: start task (1), enter task description, empty line, then quit
        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish description
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        # Mock run_task_with_streaming to avoid actual streaming
        # Note: start_task is called INSIDE run_task_with_streaming, so patching
        # the function means start_task won't be called
        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0):
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(mock_client)

        # Session should have been created via API
        mock_client.create_session.assert_called_once()

    def test_cli_task_handles_worktree_changes(self, mock_client, git_repo, monkeypatch):
        """CLI shows options when agent makes changes."""
        from chad.ui.cli.app import run_cli
        from chad.ui.client.api_client import Session, WorktreeStatus, DiffSummary
        from datetime import datetime

        mock_client.get_preferences.return_value = Preferences(
            last_project_path=str(git_repo),
            dark_mode=True,
            ui_mode="cli",
        )

        mock_session = Session(
            id="test-session-123",
            name="Test task",
            project_path=str(git_repo),
            active=True,
            has_worktree=True,
            has_changes=True,
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        mock_client.create_session.return_value = mock_session

        # Mock worktree with changes
        mock_client.get_worktree_status.return_value = WorktreeStatus(
            exists=True,
            path=str(git_repo / ".chad-worktrees" / "test"),
            branch="chad/test-session-123",
            base_commit="abc123",
            has_changes=True,
        )
        mock_client.get_diff_summary.return_value = DiffSummary(
            summary="1 file changed",
            files_changed=1,
            insertions=5,
            deletions=2,
        )

        # Simulate: start task, description, keep worktree, continue, quit
        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish
            "k",           # Keep worktree
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0):
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(mock_client)

        # Should NOT have called delete_session when keeping worktree
        mock_client.delete_session.assert_not_called()

    def test_cli_task_discards_changes(self, mock_client, git_repo, monkeypatch, capsys):
        """CLI discards worktree when user chooses 'x'."""
        from chad.ui.cli.app import run_cli
        from chad.ui.client.api_client import Session, WorktreeStatus, DiffSummary
        from datetime import datetime

        mock_client.get_preferences.return_value = Preferences(
            last_project_path=str(git_repo),
            dark_mode=True,
            ui_mode="cli",
        )

        mock_session = Session(
            id="test-session-123",
            name="Test task",
            project_path=str(git_repo),
            active=True,
            has_worktree=True,
            has_changes=True,
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        mock_client.create_session.return_value = mock_session

        mock_client.get_worktree_status.return_value = WorktreeStatus(
            exists=True,
            path=str(git_repo / ".chad-worktrees" / "test"),
            branch="chad/test-session-123",
            base_commit="abc123",
            has_changes=True,
        )
        mock_client.get_diff_summary.return_value = DiffSummary(
            summary="1 file changed",
            files_changed=1,
            insertions=5,
            deletions=2,
        )

        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish
            "x",           # Discard changes
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0):
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(mock_client)

        # Should have called reset and delete worktree via API
        mock_client.reset_worktree.assert_called_once_with("test-session-123")
        mock_client.delete_worktree.assert_called_once_with("test-session-123")

        captured = capsys.readouterr()
        assert "discarded" in captured.out.lower()

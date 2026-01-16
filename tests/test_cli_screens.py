"""Tests for Chad simple CLI."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import StringIO


class TestCLIHelpers:
    """Tests for CLI helper functions."""

    @pytest.fixture
    def config_manager(self, tmp_path, monkeypatch):
        """Create a ConfigManager with isolated config file."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        cm.save_config({
            "password_hash": cm.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {},
            "ui_mode": "cli",
        })
        return cm

    def test_select_from_list_returns_default_on_enter(self, monkeypatch):
        """select_from_list returns default when Enter is pressed."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda _: "")
        options = [("Option A", "a"), ("Option B", "b"), ("Option C", "c")]

        result = select_from_list("Choose:", options, default_idx=1)
        assert result == "b"

    def test_select_from_list_returns_selected_value(self, monkeypatch):
        """select_from_list returns the value for the selected option."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda _: "2")
        options = [("Option A", "a"), ("Option B", "b"), ("Option C", "c")]

        result = select_from_list("Choose:", options, default_idx=0)
        assert result == "b"

    def test_select_from_list_returns_none_on_quit(self, monkeypatch):
        """select_from_list returns None when 'q' is entered."""
        from chad.ui.cli.app import select_from_list

        monkeypatch.setattr("builtins.input", lambda _: "q")
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

    def test_launch_cli_ui_creates_config_manager_if_none(self, tmp_path, monkeypatch):
        """launch_cli_ui creates ConfigManager if not provided."""
        from chad.ui.cli.app import launch_cli_ui

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        # Mock run_cli to avoid full execution
        with patch("chad.ui.cli.app.run_cli") as mock_run:
            launch_cli_ui(password="test")
            mock_run.assert_called_once()
            # First arg should be a ConfigManager instance
            args, _ = mock_run.call_args
            assert args[0] is not None


class TestCLIFlow:
    """Tests for CLI menu flow."""

    @pytest.fixture
    def config_manager(self, tmp_path, monkeypatch):
        """Create a ConfigManager with isolated config file."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        cm.save_config({
            "password_hash": cm.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {},
            "ui_mode": "cli",
        })
        return cm

    def test_cli_exits_on_q(self, config_manager, monkeypatch, capsys):
        """CLI exits when 'q' is entered."""
        from chad.ui.cli.app import run_cli

        # Setup an account so we get to the menu
        config_manager.store_account("test-agent", "mock", "key", "test")
        config_manager.assign_role("test-agent", "CODING")

        inputs = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)  # Mock clear_screen

        run_cli(config_manager, "test")
        # Should exit without error

    def test_cli_shows_no_accounts_message(self, config_manager, monkeypatch, capsys):
        """CLI shows message when no accounts are configured."""
        from chad.ui.cli.app import run_cli

        inputs = iter([""])  # Press Enter to exit
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(config_manager, "test")

        captured = capsys.readouterr()
        assert "No accounts configured" in captured.out

    def test_cli_change_project_path(self, config_manager, monkeypatch, tmp_path):
        """CLI can change project path."""
        from chad.ui.cli.app import run_cli

        config_manager.store_account("test-agent", "mock", "key", "test")
        config_manager.assign_role("test-agent", "CODING")

        test_path = str(tmp_path)
        inputs = iter(["2", test_path, "", "q"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(config_manager, "test")

        # Check that preferences were saved
        prefs = config_manager.load_preferences()
        assert prefs is not None
        assert prefs.get("project_path") == test_path

    def test_cli_change_agent(self, config_manager, monkeypatch):
        """CLI can change coding agent."""
        from chad.ui.cli.app import run_cli

        config_manager.store_account("agent-1", "mock", "key", "test")
        config_manager.store_account("agent-2", "anthropic", "key", "test")
        config_manager.assign_role("agent-1", "CODING")

        # Select option 3 (change agent), then option 2 (agent-2), then quit
        inputs = iter(["3", "2", "", "q"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_cli(config_manager, "test")

        # Check that agent was changed
        assert config_manager.get_role_assignment("CODING") == "agent-2"


class TestCLITaskFlow:
    """Integration tests for CLI task execution flow."""

    @pytest.fixture
    def config_manager(self, tmp_path, monkeypatch):
        """Create a ConfigManager with isolated config file."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        cm.save_config({
            "password_hash": cm.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {},
            "ui_mode": "cli",
        })
        return cm

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

    def test_cli_task_creates_and_cleans_worktree(self, config_manager, git_repo, monkeypatch):
        """CLI task flow creates worktree, runs agent, and cleans up."""
        from chad.ui.cli.app import run_cli

        config_manager.store_account("test-agent", "mock", "key", "test")
        config_manager.assign_role("test-agent", "CODING")
        config_manager.save_preferences(str(git_repo))

        # Simulate: start task (1), enter task description, press enter twice, then quit
        # The mock agent just echoes, so no changes will be made and worktree gets cleaned up
        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish description
            "",            # Press Enter to continue after agent exits
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        # Mock run_agent_pty to avoid actual PTY operations
        with patch("chad.ui.cli.app.run_agent_pty", return_value=0):
            run_cli(config_manager, "test")

        # Worktree should be cleaned up (no changes made by mock agent)
        worktree_base = git_repo / ".chad-worktrees"
        if worktree_base.exists():
            worktrees = list(worktree_base.iterdir())
            assert len(worktrees) == 0, f"Worktree not cleaned up: {worktrees}"

    def test_cli_task_keeps_worktree_on_request(self, config_manager, git_repo, monkeypatch):
        """CLI keeps worktree when user chooses 'k'."""
        from chad.ui.cli.app import run_cli
        import subprocess

        config_manager.store_account("test-agent", "mock", "key", "test")
        config_manager.assign_role("test-agent", "CODING")
        config_manager.save_preferences(str(git_repo))

        # Track the worktree path created
        created_worktree = None

        def mock_run_agent_pty(cmd, cwd, env, initial_input=None):
            nonlocal created_worktree
            created_worktree = cwd
            # Simulate agent making changes
            (cwd / "new_file.txt").write_text("New content")
            subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
            return 0

        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish
            "k",           # Keep worktree
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_agent_pty", side_effect=mock_run_agent_pty):
            run_cli(config_manager, "test")

        # Worktree should still exist
        assert created_worktree is not None
        assert created_worktree.exists(), "Worktree should be kept"

    def test_cli_task_discards_worktree(self, config_manager, git_repo, monkeypatch):
        """CLI discards worktree when user chooses 'x'."""
        from chad.ui.cli.app import run_cli
        import subprocess

        config_manager.store_account("test-agent", "mock", "key", "test")
        config_manager.assign_role("test-agent", "CODING")
        config_manager.save_preferences(str(git_repo))

        created_worktree = None

        def mock_run_agent_pty(cmd, cwd, env, initial_input=None):
            nonlocal created_worktree
            created_worktree = cwd
            # Simulate agent making changes
            (cwd / "new_file.txt").write_text("New content")
            subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
            return 0

        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # Empty line to finish
            "x",           # Discard changes
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_agent_pty", side_effect=mock_run_agent_pty):
            run_cli(config_manager, "test")

        # Worktree should be removed
        assert created_worktree is not None
        assert not created_worktree.exists(), "Worktree should be discarded"

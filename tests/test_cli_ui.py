"""Tests for Chad CLI UI components.

Note: UI mode tests are in test_cli_integration.py::TestUIModeSwitching.
Note: PTY runner command tests are in test_cli_integration.py::TestProviderCommandGeneration.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class MockAccount:
    """Mock account for testing."""
    name: str
    provider: str
    model: str | None = None
    reasoning: str | None = None
    role: str | None = None
    ready: bool = True


class TestCLIImports:
    """Tests for CLI package imports."""

    def test_import_launch_cli_ui(self):
        """Can import launch_cli_ui from chad.ui.cli."""
        from chad.ui.cli import launch_cli_ui
        assert callable(launch_cli_ui)


class TestMainVersionFlag:
    """Tests for the CLI --version flag."""

    def test_version_flag_prints_version(self, capsys, monkeypatch):
        import chad.__main__ as chad_main
        from chad import __version__

        monkeypatch.setattr(chad_main, "_start_parent_watchdog", lambda: None)
        monkeypatch.setattr(chad_main, "_check_chad_import_path", lambda: None)
        monkeypatch.setattr(chad_main.sys, "argv", ["chad", "--version"])

        with pytest.raises(SystemExit):
            chad_main.main()

        captured = capsys.readouterr()
        assert __version__ in captured.out


def _stub_installer(monkeypatch, result=None):
    """Stub CLI installation so login tests never shell out to npm/pip/shell."""
    from chad.util.installer import AIToolInstaller

    def fake_ensure_tool(self, tool_key):
        if result is not None:
            return result
        return True, f"/fake/bin/{tool_key}"

    monkeypatch.setattr(AIToolInstaller, "ensure_tool", fake_ensure_tool)


class TestProviderOauthFlow:
    """Tests for CLI provider auth behavior (delegates to chad.util.provider_login)."""

    def test_kimi_no_cli_reports_not_found(self, monkeypatch, tmp_path):
        """Kimi add should fail when the CLI cannot be installed."""
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch, (False, "Kimi CLI not found. Install with: pip install kimi-cli"))
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is False
        assert "not found" in message.lower()
        assert "pip install kimi-cli" in message

    def test_kimi_accepts_complete_credentials(self, monkeypatch, tmp_path):
        """Kimi add should succeed when creds AND populated config exist."""
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch)
        # Create isolated credentials file AND populated config
        kimi_dir = tmp_path / ".chad" / "kimi-homes" / "my-kimi" / ".kimi"
        creds_dir = kimi_dir / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
        (kimi_dir / "config.toml").write_text(
            'default_model = "kimi-code/kimi-k2.5"\n\n'
            '[models."kimi-code/kimi-k2.5"]\nprovider = "managed:kimi-code"\n'
        )
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is True
        assert "Already logged in" in message

    def test_kimi_repairs_partial_login(self, monkeypatch, tmp_path):
        """Kimi add should write default config when creds exist but config is empty."""
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch)
        # Create credentials but empty config (partial login)
        kimi_dir = tmp_path / ".chad" / "kimi-homes" / "my-kimi" / ".kimi"
        creds_dir = kimi_dir / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
        (kimi_dir / "config.toml").write_text('default_model = ""\n\n[models]\n[providers]\n')
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        # Should succeed — config written directly, no re-login needed
        assert success is True
        config_text = (kimi_dir / "config.toml").read_text()
        assert "[models." in config_text
        assert "kimi-k2.5" in config_text

    def test_kimi_accepts_nonzero_login_if_creds_were_written(self, monkeypatch, tmp_path):
        """Kimi add should succeed if login writes credentials even when process exits non-zero."""
        from pathlib import Path
        from chad.ui.cli.app import _run_provider_oauth

        class Completed:
            returncode = 1

        def fake_run(cmd, env=None, timeout=None, **kwargs):
            # Simulate successful OAuth followed by model-fetch failure.
            kimi_home = Path(env["HOME"])
            creds_dir = kimi_home / ".kimi" / "credentials"
            creds_dir.mkdir(parents=True, exist_ok=True)
            (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
            return Completed()

        _stub_installer(monkeypatch)
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.util.provider_login.subprocess.run", fake_run)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is True
        config_text = (tmp_path / ".chad" / "kimi-homes" / "my-kimi" / ".kimi" / "config.toml").read_text()
        assert "[models." in config_text

    def test_mistral_prompts_for_api_key(self, monkeypatch, tmp_path):
        """Mistral auth should prompt for an API key and write it to the account's vibe home."""
        import webbrowser
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch)
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr(webbrowser, "open", lambda *_a, **_k: False)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt: "sk-test-key-123")

        success, message = _run_provider_oauth("mistral", "my-vibe")

        assert success is True
        assert "Login successful" in message
        env_file = tmp_path / ".chad" / "vibe-homes" / "my-vibe" / ".env"
        assert env_file.exists()
        assert "sk-test-key-123" in env_file.read_text()

    def test_mistral_empty_api_key_fails(self, monkeypatch, tmp_path):
        """Mistral auth should fail when user provides an empty API key."""
        import webbrowser
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch)
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr(webbrowser, "open", lambda *_a, **_k: False)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt: "")

        success, message = _run_provider_oauth("mistral", "my-vibe")

        assert success is False
        assert "API key" in message


class TestConnectionParsing:
    """Tests for _parse_connection_input used by CLI disconnected menu."""

    def test_direct_http_url(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("http://localhost:8000")
        assert url == "http://localhost:8000"
        assert token is None

    def test_direct_https_url(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("https://my.server.com")
        assert url == "https://my.server.com"
        assert token is None

    def test_url_trailing_slash_stripped(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("http://localhost:8000/")
        assert url == "http://localhost:8000"
        assert token is None

    def test_host_port_shorthand(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("localhost:8000")
        assert url == "http://localhost:8000"
        assert token is None

    def test_ip_port(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("192.168.1.5:3000")
        assert url == "http://192.168.1.5:3000"
        assert token is None

    def test_cf_tunnel_with_token(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("my-subdomain:secrettoken")
        assert url == "https://my-subdomain.trycloudflare.com"
        assert token == "secrettoken"

    def test_cf_tunnel_bare_subdomain(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("my-tunnel")
        assert url == "https://my-tunnel.trycloudflare.com"
        assert token is None

    def test_empty_string(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("")
        assert url == ""
        assert token is None

    def test_whitespace_only(self):
        from chad.ui.cli.app import _parse_connection_input
        url, token = _parse_connection_input("   ")
        assert url == ""
        assert token is None


class TestDisconnectedCLI:
    """Tests for graceful CLI behavior when server is unreachable."""

    def test_launch_cli_ui_shows_menu_instead_of_exit(self, monkeypatch):
        """launch_cli_ui should show disconnected menu instead of sys.exit(1)."""
        from chad.ui.cli.app import launch_cli_ui

        # Mock APIClient to always fail
        class FakeClient:
            def __init__(self, **kwargs):
                self.base_url = kwargs.get("base_url", "")

            def get_status(self):
                raise ConnectionError("refused")

            def close(self):
                pass

        monkeypatch.setattr("chad.ui.cli.app.APIClient", FakeClient)

        # User presses "q" to quit immediately
        inputs = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        # Should NOT raise SystemExit
        launch_cli_ui(api_base_url="http://localhost:99999")

    def test_disconnected_menu_retry_connects(self, monkeypatch):
        """Disconnected menu option 2 (retry) should attempt to connect."""
        from chad.ui.cli.app import _run_disconnected_menu

        connect_attempts = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.base_url = kwargs.get("base_url", "")
                self.token = kwargs.get("token")

            def get_status(self):
                connect_attempts.append(self.base_url)
                raise ConnectionError("still down")

            def close(self):
                pass

        monkeypatch.setattr("chad.ui.cli.app.APIClient", FakeClient)
        inputs = iter(["2", "q"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        _run_disconnected_menu("http://localhost:8000")
        assert "http://localhost:8000" in connect_attempts


class TestPause:
    """Tests for the _pause helper (EOF-safe "Press Enter" prompts)."""

    def test_pause_swallows_eof(self, monkeypatch):
        from chad.ui.cli.app import _pause

        def raise_eof(_prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        _pause()  # must not raise

    def test_pause_swallows_keyboard_interrupt(self, monkeypatch):
        from chad.ui.cli.app import _pause

        def raise_interrupt(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_interrupt)
        _pause("\nPress Enter to continue...")  # must not raise

    def test_pause_reads_input(self, monkeypatch):
        from chad.ui.cli.app import _pause

        prompts = []
        monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
        _pause()
        assert prompts == ["Press Enter to continue..."]


class TestDiffRendering:
    """Tests for CLI rendering of the full-diff API response (DiffFullResponse schema)."""

    def test_print_full_diff_uses_api_schema(self, capsys):
        """Renders old_path/new_path and hunk lines with +/-/space prefixes."""
        from chad.ui.cli.app import _print_full_diff

        full_diff = {
            "files": [
                {
                    "old_path": "src/foo.py",
                    "new_path": "src/foo.py",
                    "is_new": False,
                    "is_deleted": False,
                    "is_binary": False,
                    "hunks": [
                        {
                            "old_start": 1,
                            "old_count": 2,
                            "new_start": 1,
                            "new_count": 3,
                            "lines": [
                                {"type": "context", "content": "def foo():"},
                                {"type": "delete", "content": "    return 1"},
                                {"type": "add", "content": "    return 2"},
                                {"type": "add", "content": "    # done"},
                            ],
                        }
                    ],
                }
            ]
        }

        _print_full_diff(full_diff)

        out = capsys.readouterr().out
        assert "--- src/foo.py" in out
        assert "@@ -1,2 +1,3 @@" in out
        assert " def foo():" in out
        assert "-    return 1" in out
        assert "+    return 2" in out
        assert "unknown" not in out

    def test_print_full_diff_new_file_falls_back_to_old_path(self, capsys):
        """When new_path is empty (deleted file), old_path is used."""
        from chad.ui.cli.app import _print_full_diff

        full_diff = {
            "files": [
                {
                    "old_path": "gone.py",
                    "new_path": "",
                    "hunks": [],
                }
            ]
        }

        _print_full_diff(full_diff)

        out = capsys.readouterr().out
        assert "--- gone.py" in out


def _make_settings_client(accounts, verification_agent=None):
    """Mock API client with everything run_settings_menu reads."""
    from chad.ui.client.api_client import CleanupSettings, Preferences

    client = MagicMock()
    client.list_accounts.return_value = accounts
    client.get_cleanup_settings.return_value = CleanupSettings(retention_days=7, auto_cleanup=True)
    client.get_preferences.return_value = Preferences(last_project_path="", ui_mode="cli")
    client.get_verification_agent.return_value = verification_agent
    client.get_preferred_verification_model.return_value = None
    client.get_max_verification_attempts.return_value = 3
    client.get_local_endpoint.return_value = "http://localhost:8000"
    client.get_action_settings.return_value = []
    client.get_slack_settings.return_value = {"enabled": False, "channel": None, "has_token": False}
    return client


class TestVerificationAgentMenu:
    """Tests for the 'Set verification agent' settings option."""

    def test_none_disabled_sends_sentinel(self, monkeypatch):
        """Choosing 'None (disabled)' must send the VERIFICATION_NONE marker, not None."""
        from chad.ui.cli.app import run_settings_menu, _VERIFICATION_NONE

        client = _make_settings_client([MockAccount(name="agent-1", provider="mock", role="CODING")])

        inputs = iter(["3", "1", "", "b"])  # settings option 3 -> pick option 1 (None) -> pause -> back
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_settings_menu(client)

        client.set_verification_agent.assert_called_once_with(_VERIFICATION_NONE)

    def test_cancel_leaves_setting_unchanged(self, monkeypatch):
        """Cancelling the picker (q) must not touch the verification agent."""
        from chad.ui.cli.app import run_settings_menu

        client = _make_settings_client(
            [MockAccount(name="agent-1", provider="mock", role="CODING")],
            verification_agent="agent-1",
        )

        inputs = iter(["3", "q", "", "b"])  # settings option 3 -> cancel picker -> pause -> back
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_settings_menu(client)

        client.set_verification_agent.assert_not_called()

    def test_sentinel_displayed_as_disabled(self, monkeypatch, capsys):
        """The stored sentinel renders as '(disabled)', not the raw marker string."""
        from chad.ui.cli.app import run_settings_menu, _VERIFICATION_NONE

        client = _make_settings_client(
            [MockAccount(name="agent-1", provider="mock", role="CODING")],
            verification_agent=_VERIFICATION_NONE,
        )

        inputs = iter(["b"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_settings_menu(client)

        out = capsys.readouterr().out
        assert "Verification Agent: (disabled)" in out
        assert _VERIFICATION_NONE not in out


class TestAccountsMenuRename:
    """The CLI offers the same account rename as the web UI."""

    def test_rename_sends_the_new_name(self, monkeypatch):
        from chad.ui.cli.app import run_accounts_menu

        client = _make_settings_client([MockAccount(name="old-name", provider="mock")])

        # menu 5 -> pick account 1 -> type the new name -> pause -> back
        inputs = iter(["5", "1", "new-name", "", "b"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_accounts_menu(client)

        client.rename_account.assert_called_once_with("old-name", "new-name")

    def test_blank_or_unchanged_name_is_left_alone(self, monkeypatch):
        from chad.ui.cli.app import run_accounts_menu

        client = _make_settings_client([MockAccount(name="old-name", provider="mock")])

        inputs = iter(["5", "1", "  ", "", "5", "1", "old-name", "", "b"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        run_accounts_menu(client)

        client.rename_account.assert_not_called()


class TestAPIClientWorktreeParams:
    """Tests that APIClient forwards optional worktree parameters."""

    @pytest.fixture
    def client(self):
        from chad.ui.client.api_client import APIClient

        api = APIClient(base_url="http://test")
        api._client = MagicMock()
        return api

    def test_merge_worktree_sends_commit_message(self, client):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "message": "ok", "conflicts": None}
        client._client.post.return_value = resp

        result = client.merge_worktree("sess-1", commit_message="Custom message")

        assert result.success is True
        _, kwargs = client._client.post.call_args
        assert kwargs["json"] == {"commit_message": "Custom message"}

    def test_merge_worktree_omits_empty_commit_message(self, client):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "message": "ok", "conflicts": None}
        client._client.post.return_value = resp

        client.merge_worktree("sess-1")

        _, kwargs = client._client.post.call_args
        assert kwargs["json"] == {}

    def test_get_diff_summary_sends_compare_branch(self, client):
        resp = MagicMock()
        resp.json.return_value = {"summary": "s", "files_changed": 1, "insertions": 2, "deletions": 3}
        client._client.get.return_value = resp

        client.get_diff_summary("sess-1", compare_branch="main")

        _, kwargs = client._client.get.call_args
        assert kwargs["params"] == {"compare_branch": "main"}

    def test_get_full_diff_sends_compare_branch(self, client):
        resp = MagicMock()
        resp.json.return_value = {"session_id": "sess-1", "summary": {}, "files": []}
        client._client.get.return_value = resp

        client.get_full_diff("sess-1", compare_branch="main")

        _, kwargs = client._client.get.call_args
        assert kwargs["params"] == {"compare_branch": "main"}


class TestCLIMergeFlow:
    """Tests for the post-task merge flow in run_cli."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        import subprocess

        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
        (repo_path / "README.md").write_text("# Test")
        return repo_path

    def _make_client(self, git_repo):
        from datetime import datetime
        from chad.ui.client.api_client import (
            CleanupSettings, DiffSummary, Preferences, Session, WorktreeStatus,
        )

        client = MagicMock()
        client.base_url = "http://localhost:8000"
        client.list_accounts.return_value = [MockAccount(name="test-agent", provider="mock", role="CODING")]
        client.get_preferences.return_value = Preferences(last_project_path=str(git_repo), ui_mode="cli")
        client.get_cleanup_settings.return_value = CleanupSettings(retention_days=7, auto_cleanup=True)
        client.get_verification_agent.return_value = None
        client.list_sessions.return_value = []
        client.get_conversation.return_value = {"items": []}
        client.create_session.return_value = Session(
            id="test-session-123",
            name="Test task",
            project_path=str(git_repo),
            active=True,
            has_worktree=True,
            has_changes=True,
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        client.get_worktree_status.return_value = WorktreeStatus(
            exists=True,
            path=str(git_repo / ".chad-worktrees" / "test"),
            branch="chad/test-session-123",
            base_commit="abc123",
            has_changes=True,
        )
        client.get_diff_summary.return_value = DiffSummary(
            summary="1 file changed", files_changed=1, insertions=5, deletions=2,
        )
        return client

    def test_merge_prompts_for_commit_message(self, git_repo, monkeypatch):
        """Merging asks for an optional commit message and passes it through."""
        from chad.ui.cli.app import run_cli
        from chad.ui.client.api_client import MergeResult

        client = self._make_client(git_repo)
        client.merge_worktree.return_value = MergeResult(success=True, message="ok", conflicts=None)

        inputs = iter([
            "1",                # Start task
            "test task",        # Task description
            "",                 # End description
            "m",                # Merge
            "Custom message",   # Commit message
            "",                 # Press Enter to continue
            "q",                # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0):
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(client)

        client.merge_worktree.assert_called_once_with("test-session-123", commit_message="Custom message")

    def test_merge_conflicts_printed_as_file_paths(self, git_repo, monkeypatch, capsys):
        """Conflicts are shown as file paths with hunk counts, not raw dicts."""
        from chad.ui.cli.app import run_cli
        from chad.ui.client.api_client import MergeResult

        client = self._make_client(git_repo)
        client.merge_worktree.return_value = MergeResult(
            success=False,
            message="Merge conflicts detected",
            conflicts=[{"file_path": "src/foo.py", "hunks": [{"ours": ["a"], "theirs": ["b"], "base": []}]}],
        )

        inputs = iter([
            "1",           # Start task
            "test task",   # Task description
            "",            # End description
            "m",           # Merge
            "",            # Commit message (default)
            "",            # Press Enter to continue
            "q",           # Quit
        ])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0):
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(client)

        client.merge_worktree.assert_called_once_with("test-session-123", commit_message=None)
        out = capsys.readouterr().out
        assert "src/foo.py (1 conflicting hunks)" in out
        assert "'file_path'" not in out

    def test_verification_none_sentinel_not_passed_to_task(self, git_repo, monkeypatch):
        """A stored disable marker must not be forwarded as the verification account."""
        from chad.ui.cli.app import run_cli, _VERIFICATION_NONE
        from chad.ui.client.api_client import MergeResult

        client = self._make_client(git_repo)
        client.get_verification_agent.return_value = _VERIFICATION_NONE
        client.merge_worktree.return_value = MergeResult(success=True, message="ok", conflicts=None)

        inputs = iter(["1", "test task", "", "k", "", "q"])
        monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
        monkeypatch.setattr("os.system", lambda _: None)

        with patch("chad.ui.cli.app.run_task_with_streaming", return_value=0) as mock_run:
            with patch("chad.ui.cli.app.SyncStreamClient"):
                run_cli(client)

        assert mock_run.call_args.kwargs["verification_account"] is None


class TestProviderApiKeyPrompt:
    """Tests for the API-key login prompt copy."""

    def test_api_key_prompt_uses_provider_label(self, monkeypatch, capsys, tmp_path):
        """The prompt names the provider being added instead of hard-coding Mistral."""
        import webbrowser
        from chad.ui.cli.app import _run_provider_oauth

        _stub_installer(monkeypatch)
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr(webbrowser, "open", lambda *_a, **_k: False)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt: "sk-test-key")

        success, _message = _run_provider_oauth("mistral", "my-vibe")

        assert success is True
        out = capsys.readouterr().out
        assert "Mistral requires an API key." in out
        assert "console.mistral.ai" in out


class TestCLIStreamingMilestones:
    """Tests for milestone delivery in CLI task streaming."""

    def test_run_task_with_streaming_emits_milestones_from_api_endpoint(self, monkeypatch):
        """CLI should fetch milestones from the dedicated milestones API endpoint."""
        from unittest.mock import Mock
        from chad.ui.client.stream_client import StreamEvent
        from chad.ui.cli.app import run_task_with_streaming

        client = Mock()
        client.get_milestones.side_effect = [
            [
                {
                    "seq": 1,
                    "milestone_type": "exploration",
                    "title": "Discovery",
                    "summary": "Found auth flow in src/auth.py",
                }
            ],
            [],
        ]

        stream_client = Mock()
        stream_client.stream_events.return_value = iter(
            [StreamEvent(event_type="complete", data={"exit_code": 0})]
        )

        writes: list[bytes] = []

        def fake_write(_fd, data):
            writes.append(data)
            return len(data)

        monkeypatch.setattr("chad.ui.cli.app.get_terminal_size", lambda: (24, 80))
        monkeypatch.setattr("chad.ui.cli.app.save_terminal", lambda: None)
        monkeypatch.setattr("chad.ui.cli.app.signal.signal", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("chad.ui.cli.app.os.write", fake_write)

        exit_code = run_task_with_streaming(
            client=client,
            stream_client=stream_client,
            session_id="sess-1",
            project_path="/tmp/project",
            task_description="fix task",
            coding_account="codex",
        )

        assert exit_code == 0
        client.get_milestones.assert_any_call("sess-1", since_seq=0)
        rendered = b"".join(writes).decode("utf-8", errors="replace")
        assert "[MILESTONE] Discovery: Found auth flow in src/auth.py" in rendered

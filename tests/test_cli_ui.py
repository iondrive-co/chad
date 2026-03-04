"""Tests for Chad CLI UI components.

Note: UI mode tests are in test_cli_integration.py::TestUIModeSwitching.
Note: PTY runner command tests are in test_cli_integration.py::TestProviderCommandGeneration.
"""

import pytest


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


class TestProviderOauthFlow:
    """Tests for CLI provider auth behavior."""

    def test_opencode_detects_existing_auth(self, monkeypatch, tmp_path):
        """OpenCode should detect existing OAuth credentials."""
        import json
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        auth_dir = tmp_path / ".local" / "share" / "opencode"
        auth_dir.mkdir(parents=True)
        (auth_dir / "auth.json").write_text(json.dumps({"token": "test"}))

        success, message = _run_provider_oauth("opencode", "my-opencode")
        assert success is True
        assert "Already logged in" in message

    def test_opencode_stores_api_key_from_prompt(self, monkeypatch, tmp_path):
        """OpenCode should store an API key pasted by the user into auth.json."""
        import json
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "sk-test-key-123")

        success, message = _run_provider_oauth("opencode", "my-opencode")

        assert success is True
        assert "stored" in message.lower()
        auth_file = tmp_path / ".local" / "share" / "opencode" / "auth.json"
        assert auth_file.exists()
        data = json.loads(auth_file.read_text())
        assert data["opencode"]["key"] == "sk-test-key-123"

    def test_opencode_fails_without_key(self, monkeypatch, tmp_path):
        """OpenCode should fail when user skips API key entry."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        success, message = _run_provider_oauth("opencode", "my-opencode")
        assert success is False
        assert "No API key" in message

    def test_kimi_no_cli_reports_not_found(self, monkeypatch, tmp_path):
        """Kimi add should fail when CLI is not installed."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: None)
        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is False
        assert "not found" in message.lower()
        assert "pip install kimi-cli" in message

    def test_kimi_accepts_complete_credentials(self, monkeypatch, tmp_path):
        """Kimi add should succeed when creds AND populated config exist."""
        from chad.ui.cli.app import _run_provider_oauth

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
        from chad.ui.cli.app import _run_provider_oauth

        class Completed:
            returncode = 1

        def fake_run(cmd, env, timeout):
            # Simulate successful OAuth followed by model-fetch failure.
            kimi_home = Path(env["HOME"])
            creds_dir = kimi_home / ".kimi" / "credentials"
            creds_dir.mkdir(parents=True, exist_ok=True)
            (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
            return Completed()

        from pathlib import Path

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: "/tmp/kimi")
        monkeypatch.setattr("chad.ui.cli.app.subprocess.run", fake_run)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is True
        assert "Logged in successfully" in message
        config_text = (tmp_path / ".chad" / "kimi-homes" / "my-kimi" / ".kimi" / "config.toml").read_text()
        assert "[models." in config_text

    def test_mistral_prompts_for_api_key(self, monkeypatch, tmp_path):
        """Mistral auth should prompt for an API key and write it to ~/.vibe/.env."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt: "sk-test-key-123")

        success, message = _run_provider_oauth("mistral", "my-vibe")

        assert success is True
        assert "Login successful" in message
        env_file = tmp_path / ".vibe" / ".env"
        assert env_file.exists()
        assert "sk-test-key-123" in env_file.read_text()

    def test_mistral_empty_api_key_fails(self, monkeypatch, tmp_path):
        """Mistral auth should fail when user provides an empty API key."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt: "")

        success, message = _run_provider_oauth("mistral", "my-vibe")

        assert success is False
        assert "No API key" in message


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

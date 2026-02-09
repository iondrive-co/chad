"""Tests for Chad CLI UI components.

Note: UI mode tests are in test_cli_integration.py::TestUIModeSwitching.
Note: PTY runner command tests are in test_cli_integration.py::TestProviderCommandGeneration.
"""


class TestCLIImports:
    """Tests for CLI package imports."""

    def test_import_launch_cli_ui(self):
        """Can import launch_cli_ui from chad.ui.cli."""
        from chad.ui.cli import launch_cli_ui
        assert callable(launch_cli_ui)


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

    def test_opencode_runs_auth_login(self, monkeypatch, tmp_path):
        """OpenCode should run `opencode auth login` when no credentials exist."""
        import json
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: "/usr/bin/opencode")

        calls = []

        class Completed:
            returncode = 0

        def fake_run(cmd, timeout):
            calls.append(cmd)
            auth_dir = tmp_path / ".local" / "share" / "opencode"
            auth_dir.mkdir(parents=True, exist_ok=True)
            (auth_dir / "auth.json").write_text(json.dumps({"token": "new-token"}))
            return Completed()

        monkeypatch.setattr("chad.ui.cli.app.subprocess.run", fake_run)
        success, message = _run_provider_oauth("opencode", "my-opencode")

        assert success is True
        assert "Login successful" in message
        assert calls == [["/usr/bin/opencode", "auth", "login"]]

    def test_opencode_no_cli_reports_not_found(self, monkeypatch, tmp_path):
        """OpenCode should fail when CLI is not installed."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: None)

        success, message = _run_provider_oauth("opencode", "my-opencode")
        assert success is False
        assert "not found" in message.lower()

    def test_kimi_no_cli_reports_not_found(self, monkeypatch, tmp_path):
        """Kimi add should fail when CLI is not installed."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: None)
        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is False
        assert "not found" in message.lower()

    def test_kimi_accepts_existing_credentials(self, monkeypatch, tmp_path):
        """Kimi add should succeed when credential file already exists."""
        from chad.ui.cli.app import _run_provider_oauth

        # Create isolated credentials file
        creds_dir = tmp_path / ".chad" / "kimi-homes" / "my-kimi" / ".kimi" / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is True
        assert "Already logged in" in message

    def test_kimi_accepts_global_credentials(self, monkeypatch, tmp_path):
        """Kimi add should succeed when global credential file exists."""
        from chad.ui.cli.app import _run_provider_oauth

        # Create global credentials file
        creds_dir = tmp_path / ".kimi" / "credentials"
        creds_dir.mkdir(parents=True)
        (creds_dir / "kimi-code.json").write_text('{"token": "test"}')
        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)

        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is True
        assert "Already logged in" in message

    def test_mistral_uses_vibe_setup_command(self, monkeypatch, tmp_path):
        """Mistral auth should invoke `vibe --setup` when not yet configured."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)

        calls = []

        class Completed:
            returncode = 0

        def fake_run(cmd, timeout):
            calls.append(cmd)
            vibe_dir = tmp_path / ".vibe"
            vibe_dir.mkdir(parents=True, exist_ok=True)
            (vibe_dir / "config.toml").write_text('[general]\napi_key = "test"\n')
            return Completed()

        monkeypatch.setattr("chad.ui.cli.app.subprocess.run", fake_run)
        success, _message = _run_provider_oauth("mistral", "my-vibe")

        assert success is True
        assert calls == [["vibe", "--setup"]]

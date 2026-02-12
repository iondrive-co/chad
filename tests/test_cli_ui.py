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

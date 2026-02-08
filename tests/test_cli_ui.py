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

    def test_opencode_does_not_require_oauth_when_cli_exists(self, monkeypatch):
        """OpenCode should be addable without an interactive login step."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda cmd: "/tmp/opencode" if cmd == "opencode" else None)
        success, message = _run_provider_oauth("opencode", "my-opencode")

        assert success is True
        assert "No login required" in message

    def test_opencode_reports_missing_cli(self, monkeypatch):
        """OpenCode should fail fast when its CLI is not installed."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.shutil.which", lambda _cmd: None)
        success, message = _run_provider_oauth("opencode", "my-opencode")

        assert success is False
        assert "not found" in message.lower()

    def test_kimi_requires_prior_login(self, monkeypatch, tmp_path):
        """Kimi add should be gated until ~/.kimi/config.toml exists."""
        from chad.ui.cli.app import _run_provider_oauth

        monkeypatch.setattr("chad.ui.cli.app.Path.home", lambda: tmp_path)
        success, message = _run_provider_oauth("kimi", "my-kimi")

        assert success is False
        assert "/login" in message

    def test_kimi_accepts_existing_login(self, monkeypatch, tmp_path):
        """Kimi add should succeed when login config already exists."""
        from chad.ui.cli.app import _run_provider_oauth

        kimi_dir = tmp_path / ".kimi"
        kimi_dir.mkdir(parents=True)
        (kimi_dir / "config.toml").write_text("[auth]\nprovider='kimi'\n")
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

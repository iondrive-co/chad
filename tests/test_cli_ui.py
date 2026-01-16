"""Tests for Chad CLI UI components."""

import pytest
from pathlib import Path


class TestUIMode:
    """Tests for ui_mode config preference."""

    def test_get_ui_mode_default(self, tmp_path, monkeypatch):
        """Default ui_mode is gradio."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        assert cm.get_ui_mode() == "gradio"

    def test_set_ui_mode_cli(self, tmp_path, monkeypatch):
        """Can set ui_mode to cli."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        cm.set_ui_mode("cli")
        assert cm.get_ui_mode() == "cli"

    def test_set_ui_mode_gradio(self, tmp_path, monkeypatch):
        """Can set ui_mode to gradio."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        cm.set_ui_mode("cli")
        cm.set_ui_mode("gradio")
        assert cm.get_ui_mode() == "gradio"

    def test_set_ui_mode_invalid(self, tmp_path, monkeypatch):
        """Invalid ui_mode raises ValueError."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        with pytest.raises(ValueError, match="Invalid ui_mode"):
            cm.set_ui_mode("invalid")


class TestPTYRunner:
    """Tests for PTY runner module."""

    def test_build_agent_command_anthropic(self):
        """Can build command for Anthropic provider."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("anthropic", "test-account", Path("/tmp/test"))

        assert "claude" in cmd
        assert "-p" in cmd
        assert "--permission-mode" in cmd

    def test_build_agent_command_openai(self):
        """Can build command for OpenAI provider."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("openai", "test-account", Path("/tmp/test"))

        assert "codex" in cmd
        assert "HOME" in env

    def test_build_agent_command_gemini(self):
        """Can build command for Gemini provider."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("gemini", "test-account", Path("/tmp/test"))

        assert "gemini" in cmd
        assert "-y" in cmd

    def test_build_agent_command_qwen(self):
        """Can build command for Qwen provider."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("qwen", "test-account", Path("/tmp/test"))

        assert "qwen" in cmd

    def test_build_agent_command_mistral(self):
        """Can build command for Mistral provider."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("mistral", "test-account", Path("/tmp/test"))

        assert "vibe" in cmd

    def test_build_agent_command_unknown(self):
        """Unknown provider raises ValueError."""
        from chad.ui.cli.pty_runner import build_agent_command

        with pytest.raises(ValueError, match="Unknown provider"):
            build_agent_command("unknown", "test-account", Path("/tmp/test"))


class TestCLIImports:
    """Tests for CLI package imports."""

    def test_import_launch_cli_ui(self):
        """Can import launch_cli_ui from chad.ui.cli."""
        from chad.ui.cli import launch_cli_ui
        assert callable(launch_cli_ui)

    def test_import_screens(self):
        """Can import screens from chad.ui.cli.screens."""
        from chad.ui.cli.screens import TaskScreen, SetupScreen, MergeScreen
        assert TaskScreen is not None
        assert SetupScreen is not None
        assert MergeScreen is not None

    def test_import_widgets(self):
        """Can import widgets from chad.ui.cli.widgets."""
        from chad.ui.cli.widgets import AgentPicker, DiffViewer
        assert AgentPicker is not None
        assert DiffViewer is not None

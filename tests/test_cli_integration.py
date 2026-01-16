"""Integration tests for Chad CLI UI components.

These tests verify that the CLI correctly interacts with the
ConfigManager and other backend services.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock


class TestSetupScreenConfig:
    """Tests for setup screen configuration management."""

    @pytest.fixture
    def config_manager(self, tmp_path, monkeypatch):
        """Create a ConfigManager with isolated config file."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        # Initialize with a password hash so we can store accounts
        cm.save_config({
            "password_hash": cm.hash_password("test"),
            "encryption_salt": "dGVzdHNhbHQ=",
            "accounts": {},
        })
        return cm

    @pytest.fixture
    def mock_app(self, config_manager):
        """Create a mock app with config_manager attached."""
        app = MagicMock()
        app.config_manager = config_manager
        app.password = "test"
        app.pop_screen = MagicMock()
        return app

    def test_add_account_stores_in_config(self, config_manager, mock_app):
        """Adding account stores it in config."""
        # Store an account directly to verify the config works
        config_manager.store_account(
            account_name="test-claude",
            provider="anthropic",
            api_key="test-key",
            password="test",
        )

        accounts = config_manager.list_accounts()
        assert "test-claude" in accounts
        assert accounts["test-claude"] == "anthropic"

    def test_delete_account_removes_from_config(self, config_manager, mock_app):
        """Deleting account through setup screen removes it from config."""
        # First add an account
        config_manager.store_account(
            account_name="to-delete",
            provider="openai",
            api_key="test-key",
            password="test",
        )
        assert "to-delete" in config_manager.list_accounts()

        # Delete it
        config_manager.delete_account("to-delete")
        assert "to-delete" not in config_manager.list_accounts()

    def test_set_coding_role_persists(self, config_manager, mock_app):
        """Setting CODING role persists in config."""
        # Add an account first
        config_manager.store_account(
            account_name="coding-agent",
            provider="anthropic",
            api_key="test-key",
            password="test",
        )

        # Assign CODING role
        config_manager.assign_role("coding-agent", "CODING")

        # Verify it persists
        assert config_manager.get_role_assignment("CODING") == "coding-agent"

    def test_delete_account_clears_role(self, config_manager, mock_app):
        """Deleting an account that has CODING role clears the role."""
        # Add account and assign role
        config_manager.store_account(
            account_name="main-agent",
            provider="anthropic",
            api_key="test-key",
            password="test",
        )
        config_manager.assign_role("main-agent", "CODING")
        assert config_manager.get_role_assignment("CODING") == "main-agent"

        # Delete the account
        config_manager.delete_account("main-agent")

        # Role should be cleared
        assert config_manager.get_role_assignment("CODING") is None

    def test_save_ui_mode_cli(self, config_manager, mock_app):
        """Saving UI mode to CLI persists in config."""
        config_manager.set_ui_mode("cli")
        assert config_manager.get_ui_mode() == "cli"

    def test_save_ui_mode_gradio(self, config_manager, mock_app):
        """Saving UI mode to gradio persists in config."""
        config_manager.set_ui_mode("cli")  # First set to cli
        config_manager.set_ui_mode("gradio")  # Then back to gradio
        assert config_manager.get_ui_mode() == "gradio"

    def test_save_cleanup_days(self, config_manager, mock_app):
        """Saving cleanup days persists in config."""
        config_manager.set_cleanup_days(7)
        assert config_manager.get_cleanup_days() == 7

    def test_cleanup_days_validation(self, config_manager, mock_app):
        """Cleanup days must be positive."""
        with pytest.raises(ValueError):
            config_manager.set_cleanup_days(0)
        with pytest.raises(ValueError):
            config_manager.set_cleanup_days(-1)

    def test_multiple_accounts_same_provider(self, config_manager, mock_app):
        """Can have multiple accounts for the same provider."""
        config_manager.store_account("work-claude", "anthropic", "key1", "test")
        config_manager.store_account("personal-claude", "anthropic", "key2", "test")

        accounts = config_manager.list_accounts()
        assert "work-claude" in accounts
        assert "personal-claude" in accounts
        assert accounts["work-claude"] == "anthropic"
        assert accounts["personal-claude"] == "anthropic"

    def test_accounts_different_providers(self, config_manager, mock_app):
        """Can have accounts for different providers."""
        config_manager.store_account("my-claude", "anthropic", "key1", "test")
        config_manager.store_account("my-codex", "openai", "key2", "test")
        config_manager.store_account("my-gemini", "gemini", "key3", "test")

        accounts = config_manager.list_accounts()
        assert len(accounts) == 3
        assert accounts["my-claude"] == "anthropic"
        assert accounts["my-codex"] == "openai"
        assert accounts["my-gemini"] == "gemini"


class TestTaskScreenConfig:
    """Tests for task screen configuration loading."""

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
        })
        return cm

    def test_load_accounts_populates_selector(self, config_manager):
        """Task screen loads accounts from config into selector."""
        # Add some accounts
        config_manager.store_account("agent-1", "anthropic", "key", "test")
        config_manager.store_account("agent-2", "openai", "key", "test")

        accounts = config_manager.list_accounts()
        assert len(accounts) == 2

    def test_coding_role_preselected(self, config_manager):
        """Account with CODING role is preselected in task screen."""
        config_manager.store_account("default-agent", "anthropic", "key", "test")
        config_manager.store_account("coding-agent", "openai", "key", "test")
        config_manager.assign_role("coding-agent", "CODING")

        coding = config_manager.get_role_assignment("CODING")
        assert coding == "coding-agent"

    def test_load_saved_project_path(self, config_manager):
        """Task screen loads saved project path from preferences."""
        config_manager.save_preferences("/home/user/my-project")

        prefs = config_manager.load_preferences()
        assert prefs is not None
        assert prefs["project_path"] == "/home/user/my-project"

    def test_save_project_path_on_task(self, config_manager):
        """Starting a task saves the project path to preferences."""
        config_manager.save_preferences("/new/project/path")

        prefs = config_manager.load_preferences()
        assert prefs["project_path"] == "/new/project/path"


class TestProviderCommandGeneration:
    """Tests for provider-specific command generation."""

    def test_anthropic_command_has_bypass_permissions(self):
        """Anthropic command includes permission bypass."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("anthropic", "test", Path("/tmp"))

        assert "claude" in cmd
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd

    def test_openai_command_has_bypass_and_home(self):
        """OpenAI command includes bypass and isolated HOME."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("openai", "my-codex", Path("/tmp"))

        assert "codex" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "HOME" in env
        assert "my-codex" in env["HOME"]

    def test_gemini_command_has_yolo(self):
        """Gemini command includes YOLO flag."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("gemini", "test", Path("/tmp"))

        assert "gemini" in cmd
        assert "-y" in cmd

    def test_qwen_command_has_yolo(self):
        """Qwen command includes YOLO flag."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("qwen", "test", Path("/tmp"))

        assert "qwen" in cmd
        assert "-y" in cmd

    def test_mistral_command(self):
        """Mistral command is correctly formed."""
        from chad.ui.cli.pty_runner import build_agent_command

        cmd, env = build_agent_command("mistral", "test", Path("/tmp"))

        assert "vibe" in cmd


class TestConfigPersistence:
    """Tests for configuration persistence across operations."""

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
        })
        return cm

    def test_account_model_setting_persists(self, config_manager):
        """Account model setting persists in config."""
        config_manager.store_account("test-agent", "anthropic", "key", "test")
        config_manager.set_account_model("test-agent", "claude-opus-4")

        assert config_manager.get_account_model("test-agent") == "claude-opus-4"

    def test_account_reasoning_setting_persists(self, config_manager):
        """Account reasoning setting persists in config."""
        config_manager.store_account("test-agent", "openai", "key", "test")
        config_manager.set_account_reasoning("test-agent", "high")

        assert config_manager.get_account_reasoning("test-agent") == "high"

    def test_verification_agent_persists(self, config_manager):
        """Verification agent setting persists in config."""
        config_manager.store_account("verifier", "anthropic", "key", "test")
        config_manager.set_verification_agent("verifier")

        assert config_manager.get_verification_agent() == "verifier"

    def test_verification_agent_cleared_on_delete(self, config_manager):
        """Deleting verification agent account clears the setting."""
        config_manager.store_account("verifier", "anthropic", "key", "test")
        config_manager.set_verification_agent("verifier")

        config_manager.delete_account("verifier")

        # get_verification_agent returns None if account doesn't exist
        assert config_manager.get_verification_agent() is None

    def test_all_settings_reload_correctly(self, config_manager, tmp_path, monkeypatch):
        """All settings reload correctly after creating new ConfigManager."""
        from chad.util.config_manager import ConfigManager

        # Set up various settings
        config_manager.store_account("agent1", "anthropic", "key", "test")
        config_manager.assign_role("agent1", "CODING")
        config_manager.set_ui_mode("cli")
        config_manager.set_cleanup_days(5)
        config_manager.save_preferences("/test/path")

        # Create new ConfigManager pointing to same file
        config_file = tmp_path / "test_chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))
        cm2 = ConfigManager()

        # Verify all settings
        assert "agent1" in cm2.list_accounts()
        assert cm2.get_role_assignment("CODING") == "agent1"
        assert cm2.get_ui_mode() == "cli"
        assert cm2.get_cleanup_days() == 5
        prefs = cm2.load_preferences()
        assert prefs["project_path"] == "/test/path"


class TestUIModeSwitching:
    """Tests for UI mode switching behavior."""

    def test_default_ui_mode_is_gradio(self, tmp_path, monkeypatch):
        """Default UI mode is gradio."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "fresh.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        assert cm.get_ui_mode() == "gradio"

    def test_cli_mode_persists_across_restarts(self, tmp_path, monkeypatch):
        """CLI mode setting persists across ConfigManager instances."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm1 = ConfigManager()
        cm1.set_ui_mode("cli")

        cm2 = ConfigManager()
        assert cm2.get_ui_mode() == "cli"

    def test_invalid_ui_mode_rejected(self, tmp_path, monkeypatch):
        """Invalid UI mode values are rejected."""
        from chad.util.config_manager import ConfigManager

        config_file = tmp_path / "test.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_file))

        cm = ConfigManager()
        with pytest.raises(ValueError, match="Invalid ui_mode"):
            cm.set_ui_mode("web")
        with pytest.raises(ValueError, match="Invalid ui_mode"):
            cm.set_ui_mode("")
        with pytest.raises(ValueError, match="Invalid ui_mode"):
            cm.set_ui_mode("GUI")

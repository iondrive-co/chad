"""Test timezone display in usage information."""

import json
from unittest.mock import patch, MagicMock

import pytest

from chad.provider_ui import ProviderUIManager
from chad.security import SecurityManager


class TestUsageTimezone:
    """Test that usage displays in local timezone, not UTC."""

    @pytest.fixture
    def security_mgr(self, tmp_path):
        """Create a security manager with mock data."""
        import base64
        import bcrypt

        mgr = SecurityManager(tmp_path / "config.json")
        # Initialize config with proper structure
        password_hash = mgr.hash_password("test_password")
        encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        config = {
            "password_hash": password_hash,
            "encryption_salt": encryption_salt,
            "accounts": {}
        }
        mgr.save_config(config)
        return mgr

    @pytest.fixture
    def provider_ui(self, security_mgr):
        """Create ProviderUIManager instance."""
        return ProviderUIManager(security_mgr, "test_password")

    def test_codex_usage_displays_local_time(self, provider_ui, tmp_path, monkeypatch):
        """Test that Codex usage displays time in local timezone."""
        # Create mock account
        provider_ui.security_mgr.store_account("test-codex", "openai", "", "test_password")

        # Create mock Codex home structure
        codex_home = tmp_path / ".chad" / "codex-homes" / "test-codex"
        auth_file = codex_home / ".codex" / "auth.json"
        sessions_dir = codex_home / ".codex" / "sessions"
        sessions_dir.mkdir(parents=True)

        # Create auth file
        auth_data = {
            "tokens": {
                "access_token": "test-token"
            }
        }
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text(json.dumps(auth_data))

        # Create session file with rate limits
        session_file = sessions_dir / "test-session.jsonl"
        session_data = {
            "type": "event_msg",
            "timestamp": "2024-01-15T10:30:45Z",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": 25.0,
                        "resets_at": 1705320000
                    }
                }
            }
        }
        session_file.write_text(json.dumps(session_data))

        # Mock _get_codex_home to return our test directory
        monkeypatch.setattr(provider_ui, "_get_codex_home", lambda x: codex_home)

        # Get usage - it should display local time, not UTC
        usage = provider_ui._get_codex_session_usage("test-codex")

        # Check that the output contains local time format without UTC
        assert usage is not None
        assert "Last updated:" in usage
        assert "UTC" not in usage  # Should display in local time

    def test_claude_usage_displays_local_time(self, provider_ui, tmp_path, monkeypatch):
        """Test that Claude usage displays time in local timezone."""
        # Mock the Claude API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "five_hour": {
                "utilization": 30.0,
                "resets_at": "2024-01-15T15:30:00Z"
            },
            "seven_day": {
                "utilization": 10.0,
                "resets_at": "2024-01-22T00:00:00Z"
            }
        }

        # Create mock account and config
        provider_ui.security_mgr.store_account("test-claude", "anthropic", "", "test_password")
        config_dir = tmp_path / ".chad" / "claude-configs" / "test-claude"
        creds_file = config_dir / ".credentials.json"
        creds_file.parent.mkdir(parents=True, exist_ok=True)

        creds_data = {
            "claudeAiOauth": {
                "accessToken": "test-token",
                "subscriptionType": "pro"
            }
        }
        creds_file.write_text(json.dumps(creds_data))

        # Mock the config dir method
        monkeypatch.setattr(provider_ui, "_get_claude_config_dir", lambda x: config_dir)

        # Mock requests.get
        with patch("requests.get", return_value=mock_response):
            usage = provider_ui._get_claude_usage("test-claude")

        # Check that reset times are displayed in local format
        assert usage is not None
        assert "Resets at" in usage
        # The times should be formatted for display, not showing 'Z' suffix
        assert "Z" not in usage
        assert "UTC" not in usage

"""Edge case tests for provider UI functionality."""

import base64
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from chad.model_catalog import ModelCatalog
from chad.provider_ui import ProviderUIManager
from chad.security import SecurityManager
from chad.verification.ui_playwright_runner import create_temp_env


class TestProviderScreenshotMode:
    """Test provider card display in screenshot mode."""

    def test_screenshot_mode_duplicates_fourth_provider_card(self, monkeypatch):
        """Screenshot mode should repeat one provider card among the first four."""
        env = create_temp_env(screenshot_mode=True)
        try:
            monkeypatch.setenv("CHAD_CONFIG", str(env.config_path))
            for key, value in env.env_vars.items():
                monkeypatch.setenv(key, value)

            security_mgr = SecurityManager()
            model_catalog = ModelCatalog(security_mgr)
            provider_ui = ProviderUIManager(security_mgr, env.password, model_catalog)

            state = provider_ui.provider_state(card_slots=4)

            # Each card has 6 elements, account name is at index 3
            card_names = [state[idx * 6 + 3] for idx in range(4)]

            assert len(card_names) == 4
            assert len(set(card_names)) == 3
            assert card_names[3] in card_names[:3]
        finally:
            env.cleanup()


class TestClaudeUsageEdgeCases:
    """Test Claude usage display edge cases."""

    def test_claude_usage_handles_none_utilization(self, monkeypatch):
        """Anthropic API can return null utilization; UI should treat it as zero."""

        class DummyResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "five_hour": {"utilization": None, "resets_at": "2025-01-01T00:00:00Z"},
                    "seven_day": {"utilization": None, "resets_at": "2025-01-02T00:00:00Z"},
                    "extra_usage": {"is_enabled": True, "used_credits": 0, "monthly_limit": 0, "utilization": None},
                }

        env = create_temp_env(screenshot_mode=False)
        try:
            monkeypatch.setenv("CHAD_CONFIG", str(env.config_path))
            monkeypatch.setenv("CHAD_TEMP_HOME", str(env.temp_dir))

            security_mgr = SecurityManager()
            security_mgr.store_account("claude-1", "anthropic", api_key="dummy", password=env.password)

            claude_config_dir = Path(env.temp_dir) / ".chad" / "claude-configs" / "claude-1"
            claude_config_dir.mkdir(parents=True, exist_ok=True)
            creds_file = claude_config_dir / ".credentials.json"
            creds_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token", "subscriptionType": "pro"}}))

            monkeypatch.setattr("requests.get", lambda *_, **__: DummyResponse())

            provider_ui = ProviderUIManager(security_mgr, env.password, ModelCatalog(security_mgr))

            usage_text = provider_ui.get_provider_usage("claude-1")
            remaining = provider_ui.get_remaining_usage("claude-1")

            assert "0% used" in usage_text
            assert remaining == pytest.approx(1.0)
        finally:
            env.cleanup()


class TestCodexUsageEdgeCases:
    """Test Codex usage display edge cases."""

    @staticmethod
    def _write_auth(auth_file: Path) -> None:
        payload = {"https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
        encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        token = f"hdr.{encoded_payload}.sig"
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text(json.dumps({"tokens": {"access_token": token}}))

    @staticmethod
    def _write_rate_limit_session(session_file: Path) -> None:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "event_msg",
            "timestamp": "2025-01-01T00:00:00Z",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 25, "resets_at": 1700000000},
                    "secondary": {"used_percent": 10, "resets_at": 1700000000},
                },
            },
        }
        session_file.write_text(json.dumps(record))

    def test_codex_usage_syncs_windows_real_home(self, monkeypatch, tmp_path):
        """On Windows, Codex usage should sync from real home directory."""
        isolated_home = tmp_path / "isolated"
        real_home = tmp_path / "real-home"

        monkeypatch.setenv("CHAD_TEMP_HOME", str(isolated_home))
        monkeypatch.setattr(os, "name", "nt", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: real_home))

        account_name = "codex-work"
        self._write_auth(real_home / ".codex" / "auth.json")
        self._write_rate_limit_session(real_home / ".codex" / "sessions" / "2025" / "01" / "01" / "session.jsonl")

        security_mgr = Mock()
        security_mgr.list_accounts.return_value = {account_name: "openai"}

        provider_ui = ProviderUIManager(security_mgr, "test-password", ModelCatalog(security_mgr))
        usage_text = provider_ui.get_provider_usage(account_name)

        assert "Current Usage" in usage_text
        assert "Usage data unavailable" not in usage_text

import json
from pathlib import Path

import pytest

from chad.model_catalog import ModelCatalog
from chad.provider_ui import ProviderUIManager
from chad.security import SecurityManager
from chad.verification.ui_playwright_runner import create_temp_env


class DummyResponse:
    status_code = 200

    @staticmethod
    def json():
        return {
            "five_hour": {"utilization": None, "resets_at": "2025-01-01T00:00:00Z"},
            "seven_day": {"utilization": None, "resets_at": "2025-01-02T00:00:00Z"},
            "extra_usage": {"is_enabled": True, "used_credits": 0, "monthly_limit": 0, "utilization": None},
        }


def test_claude_usage_handles_none_utilization(monkeypatch):
    """Anthropic usage API can return null utilization; UI should treat it as zero."""
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

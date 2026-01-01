"""Tests for provider screenshot card ordering."""

from chad.model_catalog import ModelCatalog
from chad.provider_ui import ProviderUIManager
from chad.security import SecurityManager
from chad.screenshot_fixtures import MOCK_ACCOUNTS
from chad.ui_playwright_runner import create_temp_env


def test_screenshot_mode_duplicates_fourth_provider_card(monkeypatch):
    """Ensure screenshot mode repeats one provider card among the first four."""
    env = create_temp_env(screenshot_mode=True)
    try:
        monkeypatch.setenv("CHAD_CONFIG", str(env.config_path))
        for key, value in env.env_vars.items():
            monkeypatch.setenv(key, value)

        security_mgr = SecurityManager()
        model_catalog = ModelCatalog(security_mgr)
        provider_ui = ProviderUIManager(security_mgr, env.password, model_catalog)

        state = provider_ui.provider_state(card_slots=4)
        list_md = state[0]

        for account_name in MOCK_ACCOUNTS:
            assert account_name in list_md

        card_names = []
        for idx in range(4):
            base = 1 + idx * 5
            card_names.append(state[base + 2])

        assert len(card_names) == 4
        assert len(set(card_names)) == 3
        assert card_names[3] in card_names[:3]
    finally:
        env.cleanup()


def test_provider_summary_hides_model_and_reasoning(monkeypatch):
    """Provider summary should not show preferred model or reasoning text."""
    env = create_temp_env(screenshot_mode=True)
    try:
        monkeypatch.setenv("CHAD_CONFIG", str(env.config_path))
        for key, value in env.env_vars.items():
            monkeypatch.setenv(key, value)

        security_mgr = SecurityManager()
        model_catalog = ModelCatalog(security_mgr)
        provider_ui = ProviderUIManager(security_mgr, env.password, model_catalog)

        summary = provider_ui.list_providers()

        assert "preferred model" not in summary
        assert "reasoning:" not in summary
    finally:
        env.cleanup()


def test_provider_summary_hides_model_and_reasoning_without_screenshot_mode(monkeypatch):
    """Provider summary should omit preferred model/reasoning in normal mode too."""
    env = create_temp_env(screenshot_mode=False)
    try:
        monkeypatch.setenv("CHAD_CONFIG", str(env.config_path))

        security_mgr = SecurityManager()
        security_mgr.set_account_reasoning("mock-coding", "high")

        model_catalog = ModelCatalog(security_mgr)
        provider_ui = ProviderUIManager(security_mgr, env.password, model_catalog)

        summary = provider_ui.list_providers()

        assert "preferred model" not in summary
        assert "mock-model" not in summary
        assert "reasoning:" not in summary
    finally:
        env.cleanup()

"""Tests for provider screenshot card ordering."""

from chad.model_catalog import ModelCatalog
from chad.provider_ui import ProviderUIManager
from chad.security import SecurityManager
from chad.verification.ui_playwright_runner import create_temp_env


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

        # Each card has 6 elements: column, group, header, account_state, usage, delete_btn
        # Account state (name) is at index 3 within each card block
        card_names = []
        for idx in range(4):
            base = idx * 6
            card_names.append(state[base + 3])

        assert len(card_names) == 4
        assert len(set(card_names)) == 3
        assert card_names[3] in card_names[:3]
    finally:
        env.cleanup()

"""Visual regression for the model-selection dropdown in the conversation view.

Every message the user sends from the chat composer should be able to carry a
per-message model override for the answer. The composer therefore renders a
model dropdown next to the input whenever the selected coding agent exposes more
than one runnable model (anything beyond the implicit ``default``). The chosen
model is sent to the backend as ``coding_model`` when the task/follow-up starts.

This drives the real React render path against a live Chad server, so it
exercises the model-catalog gating end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chad.util.config_manager import ConfigManager
from chad.util.verification.ui_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

pytestmark = pytest.mark.visual

MODEL_SELECT = ".chat-composer .model-select"


def _open_composer(coding_provider: str, coding_model: str):
    """Open a fresh session whose default coding agent uses ``coding_provider``.

    Returns (option_texts, default_value): the dropdown's option labels and its
    selected value, or (None, None) if no model dropdown is rendered.
    """
    env = create_temp_env(screenshot_mode=False)

    cfg = ConfigManager(env.config_path)
    cfg.store_account("model-agent", coding_provider, "", env.password, coding_model)
    cfg.assign_role("model-agent", "CODING")

    instance = start_chad(env)
    out_dir = Path("/tmp/chad")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open_playwright_page(instance.port, headless=True) as page:
            page.wait_for_selector(".new-session-btn", timeout=15000)
            page.click(".new-session-btn")
            page.wait_for_selector(".chat-composer", timeout=10000)
            # Give the coding-account / model-catalog fetches time to resolve.
            page.wait_for_timeout(1500)

            select = page.query_selector(MODEL_SELECT)
            if select is None:
                page.screenshot(path=str(out_dir / f"model_dropdown_{coding_provider}.png"))
                return None, None

            option_texts = page.eval_on_selector_all(
                f"{MODEL_SELECT} option",
                "els => els.map(e => e.textContent.trim())",
            )
            default_value = page.eval_on_selector(MODEL_SELECT, "el => el.value")
            page.screenshot(path=str(out_dir / f"model_dropdown_{coding_provider}.png"))
            return option_texts, default_value
    finally:
        stop_chad(instance)
        env.cleanup()


def test_composer_shows_model_dropdown_when_multiple_models():
    """A Claude account exposes several models, so the composer offers a picker."""
    try:
        options, default_value = _open_composer("anthropic", "claude-opus-4-6")
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert options is not None, "expected a model dropdown next to the composer input"
    # The default option plus at least one concrete Claude model.
    assert any("default" in o.lower() for o in options), f"missing default option; got {options}"
    assert any("claude-" in o.lower() for o in options), f"missing concrete model option; got {options}"
    # Defaults to the account's configured model (empty value = no override).
    assert default_value == "", f"dropdown should default to no override; got {default_value!r}"


def test_composer_hides_model_dropdown_with_single_model():
    """Mock provider only exposes 'default', so no model picker is rendered."""
    try:
        options, _ = _open_composer("mock", "mock-model")
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert options is None, f"model dropdown should be hidden when only 'default' exists; got {options}"

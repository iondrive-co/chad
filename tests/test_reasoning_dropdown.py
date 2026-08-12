"""Visual regression for the reasoning-level dropdown in the conversation view.

Every message the user sends from the chat composer should be able to carry a
reasoning level for the answer. The composer therefore renders a reasoning
dropdown next to the input whenever the selected coding agent's provider
supports reasoning levels (e.g. Codex). The level is sent to the backend as
``coding_reasoning`` when the task/follow-up starts.

This drives the real React render path against a live Chad server with a Codex
coding account, so it exercises the provider-capability gating end to end.
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

REASONING_SELECT = ".chat-composer .reasoning-select"


def _open_composer(coding_provider: str, coding_model: str):
    """Open a fresh session whose default coding agent uses ``coding_provider``.

    Returns (option_texts, default_value): the dropdown's option labels and its
    selected value, or (None, None) if no reasoning dropdown is rendered.
    """
    env = create_temp_env(screenshot_mode=False)

    # Make the default coding agent one whose provider may support reasoning.
    cfg = ConfigManager(env.config_path)
    cfg.store_account("reasoning-agent", coding_provider, "", env.password, coding_model)
    cfg.assign_role("reasoning-agent", "CODING")

    instance = start_chad(env)
    out_dir = Path("/tmp/chad")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open_playwright_page(instance.port, headless=True) as page:
            page.wait_for_selector(".new-session-btn", timeout=15000)
            page.click(".new-session-btn")
            page.wait_for_selector(".chat-composer", timeout=10000)
            # Give the coding-account / provider fetches time to resolve.
            page.wait_for_timeout(1500)

            select = page.query_selector(REASONING_SELECT)
            if select is None:
                page.screenshot(path=str(out_dir / f"reasoning_dropdown_{coding_provider}.png"))
                return None, None

            option_texts = page.eval_on_selector_all(
                f"{REASONING_SELECT} option",
                "els => els.map(e => e.textContent.trim())",
            )
            default_value = page.eval_on_selector(REASONING_SELECT, "el => el.value")
            page.screenshot(path=str(out_dir / f"reasoning_dropdown_{coding_provider}.png"))
            return option_texts, default_value
    finally:
        stop_chad(instance)
        env.cleanup()


@pytest.mark.parametrize(
    "provider,model",
    [("openai", "o3"), ("anthropic", "claude-opus-4-6")],
)
def test_composer_shows_reasoning_dropdown_for_reasoning_provider(provider, model):
    """Codex (openai) and Claude Code (anthropic) both support a reasoning level."""
    try:
        options, default_value = _open_composer(provider, model)
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert options is not None, "expected a reasoning dropdown next to the composer input"
    # Default plus low / medium / high.
    assert any("default" in o.lower() for o in options), f"missing default option; got {options}"
    for level in ("low", "medium", "high"):
        assert any(level in o.lower() for o in options), f"missing '{level}' option; got {options}"
    # Defaults to the provider's reasoning level (empty value = default).
    assert default_value == "", f"dropdown should default to provider default; got {default_value!r}"


def test_composer_hides_reasoning_dropdown_for_non_reasoning_provider():
    """Mock provider has no reasoning levels, so no dropdown is rendered."""
    try:
        options, _ = _open_composer("mock", "mock-model")
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert options is None, f"reasoning dropdown should be hidden for non-reasoning providers; got {options}"


# Chromium reserves space for the native <select> arrow inside the box, on
# top of the author's own padding/border. That space isn't reported by
# getComputedStyle, so it has to be accounted for by hand when checking
# whether an option's text actually fits in the closed control.
_SELECT_ARROW_ALLOWANCE_PX = 20


def test_reasoning_dropdown_options_are_not_clipped():
    """Every option's label must fit inside the select's closed-box width.

    Regression test: the select's CSS width was too narrow for its longest
    option ("Reasoning: default"), so the browser silently truncated the
    last few characters (e.g. "Reasoning: defau") instead of showing the
    full label.
    """
    env = create_temp_env(screenshot_mode=False)
    cfg = ConfigManager(env.config_path)
    cfg.store_account("reasoning-agent", "openai", "", env.password, "o3")
    cfg.assign_role("reasoning-agent", "CODING")

    instance = start_chad(env)
    try:
        with open_playwright_page(instance.port, headless=True) as page:
            page.wait_for_selector(".new-session-btn", timeout=15000)
            page.click(".new-session-btn")
            page.wait_for_selector(".chat-composer", timeout=10000)
            page.wait_for_timeout(1500)

            select = page.query_selector(REASONING_SELECT)
            if select is None:
                pytest.skip("reasoning dropdown not rendered for this provider")

            metrics = page.eval_on_selector(
                REASONING_SELECT,
                """el => {
                    const cs = getComputedStyle(el);
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
                    const widths = Array.from(el.options).map(
                        o => ctx.measureText(o.textContent).width
                    );
                    return {
                        clientWidth: el.clientWidth,
                        paddingLeft: parseFloat(cs.paddingLeft),
                        paddingRight: parseFloat(cs.paddingRight),
                        maxTextWidth: Math.max(...widths),
                    };
                }""",
            )
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")
    finally:
        stop_chad(instance)
        env.cleanup()

    available = (
        metrics["clientWidth"]
        - metrics["paddingLeft"]
        - metrics["paddingRight"]
        - _SELECT_ARROW_ALLOWANCE_PX
    )
    assert available >= metrics["maxTextWidth"], (
        f"reasoning-select is too narrow to show its longest option without "
        f"clipping: available={available:.1f}px, needed={metrics['maxTextWidth']:.1f}px"
    )

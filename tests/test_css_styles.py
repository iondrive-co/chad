"""Tests for CSS styling consistency.

These tests verify that UI CSS variables are defined correctly for both light
and dark modes, ensuring visual consistency between related components.
"""

import re
from pathlib import Path

import pytest


CSS_FILE = Path(__file__).parent.parent / "ui" / "src" / "styles" / "main.css"


def parse_css_variables(css_content: str) -> dict[str, dict[str, str]]:
    """Parse CSS variables from :root blocks, separating light/dark modes.

    Returns dict with keys 'light' and 'dark', each containing var name -> value.
    """
    result = {"light": {}, "dark": {}}

    # Find the main :root block (light mode)
    root_match = re.search(r":root\s*\{([^}]+)\}", css_content)
    if root_match:
        vars_block = root_match.group(1)
        for match in re.finditer(r"--([\w-]+):\s*([^;]+);", vars_block):
            result["light"][match.group(1)] = match.group(2).strip()

    # Find the dark mode :root block
    dark_match = re.search(
        r"@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)\s*\{\s*:root\s*\{([^}]+)\}",
        css_content,
    )
    if dark_match:
        vars_block = dark_match.group(1)
        for match in re.finditer(r"--([\w-]+):\s*([^;]+);", vars_block):
            result["dark"][match.group(1)] = match.group(2).strip()

    return result


class TestTerminalStylingMatchesMilestone:
    """Ensure terminal background matches milestone styling for readability."""

    @pytest.fixture
    def css_vars(self) -> dict[str, dict[str, str]]:
        """Load and parse CSS variables."""
        content = CSS_FILE.read_text()
        return parse_css_variables(content)

    def test_light_mode_terminal_bg_is_light(self, css_vars):
        """In light mode, terminal background should be light (like milestone bg)."""
        terminal_bg = css_vars["light"].get("terminal-bg", "")
        # Check it's a light color (white, near-white, or uses --bg variable)
        # The terminal should use --bg (white) or a similarly light color
        assert terminal_bg in (
            "#ffffff",
            "#fff",
            "var(--bg)",
            "#f5f5f5",
            "#fafafa",
        ), f"Light mode terminal-bg should be light, got: {terminal_bg}"

    def test_dark_mode_terminal_bg_is_dark(self, css_vars):
        """In dark mode, terminal background should be dark."""
        terminal_bg = css_vars["dark"].get("terminal-bg", "")
        bg = css_vars["dark"].get("bg", "")
        # Should be dark blue or use --bg
        assert terminal_bg in (
            "#1a1a2e",
            "#0d0d1a",
            "#16213e",
            "var(--bg)",
            bg,
        ), f"Dark mode terminal-bg should be dark, got: {terminal_bg}"

    def test_terminal_text_color_defined(self):
        """Terminal output should use --text for color, not hardcoded."""
        content = CSS_FILE.read_text()
        # Find .terminal-output rule
        terminal_output_match = re.search(
            r"\.terminal-output\s*\{([^}]+)\}",
            content,
        )
        assert terminal_output_match, "Should have .terminal-output rule"

        rule_content = terminal_output_match.group(1)

        # Should use var(--text) for color, not hardcoded #e0e0e0
        assert "color: var(--text)" in rule_content or "color: var(--terminal-text)" in rule_content, (
            "Terminal output should use CSS variable for text color, not hardcoded value"
        )

    def test_light_mode_has_readable_terminal_text(self, css_vars):
        """In light mode, terminal text should be dark for readability."""
        # Either terminal-text is defined, or --text is used
        terminal_text = css_vars["light"].get("terminal-text", css_vars["light"].get("text", ""))
        # Should be a dark color
        assert terminal_text in (
            "#1a1a2e",
            "#000",
            "#000000",
            "#1a1a1a",
            "#0d0d0d",
        ), f"Light mode terminal text should be dark, got: {terminal_text}"


class TestChatViewLayoutClasses:
    """Verify CSS classes for chat view agent picker layout."""

    def test_agent_pickers_container_exists(self):
        """The .chat-agent-pickers container should exist for grouping pickers."""
        content = CSS_FILE.read_text()
        assert ".chat-agent-pickers" in content, (
            "CSS should define .chat-agent-pickers for grouping coding and verification pickers"
        )

    def test_agent_pickers_uses_flexbox(self):
        """The agent pickers container should use flexbox for side-by-side layout."""
        content = CSS_FILE.read_text()
        # Find .chat-agent-pickers rule
        match = re.search(r"\.chat-agent-pickers\s*\{([^}]+)\}", content)
        assert match, "Should have .chat-agent-pickers rule"
        rule_content = match.group(1)
        assert "display: flex" in rule_content, (
            "Agent pickers container should use flexbox"
        )

    def test_agent_picker_has_min_width(self):
        """Individual agent pickers should have min-width for responsive layout."""
        content = CSS_FILE.read_text()
        match = re.search(r"\.chat-agent-picker\s*\{([^}]+)\}", content)
        assert match, "Should have .chat-agent-picker rule"
        rule_content = match.group(1)
        assert "min-width" in rule_content, (
            "Agent picker should have min-width for proper layout"
        )

    def test_verification_picker_has_min_width(self):
        """Verification picker should have min-width for responsive layout."""
        content = CSS_FILE.read_text()
        match = re.search(r"\.chat-verification-picker\s*\{([^}]+)\}", content)
        assert match, "Should have .chat-verification-picker rule"
        rule_content = match.group(1)
        assert "min-width" in rule_content, (
            "Verification picker should have min-width for proper layout"
        )

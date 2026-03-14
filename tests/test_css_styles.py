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


class TestFontSizeReadability:
    """Ensure font sizes are large enough to be readable."""

    MINIMUM_FONT_SIZE_PX = 13  # Minimum acceptable font size in pixels

    def test_no_tiny_font_sizes(self):
        """Font sizes should not be smaller than the minimum threshold."""
        content = CSS_FILE.read_text()
        # Find all explicit pixel font sizes
        font_size_matches = re.findall(r"font-size:\s*(\d+)px", content)
        small_sizes = [int(s) for s in font_size_matches if int(s) < self.MINIMUM_FONT_SIZE_PX]
        assert not small_sizes, (
            f"Found font sizes below {self.MINIMUM_FONT_SIZE_PX}px threshold: {sorted(set(small_sizes))}px. "
            "Small fonts make the UI difficult to read."
        )

    def test_html_has_base_font_size(self):
        """HTML element should have a base font-size for rem calculations."""
        content = CSS_FILE.read_text()
        # Look for html rule with font-size
        html_match = re.search(r"html\s*\{([^}]+)\}", content)
        assert html_match, "Should have html {} rule for base font-size"
        rule_content = html_match.group(1)
        assert "font-size:" in rule_content, (
            "html element should set font-size as base for rem units"
        )


class TestJetBrainsMonoFont:
    """Ensure JetBrains Mono is used as the primary font throughout the UI."""

    def test_body_uses_font_mono_variable(self):
        """Body should use the --font-mono variable (JetBrains Mono)."""
        content = CSS_FILE.read_text()
        # Find body rule
        body_match = re.search(r"body\s*\{([^}]+)\}", content)
        assert body_match, "Should have body {} rule"
        rule_content = body_match.group(1)
        assert "font-family: var(--font-mono)" in rule_content, (
            "body should use font-family: var(--font-mono) for JetBrains Mono font"
        )

    def test_font_mono_variable_includes_jetbrains_mono(self):
        """The --font-mono CSS variable should include JetBrains Mono."""
        content = CSS_FILE.read_text()
        # Find :root with --font-mono
        root_match = re.search(r":root\s*\{([^}]+)\}", content)
        assert root_match, "Should have :root {} rule"
        root_content = root_match.group(1)
        font_mono_match = re.search(r"--font-mono:\s*([^;]+);", root_content)
        assert font_mono_match, "Should have --font-mono variable defined"
        font_mono_value = font_mono_match.group(1)
        assert "JetBrains Mono" in font_mono_value, (
            f"--font-mono should include JetBrains Mono, got: {font_mono_value}"
        )

    def test_index_html_loads_jetbrains_mono(self):
        """index.html should load JetBrains Mono from Google Fonts."""
        index_file = CSS_FILE.parent.parent.parent / "index.html"
        content = index_file.read_text()
        assert "fonts.googleapis.com" in content, (
            "index.html should load fonts from Google Fonts"
        )
        assert "JetBrains+Mono" in content, (
            "index.html should load JetBrains Mono font"
        )


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

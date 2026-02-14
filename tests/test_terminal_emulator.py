"""Tests for the pyte-based terminal emulator."""

import base64

from chad.ui.terminal_emulator import (
    TerminalEmulator,
    get_terminal_text_from_events,
    stream_terminal_text,
    _color_to_rgb,
    ANSI_COLORS,
    DEFAULT_FG,
)


class TestColorConversion:
    """Tests for color conversion utilities."""

    def test_default_color(self):
        """Default/None returns the default value."""
        assert _color_to_rgb(None, DEFAULT_FG) == DEFAULT_FG
        assert _color_to_rgb("default", DEFAULT_FG) == DEFAULT_FG

    def test_basic_ansi_colors(self):
        """Basic 16-color ANSI codes work."""
        assert _color_to_rgb(0, DEFAULT_FG) == ANSI_COLORS[0]
        assert _color_to_rgb(1, DEFAULT_FG) == ANSI_COLORS[1]
        assert _color_to_rgb(15, DEFAULT_FG) == ANSI_COLORS[15]

    def test_256_color_cube(self):
        """256-color cube values work."""
        # Color 16 is the start of the 216-color cube (0,0,0)
        result = _color_to_rgb(16, DEFAULT_FG)
        assert result == (0, 0, 0)

        # Color 231 is the end of the cube (255,255,255)
        result = _color_to_rgb(231, DEFAULT_FG)
        assert result == (255, 255, 255)

    def test_256_color_grayscale(self):
        """256-color grayscale values work."""
        # Color 232 is the start of grayscale
        result = _color_to_rgb(232, DEFAULT_FG)
        assert result[0] == result[1] == result[2] == 8

        # Color 255 is near white
        result = _color_to_rgb(255, DEFAULT_FG)
        assert result[0] == result[1] == result[2] == 238

    def test_hex_color(self):
        """Hex color strings work."""
        assert _color_to_rgb("#ff0000", DEFAULT_FG) == (255, 0, 0)
        assert _color_to_rgb("#00ff00", DEFAULT_FG) == (0, 255, 0)
        assert _color_to_rgb("#0000ff", DEFAULT_FG) == (0, 0, 255)


class TestTerminalEmulator:
    """Tests for the TerminalEmulator class."""

    def test_create_emulator(self):
        """Can create terminal emulator with dimensions."""
        emu = TerminalEmulator(80, 24)
        assert emu.screen.columns == 80
        assert emu.screen.lines == 24

    def test_feed_bytes(self):
        """Can feed bytes to terminal."""
        emu = TerminalEmulator(80, 24)
        emu.feed(b"Hello, World!")
        assert "Hello, World!" in emu.get_text()

    def test_feed_string(self):
        """Can feed strings to terminal."""
        emu = TerminalEmulator(80, 24)
        emu.feed("Hello, World!")
        assert "Hello, World!" in emu.get_text()

    def test_feed_base64(self):
        """Can feed base64-encoded data."""
        emu = TerminalEmulator(80, 24)
        encoded = base64.b64encode(b"Hello!").decode()
        emu.feed_base64(encoded)
        assert "Hello!" in emu.get_text()

    def test_ansi_colors_preserved(self):
        """ANSI color codes are converted to HTML."""
        emu = TerminalEmulator(80, 24)
        # Green text: ESC[32m
        emu.feed("\x1b[32mGreen\x1b[0m")
        html = emu.render_html()
        # Should have color style
        assert "color:rgb" in html
        assert "Green" in html

    def test_bold_text(self):
        """Bold text is rendered with font-weight."""
        emu = TerminalEmulator(80, 24)
        emu.feed("\x1b[1mBold\x1b[0m")
        html = emu.render_html()
        assert "font-weight:bold" in html
        assert "Bold" in html

    def test_cursor_movement(self):
        """Cursor movement sequences work."""
        emu = TerminalEmulator(80, 24)
        # Move cursor to position (5, 10) and write
        emu.feed("\x1b[5;10HText at position")
        text = emu.get_text()
        # The text should appear after spaces
        lines = text.split("\n")
        assert len(lines) >= 5
        assert "Text at position" in lines[4]

    def test_clear_screen(self):
        """Clear screen sequence works."""
        emu = TerminalEmulator(80, 24)
        emu.feed("First line")
        emu.feed("\x1b[2J\x1b[HSecond line")
        text = emu.get_text()
        # After clear, should only have Second line
        assert "Second line" in text
        assert "First line" not in text

    def test_newlines(self):
        """Newlines create separate lines."""
        emu = TerminalEmulator(80, 24)
        emu.feed("Line 1\nLine 2\nLine 3")
        text = emu.get_text()
        lines = text.split("\n")
        assert "Line 1" in lines[0]
        assert "Line 2" in lines[1]
        assert "Line 3" in lines[2]

    def test_resize(self):
        """Terminal can be resized."""
        emu = TerminalEmulator(80, 24)
        emu.resize(120, 50)
        assert emu.screen.columns == 120
        assert emu.screen.lines == 50

    def test_total_bytes_tracked(self):
        """Total bytes fed is tracked."""
        emu = TerminalEmulator(80, 24)
        assert emu.total_bytes == 0
        emu.feed("12345")
        assert emu.total_bytes == 5
        emu.feed("67890")
        assert emu.total_bytes == 10

    def test_html_escapes_special_chars(self):
        """HTML output escapes special characters."""
        emu = TerminalEmulator(80, 24)
        emu.feed("<script>alert('xss')</script>")
        html = emu.render_html()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_render_empty_screen(self):
        """Empty screen renders to minimal output."""
        emu = TerminalEmulator(80, 24)
        html = emu.render_html()
        # Should be minimal (just whitespace or empty)
        assert len(html) < 100


class TestGetTerminalTextFromEvents:
    """Tests for extracting terminal text from events."""

    def test_get_text_single_event(self):
        """Can get text from a single terminal event."""
        events = [
            {"type": "terminal_output", "data": "Hello!"}
        ]
        text = get_terminal_text_from_events(events)
        assert text == "Hello!"

    def test_get_text_multiple_events_returns_last(self):
        """Multiple events returns the last one (full screen state)."""
        events = [
            {"type": "terminal_output", "data": "First screen"},
            {"type": "terminal_output", "data": "Second screen"},
            {"type": "terminal_output", "data": "Final screen"},
        ]
        text = get_terminal_text_from_events(events)
        assert text == "Final screen"

    def test_get_text_ignores_non_terminal_events(self):
        """Non-terminal events are ignored."""
        events = [
            {"type": "session_started", "task_description": "test"},
            {"type": "terminal_output", "data": "Output"},
            {"type": "session_ended", "success": True},
        ]
        text = get_terminal_text_from_events(events)
        assert text == "Output"

    def test_get_text_empty_events(self):
        """Empty event list returns empty string."""
        text = get_terminal_text_from_events([])
        assert text == ""


class TestStreamTerminalText:
    """Tests for streaming terminal text."""

    def test_stream_yields_text(self):
        """Stream yields text from each terminal event."""
        events = [
            {"type": "terminal_output", "data": "Screen A"},
            {"type": "terminal_output", "data": "Screen B"},
        ]
        outputs = list(stream_terminal_text(iter(events)))
        assert len(outputs) == 2
        assert outputs[0] == "Screen A"
        assert outputs[1] == "Screen B"

    def test_stream_ignores_other_events(self):
        """Non-terminal events are skipped."""
        events = [
            {"type": "session_started"},
            {"type": "terminal_output", "data": "Output"},
            {"type": "session_ended"},
        ]
        outputs = list(stream_terminal_text(iter(events)))
        assert len(outputs) == 1
        assert outputs[0] == "Output"


class TestTerminalEmulatorEdgeCases:
    """Edge case tests for terminal emulator."""

    def test_invalid_base64_handled(self):
        """Invalid base64 data doesn't crash."""
        emu = TerminalEmulator(80, 24)
        emu.feed_base64("not valid base64!!!")
        # Should not crash, just ignore
        assert emu.get_text() == ""

    def test_unicode_handling(self):
        """Unicode characters work correctly."""
        emu = TerminalEmulator(80, 24)
        emu.feed("Hello 世界 🎉")
        text = emu.get_text()
        assert "Hello" in text
        # CJK characters take 2 columns each, so pyte may add padding
        # Just verify the characters are present somewhere
        assert "世" in text
        assert "界" in text

    def test_very_long_line(self):
        """Very long lines are handled."""
        emu = TerminalEmulator(80, 24)
        long_text = "x" * 1000
        emu.feed(long_text)
        # Should wrap or truncate, not crash
        text = emu.get_text()
        assert "x" in text

    def test_many_colors(self):
        """Many color changes don't break rendering."""
        emu = TerminalEmulator(80, 24)
        for i in range(256):
            emu.feed(f"\x1b[38;5;{i}m{i} ")
        emu.feed("\x1b[0m")
        html = emu.render_html()
        assert "color:rgb" in html

    def test_private_sgr_sequence_is_ignored_without_crashing(self):
        """Private CSI m sequences should not crash pyte parsing."""
        emu = TerminalEmulator(80, 24)
        emu.feed("\x1b[?2004mHello")
        assert "Hello" in emu.get_text()


class TestTerminalLayoutForDisplay:
    """Tests that terminal output renders correctly for UI display.

    These tests verify that the terminal configuration produces output
    suitable for display in a typical UI panel without excessive
    horizontal scrolling or garbled layout.
    """

    def test_default_terminal_width_is_reasonable_fallback(self):
        """Default TERMINAL_COLS should be a reasonable fallback.

        The actual terminal width is dynamically calculated from the container
        width using JavaScript in gradio_ui.py. This constant is just a fallback
        when dynamic calculation is unavailable. 120 columns is safe for most screens.
        """
        from chad.ui.terminal_emulator import TERMINAL_COLS
        assert 80 <= TERMINAL_COLS <= 160, (
            f"TERMINAL_COLS={TERMINAL_COLS} should be 80-160 as a reasonable "
            "fallback when dynamic width calculation is unavailable."
        )

    def test_tui_box_renders_within_terminal_width(self):
        """TUI-style box drawing should render within terminal width.

        When a CLI tool draws a box (like status panel), the text should
        stay within the terminal width without scattering across the screen.
        """
        from chad.ui.terminal_emulator import TERMINAL_COLS, TERMINAL_ROWS
        emu = TerminalEmulator(TERMINAL_COLS, TERMINAL_ROWS)

        # Simulate a TUI box being drawn (like codex/claude status panels)
        box_width = 80  # Use a reasonable fixed width for the box
        emu.feed("─" * box_width + "\n")
        emu.feed("│ Status: Running" + " " * (box_width - 18) + "│\n")
        emu.feed("─" * box_width + "\n")

        html = emu.render_html()
        lines = html.split("\n")

        # Each line should be ≤ TERMINAL_COLS characters of actual content
        for line in lines:
            # Strip HTML tags to count visible characters
            import re
            text_only = re.sub(r'<[^>]+>', '', line)
            assert len(text_only) <= TERMINAL_COLS + 5, (  # +5 for tolerance
                f"Line exceeds terminal width: {len(text_only)} > {TERMINAL_COLS}"
            )

    def test_cursor_positioned_text_stays_within_bounds(self):
        """Text positioned via cursor sequences should stay within terminal width."""
        from chad.ui.terminal_emulator import TERMINAL_COLS, TERMINAL_ROWS
        emu = TerminalEmulator(TERMINAL_COLS, TERMINAL_ROWS)

        # Move cursor to various positions and write
        emu.feed("\x1b[1;1HStart")  # Row 1, Col 1
        emu.feed(f"\x1b[2;{TERMINAL_COLS - 5}HEnd")  # Row 2, near end

        html = emu.render_html()
        lines = html.split("\n")

        # Text should be within bounds
        for line in lines:
            import re
            text_only = re.sub(r'<[^>]+>', '', line)
            assert len(text_only) <= TERMINAL_COLS + 5, (
                f"Positioned text exceeds bounds: {len(text_only)} chars"
            )

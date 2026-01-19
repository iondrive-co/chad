"""Terminal emulator using pyte for accurate layout rendering.

This module provides a terminal emulator that maintains screen state,
handling cursor movement, colors, and other terminal sequences properly.
It's used by the Gradio UI to render agent output with preserved layout.
"""

from __future__ import annotations

import base64
import re
from html import escape
from typing import Iterator

import pyte


# Terminal geometry constants - default values used when client doesn't provide dimensions.
# When possible, clients should pass their actual terminal size to start_task() and
# send resize signals when the terminal is resized (via SIGWINCH on Unix).
# These defaults are reasonable for fallback when dimensions aren't available.
TERMINAL_COLS = 80
TERMINAL_ROWS = 24


# ANSI 16-color to RGB mapping (One Dark theme-inspired)
ANSI_COLORS = {
    0: (40, 44, 52),      # black
    1: (224, 108, 117),   # red
    2: (152, 195, 121),   # green
    3: (229, 192, 123),   # yellow
    4: (97, 175, 239),    # blue
    5: (198, 120, 221),   # magenta
    6: (86, 182, 194),    # cyan
    7: (171, 178, 191),   # white
    8: (92, 99, 112),     # bright black
    9: (224, 108, 117),   # bright red
    10: (152, 195, 121),  # bright green
    11: (229, 192, 123),  # bright yellow
    12: (97, 175, 239),   # bright blue
    13: (198, 120, 221),  # bright magenta
    14: (86, 182, 194),   # bright cyan
    15: (255, 255, 255),  # bright white
}

# Default colors
DEFAULT_FG = (171, 178, 191)  # Light gray
DEFAULT_BG = None  # Transparent (use container background)

# Named color mapping (pyte uses lowercase names)
NAMED_COLORS = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "brown": 3,  # Alias
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "brightblack": 8,
    "brightred": 9,
    "brightgreen": 10,
    "brightyellow": 11,
    "brightblue": 12,
    "brightmagenta": 13,
    "brightcyan": 14,
    "brightwhite": 15,
}


def _color_to_rgb(color: str | None, default: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
    """Convert pyte color to RGB tuple.

    Args:
        color: Color value from pyte (number, hex string, or "default")
        default: Default color to use if color is None or "default"

    Returns:
        RGB tuple or None for transparent
    """
    if color is None or color == "default":
        return default

    if isinstance(color, str):
        # Hex color like "#ff0000"
        if color.startswith("#"):
            try:
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
                return (r, g, b)
            except ValueError:
                return default
        # Named color (pyte uses lowercase names like "green", "red")
        color_lower = color.lower()
        if color_lower in NAMED_COLORS:
            return ANSI_COLORS.get(NAMED_COLORS[color_lower], default)
        # Number as string
        try:
            color = int(color)
        except ValueError:
            return default

    if isinstance(color, int):
        if 0 <= color <= 15:
            return ANSI_COLORS.get(color, default)
        elif 16 <= color <= 231:
            # 216-color cube
            color -= 16
            r = (color // 36) * 51
            g = ((color % 36) // 6) * 51
            b = (color % 6) * 51
            return (r, g, b)
        elif 232 <= color <= 255:
            # Grayscale
            gray = (color - 232) * 10 + 8
            return (gray, gray, gray)

    return default


class TerminalEmulator:
    """Terminal emulator using pyte for accurate screen rendering.

    Maintains terminal state (screen buffer, cursor position, colors)
    and renders to HTML with proper styling.
    """

    def __init__(self, cols: int = TERMINAL_COLS, rows: int = TERMINAL_ROWS):
        """Initialize the terminal emulator.

        Args:
            cols: Number of columns (width)
            rows: Number of rows (height)
        """
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self._total_bytes = 0

    def feed(self, data: bytes | str) -> None:
        """Feed data into the terminal.

        Args:
            data: Terminal data (bytes or string)
        """
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = data

        # Normalize bare LFs to CRLF so the cursor returns to column 0 on new lines.
        text = re.sub(r"(?<!\r)\n", "\r\n", text)

        self._total_bytes += len(text.encode("utf-8"))
        self.stream.feed(text)

    def feed_base64(self, encoded: str) -> None:
        """Feed base64-encoded data into the terminal.

        Args:
            encoded: Base64-encoded terminal data
        """
        try:
            data = base64.b64decode(encoded)
            self.feed(data)
        except Exception:
            pass

    def resize(self, cols: int, rows: int) -> None:
        """Resize the terminal.

        Args:
            cols: New column count
            rows: New row count
        """
        self.screen.resize(rows, cols)

    def render_html(self, include_cursor: bool = False) -> str:
        """Render the terminal screen to HTML.

        Args:
            include_cursor: Whether to show cursor position

        Returns:
            HTML string with styled terminal content
        """
        lines = []

        for y in range(self.screen.lines):
            # First, find the last non-whitespace column on this line
            last_nonspace = -1
            for x in range(self.screen.columns - 1, -1, -1):
                char_data = self.screen.buffer[y][x].data
                if char_data and char_data.strip():
                    last_nonspace = x
                    break

            # Skip entirely empty lines
            if last_nonspace < 0:
                continue

            # Render up to and including the last non-whitespace character
            line_spans = []
            x = 0

            while x <= last_nonspace:
                char = self.screen.buffer[y][x]

                # Collect consecutive characters with same style
                span_chars = []
                span_style = self._get_char_style(char)

                while x <= last_nonspace:
                    char = self.screen.buffer[y][x]
                    if self._get_char_style(char) != span_style:
                        break

                    # Handle cursor
                    if include_cursor and y == self.screen.cursor.y and x == self.screen.cursor.x:
                        span_chars.append(f'<span class="cursor">{escape(char.data or " ")}</span>')
                    else:
                        span_chars.append(escape(char.data or " "))
                    x += 1

                if span_chars:
                    content = "".join(span_chars)
                    if span_style:
                        line_spans.append(f'<span style="{span_style}">{content}</span>')
                    else:
                        line_spans.append(content)

            line = "".join(line_spans)
            if line:
                lines.append(line)

        # Add at least one line if buffer was all whitespace
        if not lines:
            lines.append(" ")

        return "\n".join(lines)

    def _get_char_style(self, char: pyte.screens.Char) -> str:
        """Get CSS style string for a character.

        Args:
            char: pyte character with style attributes

        Returns:
            CSS style string (may be empty)
        """
        styles = []

        # Foreground color - always include to ensure visibility on dark backgrounds
        # (CSS inheritance may fail due to specificity issues in chat bubbles)
        fg = _color_to_rgb(char.fg, DEFAULT_FG)
        if fg:
            styles.append(f"color:rgb({fg[0]},{fg[1]},{fg[2]})")

        # Background color (skip for default/transparent)
        bg = _color_to_rgb(char.bg, DEFAULT_BG)
        if bg:
            styles.append(f"background:rgb({bg[0]},{bg[1]},{bg[2]})")

        # Text attributes
        if char.bold:
            styles.append("font-weight:bold")
        if char.italics:
            styles.append("font-style:italic")
        if char.underscore:
            styles.append("text-decoration:underline")
        if char.reverse:
            # Swap fg/bg for reverse video
            if fg:
                styles.append(f"background:rgb({fg[0]},{fg[1]},{fg[2]})")
            if bg:
                styles.append(f"color:rgb({bg[0]},{bg[1]},{bg[2]})")
            else:
                styles.append("color:rgb(40,44,52)")  # Dark background color

        return ";".join(styles)

    def get_text(self) -> str:
        """Get plain text content of the screen.

        Returns:
            Plain text without styling
        """
        lines = []
        for y in range(self.screen.lines):
            line = "".join(
                self.screen.buffer[y][x].data or " "
                for x in range(self.screen.columns)
            ).rstrip()
            lines.append(line)

        # Remove trailing empty lines
        while lines and not lines[-1]:
            lines.pop()

        return "\n".join(lines)

    @property
    def total_bytes(self) -> int:
        """Total bytes fed into the terminal."""
        return self._total_bytes


def get_terminal_text_from_events(events: list[dict]) -> str:
    """Get the final terminal text from a list of events.

    Args:
        events: List of event dicts (from EventLog)

    Returns:
        Final terminal text content (from the last terminal_output event)
    """
    terminal_events = [e for e in events if e.get("type") == "terminal_output"]
    if terminal_events:
        return terminal_events[-1].get("data", "")
    return ""


def stream_terminal_text(events: Iterator[dict]) -> Iterator[str]:
    """Stream terminal text as events arrive.

    Args:
        events: Iterator of event dicts

    Yields:
        Terminal text from each terminal_output event
    """
    for event in events:
        if event.get("type") == "terminal_output":
            data = event.get("data", "")
            if data:
                yield data

"""Unix terminal I/O helpers using termios/tty/select."""

import select
import sys
import termios
import tty


def save_terminal():
    """Save terminal state. Returns settings or None if not a TTY."""
    try:
        return termios.tcgetattr(sys.stdin)
    except termios.error:
        return None


def restore_terminal(old_settings):
    """Restore terminal state from saved settings."""
    if old_settings:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def enter_raw_mode():
    """Put terminal into raw mode for passthrough."""
    tty.setraw(sys.stdin.fileno())


def poll_stdin():
    """Non-blocking check for stdin input. Returns True if data is available."""
    rlist, _, _ = select.select([sys.stdin], [], [], 0)
    return sys.stdin in rlist

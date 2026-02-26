"""Windows terminal I/O helpers using msvcrt."""


def save_terminal():
    """Save terminal state — no-op on Windows (no termios)."""
    return None


def restore_terminal(old_settings):
    """Restore terminal state — no-op on Windows."""
    pass


def enter_raw_mode():
    """Enter raw mode — no-op on Windows (msvcrt handles input differently)."""
    pass


def poll_stdin():
    """Non-blocking check for stdin input on Windows."""
    try:
        import msvcrt
        return msvcrt.kbhit()
    except ImportError:
        return False

"""PTY streaming service — dispatches to platform-specific implementation."""

import sys

if sys.platform == "win32":
    from .pty_stream_win import *  # noqa: F401,F403
else:
    from .pty_stream_unix import *  # noqa: F401,F403

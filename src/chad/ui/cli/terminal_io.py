"""Platform-dispatched terminal I/O helpers."""

import sys

if sys.platform == "win32":
    from ._terminal_io_win import *  # noqa: F401,F403
else:
    from ._terminal_io_unix import *  # noqa: F401,F403

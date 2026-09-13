"""A system tray icon for Chad, with no GUI toolkit behind it.

Chad ships as a PyInstaller bundle, so a tray that needed GTK or Qt would be a
tray that only worked where those happened to be installed. Each backend talks
to the platform's own tray service directly: D-Bus StatusNotifierItem on Linux,
AppKit through ctypes on macOS, Shell_NotifyIcon through ctypes on Windows.

    tray = Tray("Chad", [MenuItem("Open Chad", open_browser),
                         MenuItem("Quit Chad", stop_everything)])
    tray.start()          # blocks on the platform's event loop
"""

from __future__ import annotations

import sys
from typing import Callable


class Unavailable(RuntimeError):
    """Raised when this system has no tray for an icon to live in."""


class MenuItem:
    """A row in the tray menu.

    No action makes the row read-only: it is there to be read, and every
    backend publishes it as disabled so a click cannot land on it.
    """

    def __init__(
        self,
        label: str,
        action: Callable[[], None] | None = None,
        *,
        separator: bool = False,
    ) -> None:
        self.label = label
        self.action = action
        self.separator = separator

    @property
    def enabled(self) -> bool:
        return self.action is not None

    @classmethod
    def rule(cls) -> "MenuItem":
        """A dividing line between sections of the menu."""
        return cls("", separator=True)


class Tray:
    """A tray icon and its menu. The first item is also the click action.

    Pass ``on_open`` to build the menu fresh each time it is shown — that is
    what keeps changing numbers in it current, since a menu is only read when
    the platform is about to draw it.
    """

    def __init__(
        self,
        title: str,
        items: list[MenuItem],
        on_open: Callable[[], list[MenuItem]] | None = None,
        tooltip: Callable[[], str] | None = None,
    ) -> None:
        self.title = title
        self.items = items
        self.on_open = on_open
        self._tooltip = tooltip
        self._backend = None

    def tooltip(self) -> str:
        """What to show when the pointer rests on the icon.

        Only what the tooltip provider says: the icon's own title stands in
        when there is nothing to report, rather than heading the text a viewer
        actually wanted to read. Read on the platform's own thread, so the
        provider must answer from what it knows rather than going to look.
        """
        text = self._tooltip() if self._tooltip is not None else ""
        return text or self.title

    def refresh(self) -> bool:
        """Rebuild the menu before it is shown. True when the labels changed."""
        if self.on_open is None:
            return False
        before = [item.label for item in self.items]
        self.items = self.on_open()
        return [item.label for item in self.items] != before

    def notify_changed(self) -> None:
        """Rebuild the menu and, where a platform needs telling, tell it.

        Safe to call from another thread: it is how a background reading
        reaches a menu that is already on screen.
        """
        if self.refresh() and self._backend is not None:
            self._backend.menu_changed()

    def click(self, item_id: int) -> None:
        """Run the action of a 1-based menu item id, if it has one."""
        if 1 <= item_id <= len(self.items):
            item = self.items[item_id - 1]
            if item.action is not None:
                item.action()

    def fire_default(self) -> None:
        """Run the first action in the menu, for a plain click on the icon.

        The first rows are the usage table, which has no actions, so this is
        the first row that does — Open Chad.
        """
        for item in self.items:
            if item.action is not None:
                item.action()
                return

    def start(self) -> None:
        """Show the icon and run the platform event loop until stop()."""
        self._backend = _backend(self)
        try:
            self._backend.start()
        except Exception as exc:
            self._backend = None
            if isinstance(exc, Unavailable):
                raise
            raise Unavailable(str(exc)) from exc

    def stop(self) -> None:
        """Ask the event loop to finish. Safe to call from another thread."""
        if self._backend is not None:
            self._backend.stop()


def _backend(owner: Tray):
    if sys.platform == "darwin":
        from . import macos
        return macos.Backend(owner)
    if sys.platform == "win32":
        from . import windows
        return windows.Backend(owner)
    if sys.platform.startswith("linux"):
        from . import linux
        return linux.Backend(owner)
    raise Unavailable(f"no tray backend for {sys.platform}")


def available() -> bool:
    """True when a tray icon can be shown on this system right now."""
    try:
        if sys.platform == "darwin":
            from . import macos
            return macos.available()
        if sys.platform == "win32":
            from . import windows
            return windows.available()
        if sys.platform.startswith("linux"):
            from . import linux
            return linux.available()
    except Exception:
        return False
    return False

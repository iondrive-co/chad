"""Linux tray icon: an org.kde.StatusNotifierItem exported on the session bus.

Adapted from the sibling xenia project, which implements the tray protocols
directly so that no GUI toolkit has to be installed alongside the app.
"""

from __future__ import annotations

import os
import threading
import time

from . import Unavailable
from . import dbus, icon

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
INTROSPECT_IFACE = "org.freedesktop.DBus.Introspectable"
WATCHER = "org.kde.StatusNotifierWatcher"

# A Chad started at login can beat the panel to the session bus, so the
# watcher is waited for rather than demanded on the first look.
WATCHER_WAIT_SECONDS = 30


class Backend:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.conn: dbus.Connection | None = None
        self._stop = threading.Event()
        # Hosts cache the menu and re-read it when the revision moves on.
        self._revision = 1

    def start(self) -> None:
        try:
            self.conn = dbus.Connection().connect()
        except Exception as exc:
            raise Unavailable(f"no session bus: {exc}") from exc

        if not self._wait_for_watcher():
            self.conn.close()
            raise Unavailable(
                f"no org.kde.StatusNotifierWatcher on the session bus after "
                f"{WATCHER_WAIT_SECONDS}s — this desktop is not running a tray host")

        self.conn.export(ITEM_PATH, ITEM_IFACE, self._item_method)
        self.conn.export(ITEM_PATH, PROPS_IFACE, self._item_props)
        self.conn.export(ITEM_PATH, INTROSPECT_IFACE, self._introspect)
        self.conn.export(MENU_PATH, MENU_IFACE, self._menu_method)
        self.conn.export(MENU_PATH, PROPS_IFACE, self._menu_props)
        self.conn.export(MENU_PATH, INTROSPECT_IFACE, self._introspect)

        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.conn.request_name(name)
        try:
            self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER,
                           "RegisterStatusNotifierItem", "s", [name])
        except dbus.DBusError as exc:
            self.conn.close()
            raise Unavailable(f"the tray host refused registration: {exc}") from exc

        self._run_loop()

    def _wait_for_watcher(self) -> bool:
        """True once a tray host owns the watcher name, waiting for it to appear."""
        deadline = time.monotonic() + WATCHER_WAIT_SECONDS
        while True:
            if self.conn.name_has_owner(WATCHER):
                return True
            if time.monotonic() >= deadline or self._stop.is_set():
                return False
            self._stop.wait(0.5)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(0.5)

        if self.conn is not None:
            self.conn.close()

    def stop(self) -> None:
        self._stop.set()

    def _item_property(self, name: str) -> dbus.Variant | None:
        owner = self.owner
        if name == "Category":
            return dbus.Variant("s", "ApplicationStatus")
        if name in ("Id", "Title"):
            return dbus.Variant("s", owner.title)
        if name == "Status":
            return dbus.Variant("s", "Active")
        if name in ("IconName", "OverlayIconName", "AttentionIconName"):
            return dbus.Variant("s", "")
        if name == "IconPixmap":
            width, height, argb = icon.argb_for_dbus()
            return dbus.Variant("a(iiay)", [(width, height, argb)])
        if name == "ToolTip":
            # (icon name, icon data, title, description). Everything goes in
            # the title: xfce4-panel's tray draws the description under a bold
            # title with a blank line between them, and shows no tooltip at
            # all when the title is empty. Verified by hovering the icon.
            return dbus.Variant("(sa(iiay)ss)", ("", [], owner.tooltip(), ""))
        if name == "ItemIsMenu":
            return dbus.Variant("b", False)
        if name == "Menu":
            return dbus.Variant("o", MENU_PATH)
        return None

    _ITEM_PROPS = ("Category", "Id", "Title", "Status", "IconName", "IconPixmap",
                   "OverlayIconName", "AttentionIconName", "ToolTip",
                   "ItemIsMenu", "Menu")

    def _menu_property(self, name: str) -> dbus.Variant | None:
        return {
            "Version": dbus.Variant("u", 3),
            "TextDirection": dbus.Variant("s", "ltr"),
            "Status": dbus.Variant("s", "normal"),
            "IconThemePath": dbus.Variant("as", []),
        }.get(name)

    _MENU_PROPS = ("Version", "TextDirection", "Status", "IconThemePath")

    def _props_handler(self, message, lookup, names):
        if message.member == "Get":
            _iface, prop = message.body
            value = lookup(prop)
            if value is None:
                raise dbus.DBusError(f"no such property: {prop}")
            return "v", [value]
        if message.member == "GetAll":
            found = {name: lookup(name) for name in names}
            return "a{sv}", [{k: v for k, v in found.items() if v is not None}]
        if message.member == "Set":
            raise dbus.DBusError("all properties are read-only")
        raise dbus.DBusError(f"unknown method: {message.member}")

    def _item_props(self, message):
        return self._props_handler(message, self._item_property, self._ITEM_PROPS)

    def _menu_props(self, message):
        return self._props_handler(message, self._menu_property, self._MENU_PROPS)

    def _introspect(self, message):
        return "s", ["<node/>"]

    def _item_method(self, message):
        if message.member in ("Activate", "SecondaryActivate"):
            self.owner.fire_default()
        return "", []

    def _item_properties_for(self, item):
        if item.separator:
            return {
                "type": dbus.Variant("s", "separator"),
                "visible": dbus.Variant("b", True),
            }
        return {
            "label": dbus.Variant("s", item.label),
            "enabled": dbus.Variant("b", item.enabled),
            "visible": dbus.Variant("b", True),
        }

    def _layout(self):
        children = [
            dbus.Variant("(ia{sv}av)", (index, self._item_properties_for(item), []))
            for index, item in enumerate(self.owner.items, start=1)
        ]
        return (0, {"children-display": dbus.Variant("s", "submenu")}, children)

    def _rebuild(self) -> bool:
        """Refresh the menu for a panel that is about to draw it."""
        changed = self.owner.refresh()
        if changed:
            self._revision += 1
        return changed

    def menu_changed(self) -> None:
        """Tell the panel to re-read the menu, for numbers that arrived late.

        The parent argument is a signed int: sent as "uu" the signal is
        dropped on the floor by a panel, silently, and the menu it drew at
        registration is the menu it keeps.
        """
        if self.conn is None or self._stop.is_set():
            return
        self._revision += 1
        self.conn.emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", "ui", [self._revision, 0])
        # The pointer-over text is a property of the icon, not the menu, and a
        # panel only re-reads it when told.
        self.conn.emit(ITEM_PATH, ITEM_IFACE, "NewToolTip")

    def _menu_method(self, message):
        member = message.member

        if member == "GetLayout":
            return "u(ia{sv}av)", [self._revision, self._layout()]

        if member == "GetGroupProperties":
            return "a(ia{sv})", [[
                (index, self._item_properties_for(item))
                for index, item in enumerate(self.owner.items, start=1)
            ]]

        if member == "GetProperty":
            item_id, name = message.body
            props = {}
            if 1 <= item_id <= len(self.owner.items):
                props = self._item_properties_for(self.owner.items[item_id - 1])
            return "v", [props.get(name, dbus.Variant("s", ""))]

        if member == "Event":
            item_id, event_id = message.body[0], message.body[1]
            if event_id == "clicked":
                self.owner.click(item_id)
            return "", []

        if member == "EventGroup":
            for entry in message.body[0]:
                if entry[1] == "clicked":
                    self.owner.click(entry[0])
            return "ai", [[]]

        if member == "AboutToShow":
            return "b", [self._rebuild()]

        if member == "AboutToShowGroup":
            return "aiai", [[0] if self._rebuild() else [], []]

        raise dbus.DBusError(f"unknown method: {member}")


def available() -> bool:
    """True when a tray host is on the session bus right now.

    The bus alone is not enough to answer with: a headless server reached over
    a tunnel has a user session bus and no panel at all, and offering it
    start-at-login would write an entry that never shows anything.
    """
    try:
        conn = dbus.Connection().connect()
    except Exception:
        return False
    try:
        return conn.name_has_owner(WATCHER)
    except Exception:
        return False
    finally:
        conn.close()

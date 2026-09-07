"""macOS tray icon: an NSStatusItem driven through the Objective-C runtime.

Adapted from the sibling xenia project, which reaches AppKit through ctypes so
that no PyObjC install stands between the app and its status bar.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

from . import Unavailable
from . import icon

objc_id = ctypes.c_void_p
SEL = ctypes.c_void_p
Class = ctypes.c_void_p

NSSquareStatusItemLength = -2.0
NSVariableStatusItemLength = -1.0


class _Runtime:
    def __init__(self) -> None:
        objc_path = ctypes.util.find_library("objc")
        if objc_path is None:
            raise Unavailable("libobjc not found — is this really macOS?")
        self.objc = ctypes.cdll.LoadLibrary(objc_path)

        appkit = ctypes.util.find_library("AppKit")
        if appkit is None:
            raise Unavailable("AppKit not found")
        ctypes.cdll.LoadLibrary(appkit)

        self.objc.objc_getClass.restype = Class
        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = SEL
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.objc_allocateClassPair.restype = Class
        self.objc.objc_allocateClassPair.argtypes = [Class, ctypes.c_char_p, ctypes.c_size_t]
        self.objc.objc_registerClassPair.argtypes = [Class]
        self.objc.class_addMethod.restype = ctypes.c_bool
        self.objc.class_addMethod.argtypes = [Class, SEL, ctypes.c_void_p, ctypes.c_char_p]

        self._senders: dict[tuple, ctypes.CFUNCTYPE] = {}

    def cls(self, name: str) -> Class:
        handle = self.objc.objc_getClass(name.encode())
        if not handle:
            raise Unavailable(f"Objective-C class {name} is not registered")
        return handle

    def sel(self, name: str) -> SEL:
        return self.objc.sel_registerName(name.encode())

    def send(self, receiver, selector: str, *args, restype=objc_id, argtypes=()):
        key = (restype, argtypes)
        sender = self._senders.get(key)
        if sender is None:
            prototype = ctypes.CFUNCTYPE(restype, objc_id, SEL, *argtypes)
            sender = prototype(("objc_msgSend", self.objc))
            self._senders[key] = sender
        return sender(receiver, self.sel(selector), *args)


class Backend:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.rt: _Runtime | None = None
        self._app = None
        self._item = None
        self._callbacks: list = []
        self._targets: dict[int, objc_id] = {}
        self._tooltip = None
        self._stopped = False

    def start(self) -> None:
        if sys.platform != "darwin":
            raise Unavailable("the macOS backend only runs on macOS")

        self.rt = rt = _Runtime()

        app_cls = rt.cls("NSApplication")
        self._app = rt.send(app_cls, "sharedApplication")
        NSApplicationActivationPolicyAccessory = 1
        rt.send(self._app, "setActivationPolicy:",
                ctypes.c_long(NSApplicationActivationPolicyAccessory),
                restype=ctypes.c_bool, argtypes=[ctypes.c_long])

        status_bar = rt.send(rt.cls("NSStatusBar"), "systemStatusBar")
        self._item = rt.send(status_bar, "statusItemWithLength:",
                             ctypes.c_double(NSSquareStatusItemLength),
                             argtypes=[ctypes.c_double])
        if not self._item:
            raise Unavailable("the system status bar refused a new item")
        rt.send(self._item, "retain")

        self._apply_icon()
        self._apply_menu()
        self._run_loop()

    def _run_loop(self) -> None:
        rt = self.rt
        date_cls = rt.cls("NSDate")
        run_loop = rt.send(rt.cls("NSRunLoop"), "currentRunLoop")
        mode = self._nsstring("kCFRunLoopDefaultMode")

        while not self._stopped:
            until = rt.send(date_cls, "dateWithTimeIntervalSinceNow:",
                            ctypes.c_double(0.3), argtypes=[ctypes.c_double])
            rt.send(run_loop, "runMode:beforeDate:", mode, until,
                    restype=ctypes.c_bool, argtypes=[objc_id, objc_id])
            # AppKit is a main-thread affair, and this is the main thread: a
            # reading that landed on a worker gets applied from here.
            self._sync_tooltip()

    def stop(self) -> None:
        self._stopped = True

    def _nsstring(self, text: str):
        rt = self.rt
        return rt.send(rt.cls("NSString"), "stringWithUTF8String:",
                       text.encode(), argtypes=[ctypes.c_char_p])

    def _apply_icon(self) -> None:
        rt = self.rt
        png = icon.render()

        data = rt.send(rt.cls("NSData"), "dataWithBytes:length:",
                       png, ctypes.c_ulong(len(png)),
                       argtypes=[ctypes.c_char_p, ctypes.c_ulong])
        image = rt.send(rt.send(rt.cls("NSImage"), "alloc"),
                        "initWithData:", data, argtypes=[objc_id])
        if not image:
            return

        size = getattr(icon, "SIZE", 32) / 2.0
        rt.send(image, "setSize:", _NSSize(size, size), argtypes=[_NSSize])
        rt.send(image, "setTemplate:", ctypes.c_bool(False),
                restype=None, argtypes=[ctypes.c_bool])

        button = rt.send(self._item, "button")
        if button:
            rt.send(button, "setImage:", image, restype=None, argtypes=[objc_id])
        else:
            rt.send(self._item, "setImage:", image, restype=None, argtypes=[objc_id])
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        """Put the current pointer-over text on the icon, if it has changed."""
        text = self.owner.tooltip()
        if text == self._tooltip:
            return
        self._tooltip = text
        button = self.rt.send(self._item, "button")
        target = button or self._item
        self.rt.send(target, "setToolTip:", self._nsstring(text),
                     restype=None, argtypes=[objc_id])

    def _apply_menu(self) -> None:
        rt = self.rt
        menu = rt.send(rt.send(rt.cls("NSMenu"), "alloc"), "init")
        rt.send(menu, "setAutoenablesItems:", ctypes.c_bool(False),
                restype=None, argtypes=[ctypes.c_bool])
        # AppKit asks the delegate to bring the menu up to date each time it
        # is about to open, which is where changing numbers in it come from.
        rt.send(menu, "setDelegate:", self._menu_delegate(),
                restype=None, argtypes=[objc_id])
        self._fill_menu(menu)
        rt.send(self._item, "setMenu:", menu, restype=None, argtypes=[objc_id])

    def _fill_menu(self, menu) -> None:
        rt = self.rt
        rt.send(menu, "removeAllItems", restype=None)

        for index, item in enumerate(self.owner.items):
            if item.separator:
                rt.send(menu, "addItem:", rt.send(rt.cls("NSMenuItem"), "separatorItem"),
                        restype=None, argtypes=[objc_id])
                continue

            entry = rt.send(
                rt.send(rt.cls("NSMenuItem"), "alloc"),
                "initWithTitle:action:keyEquivalent:",
                self._nsstring(item.label), None, self._nsstring(""),
                argtypes=[objc_id, SEL, objc_id])

            # setAutoenablesItems: is off, so a read-only row stays greyed.
            rt.send(entry, "setEnabled:", ctypes.c_bool(item.enabled),
                    restype=None, argtypes=[ctypes.c_bool])
            if item.enabled:
                target = self._target_for(index)
                rt.send(entry, "setTarget:", target, restype=None, argtypes=[objc_id])
                rt.send(entry, "setAction:", rt.sel("invoke:"),
                        restype=None, argtypes=[SEL])
            rt.send(menu, "addItem:", entry, restype=None, argtypes=[objc_id])

    def menu_changed(self) -> None:
        """Nothing to do here: the delegate refills the menu when it opens, and
        the run loop puts the new pointer-over text on the icon."""

    def _menu_delegate(self):
        def needs_update(_self, _sel, menu):
            self.owner.refresh()
            self._fill_menu(menu)

        return self._objc_object("ChadTrayMenuDelegate", "menuNeedsUpdate:", needs_update)

    def _target_for(self, index: int):
        """The click target for a menu row, made once and kept.

        Rows are rebuilt every time the menu opens, so a target per open would
        leak an Objective-C object each time the user looked at the menu.
        """
        target = self._targets.get(index)
        if target is None:
            def handler(_self, _sel, _sender, row=index):
                self.owner.click(row + 1)

            target = self._objc_object(f"ChadTrayTarget{index}", "invoke:", handler)
            self._targets[index] = target
        return target

    def _objc_object(self, name: str, selector: str, handler):
        """An Objective-C object with one method, wired to a Python callable."""
        rt = self.rt
        class_name = f"{name}_{id(self):x}".encode()
        cls = rt.objc.objc_allocateClassPair(rt.cls("NSObject"), class_name, 0)
        if not cls:
            cls = rt.cls(class_name.decode())

        prototype = ctypes.CFUNCTYPE(None, objc_id, SEL, objc_id)
        callback = prototype(handler)
        rt.objc.class_addMethod(cls, rt.sel(selector),
                                ctypes.cast(callback, ctypes.c_void_p), b"v@:@")
        rt.objc.objc_registerClassPair(cls)

        # The callback is what the class points at: dropping it would leave
        # AppKit calling into freed memory the next time the menu opens.
        self._callbacks.append(callback)
        return rt.send(rt.send(cls, "alloc"), "init")


class _NSSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    def __init__(self, width: float, height: float) -> None:
        super().__init__(width, height)


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        return ctypes.util.find_library("objc") is not None
    except Exception:
        return False

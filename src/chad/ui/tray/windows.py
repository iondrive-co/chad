"""Windows tray icon: a Shell_NotifyIcon on a hidden message window.

Reached through ctypes for the same reason as the other backends — a
PyInstaller bundle should not need a GUI toolkit beside it. The window is
created but never shown; it exists to receive the icon's callback message and
to own the popup menu.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from . import Unavailable
from . import icon

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000

# Our own messages: the icon's callback, and stop() asking the loop to end.
WM_TRAY_CALLBACK = WM_APP + 1
WM_TRAY_QUIT = WM_APP + 2

NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

CW_USEDEFAULT = -0x80000000
WS_OVERLAPPED = 0x00000000

# Menu item ids start above 0 because TrackPopupMenu returns 0 for "dismissed".
FIRST_MENU_ID = 100

# szTip holds 128 WCHARs including the terminator, so this is what fits.
TIP_MAX_CHARS = 127

# A window procedure is stdcall, which is what WINFUNCTYPE builds. It exists
# only on Windows; CFUNCTYPE stands in elsewhere purely so this module still
# imports for tests and linters on the platforms that never call into it.
_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

# LRESULT is pointer-sized. c_long would truncate it on 64-bit Windows, which
# is the same trap every handle below is annotated to avoid.
LRESULT = ctypes.c_ssize_t

WNDPROC = _CALLBACK(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _declare(user32, shell32, kernel32) -> None:
    """Give ctypes the real signatures before any of these are called.

    Without this every handle comes back through the default c_int return
    type, which silently truncates a 64-bit HWND or HICON to nonsense — the
    window then fails to create, or the icon never appears.
    """
    HWND, HMENU = wintypes.HWND, wintypes.HMENU
    UINT, DWORD, INT = wintypes.UINT, wintypes.DWORD, ctypes.c_int

    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.CreateWindowExW.restype = HWND
    user32.CreateWindowExW.argtypes = [
        DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD,
        INT, INT, INT, INT, HWND, HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.DestroyWindow.argtypes = [HWND]
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.RegisterWindowMessageW.restype = UINT
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, UINT, INT, INT, UINT,
    ]
    user32.CreatePopupMenu.restype = HMENU
    user32.AppendMenuW.argtypes = [HMENU, UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.DestroyMenu.argtypes = [HMENU]
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    # With TPM_RETURNCMD the return is the chosen menu id, not a boolean.
    user32.TrackPopupMenu.restype = INT
    user32.TrackPopupMenu.argtypes = [
        HMENU, UINT, INT, INT, INT, HWND, wintypes.LPVOID,
    ]

    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [DWORD, ctypes.POINTER(NOTIFYICONDATA)]

    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class Backend:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.user32 = None
        self.shell32 = None
        self.hwnd = None
        self._icon_handle = None
        self._taskbar_created = None
        self._wndproc = None  # kept alive for as long as the window is

    def start(self) -> None:
        if sys.platform != "win32":
            raise Unavailable("the Windows backend only runs on Windows")

        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        _declare(self.user32, self.shell32, kernel32)

        # Explorer restarting takes every tray icon with it and broadcasts
        # this message so each owner can put its own back.
        self._taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")

        self._wndproc = WNDPROC(self._on_message)
        class_name = f"ChadTray{id(self):x}"

        window_class = WNDCLASS()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = kernel32.GetModuleHandleW(None)
        window_class.lpszClassName = class_name
        if not self.user32.RegisterClassW(ctypes.byref(window_class)):
            raise Unavailable("could not register the tray window class")

        self.hwnd = self.user32.CreateWindowExW(
            0, class_name, self.owner.title, WS_OVERLAPPED,
            CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
            None, None, window_class.hInstance, None,
        )
        if not self.hwnd:
            raise Unavailable("could not create the tray window")

        self._add_icon()
        self._run_loop()

    def _icon_path(self):
        """Write the icon where LoadImage can read it back as an HICON."""
        import os
        from pathlib import Path

        chad_dir = Path(os.environ.get("CHAD_DIR") or Path.home() / ".chad")
        chad_dir.mkdir(parents=True, exist_ok=True)
        path = chad_dir / "tray.ico"
        path.write_bytes(icon.ico())
        return path

    def _tip(self) -> str:
        """The pointer-over text, as far as szTip has room for it.

        Trimmed a whole line at a time — the shell silently truncates a longer
        string, and a tooltip cut off mid-number is worse than a short one.
        """
        lines = self.owner.tooltip().splitlines()
        while len(lines) > 1 and len("\n".join(lines)) > TIP_MAX_CHARS:
            lines.pop()
        return "\n".join(lines)[:TIP_MAX_CHARS]

    def _notify_icon(self, flags: int) -> NOTIFYICONDATA:
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        return data

    def menu_changed(self) -> None:
        """Refresh the pointer-over text. The menu itself is built on opening."""
        if not self.hwnd:
            return
        data = self._notify_icon(NIF_TIP)
        data.szTip = self._tip()
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _add_icon(self) -> None:
        self._icon_handle = self.user32.LoadImageW(
            None, str(self._icon_path()), IMAGE_ICON,
            icon.SIZE, icon.SIZE, LR_LOADFROMFILE,
        )

        data = self._notify_icon(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        data.uCallbackMessage = WM_TRAY_CALLBACK
        data.hIcon = self._icon_handle
        data.szTip = self._tip()

        if not self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise Unavailable("the shell refused the tray icon")

    def _remove_icon(self) -> None:
        self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_icon(0)))

    def _show_menu(self) -> None:
        # Built from scratch on every click, so a rebuilt menu needs no signal.
        self.owner.refresh()
        menu = self.user32.CreatePopupMenu()
        for index, item in enumerate(self.owner.items):
            if item.separator:
                self.user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            # A read-only row is greyed: the shell then refuses to send its id.
            flags = MF_STRING if item.enabled else MF_STRING | MF_GRAYED
            self.user32.AppendMenuW(menu, flags, FIRST_MENU_ID + index, item.label)

        # The shell requires the owning window in the foreground, or the menu
        # stays up after the click that should have dismissed it.
        self.user32.SetForegroundWindow(self.hwnd)

        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        chosen = self.user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y,
            0, self.hwnd, None,
        )
        # The documented companion to the SetForegroundWindow above: without
        # a message after the menu closes, the next click is swallowed.
        self.user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        self.user32.DestroyMenu(menu)

        if chosen >= FIRST_MENU_ID:
            self.owner.click(chosen - FIRST_MENU_ID + 1)

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY_CALLBACK:
            if lparam == WM_LBUTTONUP:
                self.owner.fire_default()
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0

        if message == self._taskbar_created:
            self._add_icon()
            return 0

        if message == WM_COMMAND:
            item_id = wparam & 0xFFFF
            if item_id >= FIRST_MENU_ID:
                self.owner.click(item_id - FIRST_MENU_ID + 1)
            return 0

        if message in (WM_TRAY_QUIT, WM_CLOSE):
            self.user32.DestroyWindow(hwnd)
            return 0

        if message == WM_DESTROY:
            self._remove_icon()
            self.user32.PostQuitMessage(0)
            return 0

        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run_loop(self) -> None:
        message = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def stop(self) -> None:
        """Ask the loop to end. Posting is what makes this thread-safe."""
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, WM_TRAY_QUIT, 0, 0)


def available() -> bool:
    return sys.platform == "win32" and hasattr(ctypes, "windll")

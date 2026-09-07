"""Start Chad at login, in the tray, the way the desktop starts it by hand.

One mechanism per platform — the one that platform's desktop session actually
uses — so that the entry lands where the user can see and remove it:

    Linux    ~/.config/autostart/chad.desktop   (XDG autostart)
    macOS    ~/Library/LaunchAgents/…plist      (a launchd agent, RunAtLoad)
    Windows  HKCU\\…\\CurrentVersion\\Run          (the per-user Run key)

All three are per-user: Chad's accounts, tools and provider logins live under
the home directory, so a machine-wide service would start with the wrong one.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

LABEL = "co.iondrive.chad"
DESKTOP_FILE = "chad.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Chad"


def launch_command() -> list[str]:
    """The command a login should run to bring Chad up with its tray icon."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: the executable is chad itself.
        return [sys.executable, "--tray"]

    script = Path(sys.executable).with_name("chad.exe" if _is_windows() else "chad")
    if script.exists():
        return [str(script), "--tray"]

    # Installed as a library without its console script (an editable checkout
    # run with `python -m chad`, say).
    return [sys.executable, "-m", "chad", "--tray"]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def entry_path() -> Path:
    """Where this platform records the login entry."""
    if _is_macos():
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if _is_windows():
        return Path(RUN_KEY) / RUN_VALUE
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart" / DESKTOP_FILE


def is_enabled() -> bool:
    """True when Chad is set to start at login on this machine."""
    if _is_windows():
        return _read_run_value() is not None
    return entry_path().exists()


def enable() -> Path:
    """Register Chad to start at login. Returns where that was recorded."""
    path = entry_path()

    if _is_windows():
        _write_run_value(subprocess.list2cmdline(launch_command()))
        return path

    path.parent.mkdir(parents=True, exist_ok=True)

    if _is_macos():
        import plistlib

        with path.open("wb") as handle:
            plistlib.dump({
                "Label": LABEL,
                "ProgramArguments": launch_command(),
                "RunAtLoad": True,
                "KeepAlive": False,
                "ProcessType": "Interactive",
            }, handle)
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        return path

    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Chad\n"
        "Comment=YOLO AI — coding agents that deliver a one-shot result\n"
        f"Exec={shlex.join(launch_command())}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return path


def disable() -> None:
    """Stop Chad starting at login. Does nothing if it wasn't."""
    if _is_windows():
        _delete_run_value()
        return

    path = entry_path()
    if _is_macos() and path.exists():
        _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    path.unlink(missing_ok=True)


def apply(enabled: bool) -> None:
    """Make the login entry match `enabled`."""
    if enabled:
        enable()
    else:
        disable()


def describe() -> str:
    """A one-line description of the login entry, for a UI to show."""
    if not is_enabled():
        return "not starting at login"
    if _is_windows():
        return f"HKCU\\{RUN_KEY}\\{RUN_VALUE}"
    return str(entry_path())


def _launchctl(*args: str) -> None:
    """Ask launchd to (re)load the agent, ignoring a duplicate load."""
    try:
        subprocess.run(["launchctl", *args], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def _open_run_key(write: bool):
    import winreg

    access = winreg.KEY_WRITE if write else winreg.KEY_READ
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def _read_run_value() -> str | None:
    import winreg

    try:
        with _open_run_key(write=False) as key:
            return winreg.QueryValueEx(key, RUN_VALUE)[0]
    except OSError:
        return None


def _write_run_value(command: str) -> None:
    import winreg

    with _open_run_key(write=True) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


def _delete_run_value() -> None:
    import winreg

    try:
        with _open_run_key(write=True) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except OSError:
        pass

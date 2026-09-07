"""Starting Chad at login, and living in the system tray while it waits.

The tray backends can only really be exercised on their own platform, so what
is pinned here is everything around them: that each backend module imports
anywhere (a Windows-only import at module scope would break the Linux build and
vice versa), that the login entry says what it should on every platform, that
the icon is a real image, and that first launch offers the choice exactly once.
"""

import importlib
import struct
import sys
import time
import types
import zlib
from pathlib import Path

import pytest

from chad.ui import tray
from chad.ui.tray import icon
from chad.util import autostart
from chad.util.config_manager import ConfigManager


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point every per-user path this feature writes to at a temp dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG autostart is the Linux mechanism"
)


def linux_module():
    from chad.ui.tray import linux

    return linux


def _reading(name, provider, session_pct=7.0, weekly_pct=8.0):
    """A canned usage reading, for menus that must not touch the network."""
    from chad.util.account_usage import UsageReading

    return UsageReading(
        account_name=name, provider=provider,
        session_pct=session_pct, weekly_pct=weekly_pct,
    )


class TestLoginEntry:
    """The entry that makes a desktop session start Chad."""

    @linux_only
    def test_enable_writes_an_entry_that_asks_for_the_tray(self, fake_home):
        path = autostart.enable()

        assert path.exists()
        contents = path.read_text()
        assert "Type=Application" in contents
        assert "--tray" in contents, "a login start must not open a browser window"

    @linux_only
    def test_enable_then_disable_leaves_nothing_behind(self, fake_home):
        assert autostart.is_enabled() is False

        autostart.enable()
        assert autostart.is_enabled() is True

        autostart.disable()
        assert autostart.is_enabled() is False
        assert not autostart.entry_path().exists()

    @linux_only
    def test_enabling_twice_is_still_one_entry(self, fake_home):
        autostart.enable()
        autostart.enable()

        entries = list(autostart.entry_path().parent.iterdir())
        assert len(entries) == 1

    @linux_only
    def test_apply_follows_the_flag(self, fake_home):
        autostart.apply(True)
        assert autostart.is_enabled() is True

        autostart.apply(False)
        assert autostart.is_enabled() is False

    @linux_only
    def test_disable_is_quiet_when_nothing_is_enabled(self, fake_home):
        autostart.disable()  # must not raise

        assert autostart.is_enabled() is False

    @linux_only
    def test_describe_names_the_entry(self, fake_home):
        assert "not starting" in autostart.describe()

        autostart.enable()
        assert str(autostart.entry_path()) == autostart.describe()


class TestLaunchCommand:
    """What a login actually runs."""

    def test_it_asks_for_the_tray(self):
        assert autostart.launch_command()[-1] == "--tray"

    def test_a_frozen_bundle_launches_itself(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/chad/chad")

        assert autostart.launch_command() == ["/opt/chad/chad", "--tray"]

    def test_a_checkout_launches_the_console_script(self, tmp_path, monkeypatch):
        script = tmp_path / "chad"
        script.write_text("")
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))

        assert autostart.launch_command() == [str(script), "--tray"]

    def test_a_library_install_launches_the_module(self, tmp_path, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))

        assert autostart.launch_command() == [str(tmp_path / "python"), "-m", "chad", "--tray"]


class TestPlatformEntries:
    """Each platform records the login entry where that platform looks."""

    def test_windows_uses_the_run_key(self, monkeypatch, fake_home):
        monkeypatch.setattr(sys, "platform", "win32")

        assert str(autostart.entry_path()).endswith(autostart.RUN_VALUE)
        assert "CurrentVersion" in str(autostart.entry_path())

    def test_windows_launches_the_exe(self, tmp_path, monkeypatch):
        exe = tmp_path / "chad.exe"
        exe.write_text("")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        assert autostart.launch_command() == [str(exe), "--tray"]

    def test_macos_uses_a_launch_agent(self, monkeypatch, fake_home):
        monkeypatch.setattr(sys, "platform", "darwin")
        path = autostart.entry_path()

        assert path.parent.name == "LaunchAgents"
        assert path.name == f"{autostart.LABEL}.plist"

    @pytest.mark.skipif(sys.platform == "win32", reason="posix plist writing")
    def test_macos_agent_runs_at_load(self, monkeypatch, fake_home):
        import plistlib

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(autostart, "_launchctl", lambda *args: None)

        path = autostart.enable()
        agent = plistlib.loads(path.read_bytes())

        assert agent["RunAtLoad"] is True
        assert agent["ProgramArguments"][-1] == "--tray"
        assert autostart.is_enabled() is True


class TestTrayBackends:
    """A backend module must import everywhere, and run only where it fits."""

    @pytest.mark.parametrize("name", ["linux", "macos", "windows", "dbus", "icon"])
    def test_backend_modules_import_on_this_platform(self, name):
        assert importlib.import_module(f"chad.ui.tray.{name}") is not None

    def test_a_platform_without_a_backend_says_so(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "sunos5")

        with pytest.raises(tray.Unavailable):
            tray.Tray("Chad", []).start()

        assert tray.available() is False

    def test_available_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            "chad.ui.tray.linux.available",
            lambda: (_ for _ in ()).throw(RuntimeError("no bus")),
        )

        assert tray.available() in (True, False)


class TestLinuxTrayOnADesktop:
    """The Linux backend against a real session bus, when one is there.

    Skipped on headless CI, which is exactly where a tray cannot be reached —
    but on a desktop this is the only test that proves the icon a panel picks
    up is the icon and menu we think we published.
    """

    @pytest.fixture
    def published(self):
        import threading
        import time

        if not sys.platform.startswith("linux"):
            pytest.skip("the StatusNotifierItem backend is the Linux one")

        from chad.ui.tray import dbus, linux

        # Asked up front so a bus without a panel skips here rather than
        # waiting out the backend's login-race grace period.
        try:
            probe = dbus.Connection().connect()
        except Exception as exc:
            pytest.skip(f"no session bus: {exc}")
        try:
            if not probe.name_has_owner(linux.WATCHER):
                pytest.skip("no tray host on this session bus")
        finally:
            probe.close()

        clicked = []
        # Stands in for the usage lines: a panel reading the menu must see
        # whatever these say at the moment it asks.
        usage = []

        def build():
            return [
                tray.MenuItem("Open Chad", lambda: clicked.append("open")),
                *(tray.MenuItem(line, lambda: clicked.append("usage")) for line in usage),
                tray.MenuItem("Quit Chad", lambda: clicked.append("quit")),
            ]

        menu = tray.Tray("Chad", build(), on_open=build)
        asked = []
        failure = []

        def run():
            try:
                menu.start()
            except Exception as exc:  # no bus, or no tray host on it
                failure.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        for _ in range(20):
            if failure or (menu._backend is not None and menu._backend.conn is not None):
                break
            time.sleep(0.1)
        if failure:
            pytest.skip(f"no tray on this session bus: {failure[0]}")

        backend = menu._backend
        served = backend._menu_method

        def recording(message):
            asked.append(message.member)
            return served(message)

        backend._menu_method = recording
        backend.conn.export(linux_module().MENU_PATH, linux_module().MENU_IFACE, recording)
        time.sleep(0.3)

        yield menu, clicked, usage, asked

        menu.stop()
        thread.join(timeout=3)

    def test_a_panel_sees_the_menu_the_icon_and_the_clicks(self, published):
        import os
        import time

        from chad.ui.tray import dbus

        menu, clicked, _usage, _asked = published
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        conn = dbus.Connection().connect()
        try:
            layout = conn.call(name, "/MenuBar", "com.canonical.dbusmenu",
                               "GetLayout", "iias", [0, -1, ["label"]])
            labels = [child.value[1]["label"].value for child in layout[1][2]]
            assert labels == ["Open Chad", "Quit Chad"]

            props = conn.call(name, "/StatusNotifierItem",
                              "org.freedesktop.DBus.Properties", "GetAll", "s",
                              ["org.kde.StatusNotifierItem"])[0]
            width, height, argb = props["IconPixmap"].value[0]
            assert (width, height) == (icon.SIZE, icon.SIZE)
            assert len(argb) == icon.SIZE * icon.SIZE * 4
            assert props["Title"].value == "Chad"

            conn.call(name, "/MenuBar", "com.canonical.dbusmenu", "Event", "isvu",
                      [1, "clicked", dbus.Variant("s", ""), 0])
            conn.call(name, "/StatusNotifierItem", "org.kde.StatusNotifierItem",
                      "Activate", "ii", [0, 0])
            time.sleep(0.5)
        finally:
            conn.close()

        assert clicked == ["open", "open"], "menu item and plain click both open Chad"

    def test_a_panel_picks_up_usage_when_it_opens_the_menu(self, published):
        """AboutToShow is the hook that puts current numbers in the menu."""
        import os

        from chad.ui.tray import dbus

        menu, _clicked, usage, asked = published
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        conn = dbus.Connection().connect()
        try:
            def labels():
                layout = conn.call(name, "/MenuBar", "com.canonical.dbusmenu",
                                   "GetLayout", "iias", [0, -1, ["label"]])
                return layout[0], [child.value[1]["label"].value for child in layout[1][2]]

            revision, before = labels()
            assert before == ["Open Chad", "Quit Chad"]

            usage.append("claude-work: session 43% · week 43%")
            changed = conn.call(name, "/MenuBar", "com.canonical.dbusmenu",
                                "AboutToShow", "i", [0])[0]
            assert changed is True, "a changed menu must tell the panel to re-read it"

            after_revision, after = labels()
            assert after == [
                "Open Chad", "claude-work: session 43% · week 43%", "Quit Chad",
            ]
            assert after_revision > revision, "the revision moves so caches drop"

            # Nothing changed this time, so the panel is told to keep its copy.
            assert conn.call(name, "/MenuBar", "com.canonical.dbusmenu",
                             "AboutToShow", "i", [0])[0] is False
        finally:
            conn.close()

    def test_a_reading_that_lands_late_makes_the_panel_re_read(self, published):
        """The panel has to come back for the new menu, not just be signalled.

        This is the whole update path: a panel reads the menu once when the
        icon registers (xfce4-panel never calls AboutToShow at all), so if it
        does not re-read here, the numbers a user sees are the ones from login.
        """
        menu, _clicked, usage, asked = published

        asked.clear()
        usage.append("codex: session 1% · week 0%")
        menu.notify_changed()
        for _ in range(20):
            if "GetLayout" in asked:
                break
            time.sleep(0.1)

        assert "GetLayout" in asked, (
            f"the panel never came back for the changed menu; it asked {asked}"
        )
        assert [item.label for item in menu.items] == [
            "Open Chad", "codex: session 1% · week 0%", "Quit Chad",
        ]


class TestLinuxMenuUpdates:
    """What the Linux backend tells a panel when the menu changes under it.

    The signal is checked without a bus: the client here can send signals but
    not receive them, and this is the half a panel acts on.
    """

    class FakeConn:
        def __init__(self):
            self.emitted = []

        def emit(self, path, interface, member, signature="", body=()):
            self.emitted.append((path, interface, member, signature, body))

    def backend(self, labels=("Open Chad",)):
        from chad.ui.tray import linux

        menu = tray.Tray("Chad", [tray.MenuItem(label, lambda: None) for label in labels])
        instance = linux.Backend(menu)
        instance.conn = self.FakeConn()
        return instance

    def test_a_late_reading_emits_layout_updated_with_a_new_revision(self):
        from chad.ui.tray import linux

        instance = self.backend()
        first = instance._revision

        instance.menu_changed()

        assert instance.conn.emitted == [
            (linux.MENU_PATH, linux.MENU_IFACE, "LayoutUpdated", "ui", [first + 1, 0]),
            (linux.ITEM_PATH, linux.ITEM_IFACE, "NewToolTip", "", ()),
        ], "the dbusmenu parent argument is signed; 'uu' is dropped by panels"

    def test_a_panel_that_asks_again_is_told_the_revision_moved(self):
        """A revision that stood still would leave the panel on its cached copy."""
        instance = self.backend()
        seen = []

        instance.owner.on_open = lambda: [tray.MenuItem(f"acct: session {len(seen)}%", lambda: None)]
        seen.append(1)
        assert instance._rebuild() is True
        first = instance._revision

        seen.append(1)
        assert instance._rebuild() is True
        second = instance._revision
        assert second > first

        assert instance._rebuild() is False, "an unchanged menu keeps its revision"
        assert instance._revision == second

    def test_nothing_is_emitted_before_the_icon_is_up(self):
        """prime() can finish before start(), and must not blow up in the thread."""
        from chad.ui.tray import linux

        instance = linux.Backend(tray.Tray("Chad", []))

        instance.menu_changed()  # conn is still None


class TestTrayMenu:
    """The menu dispatch every backend shares."""

    def test_click_runs_that_item(self):
        clicked = []
        menu = tray.Tray("Chad", [
            tray.MenuItem("Open Chad", lambda: clicked.append("open")),
            tray.MenuItem("Quit Chad", lambda: clicked.append("quit")),
        ])

        menu.click(2)
        assert clicked == ["quit"]

    def test_clicking_the_icon_opens_chad(self):
        clicked = []
        menu = tray.Tray("Chad", [
            tray.MenuItem("Open Chad", lambda: clicked.append("open")),
            tray.MenuItem("Quit Chad", lambda: clicked.append("quit")),
        ])

        menu.fire_default()
        assert clicked == ["open"], "a plain click must open the window, not quit"

    def test_an_id_outside_the_menu_does_nothing(self):
        menu = tray.Tray("Chad", [tray.MenuItem("Open Chad", lambda: pytest.fail("ran"))])

        menu.click(0)
        menu.click(7)


class TestTrayUsageTable:
    """The read-only table at the top of the menu, and the icon's tooltip."""

    def reading(self, name="acct", provider="anthropic", **fields):
        from chad.util.account_usage import UsageReading

        return UsageReading(account_name=name, provider=provider, **fields)

    def test_a_row_is_code_then_session_then_weekly(self):
        from chad.ui.tray.usage import usage_rows

        rows = usage_rows([("CWO", self.reading(session_pct=54.0, weekly_pct=44.0))])

        assert len(rows) == 2, "a header and one account"
        header, row = rows
        assert header.index("session") < header.index("weekly")
        assert row.startswith("CWO")
        assert row.index("54%") < row.index("44%")

    def test_each_number_comes_with_a_bar(self):
        from chad.ui.tray.usage import BAR_CELLS, EMPTY, FILLED, usage_rows

        _header, row = usage_rows([("A", self.reading(session_pct=100.0, weekly_pct=0.0))])

        assert FILLED * BAR_CELLS in row, "a full window draws a full bar"
        assert EMPTY * BAR_CELLS in row, "an empty one draws an empty bar"

    def test_bars_and_numbers_line_up_across_rows(self):
        """Every row is the same shape, so the columns read as columns."""
        from chad.ui.tray.usage import usage_rows

        rows = usage_rows([
            ("CWO", self.reading(session_pct=54.0, weekly_pct=44.0)),
            ("COD", self.reading(session_pct=1.0, weekly_pct=100.0)),
            ("CAL", self.reading(session_pct=None, weekly_pct=99.0)),
        ])

        assert len({len(row) for row in rows}) == 1, rows

    def test_a_logged_out_account_is_left_out(self):
        """Asked for: the tray lists what is usable, not what is not."""
        from chad.ui.tray.usage import usage_rows

        rows = usage_rows([
            ("MIS", self.reading("mistral", logged_out=True)),
            ("COD", self.reading("codex", session_pct=1.0, weekly_pct=0.0)),
        ])

        assert len(rows) == 2
        assert not any("MIS" in row for row in rows)

    def test_an_account_with_nothing_to_report_is_left_out(self):
        from chad.ui.tray.usage import usage_rows

        assert usage_rows([("LOC", self.reading("LocalQwen", provider="local"))]) == []

    def test_no_usage_means_no_table_at_all(self):
        """No header and no separator either — the section just is not there."""
        from chad.ui.tray.usage import usage_rows

        assert usage_rows([]) == []

    def test_the_tooltip_is_one_line_an_account(self):
        from chad.ui.tray.usage import tooltip_text

        readings = [
            (f"A{i}", self.reading(f"account-{i}", session_pct=99.0, weekly_pct=99.0))
            for i in range(8)
        ]

        text = tooltip_text(readings)

        assert text.count("\n") == 7
        assert "A0" in text and "A7" in text

    def test_a_reset_shows_whole_hours(self):
        from chad.ui.tray.usage import usage_rows

        _header, row = usage_rows([
            ("A", self.reading(session_pct=1.0, weekly_pct=2.0,
                               session_reset_eta="4h 18m", weekly_reset_eta="166h 30m")),
        ])

        assert "4h" in row and "166h" in row
        assert "18m" not in row and "30m" not in row, "minutes are noise at a glance"

    def test_a_reset_inside_the_hour_says_so(self):
        """Rounding "resets in 45m" down to 0h would read as "already reset"."""
        from chad.ui.tray.usage import usage_rows

        _header, row = usage_rows([
            ("A", self.reading(session_pct=99.0, weekly_pct=1.0, session_reset_eta="45m")),
        ])

        assert "(≈1h)" in row

    def test_nothing_in_the_table_can_break_pango_markup(self):
        """Some panels put the tooltip through markup, where "<" starts a tag.

        A parse failure there is not a wonky tooltip, it is no tooltip.
        """
        from chad.ui.tray.usage import tooltip_text, usage_rows

        readings = [
            ("A", self.reading(session_pct=99.0, weekly_pct=1.0, session_reset_eta="45m")),
            ("B", self.reading(session_pct=1.0, weekly_pct=100.0, weekly_reset_eta="9h 1m")),
        ]

        for text in [*usage_rows(readings), tooltip_text(readings)]:
            assert "<" not in text and "&" not in text, text

    def test_a_window_with_no_known_reset_leaves_the_column_blank(self):
        from chad.ui.tray.usage import usage_rows

        rows = usage_rows([
            ("A", self.reading(session_pct=1.0, weekly_pct=2.0)),
            ("B", self.reading(session_pct=1.0, weekly_pct=2.0, session_reset_eta="2h 0m")),
        ])

        assert len(set(len(row) for row in rows)) == 1, "the columns still line up"
        assert "h" not in rows[1], rows[1]

    def test_the_tooltip_leaves_out_what_the_table_does(self):
        from chad.ui.tray.usage import tooltip_text

        text = tooltip_text([
            ("MIS", self.reading("mistral", logged_out=True)),
            ("COD", self.reading("codex", session_pct=1.0, weekly_pct=2.0)),
        ])

        assert text.startswith("COD")
        assert "1%" in text and "2%" in text
        assert "MIS" not in text
        assert "\n" not in text, "one account, one line"

    def test_nothing_to_show_has_no_tooltip_of_its_own(self):
        from chad.ui.tray.usage import tooltip_text

        assert tooltip_text([]) == ""


class TestTrayUsageSnapshot:
    """Reading usage is a network call, so the menu never waits on one."""

    def summary(self, monkeypatch, readings, delay=0.0, **kwargs):
        """A UsageSummary whose account readings are canned, and counted."""
        import time

        from chad.ui.tray import usage as usage_module
        from chad.util.account_usage import UsageReading

        calls = []

        def fake_read(name, provider, model="default"):
            calls.append(name)
            if delay:
                time.sleep(delay)
            value = readings[name]
            if isinstance(value, Exception):
                raise value
            return UsageReading(account_name=name, provider=provider, **value)

        monkeypatch.setattr(usage_module, "read_account_usage", fake_read)
        monkeypatch.setattr(
            usage_module, "tray_accounts",
            lambda: [(name, "anthropic", name[:3].upper()) for name in readings],
        )
        return usage_module.UsageSummary(**kwargs), calls

    def test_the_first_look_reads_and_the_next_one_reuses_the_snapshot(self, monkeypatch):
        summary, calls = self.summary(monkeypatch, {"a": {"session_pct": 5.0, "weekly_pct": 6.0}})

        rows = summary.rows()
        assert any("5%" in row and "6%" in row for row in rows), rows
        assert summary.rows() == rows
        assert calls == ["a"], "a fresh snapshot must not be read again"

    def test_the_tooltip_comes_from_the_same_snapshot_without_waiting(self, monkeypatch):
        """A hover must never block: it draws whatever has been read."""
        import time

        summary, _ = self.summary(
            monkeypatch, {"a": {"session_pct": 5.0, "weekly_pct": 6.0}}, delay=2.0,
        )

        started = time.monotonic()
        assert summary.tooltip() == ""
        assert time.monotonic() - started < 0.5

        summary.refresh_now()
        assert "5%" in summary.tooltip()

    def test_a_stale_snapshot_is_read_again(self, monkeypatch):
        summary, calls = self.summary(
            monkeypatch, {"a": {"session_pct": 5.0, "weekly_pct": 6.0}}, fresh_for=0.0,
        )

        summary.rows()
        summary.rows()

        assert len(calls) == 2

    def test_a_slow_read_does_not_hold_the_menu_shut(self, monkeypatch):
        """The menu draws the snapshot it has; the slow reading lands after."""
        import time

        summary, calls = self.summary(
            monkeypatch,
            {"a": {"session_pct": 5.0, "weekly_pct": 6.0}},
            delay=2.0,
            open_wait=0.2,
        )

        started = time.monotonic()
        rows = summary.rows()
        waited = time.monotonic() - started

        from chad.ui.tray.usage import READING

        assert waited < 1.0, f"the menu waited {waited:.1f}s on a slow provider"
        assert rows == [READING]

    def test_one_unreadable_account_does_not_lose_the_others(self, monkeypatch):
        summary, _ = self.summary(monkeypatch, {
            "good": {"session_pct": 5.0, "weekly_pct": 6.0},
            "broken": ValueError("Unsupported provider: opencode"),
        })

        rows = summary.rows()

        assert any("5%" in row and "6%" in row for row in rows), rows
        # The unreadable one counts as logged out, so it drops out of the table.
        assert not any("BRO" in row for row in rows), rows

    def test_polling_reads_at_once_and_again_until_stopped(self, monkeypatch):
        """A panel that never asks is the reason the reading has to be pushed."""
        summary, calls = self.summary(
            monkeypatch, {"a": {"session_pct": 5.0, "weekly_pct": 6.0}}, fresh_for=0.0,
        )

        summary.start(interval=0.05)
        for _ in range(40):
            if len(calls) >= 2:
                break
            time.sleep(0.05)
        assert len(calls) >= 2, "polling stopped after the first read"

        summary.stop()
        time.sleep(0.2)
        settled = len(calls)
        time.sleep(0.3)

        assert len(calls) == settled, "stopping the tray must stop the reading"

    def test_a_finished_read_announces_itself(self, monkeypatch):
        """The backend needs telling, or an already-open menu keeps the old numbers."""
        changed = []
        summary, _ = self.summary(
            monkeypatch,
            {"a": {"session_pct": 5.0, "weekly_pct": 6.0}},
            on_change=lambda: changed.append(True),
        )

        summary.refresh_now()

        assert changed == [True]


class TestReadOnlyRowsAndSeparators:
    """The usage table is there to be read, not clicked."""

    def test_a_row_with_no_action_is_not_enabled(self):
        row = tray.MenuItem("CWO  ███░░ 54%")

        assert row.enabled is False
        assert tray.MenuItem("Open Chad", lambda: None).enabled is True

    def test_clicking_a_read_only_row_does_nothing(self):
        menu = tray.Tray("Chad", [
            tray.MenuItem("CWO  ███░░ 54%"),
            tray.MenuItem.rule(),
            tray.MenuItem("Open Chad", lambda: pytest.fail("a read-only row ran an action")),
        ])

        menu.click(1)
        menu.click(2)

    def test_a_plain_click_on_the_icon_skips_the_table(self):
        """items[0] is a usage row now, so the default is the first real action."""
        clicked = []
        menu = tray.Tray("Chad", [
            tray.MenuItem("     session     weekly"),
            tray.MenuItem("CWO  ███░░ 54%  ██░░░ 44%"),
            tray.MenuItem.rule(),
            tray.MenuItem("Open Chad", lambda: clicked.append("open")),
            tray.MenuItem("Quit Chad", lambda: clicked.append("quit")),
        ])

        menu.fire_default()

        assert clicked == ["open"]

    def test_the_linux_backend_publishes_them_as_such(self):
        from chad.ui.tray import linux

        menu = tray.Tray("Chad", [
            tray.MenuItem("CWO  ███░░ 54%"),
            tray.MenuItem.rule(),
            tray.MenuItem("Open Chad", lambda: None),
        ])
        backend = linux.Backend(menu)

        read_only, rule, action = (backend._item_properties_for(item) for item in menu.items)

        assert read_only["enabled"].value is False
        assert read_only["label"].value == "CWO  ███░░ 54%"
        assert rule["type"].value == "separator"
        assert action["enabled"].value is True

    def test_the_tooltip_is_the_table_and_nothing_else(self):
        """Verified by hovering the real icon: a panel draws the description
        under a bold title with a blank line between, and draws no tooltip at
        all when the title is empty. So the table is the title."""
        from chad.ui.tray import linux

        menu = tray.Tray("Chad", [], tooltip=lambda: "CWO  54% (2h)")
        backend = linux.Backend(menu)

        _icon, _data, title, description = backend._item_property("ToolTip").value

        assert title == "CWO  54% (2h)"
        assert description == "", "a description arrives under a heading"

    def test_a_tray_with_nothing_to_report_names_itself(self):
        """An icon with no tooltip at all would be a mystery in the panel."""
        from chad.ui.tray import linux

        backend = linux.Backend(tray.Tray("Chad", [], tooltip=lambda: ""))

        _icon, _data, title, description = backend._item_property("ToolTip").value

        assert (title, description) == ("Chad", "")
        assert tray.Tray("Chad", []).tooltip() == "Chad"


class TestTrayMenuRefresh:
    """The menu is rebuilt on open, so the numbers in it are current."""

    def test_refresh_rebuilds_the_items_and_reports_the_change(self):
        labels = iter([["Open Chad", "a: session 1% · week 2%", "Quit Chad"],
                       ["Open Chad", "a: session 9% · week 2%", "Quit Chad"]])

        def build():
            return [tray.MenuItem(label, lambda: None) for label in next(labels)]

        menu = tray.Tray("Chad", build(), on_open=build)

        assert menu.refresh() is True
        assert [item.label for item in menu.items][1] == "a: session 9% · week 2%"

    def test_an_unchanged_menu_reports_no_change(self):
        def build():
            return [tray.MenuItem("Open Chad", lambda: None)]

        menu = tray.Tray("Chad", build(), on_open=build)

        assert menu.refresh() is False

    def test_a_menu_with_no_builder_never_changes(self):
        menu = tray.Tray("Chad", [tray.MenuItem("Open Chad", lambda: None)])

        assert menu.refresh() is False
        menu.notify_changed()  # no backend, no builder: still safe


class TestWindowsTooltip:
    """szTip holds 128 characters, and the shell truncates silently past that."""

    def tray_with(self, tooltip: str):
        from chad.ui.tray import windows

        menu = tray.Tray("Chad", [], tooltip=lambda: tooltip)
        backend = windows.Backend(menu)
        backend.hwnd = 1
        return backend

    def test_the_tip_is_the_accounts(self):
        backend = self.tray_with("ACC   1%  2h\nBCC   3%  4h")

        assert backend._tip().splitlines() == ["ACC   1%  2h", "BCC   3%  4h"]

    def test_more_accounts_than_fit_lose_whole_lines(self):
        from chad.ui.tray.windows import TIP_MAX_CHARS

        backend = self.tray_with("\n".join(f"A{i}   99%  2h" for i in range(20)))

        tip = backend._tip()

        assert len(tip) <= TIP_MAX_CHARS
        assert all(line.endswith("2h") for line in tip.splitlines()), tip

    def test_a_tray_with_nothing_to_report_still_names_itself(self):
        assert self.tray_with("")._tip() == "Chad"


class TestTrayIcon:
    """The icon is drawn here, so it has to be a real image."""

    def test_png_is_a_png(self):
        data = icon.render()

        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (icon.SIZE, icon.SIZE)

    def test_png_pixels_survive_a_round_trip(self):
        data = icon.render()
        start = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[start - 8:start - 4])[0]
        raw = zlib.decompress(data[start:start + length])

        assert len(raw) == icon.SIZE * (1 + icon.SIZE * 4)

    def test_argb_has_every_pixel(self):
        width, height, argb = icon.argb_for_dbus()

        assert (width, height) == (icon.SIZE, icon.SIZE)
        assert len(argb) == icon.SIZE * icon.SIZE * 4

    def test_ico_is_a_single_32_pixel_image(self):
        data = icon.ico()

        assert data[:4] == b"\x00\x00\x01\x00", "ICO magic plus one image"
        assert data[6] == icon.SIZE and data[7] == icon.SIZE

    def test_the_icon_is_a_c_and_not_a_blob(self):
        pixels = icon.rgba()
        middle = icon.SIZE // 2

        assert pixels[middle][0][3] == 0, "outside the ring is transparent"
        assert pixels[middle][4][3] > 0, "the left of the ring is drawn"
        assert pixels[middle][middle][3] == 0, "the middle of the ring is hollow"
        assert pixels[middle][icon.SIZE - 4][3] == 0, "the C is open on the right"


class TestFirstLaunchOffer:
    """First launch is where this gets set up — exactly once."""

    @pytest.fixture
    def config(self, tmp_path):
        manager = ConfigManager(config_path=tmp_path / "chad.conf")
        manager.save_config({"password_hash": "", "encryption_salt": "c2FsdA==", "accounts": {}})
        return manager

    @pytest.fixture
    def desktop(self, monkeypatch):
        """A machine with a tray, a terminal, and a login entry we can watch."""
        applied = []
        monkeypatch.setattr("chad.ui.tray.available", lambda: True)
        monkeypatch.setattr(autostart, "enable", lambda: applied.append(True) or Path("/tmp/x"))
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
        return applied

    def _answer(self, monkeypatch, reply):
        monkeypatch.setattr("builtins.input", lambda *args: reply)

    def test_accepting_records_and_installs_it(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        self._answer(monkeypatch, "")  # bare Enter takes the default
        offer_autostart(config)

        assert config.get_autostart() is True
        assert desktop == [True]

    def test_declining_is_remembered(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        self._answer(monkeypatch, "n")
        offer_autostart(config)

        assert config.get_autostart() is False
        assert desktop == []

    def test_the_question_is_asked_only_once(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        config.set_autostart(False)
        monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("asked twice"))

        offer_autostart(config)

    def test_nothing_is_recorded_without_a_terminal(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
        offer_autostart(config)

        assert config.get_autostart() is None, "ask again when someone is there to answer"

    def test_nothing_is_recorded_without_a_tray(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        monkeypatch.setattr("chad.ui.tray.available", lambda: False)
        offer_autostart(config)

        assert config.get_autostart() is None

    def test_an_interrupted_answer_is_not_an_answer(self, config, desktop, monkeypatch):
        from chad.__main__ import offer_autostart

        def interrupt(*args):
            raise EOFError

        monkeypatch.setattr("builtins.input", interrupt)
        offer_autostart(config)

        assert config.get_autostart() is None


class TestRunTray:
    """Waiting in the tray, and what happens when there is none."""

    def test_no_tray_is_reported_and_not_fatal(self, monkeypatch, capsys):
        """A desktop Chad must keep serving even if the panel is missing."""
        from chad.__main__ import run_tray

        monkeypatch.setattr("chad.ui.tray.usage.tray_accounts", list)
        monkeypatch.setattr(sys, "platform", "sunos5")

        assert run_tray("http://127.0.0.1:3184") is False
        assert "No system tray" in capsys.readouterr().out

    def test_the_usage_table_is_a_read_only_section_at_the_top(self, monkeypatch):
        from chad.__main__ import run_tray

        monkeypatch.setattr(
            "chad.ui.tray.usage.tray_accounts", lambda: [("acct", "anthropic", "ACC")],
        )
        monkeypatch.setattr(
            "chad.ui.tray.usage.read_account_usage",
            lambda name, provider, model="default": _reading(name, provider),
        )

        built = {}

        class FakeTray:
            def __init__(self, title, items, on_open=None, tooltip=None):
                built["items"] = items
                built["on_open"] = on_open
                built["tooltip"] = tooltip

            def notify_changed(self):
                built["notified"] = True

            def start(self):
                raise tray.Unavailable("no panel in this test")

        monkeypatch.setattr("chad.ui.tray.Tray", FakeTray)
        run_tray("http://127.0.0.1:3184")

        # The icon goes up without waiting on a reading, and without the
        # on_change callback reaching for a tray that is not built yet.
        assert [item.label for item in built["items"]] == ["Open Chad", "Quit Chad"]

        items = built["on_open"]()
        labels = [item.label for item in items]
        assert labels[-2:] == ["Open Chad", "Quit Chad"]
        assert labels[0].strip().startswith("session"), labels
        assert any("ACC" in label for label in labels), labels
        # Header and account rows read only, then a rule, then the actions.
        assert [item.enabled for item in items] == [False, False, False, True, True]
        assert items[2].separator is True
        assert "ACC" in built["tooltip"](), "the tooltip comes from the same snapshot"

    def test_attaching_adds_no_second_icon(self, monkeypatch):
        """The process holding the server holds the icon; a client adds none."""
        import chad.__main__ as entry

        trays = []
        monkeypatch.setattr(entry, "run_tray", lambda url: trays.append(url) or True)
        monkeypatch.setattr("webbrowser.open", lambda url: True)

        def stop_waiting(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(entry.time, "sleep", stop_waiting)

        entry.run_unified(None, api_port=0, ui_mode="react",
                          server_url="http://127.0.0.1:3184", tray=True)

        assert trays == []


class TestAlreadyRunning:
    """A second chad attaches to the one in the tray rather than racing it."""

    def test_no_port_file_means_nothing_is_running(self, tmp_path, monkeypatch):
        from chad.__main__ import running_server_url

        monkeypatch.setenv("CHAD_DIR", str(tmp_path))

        assert running_server_url() is None

    def test_a_port_taken_by_something_else_is_not_chad(self, tmp_path, monkeypatch):
        """A stale port file can point at whatever took the port next."""
        import http.server
        import threading

        from chad.__main__ import running_server_url

        monkeypatch.setenv("CHAD_DIR", str(tmp_path))
        server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        (tmp_path / "server.port").write_text(f"{server.server_address[1]}\n")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert running_server_url() is None
        finally:
            server.shutdown()

    def test_a_stale_port_file_is_not_a_running_chad(self, tmp_path, monkeypatch):
        import socket

        from chad.__main__ import running_server_url

        monkeypatch.setenv("CHAD_DIR", str(tmp_path))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        (tmp_path / "server.port").write_text(f"{dead_port}\n")

        assert running_server_url() is None


class TestConfigRecord:
    """The recorded answer, which is what stops the question coming back."""

    def test_unanswered_reads_as_none(self, tmp_path):
        manager = ConfigManager(config_path=tmp_path / "chad.conf")
        manager.save_config({"password_hash": "", "encryption_salt": "c2FsdA==", "accounts": {}})

        assert manager.get_autostart() is None

    def test_the_answer_survives_a_reload(self, tmp_path):
        path = tmp_path / "chad.conf"
        manager = ConfigManager(config_path=path)
        manager.save_config({"password_hash": "", "encryption_salt": "c2FsdA==", "accounts": {}})
        manager.set_autostart(True)

        assert ConfigManager(config_path=path).get_autostart() is True

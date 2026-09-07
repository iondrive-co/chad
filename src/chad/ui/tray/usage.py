"""The account usage table at the top of the tray menu.

A read-only section, one row per account: its code, then each window as a
bar, a percentage, and the hours until it resets.

            session           weekly
    CWO  ███░░  54% (3h)   ██░░░  44% (92h)
    CIO  █░░░░  21% (5h)   █░░░░  11% (44h)

and the same figures, without the bars, as the icon's pointer-over text.
Accounts are named by a short code (`chad.util.config_manager` assigns and
keeps those) because a menu row has no room for `claude-iondrive` twice over.
Only accounts with usage to report are listed: a logged-out account, or a
provider that reports none, has nothing to put in the columns.

Reading those numbers takes an HTTPS round trip per Anthropic account, which
is not something a menu can be left waiting on. So a snapshot is kept and
pushed: a reading that lands announces itself, and the backend tells the
platform to re-read the menu.

Pushing is what makes this work at all. Not every tray host asks before it
draws — xfce4-panel reads the menu once when the icon registers and never
calls AboutToShow — so a menu that only refreshed when it was opened would
show whatever was known at login, forever. Hosts that *do* ask get a refresh
then too, which is what the freshness window below is for.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from chad.util.account_usage import UsageReading, read_account_usage
from chad.util.config_manager import ACCOUNT_CODE_LENGTH, ConfigManager

# How often the snapshot is read again while the tray sits there. The only
# thing that moves usage is a task running, so this is slow on purpose.
POLL_SECONDS = 300.0
# How long a reading stays fresh before a menu open replaces it, on the hosts
# that announce an opening menu.
FRESH_FOR_SECONDS = 60.0
# How long an opening menu waits for that fresh reading before drawing.
OPEN_WAIT_SECONDS = 1.5
# Accounts are read at once, so one slow provider doesn't delay the rest.
MAX_PARALLEL_READS = 8

# The bar drawn beside each percentage. Five cells is 20% a cell, which is as
# much resolution as a glance at a menu is worth.
BAR_CELLS = 5
FILLED = "█"
EMPTY = "░"
# A digit-width space. Menus are drawn in a proportional font, where ordinary
# spaces are narrower than digits and the columns come out ragged.
FIGURE_SPACE = " "
# Columns wide enough for the widest thing that goes in them: "100%", and a
# weekly reset that can be a week away ("(167h)").
PERCENT_WIDTH = 4
RESET_WIDTH = 6

READING = "Reading usage…"


def tray_accounts() -> list[tuple[str, str, str]]:
    """Every account as (name, provider, code), in the order the UI lists them."""
    config = ConfigManager()
    codes = config.account_codes()
    return [
        (name, provider, codes.get(name, ""))
        for name, provider in config.list_accounts().items()
    ]


def usage_rows(readings: list[tuple[str, UsageReading]]) -> list[str]:
    """The table: a header, then a row per account with usage to report.

    Empty when no account has any, so the menu carries no empty section.
    """
    rows = [
        f"{code:<{ACCOUNT_CODE_LENGTH}}  "
        f"{_gauge(r.session_pct, r.session_reset_eta)}  "
        f"{_gauge(r.weekly_pct, r.weekly_reset_eta)}"
        for code, r in readings if _worth_showing(r)
    ]
    if not rows:
        return []
    return [_header(), *rows]


def tooltip_text(readings: list[tuple[str, UsageReading]]) -> str:
    """The same figures without the bars, for the icon's pointer-over text."""
    lines = [
        f"{code:<{ACCOUNT_CODE_LENGTH}}  "
        f"{_percent(r.session_pct)} {_reset(r.session_reset_eta)}  "
        f"{_percent(r.weekly_pct)} {_reset(r.weekly_reset_eta)}"
        for code, r in readings if _worth_showing(r)
    ]
    return "\n".join(lines)


def _worth_showing(reading: UsageReading) -> bool:
    """Whether an account has anything to put in the table.

    A logged-out account is left out — the tray lists what is usable — and so
    is a provider that reports no usage, which would sit there blank forever.
    """
    if reading.logged_out:
        return False
    return reading.session_pct is not None or reading.weekly_pct is not None


def _header() -> str:
    return (
        FIGURE_SPACE * ACCOUNT_CODE_LENGTH
        + f"  {_header_column('session')}  {_header_column('weekly')}"
    )


def _header_column(label: str) -> str:
    """A label centred over a gauge column, in the gauge's own shape.

    Built by overlaying the label on a template with one padding character per
    character of the row below it, because a menu is drawn in a proportional
    font: an ordinary space is half the width of a digit there, so padding a
    header with spaces left the weekly label drifting a whole column left of
    the numbers it was meant to sit over.
    """
    cells = (
        [FIGURE_SPACE] * BAR_CELLS + [" "]
        + [FIGURE_SPACE] * PERCENT_WIDTH + [" "]
        + [FIGURE_SPACE] * RESET_WIDTH
    )
    start = max(0, (len(cells) - len(label)) // 2)
    for offset, character in enumerate(label[:len(cells)]):
        cells[start + offset] = character
    return "".join(cells)


def _gauge(value: float | None, reset_eta: str | None) -> str:
    """A window as a bar, a percentage and its reset — one fixed width."""
    return f"{_bar(value)} {_percent(value)} {_reset(reset_eta)}"


def _reset(eta: str | None) -> str:
    """Whole hours until a window resets, bracketed, right after its percentage.

    Minutes are noise at a glance, but "resets within the hour" is not, so a
    reset under an hour away says so rather than rounding to 0h. Padded on the
    right so the opening bracket lands in the same place on every row.

    "≈1h" rather than "<1h": a tooltip reaches some panels as Pango markup,
    where a bare "<" is the start of a tag and a parse failure means no
    tooltip at all.
    """
    if not eta:
        return FIGURE_SPACE * RESET_WIDTH
    hours = re.match(r"(\d+)h", eta)
    text = f"({hours.group(1)}h)" if hours else "(≈1h)"
    return text.ljust(RESET_WIDTH, FIGURE_SPACE)


def _bar(value: float | None) -> str:
    if value is None:
        return EMPTY * BAR_CELLS
    filled = min(BAR_CELLS, max(0, round(value / (100 / BAR_CELLS))))
    return FILLED * filled + EMPTY * (BAR_CELLS - filled)


def _percent(value: float | None) -> str:
    """A percentage in four columns, padded so the digits line up."""
    if value is None:
        return "n/a".rjust(PERCENT_WIDTH, FIGURE_SPACE)
    return f"{value:.0f}%".rjust(PERCENT_WIDTH, FIGURE_SPACE)


class UsageSummary:
    """The usage table for the tray, kept fresh off the platform's threads."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
        fresh_for: float = FRESH_FOR_SECONDS,
        open_wait: float = OPEN_WAIT_SECONDS,
    ) -> None:
        self._on_change = on_change
        self._fresh_for = fresh_for
        self._open_wait = open_wait
        self._rows: list[str] = [READING]
        self._tooltip = ""
        self._read_at: float | None = None
        self._lock = threading.Lock()
        self._reading: threading.Event | None = None
        self._stopped = threading.Event()

    def rows(self) -> list[str]:
        """The table to draw now, replacing a stale snapshot first if it can."""
        finished = self._begin_read()
        if finished is not None:
            finished.wait(self._open_wait)
        with self._lock:
            return list(self._rows)

    def tooltip(self) -> str:
        """The pointer-over text, from what has been read. Never blocks."""
        with self._lock:
            return self._tooltip

    def start(self, interval: float = POLL_SECONDS) -> None:
        """Read the accounts now, and again every ``interval`` until stopped."""
        def poll() -> None:
            while not self._stopped.is_set():
                self.refresh_now()
                self._stopped.wait(interval)

        threading.Thread(target=poll, daemon=True, name="chad-tray-usage-poll").start()

    def stop(self) -> None:
        """Stop reading. The tray is going away."""
        self._stopped.set()

    def refresh_now(self) -> None:
        """Read every account and replace the snapshot. Blocking."""
        readings = self._read_accounts()
        rows = usage_rows(readings)
        tooltip = tooltip_text(readings)
        with self._lock:
            changed = rows != self._rows or tooltip != self._tooltip
            self._rows = rows
            self._tooltip = tooltip
            self._read_at = time.monotonic()
        if changed and self._on_change is not None:
            self._on_change()

    def _begin_read(self) -> threading.Event | None:
        """Start a background read unless one is running or the snapshot is fresh.

        Returns the event that read will set, or None when there is nothing to
        wait for.
        """
        with self._lock:
            if self._reading is not None:
                return self._reading
            if self._read_at is not None and time.monotonic() - self._read_at < self._fresh_for:
                return None
            finished = threading.Event()
            self._reading = finished

        def read() -> None:
            try:
                self.refresh_now()
            finally:
                with self._lock:
                    self._read_at = time.monotonic()
                    self._reading = None
                finished.set()

        threading.Thread(target=read, daemon=True, name="chad-tray-usage").start()
        return finished

    def _read_accounts(self) -> list[tuple[str, UsageReading]]:
        """Read every account at once, keeping whatever answers."""
        accounts = tray_accounts()
        if not accounts:
            return []
        with ThreadPoolExecutor(
            max_workers=min(len(accounts), MAX_PARALLEL_READS),
            thread_name_prefix="chad-tray-usage",
        ) as pool:
            return list(pool.map(_read_one, accounts))


def _read_one(account: tuple[str, str, str]) -> tuple[str, UsageReading]:
    """Read one account, reporting a failure as a reading rather than raising.

    An account Chad cannot read — a provider type that no longer exists, an
    unreadable credentials file — is reported the way is_logged_in already
    reports one: logged out. It must not cost the other accounts their numbers.
    """
    name, provider, code = account
    try:
        return code, read_account_usage(name, provider)
    except Exception:
        return code, UsageReading(account_name=name, provider=provider, logged_out=True)

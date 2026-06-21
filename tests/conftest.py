from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
TESTS_DIR = Path(__file__).resolve().parent

# Force current worktree src to the front of sys.path so imports use this tree,
# not any installed or sibling worktrees. Keep the tests dir on the path too so
# sibling helpers (test_helpers) import by name in every phase — collection,
# test runtime, and session teardown hooks.
SRC_STR = str(SRC_PATH)
TESTS_STR = str(TESTS_DIR)
sys.path = [SRC_STR, TESTS_STR] + [
    p for p in sys.path if p not in (SRC_STR, TESTS_STR)
]


# ---------------------------------------------------------------------------
# Process-leak safety net
#
# Many tests spawn real subprocesses and clean them up in a per-test ``finally``
# block (e.g. the preview-tunnel ``serve.py`` autodetect test). When a test is
# skipped, errors, or the whole run is aborted with Ctrl-C, that teardown may not
# run and the child is reparented to init and survives forever. At the end of the
# session — which still runs on normal completion, test failures, and
# KeyboardInterrupt — reap any child the suite left behind so a run never leaks
# processes regardless of whether an individual test cleaned up after itself.
#
# We deliberately do NOT install a SIGTERM handler: SIGTERM is exercised by the
# app and by individual tests as a normal mechanism, so a global handler that
# reaped on every SIGTERM would tear down a still-running test's own children.
# ---------------------------------------------------------------------------


def pytest_sessionfinish(session, exitstatus):
    from test_helpers import reap_child_processes

    leaked = reap_child_processes()
    if leaked:
        warnings.warn(
            f"Test session leaked {len(leaked)} child process(es) "
            f"(PIDs {leaked}); they were terminated. A test is not cleaning up "
            "a subprocess it spawned.",
            stacklevel=1,
        )


class _NoOpSlackService:
    """SlackService stand-in that never contacts real Slack."""

    def post_milestone(self, *a, **kw):
        return False

    def post_milestone_async(self, *a, **kw):
        pass


class _NoOpTunnelService:
    """TunnelService stand-in that never spawns real cloudflared."""

    _url = None
    _subdomain = None
    _error = None
    _proc = None

    @property
    def is_running(self):
        return False

    def start(self, port):
        return None

    def stop(self):
        pass

    def status(self):
        return {"running": False, "url": None, "subdomain": None, "error": None}


@pytest.fixture(autouse=True)
def _isolate_session_logs(tmp_path_factory, monkeypatch):
    """Keep session logs isolated and Slack disabled per test run."""
    log_dir = tmp_path_factory.mktemp("session_logs")
    monkeypatch.setenv("CHAD_SESSION_LOG_DIR", str(log_dir))
    monkeypatch.setenv("CHAD_SESSION_LOG_MAX_FILES", "200")

    # Prevent any test from sending real Slack notifications.
    noop = _NoOpSlackService()
    monkeypatch.setattr(
        "chad.server.services.slack_service.get_slack_service", lambda: noop,
    )

    # Prevent any test from spawning real cloudflared.
    noop_tunnel = _NoOpTunnelService()
    monkeypatch.setattr(
        "chad.server.services.tunnel_service.get_tunnel_service", lambda: noop_tunnel,
    )

    # Prevent tests from opening real browser windows.
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)

    yield

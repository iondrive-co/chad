from __future__ import annotations

import os
import signal
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

# Imported at module load (not lazily inside the SIGTERM handler) so a SIGTERM
# arriving mid-import can't deadlock on the import lock while the handler runs.
from test_helpers import reap_child_processes  # noqa: E402


# ---------------------------------------------------------------------------
# Process-leak safety net
#
# Many tests spawn real subprocesses and clean them up in a per-test ``finally``
# block (e.g. the preview-tunnel ``serve.py`` autodetect test). That teardown is
# skipped when a run is interrupted, leaving the child reparented to init and
# running forever. We reap any leftover child of the pytest process on two
# triggers:
#   - pytest_sessionfinish: normal completion, test failures, and Ctrl-C
#     (KeyboardInterrupt) all still run it.
#   - a SIGTERM handler: a ``timeout``-wrapped run (how agents and CI run the
#     suite) is killed with SIGTERM, which by default skips finally/atexit; this
#     is the case that originally orphaned ``serve.py``. The handler is dormant
#     during a normal run — nothing sends SIGTERM to the pytest process itself —
#     and only fires when the run is being torn down from outside.
# ---------------------------------------------------------------------------

_PREVIOUS_SIGTERM_HANDLER = None


def _sigterm_reap_handler(signum, frame):
    """Reap leaked children before a SIGTERM-interrupted run is torn down, then
    chain to the previous handler so the run still terminates."""
    reap_child_processes(timeout=2.0)
    signal.signal(signal.SIGTERM, _PREVIOUS_SIGTERM_HANDLER or signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def pytest_configure(config):
    global _PREVIOUS_SIGTERM_HANDLER
    if os.name == "nt":
        return
    _PREVIOUS_SIGTERM_HANDLER = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sigterm_reap_handler)


def pytest_unconfigure(config):
    global _PREVIOUS_SIGTERM_HANDLER
    if os.name == "nt" or _PREVIOUS_SIGTERM_HANDLER is None:
        return
    signal.signal(signal.SIGTERM, _PREVIOUS_SIGTERM_HANDLER)
    _PREVIOUS_SIGTERM_HANDLER = None


def pytest_sessionfinish(session, exitstatus):
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

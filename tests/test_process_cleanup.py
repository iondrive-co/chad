"""Regression tests for the test suite's own process-cleanup safety net.

Tests across the suite spawn real subprocesses — preview-tunnel dev servers
(``serve.py``), PTY children, idle-stall sleepers — and rely on a per-test
``finally`` block (or the function under test) to terminate them. None of that
runs when a run is interrupted: a ``timeout``-wrapped invocation gets SIGTERM,
Ctrl-C aborts mid-test, or a crash skips teardown. The orphaned child is then
reparented to init and survives indefinitely.

The conftest session reaper kills any leftover child of the pytest process at
session end and on SIGTERM, independent of whether an individual test cleaned up
after itself. These tests pin that behavior.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

from test_helpers import reap_child_processes


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


def test_reaper_kills_leaked_child():
    """A long-lived child the suite forgot to clean up is terminated."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    try:
        assert _alive(proc.pid)

        killed = reap_child_processes(timeout=5.0)

        assert proc.pid in killed
        assert _wait_dead(proc.pid), "Leaked child survived the session reaper"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_reaper_kills_whole_process_tree():
    """Reaping is recursive: an orphaned grandchild is killed too."""
    script = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])\n"
        "print(g.pid, flush=True)\n"
        "time.sleep(3600)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    grandchild_pid = None
    try:
        grandchild_pid = int(proc.stdout.readline().strip())
        assert _alive(proc.pid) and _alive(grandchild_pid)

        killed = reap_child_processes(timeout=5.0)

        assert proc.pid in killed
        assert grandchild_pid in killed
        assert _wait_dead(proc.pid), "Leaked child survived the reaper"
        assert _wait_dead(grandchild_pid), "Orphaned grandchild survived the reaper"
    finally:
        for pid in (grandchild_pid, proc.pid):
            if pid and _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        proc.wait(timeout=5)


def test_reaper_no_children_is_noop():
    """With no leaked children the reaper returns an empty list and does not raise."""
    # Drain any child this test process may already have (none expected here).
    killed = reap_child_processes(timeout=1.0)
    assert isinstance(killed, list)


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM handler only installed on Unix")
def test_sigterm_handler_installed_for_session():
    """conftest installs a SIGTERM handler so a timeout-killed run still reaps.

    A ``timeout``-wrapped run (how agents and CI invoke the suite) is torn down
    with SIGTERM, which skips ``pytest_sessionfinish``; the handler is what reaps
    leaked children in that case.
    """
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler), "No SIGTERM handler installed for the test session"
    assert getattr(handler, "__name__", "") == "_sigterm_reap_handler"

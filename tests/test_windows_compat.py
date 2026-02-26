"""Tests for Windows compatibility — verifying Unix-only modules don't leak into import chains.

These tests simulate a Windows environment by patching sys.platform and hiding
Unix-only modules (fcntl, termios, tty, pty) from sys.modules, then verify that
all critical import chains succeed.
"""

import importlib
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


# Unix-only modules that must not be imported at top level
UNIX_ONLY_MODULES = ["fcntl", "termios", "tty", "pty"]


def _hide_unix_modules(monkeypatch):
    """Block Unix-only modules from being imported.

    Patches sys.modules to make importing these modules raise ImportError,
    simulating a Windows environment.
    """
    sentinel = types.ModuleType("_blocked")

    class BlockedImporter:
        """Meta path finder that blocks Unix-only modules."""

        def find_module(self, name, path=None):
            if name in UNIX_ONLY_MODULES:
                return self
            return None

        def load_module(self, name):
            raise ImportError(f"No module named '{name}' (blocked for Windows compat test)")

    # Remove any already-imported Unix modules
    saved = {}
    for mod_name in UNIX_ONLY_MODULES:
        if mod_name in sys.modules:
            saved[mod_name] = sys.modules[mod_name]
            monkeypatch.delitem(sys.modules, mod_name)

    # Insert our blocker at the front of meta_path
    blocker = BlockedImporter()
    monkeypatch.setattr(sys, "meta_path", [blocker] + sys.meta_path)

    return saved


def _force_reimport(module_path, monkeypatch):
    """Force a fresh import of a module, clearing it and its parents from sys.modules."""
    parts = module_path.split(".")
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in sys.modules:
            monkeypatch.delitem(sys.modules, key)


class TestPtyStreamDispatcher:
    """Verify pty_stream.py selects the right module based on platform."""

    def test_unix_platform_imports_unix_module(self, monkeypatch):
        """On non-Windows, pty_stream should import from pty_stream_unix."""
        _force_reimport("chad.server.services.pty_stream", monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")

        mod = importlib.import_module("chad.server.services.pty_stream")
        importlib.reload(mod)

        # The Unix module uses fcntl in start_pty_session — verify it loaded
        assert hasattr(mod, "PTYStreamService")
        assert hasattr(mod, "PTYSession")
        assert hasattr(mod, "PTYEvent")
        assert hasattr(mod, "get_pty_stream_service")
        assert hasattr(mod, "reset_pty_stream_service")

    def test_win32_platform_imports_win_module(self, monkeypatch):
        """On Windows, pty_stream should import from pty_stream_win."""
        _force_reimport("chad.server.services.pty_stream", monkeypatch)
        _force_reimport("chad.server.services.pty_stream_win", monkeypatch)
        _hide_unix_modules(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")

        mod = importlib.import_module("chad.server.services.pty_stream")
        importlib.reload(mod)

        assert hasattr(mod, "PTYStreamService")
        assert hasattr(mod, "PTYSession")
        assert hasattr(mod, "PTYEvent")
        assert hasattr(mod, "get_pty_stream_service")
        assert hasattr(mod, "reset_pty_stream_service")


class TestCliAppImportable:
    """Verify app.py imports without Unix-only modules."""

    def test_terminal_io_dispatcher_win32(self, monkeypatch):
        """terminal_io should import _terminal_io_win on Windows."""
        _force_reimport("chad.ui.cli.terminal_io", monkeypatch)
        _force_reimport("chad.ui.cli._terminal_io_win", monkeypatch)
        _force_reimport("chad.ui.cli._terminal_io_unix", monkeypatch)
        _hide_unix_modules(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")

        mod = importlib.import_module("chad.ui.cli.terminal_io")
        importlib.reload(mod)

        assert hasattr(mod, "save_terminal")
        assert hasattr(mod, "restore_terminal")
        assert hasattr(mod, "enter_raw_mode")
        assert hasattr(mod, "poll_stdin")

    def test_terminal_io_dispatcher_unix(self, monkeypatch):
        """terminal_io should import _terminal_io_unix on Linux."""
        _force_reimport("chad.ui.cli.terminal_io", monkeypatch)
        _force_reimport("chad.ui.cli._terminal_io_unix", monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")

        mod = importlib.import_module("chad.ui.cli.terminal_io")
        importlib.reload(mod)

        assert hasattr(mod, "save_terminal")
        assert hasattr(mod, "restore_terminal")
        assert hasattr(mod, "enter_raw_mode")
        assert hasattr(mod, "poll_stdin")


class TestServerImportChain:
    """Verify the server import chain works without Unix modules."""

    def test_pty_stream_win_importable_standalone(self, monkeypatch):
        """pty_stream_win.py should import without any Unix modules."""
        _force_reimport("chad.server.services.pty_stream_win", monkeypatch)
        _hide_unix_modules(monkeypatch)

        # Should not raise ImportError
        mod = importlib.import_module("chad.server.services.pty_stream_win")
        importlib.reload(mod)

        assert hasattr(mod, "PTYStreamService")
        assert hasattr(mod, "get_pty_stream_service")


class TestPipeStreamServiceBasics:
    """Basic tests for the Windows pipe-based PTYStreamService.

    These tests can run on any platform since they only use subprocess pipes.
    """

    def test_start_and_terminate_session(self, tmp_path):
        """Can start and terminate a pipe-based session."""
        from chad.server.services.pty_stream_win import (
            PTYStreamService,
            reset_pty_stream_service,
        )

        service = PTYStreamService()
        try:
            # Start a simple command that runs briefly
            stream_id = service.start_pty_session(
                session_id="test-session",
                cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
            )

            assert stream_id.startswith("pty_")
            session = service.get_session(stream_id)
            assert session is not None
            assert session.active is True
            assert session.session_id == "test-session"
            assert session.master_fd == -1  # No PTY on pipe-based

            # Terminate
            result = service.terminate(stream_id)
            assert result is True
        finally:
            # Clean up
            for sid in service.list_sessions():
                service.terminate(sid)
            import time
            time.sleep(0.2)

    def test_output_collection(self, tmp_path):
        """Can collect output from a pipe-based session."""
        import time
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            stream_id = service.start_pty_session(
                session_id="test-output",
                cmd=[sys.executable, "-c", "print('hello from pipes')"],
                cwd=tmp_path,
            )

            # Wait for process to finish
            session = service.get_session(stream_id)
            for _ in range(50):
                if not session.active:
                    break
                time.sleep(0.1)

            # Check that events were buffered
            assert len(session._event_buffer) > 0

            # Find output event containing our text
            import base64
            found = False
            for event in session._event_buffer:
                if event.type == "output":
                    decoded = base64.b64decode(event.data).decode("utf-8", errors="replace")
                    if "hello from pipes" in decoded:
                        found = True
                        break

            assert found, "Expected output not found in buffered events"
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            time.sleep(0.2)

    def test_send_input(self, tmp_path):
        """Can send input to a pipe-based session."""
        import time
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            # Start a process that reads from stdin
            stream_id = service.start_pty_session(
                session_id="test-input",
                cmd=[sys.executable, "-c",
                     "import sys; line = sys.stdin.readline(); print(f'got: {line.strip()}')"],
                cwd=tmp_path,
            )

            # Send input
            result = service.send_input(stream_id, b"test-data\n")
            assert result is True

            # Wait for completion
            session = service.get_session(stream_id)
            for _ in range(50):
                if not session.active:
                    break
                time.sleep(0.1)

            # Check output
            import base64
            found = False
            for event in session._event_buffer:
                if event.type == "output":
                    decoded = base64.b64decode(event.data).decode("utf-8", errors="replace")
                    if "got: test-data" in decoded:
                        found = True
                        break

            assert found, "Expected echoed input not found"
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            time.sleep(0.2)

    def test_resize_is_noop(self, tmp_path):
        """Resize returns False on pipe-based sessions (no terminal)."""
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            stream_id = service.start_pty_session(
                session_id="test-resize",
                cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
            )

            # Resize should be a no-op
            result = service.resize(stream_id, 50, 120)
            assert result is False
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            import time
            time.sleep(0.2)

    def test_get_session_by_session_id(self, tmp_path):
        """Can look up session by parent session_id."""
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            stream_id = service.start_pty_session(
                session_id="parent-session-123",
                cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
            )

            session = service.get_session_by_session_id("parent-session-123")
            assert session is not None
            assert session.stream_id == stream_id

            # Non-existent session
            assert service.get_session_by_session_id("nonexistent") is None
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            import time
            time.sleep(0.2)

    def test_cleanup_session(self, tmp_path):
        """Can clean up a session from tracking."""
        import time
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            stream_id = service.start_pty_session(
                session_id="test-cleanup",
                cmd=[sys.executable, "-c", "print('done')"],
                cwd=tmp_path,
            )

            # Wait for completion
            session = service.get_session(stream_id)
            for _ in range(50):
                if not session.active:
                    break
                time.sleep(0.1)

            service.cleanup_session(stream_id)
            assert service.get_session(stream_id) is None
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            time.sleep(0.2)


class TestNoUnixModulesAtTopLevel:
    """Verify that no source files import Unix-only modules at the top level
    outside of platform-guarded blocks.
    """

    def test_pty_stream_dispatcher_has_no_unix_imports(self):
        """pty_stream.py (dispatcher) should not directly import Unix modules."""
        src = Path(__file__).parent.parent / "src" / "chad" / "server" / "services" / "pty_stream.py"
        content = src.read_text()

        for mod in UNIX_ONLY_MODULES:
            # Check for bare "import fcntl" or "from fcntl" outside of platform guard
            assert f"import {mod}" not in content or "sys.platform" in content, (
                f"pty_stream.py should not directly import {mod}"
            )

    def test_app_py_has_no_unix_imports(self):
        """app.py should not import termios/tty/select at top level."""
        src = Path(__file__).parent.parent / "src" / "chad" / "ui" / "cli" / "app.py"
        content = src.read_text()

        for mod in ["termios", "tty"]:
            assert f"import {mod}" not in content, (
                f"app.py should not import {mod} at top level — use terminal_io"
            )

    def test_pty_runner_deleted(self):
        """pty_runner.py should no longer exist (dead code)."""
        src = Path(__file__).parent.parent / "src" / "chad" / "ui" / "cli" / "pty_runner.py"
        assert not src.exists(), "pty_runner.py should be deleted (dead code)"

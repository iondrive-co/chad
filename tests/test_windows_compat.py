"""Tests for Windows compatibility — verifying Unix-only modules don't leak into import chains.

These tests simulate a Windows environment by patching sys.platform and hiding
Unix-only modules (fcntl, termios, tty, pty) from sys.modules, then verify that
all critical import chains succeed.
"""

import importlib
import sys
from pathlib import Path


# Unix-only modules that must not be imported at top level
UNIX_ONLY_MODULES = ["fcntl", "termios", "tty", "pty"]


def _hide_unix_modules(monkeypatch):
    """Block Unix-only modules from being imported.

    Patches sys.modules to make importing these modules raise ImportError,
    simulating a Windows environment.
    """
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

    def test_tunnel_service_importable_on_windows(self, monkeypatch):
        """Tunnel service must import without Unix-only modules on Windows."""
        _force_reimport("chad.server.services.tunnel_service", monkeypatch)
        _hide_unix_modules(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")

        mod = importlib.import_module("chad.server.services.tunnel_service")
        importlib.reload(mod)

        assert hasattr(mod, "TunnelService")
        assert hasattr(mod, "get_tunnel_service")


class TestPipeStreamServiceBasics:
    """Basic tests for the Windows pipe-based PTYStreamService.

    These tests can run on any platform since they only use subprocess pipes.
    """

    def test_start_and_terminate_session(self, tmp_path):
        """Can start and terminate a pipe-based session."""
        from chad.server.services.pty_stream_win import PTYStreamService

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
        """Can send input to a ConPTY session."""
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

            # Give ConPTY process time to start before sending input
            time.sleep(0.5)

            # Send input (\n is translated to \r by send_input for ConPTY)
            result = service.send_input(stream_id, b"test-data\n")
            assert result is True

            # Wait for process to finish and output to appear
            session = service.get_session(stream_id)
            import base64
            deadline = time.time() + 5.0
            found = False
            while time.time() < deadline:
                with session._lock:
                    output = b"".join(
                        base64.b64decode(e.data)
                        for e in session._event_buffer
                        if e.type == "output"
                    ).decode("utf-8", errors="replace")
                if "got: test-data" in output:
                    found = True
                    break
                time.sleep(0.1)

            assert found, f"Expected 'got: test-data' in output, got: {output!r}"
        finally:
            for sid in service.list_sessions():
                service.terminate(sid)
            time.sleep(0.2)

    def test_resize_works(self, tmp_path):
        """Resize works with ConPTY on Windows."""
        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()
        try:
            stream_id = service.start_pty_session(
                session_id="test-resize",
                cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
            )

            result = service.resize(stream_id, 50, 120)
            assert result is True
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


class TestWindowsPipeStreaming:
    """Verify Windows ConPTY streaming delivers output incrementally."""

    def test_conpty_delivers_output_immediately(self, tmp_path):
        """ConPTY must deliver small outputs without buffering delays.

        Pipe-based subprocess I/O buffers stdout in the child process when
        it's not connected to a TTY. ConPTY provides a real pseudo-terminal
        so the child process flushes output in real time.
        """
        if sys.platform != "win32":
            import pytest
            pytest.skip("Windows-only test")

        import base64
        import time

        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()

        # Script that prints a small message then stays alive for 30s.
        # With read(), the 6-byte output sits in BufferedReader's internal
        # buffer waiting for 4096 bytes total — the process never exits
        # within our timeout, so the data is never delivered.
        # With read1(), the 6 bytes are delivered immediately.
        script = (
            "import sys, time; "
            "sys.stdout.write('HELLO\\n'); sys.stdout.flush(); "
            "time.sleep(30)"
        )

        stream_id = service.start_pty_session(
            session_id="read1-test",
            cmd=[sys.executable, "-c", script],
            cwd=tmp_path,
            env={},
        )

        try:
            # Poll for output events containing HELLO.
            # ConPTY emits terminal init sequences first, then script output.
            deadline = time.time() + 5.0
            output = b""
            session = service.get_session(stream_id)
            while time.time() < deadline:
                if session:
                    with session._lock:
                        output = b"".join(
                            base64.b64decode(e.data)
                            for e in session._event_buffer
                            if e.type == "output"
                        )
                    if b"HELLO" in output:
                        break
                time.sleep(0.05)

            assert b"HELLO" in output, (
                "Output should contain HELLO — ConPTY delivers data in real time"
            )
        finally:
            service.terminate(stream_id)
            # Wait for process to die so tmp_path can be cleaned up
            time.sleep(0.5)

    def test_nesting_detection_vars_stripped(self, tmp_path, monkeypatch):
        """PTY service must strip CLAUDECODE env vars so provider CLIs start.

        When Chad is launched from within a Claude Code session, CLAUDECODE=1
        is inherited. If passed through to child processes, Claude CLI refuses
        to start with 'cannot be launched inside another Claude Code session'.
        """
        if sys.platform != "win32":
            import pytest
            pytest.skip("Windows-only test")

        import base64
        import time

        # Simulate being inside a Claude Code session
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")

        from chad.server.services.pty_stream_win import PTYStreamService

        service = PTYStreamService()

        # Script that prints the env var values — they should be absent
        script = (
            "import os, sys; "
            "cc = os.environ.get('CLAUDECODE', 'ABSENT'); "
            "ce = os.environ.get('CLAUDE_CODE_ENTRYPOINT', 'ABSENT'); "
            "sys.stdout.write('CC=' + cc + ' CE=' + ce + chr(10)); "
            "sys.stdout.flush()"
        )

        stream_id = service.start_pty_session(
            session_id="nesting-test",
            cmd=[sys.executable, "-c", script],
            cwd=tmp_path,
            env={},
        )

        try:
            deadline = time.time() + 5.0
            session = service.get_session(stream_id)
            while time.time() < deadline:
                if session:
                    with session._lock:
                        if any(e.type == "exit" for e in session._event_buffer):
                            break
                time.sleep(0.05)

            with session._lock:
                output_events = [e for e in session._event_buffer if e.type == "output"]

            output = b"".join(
                base64.b64decode(e.data) for e in output_events
            ).decode(errors="replace")

            assert "CC=ABSENT" in output, (
                f"CLAUDECODE should be stripped from child env, got: {output}"
            )
            assert "CE=ABSENT" in output, (
                f"CLAUDE_CODE_ENTRYPOINT should be stripped from child env, got: {output}"
            )
        finally:
            service.terminate(stream_id)
            time.sleep(0.5)

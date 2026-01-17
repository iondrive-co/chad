"""PTY streaming service for managing agent PTY sessions."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import select
import signal
import struct
import termios
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

try:
    import pty
except ImportError:
    pty = None


@dataclass
class PTYSession:
    """Represents an active PTY session."""

    stream_id: str
    session_id: str
    pid: int
    master_fd: int
    cmd: list[str]
    cwd: Path
    env: dict[str, str]

    # State
    active: bool = True
    exit_code: int | None = None

    # Subscribers receive events via asyncio queues
    _subscribers: list[asyncio.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Logging callback - called synchronously for every event before broadcast
    # This is a dedicated path for logging that doesn't compete with client queues
    _log_callback: "Callable[[PTYEvent], None] | None" = None

    # Output thread
    _output_thread: threading.Thread | None = None


@dataclass
class PTYEvent:
    """An event from a PTY session."""

    type: str  # "output", "exit", "error"
    stream_id: str
    data: str = ""  # Base64 encoded for output
    exit_code: int | None = None
    error: str | None = None
    has_ansi: bool = True


class PTYStreamService:
    """Manages PTY sessions and streams output to subscribers."""

    def __init__(self):
        self._sessions: dict[str, PTYSession] = {}
        self._lock = threading.Lock()

    def start_pty_session(
        self,
        session_id: str,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        rows: int = 24,
        cols: int = 80,
        log_callback: Callable[["PTYEvent"], None] | None = None,
    ) -> str:
        """Start a PTY process.

        Args:
            session_id: The session this PTY belongs to
            cmd: Command and arguments to run
            cwd: Working directory
            env: Additional environment variables
            rows: Initial terminal rows
            cols: Initial terminal columns
            log_callback: Optional callback for logging events (called synchronously
                before broadcasting to subscriber queues, never drops events)

        Returns:
            stream_id for this PTY session
        """
        if pty is None:
            raise RuntimeError("PTY not available on this platform")

        stream_id = f"pty_{uuid.uuid4().hex[:8]}"

        # Merge environment
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        # Set terminal size env vars
        full_env["LINES"] = str(rows)
        full_env["COLUMNS"] = str(cols)

        # Fork with PTY
        pid, master_fd = pty.fork()

        if pid == 0:
            # Child process
            os.chdir(cwd)
            os.execvpe(cmd[0], cmd, full_env)
        else:
            # Parent process
            # Set terminal size
            self._set_winsize(master_fd, rows, cols)

            # Make master_fd non-blocking
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Create session
            session = PTYSession(
                stream_id=stream_id,
                session_id=session_id,
                pid=pid,
                master_fd=master_fd,
                cmd=cmd,
                cwd=cwd,
                env=full_env,
                _log_callback=log_callback,
            )

            with self._lock:
                self._sessions[stream_id] = session

            # Start output reading thread
            thread = threading.Thread(
                target=self._read_output_loop,
                args=(session,),
                daemon=True,
            )
            session._output_thread = thread
            thread.start()

            return stream_id

    def _set_winsize(self, fd: int, rows: int, cols: int) -> None:
        """Set terminal window size."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    def _read_output_loop(self, session: PTYSession) -> None:
        """Read output from PTY and dispatch to subscribers."""
        while session.active:
            try:
                # Wait for data with timeout
                rlist, _, _ = select.select([session.master_fd], [], [], 0.1)

                if session.master_fd in rlist:
                    try:
                        data = os.read(session.master_fd, 4096)
                        if data:
                            # Respond to cursor position requests (CSI 6n) so TUI libraries don't hang
                            data = self._handle_cpr_request(session, data)
                            if not data:
                                continue
                            # Check for ANSI codes
                            has_ansi = b"\x1b[" in data or b"\x1b]" in data

                            event = PTYEvent(
                                type="output",
                                stream_id=session.stream_id,
                                data=base64.b64encode(data).decode("ascii"),
                                has_ansi=has_ansi,
                            )
                            self._dispatch_event(session, event)
                        else:
                            # EOF - process exited
                            break
                    except OSError:
                        # Process closed
                        break

            except (ValueError, OSError):
                # fd closed or invalid
                break

        # Process exited - get exit code
        try:
            _, status = os.waitpid(session.pid, os.WNOHANG)
            if os.WIFEXITED(status):
                session.exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                session.exit_code = -os.WTERMSIG(status)
            else:
                session.exit_code = -1
        except ChildProcessError:
            session.exit_code = 0

        session.active = False

        # Dispatch exit event
        event = PTYEvent(
            type="exit",
            stream_id=session.stream_id,
            exit_code=session.exit_code,
        )
        self._dispatch_event(session, event)

        # Close fd
        try:
            os.close(session.master_fd)
        except OSError:
            pass

    def _dispatch_event(self, session: PTYSession, event: PTYEvent) -> None:
        """Send event to logging callback and all subscribers.

        The logging callback is called first (synchronously) before broadcasting
        to subscriber queues. This ensures logging never misses events due to
        queue contention.
        """
        # Call logging callback first - this is synchronous and never drops events
        if session._log_callback:
            try:
                session._log_callback(event)
            except Exception:
                pass  # Don't let logging errors affect PTY streaming

        # Then broadcast to subscriber queues
        with session._lock:
            for q in session._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop if queue is full
                    pass

    def send_input(self, stream_id: str, data: bytes) -> bool:
        """Send input to PTY.

        Args:
            stream_id: The PTY stream ID
            data: Raw bytes to send

        Returns:
            True if sent, False if session not found
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session.active:
            return False

        try:
            os.write(session.master_fd, data)
            return True
        except OSError:
            return False

    def resize(self, stream_id: str, rows: int, cols: int) -> bool:
        """Resize terminal.

        Args:
            stream_id: The PTY stream ID
            rows: New row count
            cols: New column count

        Returns:
            True if resized, False if session not found
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session.active:
            return False

        try:
            self._set_winsize(session.master_fd, rows, cols)
            # Also send SIGWINCH to the process group
            os.killpg(os.getpgid(session.pid), signal.SIGWINCH)
            return True
        except (OSError, ProcessLookupError):
            return False

    async def subscribe(self, stream_id: str) -> AsyncIterator[PTYEvent]:
        """Subscribe to PTY output events.

        Args:
            stream_id: The PTY stream ID

        Yields:
            PTYEvent objects as they occur
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session:
            return

        # Create queue for this subscriber
        q: asyncio.Queue[PTYEvent] = asyncio.Queue(maxsize=1000)

        with session._lock:
            session._subscribers.append(q)

        try:
            while True:
                try:
                    # Wait for event with timeout
                    event = await asyncio.wait_for(q.get(), timeout=0.1)
                    yield event

                    if event.type == "exit":
                        break

                except asyncio.TimeoutError:
                    # Check if session is still active
                    if not session.active:
                        break

        finally:
            with session._lock:
                if q in session._subscribers:
                    session._subscribers.remove(q)

    def terminate(self, stream_id: str) -> bool:
        """Terminate a PTY session.

        Args:
            stream_id: The PTY stream ID

        Returns:
            True if terminated, False if not found
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session:
            return False

        session.active = False

        # Kill the process
        try:
            os.killpg(os.getpgid(session.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        # Force kill after short delay
        def force_kill():
            try:
                os.killpg(os.getpgid(session.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

        threading.Timer(2.0, force_kill).start()

        return True

    # -------------------------------------------------------------------------
    # Terminal emulation helpers
    # -------------------------------------------------------------------------
    def _handle_cpr_request(self, session: PTYSession, data: bytes) -> bytes:
        """Detect and respond to cursor position requests (CSI 6n).

        prompt_toolkit sends \x1b[6n to ask the terminal for the cursor
        position. In headless PTYs (Gradio path) there is no real terminal
        to answer, so real agents would hang and abort. We fake a response
        of ESC[1;1R to unblock them and strip the request from the stream.
        """
        cpr_seq = b"\x1b[6n"
        if cpr_seq not in data:
            return data

        # Respond with a plausible cursor position
        try:
            # Append carriage return so cooked mode delivers immediately
            os.write(session.master_fd, b"\x1b[1;1R\r")
        except OSError:
            pass

        # Remove the request from the outgoing data so it doesn't appear in logs/UI
        return data.replace(cpr_seq, b"")

    def get_session(self, stream_id: str) -> PTYSession | None:
        """Get a PTY session by ID."""
        with self._lock:
            return self._sessions.get(stream_id)

    def get_session_by_session_id(self, session_id: str) -> PTYSession | None:
        """Get a PTY session by the parent session ID."""
        with self._lock:
            for session in self._sessions.values():
                if session.session_id == session_id:
                    return session
        return None

    def list_sessions(self) -> list[str]:
        """List all active PTY stream IDs."""
        with self._lock:
            return [s.stream_id for s in self._sessions.values() if s.active]

    def cleanup_session(self, stream_id: str) -> None:
        """Remove a session from tracking."""
        with self._lock:
            if stream_id in self._sessions:
                del self._sessions[stream_id]

    def set_log_callback(
        self,
        stream_id: str,
        callback: Callable[["PTYEvent"], None] | None,
    ) -> bool:
        """Set the logging callback for a PTY session.

        The callback is called synchronously for every PTY event before
        broadcasting to subscriber queues. This ensures logging never misses
        events due to queue contention.

        Args:
            stream_id: The PTY stream ID
            callback: Callback function or None to clear

        Returns:
            True if callback was set, False if session not found
        """
        with self._lock:
            session = self._sessions.get(stream_id)
            if not session:
                return False
            session._log_callback = callback
            return True


# Global instance
_pty_stream_service: PTYStreamService | None = None


def get_pty_stream_service() -> PTYStreamService:
    """Get the global PTYStreamService instance."""
    global _pty_stream_service
    if _pty_stream_service is None:
        _pty_stream_service = PTYStreamService()
    return _pty_stream_service


def reset_pty_stream_service() -> None:
    """Reset the global service (for testing)."""
    global _pty_stream_service
    if _pty_stream_service:
        # Terminate all sessions
        for stream_id in _pty_stream_service.list_sessions():
            _pty_stream_service.terminate(stream_id)
    _pty_stream_service = None

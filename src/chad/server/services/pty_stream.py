"""PTY streaming service for managing agent PTY sessions."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import queue
import select
import signal
import struct
import subprocess
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

    # Subprocess object (when using subprocess.Popen instead of pty.fork)
    _proc: subprocess.Popen | None = None
    # Whether stdin uses a pipe instead of PTY (avoids echo issues)
    _stdin_pipe: bool = False

    # State
    active: bool = True
    exit_code: int | None = None

    # Subscribers receive events via thread-safe queues
    # Using queue.Queue instead of asyncio.Queue for safe cross-thread dispatch
    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Buffer of recent events for replay to late subscribers
    # This handles the race condition where subscribers connect after events have been dispatched
    _event_buffer: list["PTYEvent"] = field(default_factory=list)

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
        stdin_pipe: bool = False,
    ) -> str:
        """Start a PTY process.

        Uses subprocess.Popen with pty.openpty() instead of pty.fork() to avoid
        deadlock issues in multi-threaded processes (like FastAPI/uvicorn).

        Args:
            session_id: The session this PTY belongs to
            cmd: Command and arguments to run
            cwd: Working directory
            env: Additional environment variables
            rows: Initial terminal rows
            cols: Initial terminal columns
            log_callback: Optional callback for logging events (called synchronously
                before broadcasting to subscriber queues, never drops events)
            stdin_pipe: If True, use a pipe for stdin instead of PTY (avoids echo).
                Use write_stdin() to send input when this is True.

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

        # Create PTY pair - avoids pty.fork() which can deadlock in multi-threaded processes
        master_fd, slave_fd = pty.openpty()

        # Set terminal size on the master
        self._set_winsize(master_fd, rows, cols)

        # Create preexec function that sets up the controlling terminal
        _slave_fd = slave_fd

        def setup_child():
            # Create new session (become session leader)
            os.setsid()
            # Set the slave as the controlling terminal
            fcntl.ioctl(_slave_fd, termios.TIOCSCTTY, 0)

        # Start the process with the slave PTY as stdout/stderr
        # stdin can optionally use a pipe to avoid echo issues
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_pipe else slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(cwd),
            env=full_env,
            preexec_fn=setup_child,
            close_fds=True,
            pass_fds=(slave_fd,),
        )

        # Close the slave FD in the parent - the child has its own copy
        os.close(slave_fd)

        # Make master_fd non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Create session
        session = PTYSession(
            stream_id=stream_id,
            session_id=session_id,
            pid=proc.pid,
            master_fd=master_fd,
            cmd=cmd,
            cwd=cwd,
            env=full_env,
            _proc=proc,
            _stdin_pipe=stdin_pipe,
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
        if session._proc is not None:
            try:
                session._proc.wait(timeout=1.0)
                session.exit_code = session._proc.returncode
            except subprocess.TimeoutExpired:
                session.exit_code = -1
        else:
            # Legacy pty.fork() path
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

        # Dispatch exit event BEFORE setting active=False to avoid race condition.
        # Subscribers check session.active on timeout - if we set it False first,
        # they may exit before receiving the exit event.
        event = PTYEvent(
            type="exit",
            stream_id=session.stream_id,
            exit_code=session.exit_code,
        )
        self._dispatch_event(session, event)

        session.active = False

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

        Events are also buffered for replay to late subscribers who connect
        after events have been dispatched.
        """
        # Call logging callback first - this is synchronous and never drops events
        if session._log_callback:
            try:
                session._log_callback(event)
            except Exception:
                pass  # Don't let logging errors affect PTY streaming

        # Buffer event for late subscribers (keep last 1000 events)
        with session._lock:
            session._event_buffer.append(event)
            if len(session._event_buffer) > 1000:
                session._event_buffer = session._event_buffer[-1000:]

            # Then broadcast to subscriber queues (thread-safe)
            # Using queue.Queue which is inherently thread-safe
            for q in session._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    # Drop if queue is full
                    pass

    def send_input(self, stream_id: str, data: bytes, close_stdin: bool = False) -> bool:
        """Send input to PTY or stdin pipe.

        Args:
            stream_id: The PTY stream ID
            data: Raw bytes to send
            close_stdin: If True and using stdin_pipe mode, close stdin after writing.
                This signals EOF to the process (useful for single-prompt agents).

        Returns:
            True if sent, False if session not found
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session.active:
            return False

        try:
            if session._stdin_pipe and session._proc and session._proc.stdin:
                # Write to stdin pipe (no echo)
                session._proc.stdin.write(data)
                session._proc.stdin.flush()
                if close_stdin:
                    session._proc.stdin.close()
            else:
                # Write to PTY master (may echo)
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

        Late subscribers receive buffered events first, then live events.
        This handles the race condition where subscribers connect after
        events have been dispatched.

        Args:
            stream_id: The PTY stream ID

        Yields:
            PTYEvent objects as they occur
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session:
            return

        # Create thread-safe queue for this subscriber
        # Using queue.Queue for safe cross-thread dispatch from PTY read thread
        q: queue.Queue[PTYEvent] = queue.Queue(maxsize=1000)

        # Atomically: add subscriber and get buffered events to replay
        with session._lock:
            session._subscribers.append(q)
            buffered_events = list(session._event_buffer)

        # Replay buffered events first (handles late subscriber race condition)
        for event in buffered_events:
            yield event
            if event.type == "exit":
                # Session already ended - no need to wait for more events
                with session._lock:
                    if q in session._subscribers:
                        session._subscribers.remove(q)
                return

        try:
            while True:
                # Poll the thread-safe queue with non-blocking get + async sleep
                # This allows yielding to the event loop while waiting for events
                try:
                    event = q.get_nowait()
                    yield event

                    if event.type == "exit":
                        break

                except queue.Empty:
                    # No event available, check if session is still active
                    if not session.active:
                        # Session ended - drain any remaining events
                        while True:
                            try:
                                event = q.get_nowait()
                                yield event
                                if event.type == "exit":
                                    break
                            except queue.Empty:
                                break
                        break

                    # Yield to event loop briefly before checking again
                    await asyncio.sleep(0.05)

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
        if session._proc is not None:
            try:
                session._proc.terminate()
            except OSError:
                pass

            # Force kill after short delay
            def force_kill():
                try:
                    session._proc.kill()
                except OSError:
                    pass

            threading.Timer(2.0, force_kill).start()
        else:
            # Legacy pty.fork() path
            try:
                os.killpg(os.getpgid(session.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

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
            matching = [s for s in self._sessions.values() if s.session_id == session_id]

        if not matching:
            return None

        # Prefer the most recent active session (dict preserves insertion order)
        for session in reversed(matching):
            if session.active:
                return session

        # Fall back to the most recent inactive session
        return matching[-1]

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

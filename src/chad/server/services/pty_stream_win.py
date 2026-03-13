"""ConPTY-based streaming service for Windows.

Uses pywinpty (Windows ConPTY) to give child processes a real pseudo-terminal,
which ensures they flush output in real time instead of buffering it in pipes.
"""

from __future__ import annotations

import asyncio
import base64
import os
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

try:
    from winpty import PTY
except ImportError:  # pragma: no cover - exercised in Windows compat tests
    PTY = None  # type: ignore[assignment]


@dataclass
class PTYSession:
    """Represents an active ConPTY session on Windows."""

    stream_id: str
    session_id: str
    pid: int
    master_fd: int  # -1 on Windows (no fd, ConPTY uses its own handle)
    cmd: list[str]
    cwd: Path
    env: dict[str, str]

    # ConPTY object
    _pty: PTY | None = None
    _proc: subprocess.Popen[bytes] | None = None

    # State
    active: bool = True
    exit_code: int | None = None

    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _event_buffer: list["PTYEvent"] = field(default_factory=list)
    _log_callback: "Callable[[PTYEvent], None] | None" = None
    _output_thread: threading.Thread | None = None


@dataclass
class PTYEvent:
    """An event from a streaming session."""

    type: str  # "output", "exit", "error"
    stream_id: str
    data: str = ""  # Base64 encoded for output
    exit_code: int | None = None
    error: str | None = None
    has_ansi: bool = True
    text: bool = False


class PTYStreamService:
    """ConPTY-based stream service for Windows."""

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
        """Start a ConPTY-based process.

        Args:
            session_id: The session this belongs to
            cmd: Command and arguments to run
            cwd: Working directory
            env: Additional environment variables
            rows: Terminal rows
            cols: Terminal columns
            log_callback: Optional callback for logging events
            stdin_pipe: Ignored on Windows (ConPTY handles I/O)

        Returns:
            stream_id for this session
        """
        stream_id = f"pty_{uuid.uuid4().hex[:8]}"

        full_env = os.environ.copy()
        # Strip nesting-detection variables so provider CLIs don't refuse to
        # start when Chad itself is launched from inside a Claude Code session.
        for var in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            full_env.pop(var, None)
        if env:
            full_env.update(env)

        full_env["LINES"] = str(rows)
        full_env["COLUMNS"] = str(cols)

        # Create ConPTY
        if PTY is not None:
            pty = PTY(cols, rows)

            # Build command line for CreateProcess (appname + cmdline)
            appname = cmd[0]
            cmdline = subprocess.list2cmdline(cmd[1:]) if len(cmd) > 1 else None

            # Build env string: null-separated KEY=VALUE pairs
            env_str = "\0".join(f"{k}={v}" for k, v in full_env.items()) + "\0"

            pty.spawn(appname, cmdline=cmdline, cwd=str(cwd), env=env_str)

            session = PTYSession(
                stream_id=stream_id,
                session_id=session_id,
                pid=pty.pid,
                master_fd=-1,
                cmd=cmd,
                cwd=cwd,
                env=full_env,
                _pty=pty,
                _log_callback=log_callback,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=full_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            session = PTYSession(
                stream_id=stream_id,
                session_id=session_id,
                pid=proc.pid,
                master_fd=-1,
                cmd=cmd,
                cwd=cwd,
                env=full_env,
                _proc=proc,
                _log_callback=log_callback,
            )

        with self._lock:
            self._sessions[stream_id] = session

        thread = threading.Thread(
            target=self._read_output_loop,
            args=(session,),
            daemon=True,
        )
        session._output_thread = thread
        thread.start()
        return stream_id

    def _read_output_loop(self, session: PTYSession) -> None:
        """Read output from ConPTY and dispatch to subscribers."""
        import time

        pty = session._pty
        proc = session._proc
        if not pty and not proc:
            return

        if proc is not None:
            try:
                assert proc.stdout is not None
                while session.active:
                    chunk = proc.stdout.readline()
                    if chunk:
                        event = PTYEvent(
                            type="output",
                            stream_id=session.stream_id,
                            data=base64.b64encode(chunk).decode("ascii"),
                            has_ansi=b"\x1b[" in chunk or b"\x1b]" in chunk,
                        )
                        self._dispatch_event(session, event)
                    elif proc.poll() is not None:
                        break
                    else:
                        time.sleep(0.01)
            finally:
                session.exit_code = proc.poll()
                event = PTYEvent(
                    type="exit",
                    stream_id=session.stream_id,
                    exit_code=session.exit_code,
                )
                self._dispatch_event(session, event)
                session.active = False
            return

        while session.active:
            try:
                data = pty.read()
                if data:
                    data_bytes = data.encode("utf-8", errors="replace")
                    data_bytes = self._handle_cpr_request(session, data_bytes)
                    if not data_bytes:
                        continue
                    has_ansi = b"\x1b[" in data_bytes or b"\x1b]" in data_bytes
                    event = PTYEvent(
                        type="output",
                        stream_id=session.stream_id,
                        data=base64.b64encode(data_bytes).decode("ascii"),
                        has_ansi=has_ansi,
                    )
                    self._dispatch_event(session, event)
                else:
                    # No data available — avoid busy-looping
                    time.sleep(0.01)
            except Exception:
                break

            # Check if process has exited
            try:
                if not pty.isalive():
                    # Drain remaining output — ConPTY may still have buffered
                    # data after process exit, so retry a few times.
                    for _ in range(10):
                        try:
                            remaining = pty.read()
                            if remaining:
                                data_bytes = remaining.encode(
                                    "utf-8", errors="replace"
                                )
                                has_ansi = (
                                    b"\x1b[" in data_bytes
                                    or b"\x1b]" in data_bytes
                                )
                                event = PTYEvent(
                                    type="output",
                                    stream_id=session.stream_id,
                                    data=base64.b64encode(
                                        data_bytes
                                    ).decode("ascii"),
                                    has_ansi=has_ansi,
                                )
                                self._dispatch_event(session, event)
                            else:
                                time.sleep(0.05)
                        except Exception:
                            break
                    break
            except Exception:
                break

        # Get exit code
        if pty is not None:
            try:
                session.exit_code = pty.get_exitstatus()
            except Exception:
                session.exit_code = -1

        event = PTYEvent(
            type="exit",
            stream_id=session.stream_id,
            exit_code=session.exit_code,
        )
        self._dispatch_event(session, event)
        session.active = False

    def _dispatch_event(self, session: PTYSession, event: PTYEvent) -> None:
        """Send event to logging callback and all subscribers."""
        if session._log_callback:
            try:
                session._log_callback(event)
            except Exception:
                pass

        with session._lock:
            session._event_buffer.append(event)
            if len(session._event_buffer) > 1000:
                session._event_buffer = session._event_buffer[-1000:]

            for q in session._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def send_input(self, stream_id: str, data: bytes, close_stdin: bool = False) -> bool:
        """Send input to process via ConPTY.

        Note: close_stdin is ignored since ConPTY doesn't support closing
        stdin independently of the terminal session.
        """
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session.active:
            return False

        try:
            if session._pty:
                # ConPTY is a terminal — translate \n to \r (Enter key)
                text = data.decode("utf-8", errors="replace").replace("\n", "\r")
                session._pty.write(text)
            elif session._proc and session._proc.stdin:
                session._proc.stdin.write(data)
                session._proc.stdin.flush()
            return True
        except Exception:
            return False

    def resize(self, stream_id: str, rows: int, cols: int) -> bool:
        """Resize the ConPTY terminal."""
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session._pty:
            return session is not None and session._proc is not None

        try:
            session._pty.set_size(cols, rows)
            return True
        except Exception:
            return False

    async def subscribe(self, stream_id: str) -> AsyncIterator[PTYEvent]:
        """Subscribe to output events."""
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session:
            return

        q: queue.Queue[PTYEvent] = queue.Queue(maxsize=1000)

        with session._lock:
            session._subscribers.append(q)
            buffered_events = list(session._event_buffer)

        for event in buffered_events:
            yield event
            if event.type == "exit":
                with session._lock:
                    if q in session._subscribers:
                        session._subscribers.remove(q)
                return

        try:
            while True:
                try:
                    event = q.get_nowait()
                    yield event
                    if event.type == "exit":
                        break
                except queue.Empty:
                    if not session.active:
                        while True:
                            try:
                                event = q.get_nowait()
                                yield event
                                if event.type == "exit":
                                    break
                            except queue.Empty:
                                break
                        break
                    await asyncio.sleep(0.05)
        finally:
            with session._lock:
                if q in session._subscribers:
                    session._subscribers.remove(q)

    def terminate(self, stream_id: str) -> bool:
        """Terminate a session."""
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session:
            return False

        session.active = False

        # Kill the process tree via taskkill for reliable cleanup
        pid = session.pid
        try:
            if session._proc is not None:
                session._proc.terminate()
            else:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
        except (subprocess.SubprocessError, OSError):
            pass

        return True

    def _handle_cpr_request(self, session: PTYSession, data: bytes) -> bytes:
        """Detect and respond to cursor position requests (CSI 6n)."""
        cpr_seq = b"\x1b[6n"
        if cpr_seq not in data:
            return data

        # Respond via ConPTY input
        try:
            if session._pty:
                session._pty.write("\x1b[1;1R\r")
        except Exception:
            pass

        return data.replace(cpr_seq, b"")

    def get_session(self, stream_id: str) -> PTYSession | None:
        """Get a session by stream ID."""
        with self._lock:
            return self._sessions.get(stream_id)

    def get_session_by_session_id(self, session_id: str) -> PTYSession | None:
        """Get a session by the parent session ID."""
        with self._lock:
            matching = [s for s in self._sessions.values() if s.session_id == session_id]

        if not matching:
            return None

        for session in reversed(matching):
            if session.active:
                return session

        return matching[-1]

    def list_sessions(self) -> list[str]:
        """List all active stream IDs."""
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
        """Set the logging callback for a session."""
        with self._lock:
            session = self._sessions.get(stream_id)
            if not session:
                return False
            session._log_callback = callback
            return True


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
        for stream_id in _pty_stream_service.list_sessions():
            _pty_stream_service.terminate(stream_id)
    _pty_stream_service = None

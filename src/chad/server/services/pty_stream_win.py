"""Pipe-based streaming service for Windows (no PTY support)."""

from __future__ import annotations

import asyncio
import base64
import os
import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable


@dataclass
class PTYSession:
    """Represents an active streaming session (pipe-based on Windows)."""

    stream_id: str
    session_id: str
    pid: int
    master_fd: int  # -1 on Windows (no PTY fd)
    cmd: list[str]
    cwd: Path
    env: dict[str, str]

    # Subprocess object
    _proc: subprocess.Popen | None = None
    _stdin_pipe: bool = False

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
    """Pipe-based stream service for Windows."""

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
        """Start a pipe-based process (Windows equivalent of PTY).

        Args:
            session_id: The session this belongs to
            cmd: Command and arguments to run
            cwd: Working directory
            env: Additional environment variables
            rows: Terminal rows (used for env vars only, no real terminal)
            cols: Terminal columns (used for env vars only, no real terminal)
            log_callback: Optional callback for logging events
            stdin_pipe: Ignored on Windows (always uses pipes)

        Returns:
            stream_id for this session
        """
        stream_id = f"pty_{uuid.uuid4().hex[:8]}"

        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        full_env["LINES"] = str(rows)
        full_env["COLUMNS"] = str(cols)

        creation_flags = 0
        startupinfo = None
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESTDHANDLES

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            env=full_env,
            creationflags=creation_flags,
            startupinfo=startupinfo,
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
            _stdin_pipe=True,
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
        """Read output from process stdout pipe and dispatch to subscribers."""
        proc = session._proc
        if not proc or not proc.stdout:
            return

        while session.active:
            try:
                data = proc.stdout.read(4096)
                if data:
                    data = self._handle_cpr_request(session, data)
                    if not data:
                        continue
                    has_ansi = b"\x1b[" in data or b"\x1b]" in data
                    event = PTYEvent(
                        type="output",
                        stream_id=session.stream_id,
                        data=base64.b64encode(data).decode("ascii"),
                        has_ansi=has_ansi,
                    )
                    self._dispatch_event(session, event)
                else:
                    break
            except (OSError, ValueError):
                break

        # Get exit code
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
                session.exit_code = proc.returncode
            except subprocess.TimeoutExpired:
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
        """Send input to process stdin pipe."""
        with self._lock:
            session = self._sessions.get(stream_id)

        if not session or not session.active:
            return False

        try:
            if session._proc and session._proc.stdin:
                session._proc.stdin.write(data)
                session._proc.stdin.flush()
                if close_stdin:
                    session._proc.stdin.close()
            return True
        except OSError:
            return False

    def resize(self, stream_id: str, rows: int, cols: int) -> bool:
        """Resize terminal — no-op on Windows (pipes have no terminal size)."""
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

        if session._proc is not None:
            pid = session._proc.pid
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                except (subprocess.SubprocessError, OSError):
                    try:
                        session._proc.kill()
                    except OSError:
                        pass
            else:
                try:
                    session._proc.terminate()
                except OSError:
                    pass

                def force_kill():
                    try:
                        session._proc.kill()
                    except OSError:
                        pass

                threading.Timer(2.0, force_kill).start()

        return True

    def _handle_cpr_request(self, session: PTYSession, data: bytes) -> bytes:
        """Detect and respond to cursor position requests (CSI 6n)."""
        cpr_seq = b"\x1b[6n"
        if cpr_seq not in data:
            return data

        # Respond via stdin pipe
        try:
            if session._proc and session._proc.stdin:
                session._proc.stdin.write(b"\x1b[1;1R\r")
                session._proc.stdin.flush()
        except OSError:
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

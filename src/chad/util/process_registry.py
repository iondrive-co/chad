"""Centralized process lifecycle management with cleanup guarantees.

This module provides a ProcessRegistry class that manages spawned processes
with proper cleanup, file locking, and verification capabilities.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from filelock import FileLock


@dataclass
class ManagedProcess:
    """A process registered with the ProcessRegistry."""

    pid: int
    pgid: int | None  # Process group ID on Unix
    started_at: float
    description: str


@dataclass
class ProcessRegistry:
    """Centralized process lifecycle management with cleanup guarantees.

    Features:
    - File locking prevents PID file corruption from concurrent access
    - Dual tracking (in-memory + persistent) for resilience
    - Escalation pattern: SIGTERM → wait → SIGKILL → verify
    - verify_cleanup() for test assertions
    """

    pidfile: Path = field(default_factory=lambda: Path("/tmp/chad_processes.pid"))
    max_age_seconds: float = 300.0
    _processes: Dict[int, ManagedProcess] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _atexit_registered: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._file_lock = FileLock(str(self.pidfile) + ".lock", timeout=10)

    def register(self, process: subprocess.Popen, description: str = "") -> ManagedProcess:
        """Register a process for lifecycle management.

        Args:
            process: The subprocess.Popen object to track
            description: Human-readable description for debugging

        Returns:
            ManagedProcess with tracking info
        """
        pid = process.pid
        pgid = None

        # Get process group ID on Unix
        if os.name != "nt":
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, OSError):
                pass

        managed = ManagedProcess(
            pid=pid,
            pgid=pgid,
            started_at=time.time(),
            description=description,
        )

        with self._lock:
            self._processes[pid] = managed
            self._write_to_pidfile(pid, managed.started_at)

        self._ensure_atexit_registered()
        return managed

    def unregister(self, pid: int) -> None:
        """Remove a process from tracking (call after successful termination)."""
        with self._lock:
            self._processes.pop(pid, None)
            self._remove_from_pidfile(pid)

    def terminate(self, pid: int, timeout: float = 2.0) -> bool:
        """Terminate a process with escalation: SIGTERM → wait → SIGKILL → verify.

        Args:
            pid: Process ID to terminate
            timeout: Seconds to wait after SIGTERM before SIGKILL

        Returns:
            True if process is confirmed dead, False if still running
        """
        managed = self._processes.get(pid)
        pgid = managed.pgid if managed else None

        # First try graceful termination
        try:
            if os.name != "nt" and pgid:
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            # Process already dead
            self.unregister(pid)
            return True

        # Wait for graceful termination
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._is_running(pid):
                self.unregister(pid)
                return True
            time.sleep(0.1)

        # Force kill
        try:
            if os.name != "nt" and pgid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            self.unregister(pid)
            return True

        # Final verification
        time.sleep(0.2)
        if not self._is_running(pid):
            self.unregister(pid)
            return True

        return False

    def terminate_all(self) -> List[int]:
        """Terminate all registered processes.

        Returns:
            List of PIDs that failed to terminate
        """
        failed = []
        pids = list(self._processes.keys())

        for pid in pids:
            if not self.terminate(pid):
                failed.append(pid)

        return failed

    def verify_cleanup(self) -> List[int]:
        """Return PIDs that are still running (for test assertions).

        This checks both in-memory tracking and the pidfile.
        """
        still_running = []

        # Check in-memory processes
        for pid in list(self._processes.keys()):
            if self._is_running(pid):
                still_running.append(pid)

        # Check pidfile for any we might have missed
        with self._file_lock:
            for pid, _ in self._read_pidfile_entries():
                if pid not in still_running and self._is_running(pid):
                    still_running.append(pid)

        return still_running

    def cleanup_stale(self) -> List[int]:
        """Kill processes older than max_age_seconds.

        Returns:
            List of PIDs that were killed
        """
        killed = []
        now = time.time()

        with self._file_lock:
            entries = self._read_pidfile_entries()
            remaining = []

            for pid, started_at in entries:
                if not self._is_running(pid):
                    # Already dead, don't keep in file
                    continue

                age = now - started_at
                if age > self.max_age_seconds:
                    # Stale - kill it
                    if self.terminate(pid):
                        killed.append(pid)
                    else:
                        remaining.append((pid, started_at))
                else:
                    remaining.append((pid, started_at))

            self._write_pidfile_entries(remaining)

        return killed

    def _is_running(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _read_pidfile_entries(self) -> List[tuple[int, float]]:
        """Read PID entries from the pidfile (call with file lock held)."""
        if not self.pidfile.exists():
            return []
        try:
            entries = []
            for line in self.pidfile.read_text().strip().split("\n"):
                if line:
                    parts = line.split(":")
                    if len(parts) == 2:
                        entries.append((int(parts[0]), float(parts[1])))
            return entries
        except (ValueError, OSError):
            return []

    def _write_pidfile_entries(self, entries: List[tuple[int, float]]) -> None:
        """Write PID entries to the pidfile (call with file lock held)."""
        try:
            content = "\n".join(f"{pid}:{start_time}" for pid, start_time in entries)
            self.pidfile.write_text(content)
        except OSError:
            pass

    def _write_to_pidfile(self, pid: int, started_at: float) -> None:
        """Add a PID to the pidfile."""
        with self._file_lock:
            entries = self._read_pidfile_entries()
            entries.append((pid, started_at))
            self._write_pidfile_entries(entries)

    def _remove_from_pidfile(self, pid: int) -> None:
        """Remove a PID from the pidfile."""
        with self._file_lock:
            entries = [(p, t) for p, t in self._read_pidfile_entries() if p != pid]
            self._write_pidfile_entries(entries)

    def _ensure_atexit_registered(self) -> None:
        """Register cleanup handler on process exit."""
        if not self._atexit_registered:
            atexit.register(self.terminate_all)
            self._atexit_registered = True


# Global registry instance for backward compatibility during migration
_global_registry: ProcessRegistry | None = None


def get_global_registry() -> ProcessRegistry:
    """Get or create the global ProcessRegistry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ProcessRegistry()
    return _global_registry

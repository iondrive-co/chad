"""Session management service for multi-client session handling."""

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from chad.util.git_worktree import MergeConflict
from chad.util.config_manager import ConfigManager


SessionStatus = Literal["active", "completed", "interrupted"]


@dataclass
class Session:
    """Per-session state for concurrent task execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    cancel_requested: bool = False
    resume_requested: bool = False  # Set to force resume from paused state
    active: bool = False
    paused: bool = False  # Set when waiting for usage reset
    provider: Any = None
    config: Any = None
    log_path: Path | None = None
    chat_history: list = field(default_factory=list)
    task_description: str | None = None
    project_path: str | None = None
    coding_account: str | None = None
    # Git worktree support
    worktree_path: Path | None = None
    worktree_branch: str | None = None
    worktree_base_commit: str | None = None
    has_worktree_changes: bool = False
    merge_conflicts: list[MergeConflict] | None = None
    # Provider session tracking for handoffs
    provider_session_id: str | None = None  # Native session ID (thread_id, session_id, etc.)
    provider_type: str | None = None  # Provider type (anthropic, openai, gemini, etc.)
    # Session status for resume support
    status: SessionStatus = "active"
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionManager:
    """Thread-safe session manager for concurrent client handling."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def create_session(
        self,
        project_path: str | None = None,
        name: str | None = None,
    ) -> Session:
        """Create a new session.

        Args:
            project_path: Optional project directory path
            name: Optional session name

        Returns:
            Newly created Session object
        """
        with self._lock:
            ConfigManager().ensure_recent_backup()
            session = Session(
                project_path=project_path,
            )
            # Default name to session ID if none provided
            session.name = name or session.id
            self._sessions[session.id] = session
            return session

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            Session if found, None otherwise
        """
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: str) -> Session:
        """Get an existing session or create a new one.

        Args:
            session_id: The session ID

        Returns:
            Existing or newly created Session
        """
        with self._lock:
            if session_id not in self._sessions:
                session = Session(id=session_id, name=session_id)
                self._sessions[session_id] = session
            return self._sessions[session_id]

    def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of all sessions sorted by creation time
        """
        with self._lock:
            return sorted(
                self._sessions.values(),
                key=lambda s: s.created_at,
                reverse=True,
            )

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: The session ID to delete

        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def update_activity(self, session_id: str) -> None:
        """Update the last activity timestamp for a session.

        Args:
            session_id: The session ID
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_activity = datetime.now(timezone.utc)

    def set_cancel_requested(self, session_id: str, value: bool = True) -> bool:
        """Set the cancel_requested flag for a session.

        Args:
            session_id: The session ID
            value: The flag value (default True)

        Returns:
            True if session was found and updated
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.cancel_requested = value
                return True
            return False

    def set_paused(self, session_id: str, value: bool = True) -> bool:
        """Set the paused flag for a session.

        Args:
            session_id: The session ID
            value: The flag value (default True)

        Returns:
            True if session was found and updated
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.paused = value
                return True
            return False

    def set_resume_requested(self, session_id: str, value: bool = True) -> bool:
        """Set the resume_requested flag for a session.

        Args:
            session_id: The session ID
            value: The flag value (default True)

        Returns:
            True if session was found and updated
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.resume_requested = value
                return True
            return False

    def get_active_sessions(self) -> list[Session]:
        """Get all sessions with active tasks.

        Returns:
            List of active sessions
        """
        with self._lock:
            return [s for s in self._sessions.values() if s.active]

    def count(self) -> int:
        """Get the total number of sessions.

        Returns:
            Number of sessions
        """
        with self._lock:
            return len(self._sessions)

    def load_from_logs(self, max_age_days: int = 3) -> int:
        """Restore sessions from persisted event logs on disk.

        Scans ~/.chad/logs/*.jsonl for previous sessions, reads the first
        event (session_started) for metadata and the last event to determine
        whether the session completed or was interrupted.

        Args:
            max_age_days: Skip log files older than this many days

        Returns:
            Number of sessions restored
        """
        from chad.util.event_log import EventLog
        from chad.util.git_worktree import GitWorktreeManager

        log_dir = EventLog.get_log_dir()
        if not log_dir.exists():
            return 0

        import time
        cutoff = time.time() - (max_age_days * 86400)
        restored = 0

        for log_file in log_dir.glob("*.jsonl"):
            try:
                # Skip old files
                if log_file.stat().st_mtime < cutoff:
                    continue

                session_id = log_file.stem

                # Skip if already loaded (e.g. active session)
                with self._lock:
                    if session_id in self._sessions:
                        continue

                # Read first and last lines
                first_line = None
                last_line = None
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if first_line is None:
                            first_line = line
                        last_line = line

                if not first_line:
                    continue

                first_event = json.loads(first_line)
                last_event = json.loads(last_line) if last_line else first_event

                # Only restore sessions that started properly
                if first_event.get("type") != "session_started":
                    continue

                task_desc = first_event.get("task_description", "")
                project_path = first_event.get("project_path")
                coding_account = first_event.get("coding_account")
                provider_type = first_event.get("coding_provider")

                # Determine status from last event
                if last_event.get("type") == "session_ended":
                    status: SessionStatus = "completed"
                else:
                    status = "interrupted"

                # Parse timestamp from first event
                ts_str = first_event.get("ts", "")
                try:
                    created_at = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    created_at = datetime.now(timezone.utc)

                # Parse last activity from last event
                last_ts_str = last_event.get("ts", ts_str)
                try:
                    last_activity = datetime.fromisoformat(last_ts_str)
                except (ValueError, TypeError):
                    last_activity = created_at

                # Build session name from task description
                name = task_desc[:60].strip() if task_desc else session_id
                if len(task_desc) > 60:
                    name += "..."

                session = Session(
                    id=session_id,
                    name=name,
                    task_description=task_desc,
                    project_path=project_path,
                    coding_account=coding_account,
                    provider_type=provider_type,
                    status=status,
                    active=False,
                    created_at=created_at,
                    last_activity=last_activity,
                )

                # Check if worktree still exists
                if project_path:
                    try:
                        git_mgr = GitWorktreeManager(Path(project_path))
                        branch = git_mgr._branch_name(session_id)
                        wt_path = git_mgr._worktree_path(session_id)
                        if wt_path.exists():
                            session.worktree_path = wt_path
                            session.worktree_branch = branch
                            session.has_worktree_changes = git_mgr.has_changes(session_id)
                    except Exception:
                        pass

                with self._lock:
                    self._sessions[session_id] = session
                restored += 1

            except Exception:
                # Skip unreadable log files
                continue

        return restored


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance.

    Returns:
        The global SessionManager
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def reset_session_manager() -> None:
    """Reset the global session manager (for testing)."""
    global _session_manager
    _session_manager = None

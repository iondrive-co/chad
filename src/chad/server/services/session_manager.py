"""Session management service for multi-client session handling."""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chad.util.git_worktree import MergeConflict


@dataclass
class Session:
    """Per-session state for concurrent task execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "New Session"
    cancel_requested: bool = False
    active: bool = False
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
            session = Session(
                project_path=project_path,
                name=name or "New Session",
            )
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
                session = Session(id=session_id)
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

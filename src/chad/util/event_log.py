"""Structured event logging for session handovers.

Events are stored as JSONL (one JSON object per line) in ~/.chad/logs/{session_id}.jsonl
Large artifacts (stdout/stderr >10KB) are stored separately in ~/.chad/logs/artifacts/
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


# Event types
EventType = Literal[
    "session_started",
    "model_selected",
    "provider_switched",
    "user_message",
    "assistant_message",
    "tool_declared",
    "tool_call_started",
    "tool_call_finished",
    "verification_attempt",
    "context_condensed",
    "terminal_output",
    "session_ended",
]


@dataclass
class ArtifactRef:
    """Reference to an artifact file."""

    path: str  # Relative path from logs directory
    sha256: str
    size: int


@dataclass
class MessageBlock:
    """A block within an assistant message."""

    kind: Literal["text", "thinking", "tool_call", "tool_result", "error"]
    content: str = ""
    tool: str | None = None
    tool_call_id: str | None = None
    args: dict[str, Any] | None = None


@dataclass
class EventBase:
    """Base class for all events."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    seq: int = 0
    session_id: str = ""
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        d = asdict(self)
        d["type"] = self.event_type
        return d

    @property
    def event_type(self) -> str:
        """Return the event type name."""
        # Convert class name to snake_case
        name = self.__class__.__name__
        if name.endswith("Event"):
            name = name[:-5]
        # Convert CamelCase to snake_case
        result = []
        for i, c in enumerate(name):
            if c.isupper() and i > 0:
                result.append("_")
            result.append(c.lower())
        return "".join(result)


@dataclass
class SessionStartedEvent(EventBase):
    """Logged when a session starts."""

    task_description: str = ""
    project_path: str = ""
    coding_provider: str = ""
    coding_account: str = ""
    coding_model: str | None = None


@dataclass
class ModelSelectedEvent(EventBase):
    """Logged when a model is selected or changed."""

    provider: str = ""
    model: str = ""
    reasoning_effort: str | None = None


@dataclass
class ProviderSwitchedEvent(EventBase):
    """Logged when switching between providers."""

    from_provider: str = ""
    to_provider: str = ""
    from_model: str = ""
    to_model: str = ""
    reason: str = ""


@dataclass
class UserMessageEvent(EventBase):
    """Logged when user sends a message."""

    content: str = ""


@dataclass
class AssistantMessageEvent(EventBase):
    """Logged when assistant responds."""

    blocks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_blocks(cls, blocks: list[MessageBlock], **kwargs) -> "AssistantMessageEvent":
        """Create from MessageBlock objects."""
        return cls(blocks=[asdict(b) for b in blocks], **kwargs)


@dataclass
class ToolDeclaredEvent(EventBase):
    """Logged when a tool is declared/available."""

    name: str = ""
    args_schema: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"


@dataclass
class ToolCallStartedEvent(EventBase):
    """Logged when a tool call begins."""

    tool_call_id: str = field(default_factory=lambda: f"tc_{uuid.uuid4().hex[:8]}")
    tool: str = ""  # "bash", "read", "write", "edit", "mcp", "glob", "grep"

    # For bash commands
    cwd: str | None = None
    command: str | None = None
    env_redactions: list[str] | None = None
    timeout_s: float | None = None

    # For file operations
    path: str | None = None
    file_bytes: int | None = None
    sha256: str | None = None

    # For file edits
    before_sha256: str | None = None

    # For MCP tools
    server: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None


@dataclass
class ToolCallFinishedEvent(EventBase):
    """Logged when a tool call completes."""

    tool_call_id: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    stdout_ref: dict[str, Any] | None = None  # ArtifactRef as dict
    stderr_ref: dict[str, Any] | None = None  # ArtifactRef as dict
    llm_summary: str = ""  # Bounded summary for handover

    # For file edits
    after_sha256: str | None = None
    patch_ref: dict[str, Any] | None = None  # ArtifactRef as dict


@dataclass
class VerificationAttemptEvent(EventBase):
    """Logged for each verification attempt."""

    attempt_number: int = 1
    tool_call_refs: list[str] = field(default_factory=list)
    passed: bool = False
    summary: str = ""
    issues: list[str] = field(default_factory=list)


@dataclass
class ContextCondensedEvent(EventBase):
    """Logged when context is condensed/summarized."""

    replaces_seq_range: tuple[int, int] = (0, 0)
    summary_text: str = ""
    policy: str = "rolling_window"


@dataclass
class TerminalOutputEvent(EventBase):
    """Logged for raw PTY terminal output."""

    data: str = ""  # Base64 encoded bytes
    has_ansi: bool = True
    text: str | None = None  # Human-readable decoded text (best-effort)


@dataclass
class SessionEndedEvent(EventBase):
    """Logged when a session ends."""

    success: bool = False
    reason: str = ""
    total_tool_calls: int = 0
    total_turns: int = 0


# Size threshold for storing artifacts separately (10KB)
ARTIFACT_SIZE_THRESHOLD = 10 * 1024

# Maximum artifact size (10MB)
MAX_ARTIFACT_SIZE = 10 * 1024 * 1024


class EventLog:
    """Manages structured event logging for a session."""

    def __init__(
        self,
        session_id: str,
        base_dir: Path | None = None,
    ):
        self.session_id = session_id
        self._seq = 0
        self._current_turn_id: str | None = None

        # Determine base directory
        env_dir = os.environ.get("CHAD_LOG_DIR")
        if base_dir:
            self.base_dir = Path(base_dir)
        elif env_dir:
            self.base_dir = Path(env_dir)
        else:
            self.base_dir = Path.home() / ".chad" / "logs"

        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Artifacts directory
        self.artifacts_dir = self.base_dir / "artifacts" / session_id
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Log file path
        self.log_path = self.base_dir / f"{session_id}.jsonl"

        # Seed sequence counter from existing log if present
        if self.log_path.exists():
            try:
                last_line = ""
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            last_line = line
                if last_line:
                    last_event = json.loads(last_line)
                    self._seq = int(last_event.get("seq", 0))
            except Exception:
                # If log is unreadable, fall back to starting at 0
                self._seq = 0

    def _next_seq(self) -> int:
        """Get next sequence number."""
        self._seq += 1
        return self._seq

    def start_turn(self) -> str:
        """Start a new conversation turn, returns turn_id."""
        self._current_turn_id = str(uuid.uuid4())[:8]
        return self._current_turn_id

    def log(self, event: EventBase) -> None:
        """Log an event to the session log."""
        # Set sequence and session info
        event.seq = self._next_seq()
        event.session_id = self.session_id
        if event.turn_id is None:
            event.turn_id = self._current_turn_id

        # Serialize and append
        event_dict = event.to_dict()

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_dict) + "\n")

    def store_artifact(
        self,
        content: bytes | str,
        name: str,
    ) -> ArtifactRef | None:
        """Store content as an artifact if it exceeds threshold.

        Args:
            content: The content to store
            name: Base name for the artifact file

        Returns:
            ArtifactRef if stored as artifact, None if content is small
        """
        if isinstance(content, str):
            content = content.encode("utf-8")

        size = len(content)

        # Truncate if too large
        if size > MAX_ARTIFACT_SIZE:
            content = content[:MAX_ARTIFACT_SIZE] + b"\n[TRUNCATED - exceeded 10MB limit]"
            size = len(content)

        if size < ARTIFACT_SIZE_THRESHOLD:
            return None

        # Calculate hash
        sha256 = hashlib.sha256(content).hexdigest()

        # Store file
        artifact_path = self.artifacts_dir / f"{name}_{sha256[:8]}.txt"
        with open(artifact_path, "wb") as f:
            f.write(content)

        # Return relative path
        rel_path = str(artifact_path.relative_to(self.base_dir))
        return ArtifactRef(path=rel_path, sha256=sha256, size=size)

    def get_events(
        self,
        since_seq: int = 0,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read events from the log.

        Args:
            since_seq: Return events after this sequence number
            event_types: Filter to these event types (None = all)

        Returns:
            List of event dictionaries
        """
        if not self.log_path.exists():
            return []

        events = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("seq", 0) > since_seq:
                        if event_types is None or event.get("type") in event_types:
                            events.append(event)
                except json.JSONDecodeError:
                    continue

        return events

    def get_artifact(self, ref: ArtifactRef | dict[str, Any]) -> bytes | None:
        """Read an artifact's content.

        Args:
            ref: ArtifactRef or dict with path key

        Returns:
            Artifact content as bytes, or None if not found
        """
        if isinstance(ref, dict):
            path = ref.get("path", "")
        else:
            path = ref.path

        artifact_path = self.base_dir / path
        if not artifact_path.exists():
            return None

        with open(artifact_path, "rb") as f:
            return f.read()

    def get_latest_seq(self) -> int:
        """Get the latest sequence number in the log."""
        return self._seq

    def close(self) -> None:
        """Close the event log (cleanup if needed)."""
        pass  # Currently no cleanup needed

    @classmethod
    def get_log_dir(cls, base_dir: Path | None = None) -> Path:
        """Get the log directory path.

        Args:
            base_dir: Override base directory

        Returns:
            Path to the logs directory
        """
        if base_dir is None:
            env_dir = os.environ.get("CHAD_LOG_DIR")
            if env_dir:
                return Path(env_dir)
            else:
                return Path.home() / ".chad" / "logs"
        return base_dir

    @classmethod
    def list_sessions(cls, base_dir: Path | None = None) -> list[str]:
        """List all session IDs with logs.

        Args:
            base_dir: Override base directory

        Returns:
            List of session IDs
        """
        base_dir = cls.get_log_dir(base_dir)

        if not base_dir.exists():
            return []

        sessions = []
        for f in base_dir.glob("*.jsonl"):
            sessions.append(f.stem)

        return sorted(sessions)


def compute_file_sha256(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

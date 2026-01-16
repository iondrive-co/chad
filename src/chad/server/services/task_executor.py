"""Task execution service for orchestrating AI coding tasks."""

import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from chad.util.git_worktree import GitWorktreeManager
from chad.util.prompts import build_coding_prompt
from chad.util.providers import ModelConfig, create_provider, parse_codex_output


class TaskState(str, Enum):
    """Task execution states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class StreamEvent:
    """An event from the task stream."""

    type: str  # stream, activity, status, message_start, message_complete, progress, complete, error
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """Represents a running or completed task."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_id: str = ""
    state: TaskState = TaskState.PENDING
    progress: str | None = None
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested: bool = False

    # Internal
    _thread: threading.Thread | None = field(default=None, repr=False)
    _event_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _provider: Any = field(default=None, repr=False)


class TaskExecutor:
    """Executes coding tasks using AI providers."""

    def __init__(self, config_manager, session_manager):
        self.config_manager = config_manager
        self.session_manager = session_manager
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()

    def start_task(
        self,
        session_id: str,
        project_path: str,
        task_description: str,
        coding_account: str,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> Task:
        """Start a new coding task.

        Args:
            session_id: The session this task belongs to
            project_path: Path to the project directory
            task_description: Description of what to do
            coding_account: Account name to use for coding
            coding_model: Optional model override
            coding_reasoning: Optional reasoning level override
            on_event: Optional callback for streaming events

        Returns:
            The created Task object
        """
        session = self.session_manager.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Validate project path
        path_obj = Path(project_path).expanduser().resolve()
        if not path_obj.exists() or not path_obj.is_dir():
            raise ValueError(f"Invalid project path: {project_path}")

        # Validate account
        accounts = self.config_manager.list_accounts()
        if coding_account not in accounts:
            raise ValueError(f"Account '{coding_account}' not found")

        # Check git repo
        git_mgr = GitWorktreeManager(path_obj)
        if not git_mgr.is_git_repo():
            raise ValueError(f"Project must be a git repository: {project_path}")

        # Create task
        task = Task(session_id=session_id)
        task.started_at = datetime.now(timezone.utc)
        task.state = TaskState.RUNNING

        with self._lock:
            self._tasks[task.id] = task

        # Start execution thread
        thread = threading.Thread(
            target=self._run_task,
            args=(
                task,
                session,
                path_obj,
                git_mgr,
                task_description,
                coding_account,
                accounts[coding_account],
                coding_model,
                coding_reasoning,
                on_event,
            ),
            daemon=True,
        )
        task._thread = thread
        thread.start()

        return task

    def _run_task(
        self,
        task: Task,
        session,
        project_path: Path,
        git_mgr: GitWorktreeManager,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        coding_model: str | None,
        coding_reasoning: str | None,
        on_event: Callable[[StreamEvent], None] | None,
    ):
        """Execute the task in a background thread."""

        def emit(event_type: str, **data):
            event = StreamEvent(type=event_type, data=data)
            task._event_queue.put(event)
            if on_event:
                try:
                    on_event(event)
                except Exception:
                    pass

        try:
            # Create worktree
            emit("status", status="Creating worktree...")
            try:
                worktree_path, base_commit = git_mgr.create_worktree(task.session_id)
                session.worktree_path = worktree_path
                session.worktree_branch = git_mgr._branch_name(task.session_id)
                session.worktree_base_commit = base_commit
                session.project_path = str(project_path)
            except Exception as e:
                emit("error", error=f"Failed to create worktree: {e}")
                task.state = TaskState.FAILED
                task.error = str(e)
                task.completed_at = datetime.now(timezone.utc)
                return

            # Get model config
            model_name = coding_model or self.config_manager.get_account_model(coding_account) or "default"
            reasoning = coding_reasoning or self.config_manager.get_account_reasoning(coding_account) or "default"

            config = ModelConfig(
                provider=coding_provider,
                model_name=model_name,
                account_name=coding_account,
                reasoning_effort=None if reasoning == "default" else reasoning,
            )

            emit("status", status=f"Starting {coding_provider} provider...")

            # Create provider
            provider = create_provider(config)
            task._provider = provider
            session.provider = provider

            # Set activity callback
            def on_activity(activity_type: str, detail: str):
                if activity_type == "stream":
                    emit("stream", chunk=detail)
                elif activity_type == "tool":
                    emit("activity", activity_type="tool", detail=detail)
                elif activity_type == "thinking":
                    emit("activity", activity_type="thinking", detail=detail)

            provider.set_activity_callback(on_activity)

            # Start session
            if not provider.start_session(str(worktree_path), None):
                emit("error", error="Failed to start provider session")
                task.state = TaskState.FAILED
                task.error = "Failed to start provider session"
                task.completed_at = datetime.now(timezone.utc)
                return

            session.active = True
            session.coding_account = coding_account

            emit("status", status="Provider started, processing task...")
            emit("message_start", speaker="CODING AI")

            # Build and send prompt
            full_prompt = build_coding_prompt(task_description, "")
            provider.send_message(full_prompt)

            # Get response
            response = provider.get_response(timeout=900)  # 15 min timeout

            if task.cancel_requested:
                emit("status", status="Task cancelled")
                task.state = TaskState.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
                provider.stop_session()
                session.provider = None
                session.active = False
                return

            if response:
                parsed = parse_codex_output(response)
                emit("message_complete", speaker="CODING AI", content=parsed)
                task.result = parsed
                task.state = TaskState.COMPLETED

                # Check for changes
                session.has_worktree_changes = git_mgr.has_changes(task.session_id)

                emit(
                    "complete",
                    success=True,
                    message="Task completed successfully",
                    has_changes=session.has_worktree_changes,
                )
            else:
                emit("error", error="No response from coding AI")
                task.state = TaskState.FAILED
                task.error = "No response from coding AI"

            task.completed_at = datetime.now(timezone.utc)

            # Keep session alive for follow-ups if supported
            if not provider.supports_multi_turn() or task.state != TaskState.COMPLETED:
                provider.stop_session()
                session.provider = None
                session.active = False

        except Exception as e:
            emit("error", error=str(e))
            task.state = TaskState.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(timezone.utc)

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Request cancellation of a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.state != TaskState.RUNNING:
                return False
            task.cancel_requested = True
            # Try to stop the provider
            if task._provider:
                try:
                    task._provider.stop_session()
                except Exception:
                    pass
            return True

    def get_events(self, task_id: str, timeout: float = 0.1) -> list[StreamEvent]:
        """Get pending events from a task's queue."""
        task = self.get_task(task_id)
        if not task:
            return []

        events = []
        try:
            while True:
                event = task._event_queue.get(timeout=timeout)
                events.append(event)
        except queue.Empty:
            pass
        return events


# Global instance
_task_executor: TaskExecutor | None = None


def get_task_executor() -> TaskExecutor:
    """Get the global TaskExecutor instance."""
    global _task_executor
    if _task_executor is None:
        from chad.server.state import get_config_manager
        from chad.server.services import get_session_manager

        _task_executor = TaskExecutor(
            config_manager=get_config_manager(),
            session_manager=get_session_manager(),
        )
    return _task_executor


def reset_task_executor() -> None:
    """Reset the global TaskExecutor (for testing)."""
    global _task_executor
    _task_executor = None

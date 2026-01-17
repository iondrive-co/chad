"""Task execution service for orchestrating AI coding tasks via PTY."""

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from chad.util.git_worktree import GitWorktreeManager
from chad.util.event_log import (
    EventLog,
    SessionStartedEvent,
    UserMessageEvent,
    TerminalOutputEvent,
    SessionEndedEvent,
)
from chad.server.services.pty_stream import get_pty_stream_service, PTYEvent


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

    # PTY stream ID
    stream_id: str | None = None

    # Event log
    event_log: EventLog | None = None

    # Internal
    _thread: threading.Thread | None = field(default=None, repr=False)
    _event_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _provider: Any = field(default=None, repr=False)


def build_agent_command(
    provider: str,
    account_name: str,
    project_path: Path,
    task_description: str | None = None,
) -> tuple[list[str], dict[str, str], str | None]:
    """Build CLI command and environment for a provider.

    Args:
        provider: Provider type (anthropic, openai, gemini, qwen, mistral, mock)
        account_name: Account name for provider-specific paths
        project_path: Path to the project/worktree
        task_description: Optional task to send as initial input

    Returns:
        Tuple of (command_list, environment_dict, initial_input)
    """
    env: dict[str, str] = {}
    initial_input: str | None = None

    if provider == "anthropic":
        # Claude Code CLI
        config_dir = Path.home() / ".chad" / "claude-configs" / account_name
        cmd = ["claude", "-p", "--permission-mode", "bypassPermissions"]
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        if task_description:
            initial_input = task_description + "\n"

    elif provider == "openai":
        # Codex CLI with isolated home
        codex_home = Path.home() / ".chad" / "codex-homes" / account_name
        cmd = [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(project_path),
        ]
        env["HOME"] = str(codex_home)
        if task_description:
            cmd.extend([task_description])

    elif provider == "gemini":
        # Gemini CLI in YOLO mode
        cmd = ["gemini", "-y"]
        if task_description:
            initial_input = task_description + "\n"

    elif provider == "qwen":
        # Qwen Code CLI
        cmd = ["qwen", "-y"]
        if task_description:
            initial_input = task_description + "\n"

    elif provider == "mistral":
        # Vibe CLI (Mistral)
        cmd = ["vibe"]
        if task_description:
            initial_input = task_description + "\n"

    elif provider == "mock":
        # Mock provider - simulates an agent CLI with ANSI output
        cmd = _build_mock_agent_command(project_path, task_description)

    else:
        # Fallback - try running provider name as command
        cmd = [provider]
        if task_description:
            initial_input = task_description + "\n"

    return cmd, env, initial_input


def _build_mock_agent_command(project_path: Path, task_description: str | None) -> list[str]:
    """Build mock agent command that simulates a real agent CLI."""
    # Python script that outputs ANSI-formatted text like a real agent
    # Uses minimal delays to keep tests fast while still demonstrating ANSI output
    script = f'''
import sys
import os

# ANSI colors
BLUE = "\\033[1;34m"
GREEN = "\\033[32m"
YELLOW = "\\033[33m"
CYAN = "\\033[36m"
GRAY = "\\033[90m"
RESET = "\\033[0m"
BOLD = "\\033[1m"

def writeln(text=""):
    print(text, flush=True)

# Header
writeln(f"{{BLUE}}{{BOLD}}Mock Agent v1.0{{RESET}}")
writeln(f"{{GRAY}}Working in: {project_path}{{RESET}}")
writeln()

# Thinking
writeln(f"{{YELLOW}}> Analyzing task...{{RESET}}")

# Tool call simulation
writeln(f"{{CYAN}}Tool: Read{{RESET}} {{GRAY}}BUGS.md{{RESET}}")
writeln(f"{{GREEN}}✓{{RESET}} Read 15 lines")

writeln(f"{{CYAN}}Tool: Glob{{RESET}} {{GRAY}}src/**/*.py{{RESET}}")
writeln(f"{{GREEN}}✓{{RESET}} Found 8 files")

# Make actual change
writeln()
writeln(f"{{YELLOW}}> Making changes...{{RESET}}")

import datetime
bugs_path = "{project_path}/BUGS.md"
try:
    with open(bugs_path, "a") as f:
        f.write(f"\\n## Mock Task - {{datetime.datetime.now().isoformat()}}\\n")
        f.write("- Fixed simulated bug\\n")
        f.write("- Added mock improvements\\n")
    writeln(f"{{CYAN}}Tool: Edit{{RESET}} {{GRAY}}BUGS.md{{RESET}}")
    writeln(f"{{GREEN}}✓{{RESET}} Modified BUGS.md")
except Exception as e:
    writeln(f"{{YELLOW}}Note:{{RESET}} Could not modify BUGS.md: {{e}}")

# Verification
writeln()
writeln(f"{{YELLOW}}> Running verification...{{RESET}}")
writeln(f"{{GREEN}}✓{{RESET}} All checks passed")

# Summary
writeln()
writeln(f"{{BLUE}}{{BOLD}}Task Complete{{RESET}}")
writeln(f"{{GRAY}}Changes made to BUGS.md{{RESET}}")
'''
    return ["python3", "-c", script]


class TaskExecutor:
    """Executes coding tasks using PTY-based agent CLIs."""

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

        # Create event log
        task.event_log = EventLog(session_id)

        with self._lock:
            self._tasks[task.id] = task

        # Get provider info
        coding_provider = accounts[coding_account]

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
                coding_provider,
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
        """Execute the task in a background thread using PTY."""

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

            worktree_path = Path(worktree_path)

            # Log session start
            if task.event_log:
                task.event_log.log(SessionStartedEvent(
                    task_description=task_description,
                    project_path=str(project_path),
                    coding_provider=coding_provider,
                    coding_account=coding_account,
                    coding_model=coding_model,
                ))
                task.event_log.start_turn()
                task.event_log.log(UserMessageEvent(content=task_description))

            # Build agent command
            emit("status", status=f"Starting {coding_provider} agent...")
            cmd, env, initial_input = build_agent_command(
                coding_provider,
                coding_account,
                worktree_path,
                task_description,
            )

            # Set up logging callback for EventLog persistence
            # This callback is called synchronously for every PTY event
            # and doesn't compete with client subscriber queues
            def log_pty_event(event: PTYEvent):
                if event.type == "output":
                    emit("stream", chunk=event.data)
                    if task.event_log:
                        task.event_log.log(TerminalOutputEvent(
                            data=event.data,
                            has_ansi=event.has_ansi,
                        ))

            # Start PTY session with logging callback
            pty_service = get_pty_stream_service()
            stream_id = pty_service.start_pty_session(
                session_id=task.session_id,
                cmd=cmd,
                cwd=worktree_path,
                env=env,
                log_callback=log_pty_event,
            )
            task.stream_id = stream_id
            session.active = True
            session.coding_account = coding_account

            emit("message_start", speaker="CODING AI")

            # Send initial input if needed (for prompt)
            if initial_input:
                time.sleep(0.2)  # Brief delay for process to start
                pty_service.send_input(stream_id, initial_input.encode())

            # Wait for PTY to complete
            # The logging callback handles emitting events and persisting to EventLog
            pty_session = pty_service.get_session(stream_id)

            while pty_session and pty_session.active:
                # Check for cancellation
                if task.cancel_requested:
                    pty_service.terminate(stream_id)
                    emit("status", status="Task cancelled")
                    task.state = TaskState.CANCELLED
                    task.completed_at = datetime.now(timezone.utc)

                    if task.event_log:
                        task.event_log.log(SessionEndedEvent(
                            success=False,
                            reason="cancelled",
                        ))
                    return

                time.sleep(0.1)
                pty_session = pty_service.get_session(stream_id)

            # Get final exit code
            exit_code = 0
            if pty_session:
                exit_code = pty_session.exit_code or 0

            # Emit completion
            emit("message_complete", speaker="CODING AI", content="Task completed")

            if exit_code == 0:
                task.state = TaskState.COMPLETED
                task.result = "Task completed successfully"

                # Check for changes
                session.has_worktree_changes = git_mgr.has_changes(task.session_id)

                emit(
                    "complete",
                    success=True,
                    message="Task completed successfully",
                    has_changes=session.has_worktree_changes,
                    exit_code=exit_code,
                )
            else:
                task.state = TaskState.FAILED
                task.error = f"Agent exited with code {exit_code}"
                emit(
                    "complete",
                    success=False,
                    message=f"Agent exited with code {exit_code}",
                    exit_code=exit_code,
                )

            task.completed_at = datetime.now(timezone.utc)
            session.active = False

            # Log session end
            if task.event_log:
                task.event_log.log(SessionEndedEvent(
                    success=task.state == TaskState.COMPLETED,
                    reason="completed" if exit_code == 0 else f"exit_code_{exit_code}",
                ))

            # Cleanup PTY session
            pty_service.cleanup_session(stream_id)

        except Exception as e:
            emit("error", error=str(e))
            task.state = TaskState.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(timezone.utc)

            if task.event_log:
                task.event_log.log(SessionEndedEvent(
                    success=False,
                    reason=f"error: {e}",
                ))

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

            # Terminate PTY if active
            if task.stream_id:
                pty_service = get_pty_stream_service()
                pty_service.terminate(task.stream_id)

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

"""Task execution service for orchestrating AI coding tasks via PTY."""

import base64
import json
import os
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
from chad.util.prompts import build_coding_prompt
from chad.util.installer import AIToolInstaller
from chad.server.services.pty_stream import get_pty_stream_service, PTYEvent
from chad.ui.terminal_emulator import TERMINAL_COLS, TERMINAL_ROWS, TerminalEmulator


_CLI_INSTALLER = AIToolInstaller()


class ClaudeStreamJsonParser:
    """Parses stream-json output from Claude Code and Qwen CLI.

    Supports multiple JSON formats:

    Claude format:
    - system/init: Session initialization (ignored for display)
    - assistant: Contains message.content array with text and tool_use items
    - result: Final result summary

    Qwen format (Gemini CLI fork):
    - system: Session initialization with session_id
    - message: Contains role ("assistant") and content (string)

    This parser buffers incoming bytes, extracts complete JSON lines,
    and yields human-readable text suitable for terminal display.

    Tool calls are accumulated and displayed as a collapsed summary rather than
    individual verbose descriptions, keeping the live view focused on AI reasoning.
    """

    def __init__(self):
        self._buffer = bytearray()
        # Tool tracking for collapsed summaries
        self._tool_counts: dict[str, int] = {}
        self._tool_details: list[str] = []
        self._pending_summary = False  # True when we have tools to summarize

    def feed(self, data: bytes) -> list[str]:
        """Feed raw bytes and return list of human-readable text chunks.

        Args:
            data: Raw bytes from PTY output

        Returns:
            List of text strings to display (may be empty if no complete lines)
        """
        self._buffer.extend(data)
        results = []

        # Process complete lines (JSON objects are newline-delimited)
        while b"\n" in self._buffer:
            line_end = self._buffer.index(b"\n")
            line = self._buffer[:line_end]
            self._buffer = self._buffer[line_end + 1:]

            if not line.strip():
                continue

            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
                text = self._format_json_event(obj)
                if text:
                    # Prepend accumulated tool summary before text content
                    if self._pending_summary:
                        summary = self.get_tool_summary()
                        if summary:
                            results.append(summary)
                        self.clear_tool_tracking()
                    results.append(text)
            except json.JSONDecodeError:
                # Not JSON, pass through as-is
                results.append(line.decode("utf-8", errors="replace"))

        return results

    def _format_json_event(self, obj: dict) -> str | None:
        """Convert a stream-json event to human-readable text.

        Args:
            obj: Parsed JSON object

        Returns:
            Human-readable text or None if event should be hidden
        """
        event_type = obj.get("type", "")

        if event_type == "system":
            # System init - don't display raw, just note session started
            subtype = obj.get("subtype", "")
            if subtype == "init":
                return None  # Skip init, already shown in UI
            return None

        elif event_type == "assistant":
            # Extract content from assistant message
            message = obj.get("message", {})
            content = message.get("content", [])
            parts = []

            for item in content:
                item_type = item.get("type", "")

                if item_type == "text":
                    text = item.get("text", "")
                    if text:
                        parts.append(text)

                elif item_type == "tool_use":
                    tool_name = item.get("name", "unknown")
                    tool_input = item.get("input", {})
                    # Accumulate tool for collapsed summary instead of showing each one
                    self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1
                    tool_desc = self._format_tool_use(tool_name, tool_input)
                    if tool_desc:
                        self._tool_details.append(tool_desc)
                    self._pending_summary = True
                    # Don't add to parts - tools are shown as collapsed summary

            return "\n".join(parts) if parts else None

        elif event_type == "result":
            # Final result - could show summary but usually redundant
            return None

        elif event_type == "message":
            # Qwen/Gemini CLI format: {type: "message", role: "assistant", content: "..."}
            role = obj.get("role", "")
            if role == "assistant":
                content = obj.get("content", "")
                if content:
                    return content
            return None

        # Unknown event type - skip
        return None

    def _format_tool_use(self, name: str, input_data: dict) -> str:
        """Format a tool_use event for display.

        Args:
            name: Tool name
            input_data: Tool input parameters

        Returns:
            Formatted string describing the tool use
        """
        if name == "Read":
            path = input_data.get("file_path", "")
            return f"• Reading {path}"

        elif name == "Write":
            path = input_data.get("file_path", "")
            return f"• Writing {path}"

        elif name == "Edit":
            path = input_data.get("file_path", "")
            return f"• Editing {path}"

        elif name == "Bash":
            cmd = input_data.get("command", "")
            # Truncate long commands
            if len(cmd) > 80:
                cmd = cmd[:77] + "..."
            return f"• Running: {cmd}"

        elif name == "Glob":
            pattern = input_data.get("pattern", "")
            return f"• Searching for {pattern}"

        elif name == "Grep":
            pattern = input_data.get("pattern", "")
            return f"• Grep: {pattern}"

        elif name == "Task":
            desc = input_data.get("description", "")
            return f"• Task: {desc}"

        elif name == "WebSearch":
            query = input_data.get("query", "")
            return f"• Web search: {query}"

        elif name == "WebFetch":
            url = input_data.get("url", "")
            return f"• Fetching: {url}"

        else:
            # Generic tool display
            return f"• {name}"

    def get_tool_summary(self) -> str:
        """Return collapsed summary of accumulated tool calls.

        Returns a single line like "• 3 files read, 2 edits, 5 searches"
        instead of showing every individual tool call.
        """
        if not self._tool_counts:
            return ""

        parts = []

        # File reads
        read_count = self._tool_counts.get("Read", 0)
        if read_count:
            parts.append(f"{read_count} file{'s' if read_count > 1 else ''} read")

        # Edits (Edit + Write combined)
        edit_count = self._tool_counts.get("Edit", 0) + self._tool_counts.get("Write", 0)
        if edit_count:
            parts.append(f"{edit_count} edit{'s' if edit_count > 1 else ''}")

        # Searches (Glob + Grep combined)
        search_count = self._tool_counts.get("Glob", 0) + self._tool_counts.get("Grep", 0)
        if search_count:
            parts.append(f"{search_count} search{'es' if search_count > 1 else ''}")

        # Commands
        bash_count = self._tool_counts.get("Bash", 0)
        if bash_count:
            parts.append(f"{bash_count} command{'s' if bash_count > 1 else ''}")

        # Tasks (subagent spawns)
        task_count = self._tool_counts.get("Task", 0)
        if task_count:
            parts.append(f"{task_count} task{'s' if task_count > 1 else ''}")

        # Web operations
        web_count = self._tool_counts.get("WebSearch", 0) + self._tool_counts.get("WebFetch", 0)
        if web_count:
            parts.append(f"{web_count} web request{'s' if web_count > 1 else ''}")

        # Other tools not in categories above
        categorized = {"Read", "Edit", "Write", "Glob", "Grep", "Bash", "Task", "WebSearch", "WebFetch"}
        other = sum(c for t, c in self._tool_counts.items() if t not in categorized)
        if other:
            parts.append(f"{other} other")

        if not parts:
            return ""

        return f"• {', '.join(parts)}"

    def get_tool_details(self) -> list[str]:
        """Return full tool call details for expandable view."""
        return self._tool_details.copy()

    def has_pending_tools(self) -> bool:
        """Check if there are tool calls pending to be summarized."""
        return self._pending_summary

    def clear_tool_tracking(self):
        """Reset tool tracking after summary has been rendered."""
        self._tool_counts = {}
        self._tool_details = []
        self._pending_summary = False


def _read_project_docs(project_path: Path) -> str | None:
    """Read project documentation if present.

    Returns a reference block pointing to on-disk docs instead of inlining
    their contents.
    """
    from chad.util.project_setup import build_doc_reference_text

    return build_doc_reference_text(project_path)


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
    screenshots: list[str] | None = None,
) -> tuple[list[str], dict[str, str], str | None]:
    """Build CLI command and environment for a provider.

    Args:
        provider: Provider type (anthropic, openai, gemini, qwen, mistral, mock)
        account_name: Account name for provider-specific paths
        project_path: Path to the project/worktree
        task_description: Optional task to send as initial input
        screenshots: Optional list of screenshot file paths for agent reference

    Returns:
        Tuple of (command_list, environment_dict, initial_input)
    """
    env: dict[str, str] = {}

    # Ensure Chad-installed CLIs are on PATH so we can find provider binaries
    extra_paths = [
        _CLI_INSTALLER.bin_dir,
        _CLI_INSTALLER.tools_dir / "node_modules" / ".bin",
    ]
    existing_path = os.environ.get("PATH", "")
    prepend = [str(p) for p in extra_paths if p.exists()]
    if prepend:
        env["PATH"] = os.pathsep.join(prepend + [existing_path])

    def resolve_tool(binary: str) -> str:
        resolved = _CLI_INSTALLER.resolve_tool_path(binary)
        return str(resolved) if resolved else binary

    initial_input: str | None = None

    # Build full prompt with project docs and instructions (including progress update format)
    full_prompt: str | None = None
    if task_description:
        project_docs = _read_project_docs(project_path)
        full_prompt = build_coding_prompt(task_description, project_docs, project_path, screenshots)

    if provider == "anthropic":
        # Claude Code CLI
        config_dir = Path.home() / ".chad" / "claude-configs" / account_name
        cmd = [
            resolve_tool("claude"),
            "-p",  # non-interactive print mode
            "--verbose",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
        ]
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        # Provide prompt as positional argument (required with -p when stdin is a TTY)
        if full_prompt:
            cmd.append(full_prompt)
            initial_input = None

    elif provider == "openai":
        # Codex CLI with isolated home
        codex_home = Path.home() / ".chad" / "codex-homes" / account_name
        cmd = [
            resolve_tool("codex"),
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(project_path),
        ]
        env["HOME"] = str(codex_home)
        if full_prompt:
            cmd.extend([full_prompt])

    elif provider == "gemini":
        # Gemini CLI in YOLO mode
        cmd = [resolve_tool("gemini"), "-y"]
        if full_prompt:
            initial_input = full_prompt + "\n"

    elif provider == "qwen":
        # Qwen Code CLI - pass prompt directly to -p to trigger non-interactive mode
        # Using stdin doesn't work reliably because qwen checks stdin at startup
        # before we can send data via PTY. With subprocess.Popen(shell=False),
        # there's no shell escaping issues, just OS argv limits (~128KB on Linux).
        cmd = [resolve_tool("qwen"), "-y", "--output-format", "stream-json"]
        if full_prompt:
            cmd.extend(["-p", full_prompt])

    elif provider == "mistral":
        # Vibe CLI (Mistral)
        cmd = [resolve_tool("vibe")]
        if full_prompt:
            initial_input = full_prompt + "\n"

    elif provider == "mock":
        # Mock provider - simulates an agent CLI with ANSI output
        cmd = _build_mock_agent_command(project_path, task_description)

    else:
        # Fallback - try running provider name as command
        cmd = [provider]
        if full_prompt:
            initial_input = full_prompt + "\n"

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

    def __init__(
        self,
        config_manager,
        session_manager,
        inactivity_timeout: float | None = 900.0,
        terminal_flush_interval: float = 0.5,
    ):
        self.config_manager = config_manager
        self.session_manager = session_manager
        self.inactivity_timeout = inactivity_timeout
        self.terminal_flush_interval = terminal_flush_interval
        self._tasks: dict[str, Task] = {}
        # Track activity across all channels (PTY output AND tool calls) so timeouts
        # don't ignore heavy Read/Grep usage with no terminal writes.
        self._activity_times: dict[str, float] = {}
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
        terminal_rows: int | None = None,
        terminal_cols: int | None = None,
        screenshots: list[str] | None = None,
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
            terminal_rows: Optional terminal height (default: TERMINAL_ROWS)
            terminal_cols: Optional terminal width (default: TERMINAL_COLS)
            screenshots: Optional list of screenshot file paths for agent reference

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

        now = time.time()
        with self._lock:
            self._tasks[task.id] = task
            self._activity_times[task.id] = now

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
                terminal_rows,
                terminal_cols,
                screenshots,
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
        terminal_rows: int | None,
        terminal_cols: int | None,
        screenshots: list[str] | None,
    ):
        """Execute the task in a background thread using PTY."""
        # Use provided dimensions or fall back to defaults
        rows = terminal_rows if terminal_rows else TERMINAL_ROWS
        cols = terminal_cols if terminal_cols else TERMINAL_COLS

        last_output_time = time.time()
        last_warning_time = 0.0
        terminal_buffer = bytearray()
        terminal_lock = threading.Lock()
        last_log_flush = time.time()

        # Terminal emulator for extracting meaningful text from PTY output
        log_emulator = TerminalEmulator(cols=cols, rows=rows)
        last_logged_text = ""
        stream_id: str | None = None
        pty_service = get_pty_stream_service()

        def flush_terminal_buffer():
            nonlocal last_log_flush, last_logged_text
            with terminal_lock:
                if not terminal_buffer:
                    return
                data_bytes = bytes(terminal_buffer)
                terminal_buffer.clear()

            # Feed data to terminal emulator and extract visible text
            log_emulator.feed(data_bytes)
            current_text = log_emulator.get_text()

            # Only log if there's meaningful new content
            # Compare with last logged text to avoid logging cursor movement / redraws
            if current_text != last_logged_text and current_text.strip():
                # Log the current screen content for handoff
                if task.event_log:
                    task.event_log.log(TerminalOutputEvent(data=current_text))
                last_logged_text = current_text
            last_log_flush = time.time()

        def emit(event_type: str, **data):
            event = StreamEvent(type=event_type, data=data)
            task._event_queue.put(event)
            # Count any emitted event as activity (covers status/progress when no PTY output yet)
            try:
                self._touch_activity(task.id)
            except Exception:
                pass
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
            if screenshots:
                cmd, env, initial_input = build_agent_command(
                    coding_provider,
                    coding_account,
                    worktree_path,
                    task_description,
                    screenshots,
                )
            else:
                cmd, env, initial_input = build_agent_command(
                    coding_provider,
                    coding_account,
                    worktree_path,
                    task_description,
                )

            # Create JSON parser for providers that use stream-json output
            # Both Claude and Qwen use similar JSON formats
            json_parser = ClaudeStreamJsonParser() if coding_provider in ("anthropic", "qwen") else None

            # Set up logging callback for EventLog persistence
            # This callback is called synchronously for every PTY event
            # and doesn't compete with client subscriber queues
            def log_pty_event(event: PTYEvent):
                nonlocal last_output_time
                if event.type == "output":
                    last_output_time = time.time()
                    with self._lock:
                        self._activity_times[task.id] = last_output_time

                    try:
                        chunk_bytes = base64.b64decode(event.data)
                    except Exception:
                        chunk_bytes = b""

                    # For anthropic, parse stream-json and convert to readable text
                    if json_parser:
                        text_chunks = json_parser.feed(chunk_bytes)
                        if text_chunks:
                            # Join parsed text and encode for streaming
                            readable_text = "\n".join(text_chunks)
                            encoded = base64.b64encode(readable_text.encode()).decode()
                            emit("stream", chunk=encoded)
                            with terminal_lock:
                                terminal_buffer.extend(readable_text.encode())
                        # If no complete lines yet, don't emit anything
                    else:
                        # Non-anthropic: pass through raw PTY output
                        emit("stream", chunk=event.data)
                        with terminal_lock:
                            terminal_buffer.extend(chunk_bytes)

            # Start PTY session with logging callback
            # Use the same geometry as the terminal emulator for consistent rendering
            stream_id = pty_service.start_pty_session(
                session_id=task.session_id,
                cmd=cmd,
                cwd=worktree_path,
                env=env,
                rows=rows,
                cols=cols,
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

                # Inactivity timeout to catch hung agents
                if self.inactivity_timeout is not None:
                    now = time.time()
                    last_any_activity = last_output_time
                    with self._lock:
                        last_any_activity = self._activity_times.get(task.id, last_output_time)

                    idle_secs = now - last_any_activity
                    warn_after = self.inactivity_timeout * 0.8
                    if idle_secs > warn_after and (now - last_warning_time) > 5.0:
                        emit("status", status=f"⚠️ Agent idle for {int(idle_secs)}s — will time out at {int(self.inactivity_timeout)}s")
                        last_warning_time = now

                    if idle_secs > self.inactivity_timeout:
                        flush_terminal_buffer()
                        pty_service.terminate(stream_id)
                        emit(
                            "complete",
                            success=False,
                            message="Agent timed out due to inactivity",
                            exit_code=-1,
                        )
                        task.state = TaskState.FAILED
                        task.error = "Agent timed out due to inactivity"
                        task.completed_at = datetime.now(timezone.utc)
                        session.active = False
                        session.has_worktree_changes = git_mgr.has_changes(task.session_id)

                        if task.event_log:
                            task.event_log.log(SessionEndedEvent(
                                success=False,
                                reason="timeout",
                            ))

                        # Also emit a message_complete so UI closes the live slot
                        emit("message_complete", speaker="CODING AI", content="Task timed out due to inactivity")

                        pty_service.cleanup_session(stream_id)
                        return

                time.sleep(0.1)
                pty_session = pty_service.get_session(stream_id)
                # Note: We no longer log terminal output periodically during the session.
                # The terminal is still streamed to clients via PTY events in real-time.
                # For handoff, we only log the final terminal state at session end,
                # which dramatically reduces log size while still supporting handoff.
                # The terminal buffer is still being filled by log_pty_event callback.

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

                # Ensure UI gets a final assistant bubble when we fail without one
                if exit_code != -1:
                    emit("message_complete", speaker="CODING AI", content=f"Task failed (exit {exit_code})")

            task.completed_at = datetime.now(timezone.utc)
            session.active = False

            # Log session end
            if task.event_log:
                flush_terminal_buffer()
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
        finally:
            try:
                flush_terminal_buffer()
            except Exception:
                pass
            try:
                if task.stream_id:
                    pty_service.cleanup_session(task.stream_id)
            except Exception:
                pass
            # Drop activity tracker entry to avoid stale references
            with self._lock:
                self._activity_times.pop(task.id, None)

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def _touch_activity(self, task_id: str) -> None:
        """Update last activity timestamp for a task (tool calls, etc.)."""
        now = time.time()
        with self._lock:
            self._activity_times[task_id] = now

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

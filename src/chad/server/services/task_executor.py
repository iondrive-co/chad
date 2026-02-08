"""Task execution service for orchestrating AI coding tasks via PTY."""

import base64
import json
import os
import queue
import re
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
    StatusEvent,
    UserMessageEvent,
    TerminalOutputEvent,
    SessionEndedEvent,
)
from chad.util.prompts import (
    build_exploration_prompt,
    build_implementation_prompt,
    extract_coding_summary,
    extract_progress_update,
    get_continuation_prompt,
)
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
        other_tools = [(t, c) for t, c in self._tool_counts.items() if t not in categorized]
        if other_tools:
            # Show actual tool names instead of generic "X other"
            other_descriptions = []
            for tool_name, count in other_tools:
                if count == 1:
                    other_descriptions.append(tool_name)
                else:
                    other_descriptions.append(f"{count} {tool_name}")
            parts.append(", ".join(other_descriptions))

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
    _last_terminal_snapshot: str = field(default="", repr=False)
    _mock_duration_applied: bool = field(default=False, repr=False)


_BINARY_GARBAGE_RE = re.compile(r'[@#%*&^]{10,}')


def _strip_binary_garbage(text: str) -> str:
    """Remove runs of binary-like garbage characters from terminal output.

    Codex can emit sixel/image data that renders as @@@###%%% runs.
    """
    return _BINARY_GARBAGE_RE.sub('', text)


def build_agent_command(
    provider: str,
    account_name: str,
    project_path: Path,
    task_description: str | None = None,
    screenshots: list[str] | None = None,
    phase: str = "exploration",
    exploration_output: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    mock_run_duration_seconds: int = 0,
    override_prompt: str | None = None,
) -> tuple[list[str], dict[str, str], str | None]:
    """Build CLI command and environment for a provider.

    Args:
        provider: Provider type (anthropic, openai, gemini, qwen, mistral, mock)
        account_name: Account name for provider-specific paths
        project_path: Path to the project/worktree
        task_description: Optional task to send as initial input
        screenshots: Optional list of screenshot file paths for agent reference
        phase: Execution phase - "exploration", "implementation", or "continuation"
        exploration_output: For implementation/continuation phase, the previous output context
        model: Optional model name override (e.g., 'gpt-5.3-codex', 'claude-opus-4-6')
        reasoning_effort: Optional reasoning effort level (e.g., 'low', 'medium', 'high')
        mock_run_duration_seconds: Mock-only run duration in seconds for handover testing

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

    # Build full prompt based on phase (use override if user edited the prompt)
    full_prompt: str | None = None
    if override_prompt:
        full_prompt = override_prompt
        # Replace remaining placeholders in user-edited prompts
        if phase == "implementation" and exploration_output:
            full_prompt = full_prompt.replace("{exploration_output}", exploration_output)
    elif task_description:
        project_docs = _read_project_docs(project_path)
        if phase == "exploration":
            # Phase 1: Explore codebase and output progress JSON
            full_prompt = build_exploration_prompt(
                task_description, project_docs, project_path, screenshots
            )
        elif phase == "implementation":
            # Phase 2: Continue with implementation using exploration output
            full_prompt = build_implementation_prompt(
                task_description, exploration_output or "", project_docs, project_path
            )
        elif phase == "continuation":
            # Agent exited early without completion - send continuation prompt
            full_prompt = get_continuation_prompt(exploration_output or "")

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
        if model and model != "default":
            cmd.extend(["--model", model])
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        # Provide prompt as positional argument (required with -p when stdin is a TTY)
        if full_prompt:
            cmd.append(full_prompt)
            initial_input = None

    elif provider == "openai":
        # Codex CLI with isolated home - use exec mode for non-interactive execution
        # This prevents the agent from stopping after outputting text and waiting for input
        codex_home = Path.home() / ".chad" / "codex-homes" / account_name
        cmd = [
            resolve_tool("codex"),
            "exec",  # Non-interactive mode - runs to completion
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",  # Avoid git validation issues in worktrees
            "-C",
            str(project_path),
            "-",  # Read prompt from stdin
        ]
        if model and model != "default":
            cmd.extend(["-m", model])
        if reasoning_effort and reasoning_effort != "default":
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        env["HOME"] = str(codex_home)
        if full_prompt:
            initial_input = full_prompt + "\n"

    elif provider == "gemini":
        # Gemini CLI in YOLO mode
        cmd = [resolve_tool("gemini"), "-y"]
        if model and model != "default":
            cmd.extend(["-m", model])
        if full_prompt:
            initial_input = full_prompt + "\n"

    elif provider == "qwen":
        # Qwen Code CLI - pass prompt directly to -p to trigger non-interactive mode
        # Using stdin doesn't work reliably because qwen checks stdin at startup
        # before we can send data via PTY. With subprocess.Popen(shell=False),
        # there's no shell escaping issues, just OS argv limits (~128KB on Linux).
        cmd = [resolve_tool("qwen"), "-y", "--output-format", "stream-json"]
        if model and model != "default":
            cmd.extend(["-m", model])
        if full_prompt:
            cmd.extend(["-p", full_prompt])

    elif provider == "mistral":
        # Vibe CLI (Mistral)
        cmd = [resolve_tool("vibe")]
        if model and model != "default":
            cmd.extend(["--model", model])
        if full_prompt:
            initial_input = full_prompt + "\n"

    elif provider == "mock":
        # Mock provider - simulates an agent CLI with ANSI output
        cmd = _build_mock_agent_command(
            project_path,
            task_description,
            phase=phase,
            run_duration_seconds=mock_run_duration_seconds,
        )

    else:
        # Fallback - try running provider name as command
        cmd = [provider]
        if full_prompt:
            initial_input = full_prompt + "\n"

    return cmd, env, initial_input


def _build_mock_agent_command(
    project_path: Path,
    task_description: str | None,
    phase: str = "exploration",
    run_duration_seconds: int = 0,
) -> list[str]:
    """Build mock agent command that simulates a real agent CLI."""
    duration = max(0, int(run_duration_seconds or 0))
    # Python script that outputs ANSI-formatted text like a real agent
    # Uses minimal delays to keep tests fast while still demonstrating ANSI output
    script = f'''
import sys
import os
import time
import random

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
writeln(f"{{CYAN}}Prompt: {phase.capitalize()}{{RESET}}")
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

# Optional long-running stream simulation for handover tests
run_duration_seconds = {duration}
if run_duration_seconds > 0:
    writeln()
    writeln(f"{{YELLOW}}> Streaming simulated work for {{run_duration_seconds}}s...{{RESET}}")
    words = [
        "handover", "context", "quota", "switch", "token", "window",
        "stream", "analysis", "progress", "checkpoint", "fallback", "provider"
    ]
    end_time = time.time() + run_duration_seconds
    tick = 0
    while time.time() < end_time:
        tick += 1
        sample = " ".join(random.choice(words) for _ in range(8))
        writeln(f"{{GRAY}}[tick {{tick:03d}}] {{sample}}{{RESET}}")
        time.sleep(1)

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

    def _check_provider_threshold(
        self,
        coding_account: str,
        coding_provider: str,
        emit: Callable,
    ) -> tuple[str, str, str | None]:
        """Check usage/context thresholds and switch provider if needed.

        Returns:
            (account, provider, switched_from) - switched_from is set if a switch happened.
        """
        try:
            # Check usage threshold
            usage_threshold = self.config_manager.get_usage_switch_threshold()
            if usage_threshold < 100:
                remaining = self.config_manager.get_mock_remaining_usage(coding_account)
                used_pct = (1.0 - remaining) * 100
                if used_pct >= usage_threshold:
                    next_account = self.config_manager.get_next_fallback_provider(coding_account)
                    if next_account:
                        accounts = self.config_manager.list_accounts()
                        next_info = accounts.get(next_account)
                        if next_info:
                            next_provider = next_info.get("provider", coding_provider)
                            emit("status", status=f"Switching from {coding_account} to {next_account} (usage threshold)")
                            return next_account, next_provider, coding_account

        except Exception:
            pass  # Don't fail the task if threshold checking fails

        return coding_account, coding_provider, None

    def _decrement_mock_usage(self, coding_provider: str, coding_account: str) -> None:
        """Decrement mock usage after a successful PTY phase completion."""
        if coding_provider != "mock":
            return
        try:
            remaining = self.config_manager.get_mock_remaining_usage(coding_account)
            new_remaining = max(0.0, remaining - 0.01)
            self.config_manager.set_mock_remaining_usage(coding_account, new_remaining)
        except Exception:
            pass

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
        override_exploration_prompt: str | None = None,
        override_implementation_prompt: str | None = None,
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
                override_exploration_prompt,
                override_implementation_prompt,
            ),
            daemon=True,
        )
        task._thread = thread
        thread.start()

        return task

    def _run_phase(
        self,
        task: Task,
        session,
        worktree_path: Path,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        screenshots: list[str] | None,
        phase: str,
        exploration_output: str | None,
        rows: int,
        cols: int,
        emit: Callable,
        git_mgr: GitWorktreeManager,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        override_prompt: str | None = None,
    ) -> tuple[int, str]:
        """Execute a single phase of the task.

        Args:
            task: The task being executed
            session: The session object
            worktree_path: Path to the worktree
            task_description: The task description
            coding_account: Account name
            coding_provider: Provider type
            screenshots: Optional screenshot paths
            phase: "exploration", "implementation", or "combined"
            exploration_output: Output from exploration (for implementation phase)
            rows: Terminal rows
            cols: Terminal cols
            emit: Event emitter function
            git_mgr: Git worktree manager

        Returns:
            Tuple of (exit_code, captured_output)
        """
        last_output_time = time.time()
        last_warning_time = 0.0
        last_log_flush = time.time()
        terminal_buffer = bytearray()
        terminal_lock = threading.Lock()
        first_stream_chunk_seen = False
        captured_output: list[str] = []

        # For Codex, track prompt echo filtering state
        # Codex echoes the stdin prompt in format: "-------- user" + prompt + "mcp startup:"
        # We keep content BEFORE "-------- user", filter BETWEEN that and "mcp startup:",
        # and show content AFTER "mcp startup:"
        codex_in_prompt_echo = False  # True when we're in the echoed prompt section
        codex_past_prompt_echo = False  # True when we've seen "mcp startup:" and are done
        codex_output_buffer = ""  # Buffer to detect markers

        # Terminal emulator for extracting meaningful text from PTY output
        log_emulator = TerminalEmulator(cols=cols, rows=rows)
        # Persist dedupe baseline across phases to avoid duplicate terminal_output
        # rows when a new phase starts with an unchanged screen.
        last_logged_text = task._last_terminal_snapshot
        pty_service = get_pty_stream_service()

        def flush_terminal_buffer():
            nonlocal last_logged_text, last_log_flush
            with terminal_lock:
                if not terminal_buffer:
                    last_log_flush = time.time()
                    return
                data_bytes = bytes(terminal_buffer)
                terminal_buffer.clear()

            # Feed data to terminal emulator and extract visible text
            log_emulator.feed(data_bytes)
            current_text = log_emulator.get_text()

            # Only log if there's meaningful new content
            if current_text != last_logged_text and current_text.strip():
                if task.event_log:
                    task.event_log.log(TerminalOutputEvent(data=current_text))
                last_logged_text = current_text
                task._last_terminal_snapshot = current_text
            last_log_flush = time.time()

        # Build agent command for this phase
        mock_run_duration_seconds = 0
        if coding_provider == "mock" and not task._mock_duration_applied:
            try:
                mock_run_duration_seconds = self.config_manager.get_mock_run_duration_seconds(coding_account)
            except Exception:
                mock_run_duration_seconds = 0
            task._mock_duration_applied = mock_run_duration_seconds > 0

        cmd, env, initial_input = build_agent_command(
            coding_provider,
            coding_account,
            worktree_path,
            task_description,
            screenshots,
            phase=phase,
            exploration_output=exploration_output,
            model=coding_model,
            reasoning_effort=coding_reasoning,
            mock_run_duration_seconds=mock_run_duration_seconds,
            override_prompt=override_prompt,
        )

        # Use stdin pipe for Codex to avoid prompt echo in output
        use_stdin_pipe = coding_provider == "openai"

        # Create JSON parser for providers that use stream-json output
        json_parser = ClaudeStreamJsonParser() if coding_provider in ("anthropic", "qwen") else None

        def log_pty_event(event: PTYEvent):
            nonlocal last_output_time
            nonlocal first_stream_chunk_seen
            nonlocal codex_in_prompt_echo
            nonlocal codex_past_prompt_echo
            nonlocal codex_output_buffer
            if event.type == "output":
                last_output_time = time.time()
                with self._lock:
                    self._activity_times[task.id] = last_output_time

                try:
                    chunk_bytes = base64.b64decode(event.data)
                except Exception:
                    chunk_bytes = b""

                # Suppress the provider launch banner
                if not first_stream_chunk_seen:
                    first_stream_chunk_seen = True
                    decoded = chunk_bytes.decode(errors="ignore")
                    if "OpenAI Codex" in decoded or ("model:" in decoded and "directory:" in decoded):
                        return

                # For anthropic/qwen, parse stream-json and convert to readable text
                if json_parser:
                    text_chunks = json_parser.feed(chunk_bytes)
                    if text_chunks:
                        readable_text = "\n".join(text_chunks)
                        encoded = base64.b64encode(readable_text.encode()).decode()
                        emit("stream", chunk=encoded)
                        with terminal_lock:
                            terminal_buffer.extend(readable_text.encode())
                        captured_output.append(readable_text)
                else:
                    # Non-anthropic (Codex): filter out prompt echo
                    # Codex output structure:
                    #   [banner]
                    #   --------
                    #   [header info]
                    #   --------
                    #   user  (or [36muser[0m with ANSI)
                    #   [prompt - FILTER THIS]
                    #   mcp startup: ...
                    #   [agent work - KEEP THIS]
                    decoded = chunk_bytes.decode(errors="replace")

                    if coding_provider == "openai" and not codex_past_prompt_echo:
                        # Buffer output to detect markers
                        codex_output_buffer += decoded

                        # Normalize line endings for matching
                        normalized = codex_output_buffer.replace("\r\n", "\n").replace("\r", "\n")

                        # Strip ANSI codes for pattern matching
                        ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
                        stripped = ansi_pattern.sub('', normalized)

                        # Look for "user" on its own line (after second --------)
                        user_line_match = re.search(r'\n--------\nuser\n', stripped)

                        # Check if we're entering the prompt echo section
                        if not codex_in_prompt_echo and user_line_match:
                            # Found start of prompt echo - emit content before it
                            user_pattern = re.compile(r'\n--------\n(?:\x1b\[[0-9;]*m)*user(?:\x1b\[[0-9;]*m)*\n')
                            match = user_pattern.search(normalized)
                            if match:
                                pre_echo = normalized[:match.start()]
                                if pre_echo.strip():
                                    encoded = base64.b64encode(pre_echo.encode()).decode()
                                    emit("stream", chunk=encoded)
                                    with terminal_lock:
                                        terminal_buffer.extend(pre_echo.encode())
                                    captured_output.append(pre_echo)
                                # Now in prompt echo section - update buffer
                                codex_in_prompt_echo = True
                                codex_output_buffer = normalized[match.end():]
                                normalized = codex_output_buffer
                                stripped = ansi_pattern.sub('', normalized)

                        # Check if we've passed the prompt echo section
                        if codex_in_prompt_echo and "mcp startup:" in stripped.lower():
                            # Found end of prompt echo - extract agent output after marker
                            mcp_pattern = re.compile(r'(?:\x1b\[[0-9;]*m)*mcp startup:(?:\x1b\[[0-9;]*m)*[^\n]*\n', re.IGNORECASE)
                            match = mcp_pattern.search(normalized)
                            if match:
                                agent_output = normalized[match.end():]
                            else:
                                # Fallback
                                marker_pos = stripped.lower().find("mcp startup:")
                                newline_after = stripped.find("\n", marker_pos)
                                if newline_after != -1:
                                    agent_output = normalized[newline_after + 1:]
                                else:
                                    agent_output = ""
                            codex_past_prompt_echo = True
                            codex_output_buffer = ""
                            agent_output = _strip_binary_garbage(agent_output)
                            if agent_output.strip():
                                encoded = base64.b64encode(agent_output.encode()).decode()
                                emit("stream", chunk=encoded)
                                with terminal_lock:
                                    terminal_buffer.extend(agent_output.encode())
                                captured_output.append(agent_output)
                            return

                        # If not in prompt echo yet, emit normally (content before markers)
                        if not codex_in_prompt_echo:
                            # Keep buffering to catch the marker
                            if len(codex_output_buffer) > 2000:
                                # No marker found - emit what we have and keep looking
                                to_emit = _strip_binary_garbage(codex_output_buffer[:-500])
                                codex_output_buffer = codex_output_buffer[-500:]
                                if to_emit.strip():
                                    encoded = base64.b64encode(to_emit.encode()).decode()
                                    emit("stream", chunk=encoded)
                                    with terminal_lock:
                                        terminal_buffer.extend(to_emit.encode())
                                    captured_output.append(to_emit)
                        return

                    # Past prompt echo - emit after stripping binary garbage
                    cleaned = _strip_binary_garbage(decoded)
                    if cleaned.strip():
                        cleaned_bytes = cleaned.encode()
                        encoded = base64.b64encode(cleaned_bytes).decode()
                        emit("stream", chunk=encoded)
                        with terminal_lock:
                            terminal_buffer.extend(cleaned_bytes)
                        captured_output.append(cleaned)

        # Start PTY session
        stream_id = pty_service.start_pty_session(
            session_id=task.session_id,
            cmd=cmd,
            cwd=worktree_path,
            env=env,
            rows=rows,
            cols=cols,
            log_callback=log_pty_event,
            stdin_pipe=use_stdin_pipe,
        )
        task.stream_id = stream_id
        session.active = True
        session.coding_account = coding_account

        # Send initial input if needed
        if initial_input:
            time.sleep(0.2)
            pty_service.send_input(stream_id, initial_input.encode(), close_stdin=use_stdin_pipe)

        # Wait for PTY to complete
        pty_session = pty_service.get_session(stream_id)

        while pty_session and pty_session.active:
            # Check for cancellation
            if task.cancel_requested:
                pty_service.terminate(stream_id)
                pty_service.cleanup_session(stream_id)
                return -1, ""

            # Inactivity timeout
            if self.inactivity_timeout is not None:
                now = time.time()
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
                    pty_service.cleanup_session(stream_id)
                    return -2, ""  # -2 indicates timeout

            # Periodically flush decoded terminal snapshots to EventLog so
            # long-running sessions are observable before process exit.
            if time.time() - last_log_flush >= self.terminal_flush_interval:
                flush_terminal_buffer()

            time.sleep(0.1)
            pty_session = pty_service.get_session(stream_id)

        # Get final exit code
        exit_code = 0
        if pty_session:
            exit_code = pty_session.exit_code or 0

        # Flush any remaining Codex output buffer that wasn't emitted
        if codex_output_buffer:
            captured_output.append(codex_output_buffer)

        flush_terminal_buffer()
        pty_service.cleanup_session(stream_id)

        return exit_code, "\n".join(captured_output)

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
        override_exploration_prompt: str | None = None,
        override_implementation_prompt: str | None = None,
    ):
        """Execute the task in a background thread using PTY.

        Uses a 3-phase approach:
        1. Exploration - Agent explores codebase and outputs progress JSON
        2. Implementation - Agent implements the fix using exploration context
        3. Verification - Separate verification agent (handled by UI)
        """
        rows = terminal_rows if terminal_rows else TERMINAL_ROWS
        cols = terminal_cols if terminal_cols else TERMINAL_COLS
        status_logging_enabled = [False]

        def emit(event_type: str, **data):
            event = StreamEvent(type=event_type, data=data)
            task._event_queue.put(event)
            if event_type == "status" and task.event_log and status_logging_enabled[0]:
                try:
                    task.event_log.log(StatusEvent(status=str(data.get("status", ""))))
                except Exception:
                    pass
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
                status_logging_enabled[0] = True

            # All providers use phased execution: exploration → implementation → continuation
            emit("status", status=f"Starting {coding_provider} agent...")
            emit("message_start", speaker="CODING AI")

            accumulated_output = ""
            final_exit_code = 0
            max_continuation_attempts = 3

            # Phase 1: Exploration - agent explores codebase and outputs progress JSON
            emit("status", status="Phase 1: Exploring codebase...")
            exit_code, exploration_output = self._run_phase(
                task=task,
                session=session,
                worktree_path=worktree_path,
                task_description=task_description,
                coding_account=coding_account,
                coding_provider=coding_provider,
                screenshots=screenshots,
                phase="exploration",
                exploration_output=None,
                rows=rows,
                cols=cols,
                emit=emit,
                git_mgr=git_mgr,
                coding_model=coding_model,
                coding_reasoning=coding_reasoning,
                override_prompt=override_exploration_prompt,
            )
            accumulated_output = exploration_output
            final_exit_code = exit_code
            if exit_code == 0:
                self._decrement_mock_usage(coding_provider, coding_account)

            # Handle cancellation/timeout from exploration phase
            if task.cancel_requested or exit_code == -1:
                emit("status", status="Task cancelled")
                task.state = TaskState.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
                if task.event_log:
                    task.event_log.log(SessionEndedEvent(success=False, reason="cancelled"))
                return

            if exit_code == -2:
                emit("complete", success=False, message="Agent timed out during exploration", exit_code=-1)
                emit("message_complete", speaker="CODING AI", content="Task timed out")
                task.state = TaskState.FAILED
                task.error = "Agent timed out during exploration"
                task.completed_at = datetime.now(timezone.utc)
                session.active = False
                session.has_worktree_changes = git_mgr.has_changes(task.session_id)
                if task.event_log:
                    task.event_log.log(SessionEndedEvent(success=False, reason="timeout"))
                return

            # Extract progress update from exploration phase
            progress = extract_progress_update(exploration_output)
            if progress:
                emit("progress", summary=progress.summary, location=progress.location, next_step=progress.next_step)

            # Never treat exploration output as task completion. Exploration should
            # always hand off into implementation after a clean exit.
            if exit_code == 0:
                # Check provider thresholds before implementation phase
                coding_account, coding_provider, switched = self._check_provider_threshold(
                    coding_account, coding_provider, emit
                )
                if switched:
                    session.coding_account = coding_account

                # Phase 2: Implementation - agent continues with implementation
                emit("status", status="Phase 2: Implementing changes...")
                exit_code, impl_output = self._run_phase(
                    task=task,
                    session=session,
                    worktree_path=worktree_path,
                    task_description=task_description,
                    coding_account=coding_account,
                    coding_provider=coding_provider,
                    screenshots=None,  # Screenshots already shown in exploration
                    phase="implementation",
                    exploration_output=exploration_output,
                    rows=rows,
                    cols=cols,
                    emit=emit,
                    git_mgr=git_mgr,
                    coding_model=coding_model,
                    coding_reasoning=coding_reasoning,
                    override_prompt=override_implementation_prompt,
                )
                accumulated_output += "\n" + impl_output
                final_exit_code = exit_code
                if exit_code == 0:
                    self._decrement_mock_usage(coding_provider, coding_account)

                # Handle cancellation/timeout from implementation phase
                if task.cancel_requested or exit_code == -1:
                    emit("status", status="Task cancelled")
                    task.state = TaskState.CANCELLED
                    task.completed_at = datetime.now(timezone.utc)
                    if task.event_log:
                        task.event_log.log(SessionEndedEvent(success=False, reason="cancelled"))
                    return

                if exit_code == -2:
                    emit("complete", success=False, message="Agent timed out during implementation", exit_code=-1)
                    emit("message_complete", speaker="CODING AI", content="Task timed out")
                    task.state = TaskState.FAILED
                    task.error = "Agent timed out during implementation"
                    task.completed_at = datetime.now(timezone.utc)
                    session.active = False
                    session.has_worktree_changes = git_mgr.has_changes(task.session_id)
                    if task.event_log:
                        task.event_log.log(SessionEndedEvent(success=False, reason="timeout"))
                    return

                # Check if agent completed after implementation
                summary = extract_coding_summary(accumulated_output)

                # Continuation loop if agent exited without completion
                if summary is None and exit_code == 0:
                    for attempt in range(max_continuation_attempts):
                        # Check provider thresholds before each continuation
                        coding_account, coding_provider, switched = self._check_provider_threshold(
                            coding_account, coding_provider, emit
                        )
                        if switched:
                            session.coding_account = coding_account
                        emit("status", status=f"Agent continuing (attempt {attempt + 1})...")
                        exit_code, cont_output = self._run_phase(
                            task=task,
                            session=session,
                            worktree_path=worktree_path,
                            task_description=task_description,
                            coding_account=coding_account,
                            coding_provider=coding_provider,
                            screenshots=None,
                            phase="continuation",
                            exploration_output=accumulated_output,
                            rows=rows,
                            cols=cols,
                            emit=emit,
                            git_mgr=git_mgr,
                            coding_model=coding_model,
                            coding_reasoning=coding_reasoning,
                        )
                        accumulated_output += "\n" + cont_output
                        final_exit_code = exit_code
                        if exit_code == 0:
                            self._decrement_mock_usage(coding_provider, coding_account)

                        # Handle cancellation
                        if task.cancel_requested or exit_code == -1:
                            emit("status", status="Task cancelled")
                            task.state = TaskState.CANCELLED
                            task.completed_at = datetime.now(timezone.utc)
                            if task.event_log:
                                task.event_log.log(SessionEndedEvent(success=False, reason="cancelled"))
                            return

                        # Handle timeout
                        if exit_code == -2:
                            emit("complete", success=False, message="Agent timed out", exit_code=-1)
                            emit("message_complete", speaker="CODING AI", content="Task timed out")
                            task.state = TaskState.FAILED
                            task.error = "Agent timed out"
                            task.completed_at = datetime.now(timezone.utc)
                            session.active = False
                            session.has_worktree_changes = git_mgr.has_changes(task.session_id)
                            if task.event_log:
                                task.event_log.log(SessionEndedEvent(success=False, reason="timeout"))
                            return

                        # Check if agent completed
                        summary = extract_coding_summary(accumulated_output)
                        if summary is not None:
                            break

                        # Only continue if exit was clean
                        if exit_code != 0:
                            break

            # Emit completion
            emit("message_complete", speaker="CODING AI", content="Task completed")

            if final_exit_code == 0:
                task.state = TaskState.COMPLETED
                task.result = "Task completed successfully"
                session.has_worktree_changes = git_mgr.has_changes(task.session_id)
                emit(
                    "complete",
                    success=True,
                    message="Task completed successfully",
                    has_changes=session.has_worktree_changes,
                    exit_code=final_exit_code,
                )
            else:
                task.state = TaskState.FAILED
                task.error = f"Agent exited with code {final_exit_code}"
                emit(
                    "complete",
                    success=False,
                    message=f"Agent exited with code {final_exit_code}",
                    exit_code=final_exit_code,
                )
                emit("message_complete", speaker="CODING AI", content=f"Task failed (exit {final_exit_code})")

            task.completed_at = datetime.now(timezone.utc)
            session.active = False

            # Log session end
            if task.event_log:
                task.event_log.log(SessionEndedEvent(
                    success=task.state == TaskState.COMPLETED,
                    reason="completed" if exit_code == 0 else f"exit_code_{exit_code}",
                ))

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
                if task.stream_id:
                    pty_service = get_pty_stream_service()
                    pty_service.cleanup_session(task.stream_id)
            except Exception:
                pass
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

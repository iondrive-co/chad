"""Generic AI provider interface for supporting multiple models."""

import glob
import os
import re
import select
import shutil
import signal
import subprocess
import time
import threading
import queue
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chad.utils import platform_path, safe_home
from .installer import AIToolInstaller
from .installer import DEFAULT_TOOLS_DIR

try:
    import pty
except ImportError:  # pragma: no cover - only hit on Windows
    pty = None

_HAS_PTY = pty is not None


# Configurable timeouts via environment variables
def _get_env_float(name: str, default: float) -> float:
    """Get a float from environment variable, with fallback to default."""
    val = os.environ.get(name)
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return default


# Idle timeout after a command completes (waiting for next action from API)
CODEX_IDLE_TIMEOUT = _get_env_float("CODEX_IDLE_TIMEOUT", 90.0)
CODEX_START_IDLE_TIMEOUT = _get_env_float("CODEX_START_IDLE_TIMEOUT", 120.0)
CODEX_THINK_IDLE_TIMEOUT = _get_env_float("CODEX_THINK_IDLE_TIMEOUT", 240.0)
CODEX_COMMAND_IDLE_TIMEOUT = _get_env_float("CODEX_COMMAND_IDLE_TIMEOUT", 420.0)
# Shorter timeout during pure "thinking" phases (no command running)
CODEX_THINKING_TIMEOUT = _get_env_float("CODEX_THINKING_TIMEOUT", 60.0)

# Maximum exploration commands without implementation before triggering early timeout
# Prevents agents from getting stuck in endless search/read loops
CODEX_MAX_EXPLORATION_WITHOUT_IMPL = int(_get_env_float("CODEX_MAX_EXPLORATION_WITHOUT_IMPL", 40.0))


def find_cli_executable(name: str) -> str:
    """Find a CLI executable, checking common locations if not in PATH.

    Args:
        name: The executable name (e.g., 'codex', 'claude', 'gemini')

    Returns:
        Full path to executable, or just the name if not found (will fail later with clear error)
    """
    tools_dir = Path(os.environ.get("CHAD_TOOLS_DIR", DEFAULT_TOOLS_DIR))
    tools_candidate = tools_dir / "bin" / name
    if tools_candidate.exists():
        return str(tools_candidate)
    npm_bin = tools_dir / "node_modules" / ".bin" / name
    if npm_bin.exists():
        return str(npm_bin)

    # First check PATH
    found = shutil.which(name)
    if found:
        return found

    # Common locations for Node.js tools (nvm, fnm, etc.)
    home = os.path.expanduser("~")
    search_patterns = [
        f"{home}/.nvm/versions/node/*/bin/{name}",
        f"{home}/.fnm/node-versions/*/installation/bin/{name}",
        f"{home}/.local/bin/{name}",
        f"{home}/.cargo/bin/{name}",
        f"/usr/local/bin/{name}",
        f"{home}/bin/{name}",
    ]

    for pattern in search_patterns:
        matches = glob.glob(pattern)
        if matches:
            # Return the most recent version (last in sorted list)
            return sorted(matches)[-1]

    # Return original name - will fail with clear error message
    return name


def parse_codex_output(raw_output: str | None) -> str:  # noqa: C901
    """Parse Codex output to extract just thinking and response.

    Codex output has the format:
    - Header with version info
    - 'thinking' sections with reasoning
    - 'exec' sections with command outputs (skip these)
    - 'codex' section with the final response
    - 'tokens used' at the end

    Returns just the thinking and final response.
    """
    if not raw_output:
        return ""

    lines = raw_output.split("\n")
    result_parts = []
    in_thinking = False
    in_response = False
    in_exec = False
    current_section = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip header block (OpenAI Codex version info)
        if line.startswith("OpenAI Codex") or line.startswith("--------"):
            i += 1
            continue

        # Skip metadata lines
        if any(
            stripped.startswith(prefix)
            for prefix in [
                "workdir:",
                "model:",
                "provider:",
                "approval:",
                "sandbox:",
                "reasoning effort:",
                "reasoning summaries:",
                "session id:",
                "mcp startup:",
                "tokens used",
            ]
        ):
            i += 1
            continue

        # Skip standalone numbers (token counts) - including comma-separated like "4,481"
        if stripped.replace(",", "").isdigit() and len(stripped) <= 10:
            i += 1
            continue

        # Skip 'user' marker lines
        if stripped == "user":
            i += 1
            continue

        # Handle exec blocks - skip until next known marker
        if stripped.startswith("exec"):
            in_exec = True
            # Save current thinking section before exec
            if in_thinking and current_section:
                result_parts.append(("thinking", "\n".join(current_section)))
                current_section = []
            in_thinking = False
            i += 1
            continue

        # End exec block on next marker
        if in_exec:
            if stripped in ("thinking", "codex"):
                in_exec = False
                # Fall through to handle the marker
            else:
                i += 1
                continue

        # Capture thinking sections
        if stripped == "thinking":
            # Save previous section if any
            if current_section:
                section_type = "response" if in_response else "thinking"
                result_parts.append((section_type, "\n".join(current_section)))
            in_thinking = True
            in_response = False
            current_section = []
            i += 1
            continue

        # Capture codex response (final answer)
        if stripped == "codex":
            # Save previous section if any
            if current_section:
                section_type = "response" if in_response else "thinking"
                result_parts.append((section_type, "\n".join(current_section)))
            in_thinking = False
            in_response = True
            current_section = []
            i += 1
            continue

        # Accumulate content
        if in_thinking:
            # For thinking, just collect the core message
            if stripped:
                current_section.append(stripped)
        elif in_response:
            # For response, preserve original formatting (but strip trailing whitespace)
            current_section.append(line.rstrip())

        i += 1

    # Add final section
    if current_section:
        section_type = "response" if in_response else "thinking"
        result_parts.append((section_type, "\n".join(current_section)))

    # Format output - consolidate thinking, preserve response formatting
    thinking_parts = []
    response_parts = []

    for section_type, content in result_parts:
        if section_type == "thinking":
            # Collect all thinking for a compact summary
            thinking_parts.append(content.replace("\n", " ").strip())
        else:
            response_parts.append(content)

    formatted = []

    # Add consolidated thinking as a compact italic block
    if thinking_parts and _thinking_enabled():
        # Show last few thinking steps, not all
        recent_thoughts = thinking_parts[-5:] if len(thinking_parts) > 5 else thinking_parts
        thinking_summary = " → ".join(recent_thoughts)
        formatted.append(f"*Thinking: {thinking_summary}*")

    # Add response content with preserved formatting
    for content in response_parts:
        # Clean up excessive blank lines but preserve structure
        lines = content.split("\n")
        cleaned_lines = []
        for i, line in enumerate(lines):
            if line.strip() or (i > 0 and lines[i - 1].strip()):
                cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)
        if cleaned.strip():
            formatted.append(cleaned.strip())

    return "\n\n".join(formatted) if formatted else raw_output


def extract_final_codex_response(raw_output: str | None) -> str:
    """Extract only the final 'codex' response from Codex output.

    This is useful for isolating the final instruction text
    without all the context it was given.
    """
    if not raw_output:
        return ""

    lines = raw_output.split("\n")
    last_codex_index = -1

    # Find the last 'codex' marker
    for i, line in enumerate(lines):
        if line.strip() == "codex":
            last_codex_index = i

    if last_codex_index == -1:
        return raw_output

    # Collect everything after the last 'codex' marker until we hit a marker or end
    final_response = []
    for i in range(last_codex_index + 1, len(lines)):
        stripped = lines[i].strip()

        # Stop at next section marker
        if stripped in ("thinking", "codex", "exec"):
            break

        # Skip token counts and metadata - including comma-separated like "4,481"
        if stripped.startswith("tokens used") or (stripped.replace(",", "").isdigit() and len(stripped) <= 10):
            continue

        if stripped:
            final_response.append(stripped)

    return "\n".join(final_response) if final_response else raw_output


@dataclass
class ModelConfig:
    """Configuration for an AI model."""

    provider: str  # 'anthropic', 'openai', etc.
    model_name: str  # 'claude-3-5-sonnet-20241022', 'gpt-4', etc.
    account_name: str | None = None  # Account identifier (not an API key)
    base_url: str | None = None
    reasoning_effort: str | None = None


# Callback type for activity updates: (activity_type, detail)
# activity_type: 'tool', 'thinking', 'text', 'stream' (for raw streaming chunks)
ActivityCallback = Callable[[str, str], None] | None
# Callback type for activity updates: (activity_type, detail)
# activity_type: 'tool', 'thinking', 'text', 'stream' (for raw streaming chunks)
ActivityCallback = Callable[[str, str], None] | None


_ANSI_ESCAPE = re.compile(r"[\x1b\u241b]\[[0-9;]*[a-zA-Z]?")
CLI_INSTALLER = AIToolInstaller()
_BOOLEAN_TRUE = {"1", "true", "yes", "on"}


def _thinking_enabled() -> bool:
    """Gate verbose thinking traces behind an opt-in flag."""
    hide_flag = os.environ.get("CHAD_HIDE_THINKING", "").strip().lower()
    if hide_flag in _BOOLEAN_TRUE:
        return False

    explicit_flag = os.environ.get("CHAD_THINKING", "").strip().lower()
    if explicit_flag:
        return explicit_flag in _BOOLEAN_TRUE

    return True


def _ensure_cli_tool(tool_key: str, activity_cb: ActivityCallback = None) -> tuple[bool, str]:
    """Ensure a provider CLI is installed; optionally notify activity on failure."""
    ok, detail = CLI_INSTALLER.ensure_tool(tool_key)
    if not ok and activity_cb:
        activity_cb("text", detail)
    return ok, detail


def _strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from streamed output.

    Handles both actual escape char (0x1b) and Unicode escape symbol (␛ U+241B).
    Also removes partial/incomplete escape sequences.
    """
    text = _ANSI_ESCAPE.sub("", text)
    # Also remove incomplete escape sequences that got split across chunks
    text = re.sub(r"[\x1b\u241b]\[?[0-9;]*$", "", text)
    return text


def _close_master_fd(master_fd: int | None) -> None:
    """Safely close a master PTY file descriptor."""
    if master_fd is None:
        return
    try:
        os.close(master_fd)
    except OSError:
        pass


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill a process along with its entire process group."""
    if process.poll() is not None:
        return

    pid = getattr(process, "pid", None)

    if os.name == "nt":
        if isinstance(pid, int):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
                return
            except (subprocess.SubprocessError, OSError):
                pass
        try:
            process.kill()
        except Exception:
            pass
        return

    if isinstance(pid, int):
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                process.kill()
            return
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
                return
            except Exception:
                pass

    try:
        process.kill()
    except Exception:
        pass


def _start_pty_process(
    cmd: list[str], cwd: str | None = None, env: dict | None = None
) -> tuple[subprocess.Popen, int | None]:
    """Start a subprocess with a PTY attached for streaming output when available.

    On Unix, uses a PTY for proper terminal emulation.
    On Windows, uses pipes with CREATE_NEW_PROCESS_GROUP for proper process tree termination.
    """
    if _HAS_PTY:
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            close_fds=True,
            start_new_session=True,  # Run in own process group for clean termination
        )
        os.close(slave_fd)
        return process, master_fd

    # Windows: use pipes with flags for proper process management
    creation_flags = 0
    startupinfo = None
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP allows taskkill /T to terminate the tree
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        # Hide console window and configure stdio handles properly
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESTDHANDLES

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=env,
        creationflags=creation_flags,
        startupinfo=startupinfo,
    )
    return process, None


def _drain_pty(master_fd: int, output_chunks: list[str], on_chunk: Callable[[str], None] | None) -> None:
    """Read any available data from the PTY and forward to callbacks."""
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if not ready:
                break
            chunk = os.read(master_fd, 4096)
            if not chunk:
                break
            decoded = chunk.decode("utf-8", errors="replace")
            output_chunks.append(decoded)
            if on_chunk:
                on_chunk(decoded)
    except OSError:
        return


def _stream_pipe_output(
    process: subprocess.Popen,
    on_chunk: Callable[[str], None] | None,
    timeout: float,
    idle_timeout: float | None = None,
    idle_timeout_callback: Callable[[float], bool] | None = None,
) -> tuple[str, bool, bool]:
    """Stream output from a pipe-backed process until completion or timeout.

    On Windows, pipes can split output at arbitrary byte boundaries (unlike Unix PTYs
    which typically preserve line boundaries). This can cause JSON lines to be split
    across multiple read() calls. To handle this:
    - We buffer raw bytes and decode complete lines
    - Partial lines at the end of a chunk are held until more data arrives
    - This ensures callbacks receive complete lines for JSON parsing
    """
    output_chunks: list[str] = []
    start_time = time.time()
    last_activity = start_time
    idle_stalled = False
    output_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    # Buffer for incomplete lines (Windows pipes can split at arbitrary boundaries)
    line_buffer: list[bytes] = [b""]

    def _reader() -> None:
        try:
            if process.stdout is None:
                return
            while not stop_event.is_set():
                # Use readline() for responsive output regardless of platform
                chunk = process.stdout.readline()
                if not chunk:
                    break
                output_queue.put(chunk)
        finally:
            stop_event.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while time.time() - start_time < timeout:
            if process.poll() is not None and output_queue.empty() and stop_event.is_set():
                # Process any remaining buffered content before exiting
                if line_buffer[0]:
                    decoded = line_buffer[0].decode("utf-8", errors="replace")
                    output_chunks.append(decoded)
                    if on_chunk:
                        on_chunk(decoded)
                    line_buffer[0] = b""
                break

            try:
                chunk = output_queue.get(timeout=0.1)
            except queue.Empty:
                chunk = None

            if chunk:
                # Combine with any buffered partial line from previous chunk
                combined = line_buffer[0] + chunk
                # Split into lines, keeping the last partial line in buffer
                lines = combined.split(b"\n")
                line_buffer[0] = lines[-1]  # Keep incomplete line for next iteration

                # Process complete lines (all but the last split result)
                if len(lines) > 1:
                    complete_data = b"\n".join(lines[:-1]) + b"\n"
                    decoded = complete_data.decode("utf-8", errors="replace")
                    output_chunks.append(decoded)
                    if on_chunk:
                        on_chunk(decoded)

                start_time = time.time()
                last_activity = start_time

            if idle_timeout and (time.time() - last_activity) >= idle_timeout:
                elapsed = time.time() - last_activity
                if idle_timeout_callback and not idle_timeout_callback(elapsed):
                    continue
                # Before declaring stall, check if data arrived in the queue
                if not output_queue.empty():
                    last_activity = time.time()
                    continue
                idle_stalled = True
                break

        # Drain any remaining items from the queue before processing final buffer
        while not output_queue.empty():
            try:
                chunk = output_queue.get_nowait()
                if chunk:
                    combined = line_buffer[0] + chunk
                    lines = combined.split(b"\n")
                    line_buffer[0] = lines[-1]
                    if len(lines) > 1:
                        complete_data = b"\n".join(lines[:-1]) + b"\n"
                        decoded = complete_data.decode("utf-8", errors="replace")
                        output_chunks.append(decoded)
                        if on_chunk:
                            on_chunk(decoded)
            except queue.Empty:
                break

        # Process any remaining buffered content (from incomplete lines)
        if line_buffer[0]:
            decoded = line_buffer[0].decode("utf-8", errors="replace")
            output_chunks.append(decoded)
            if on_chunk:
                on_chunk(decoded)
            line_buffer[0] = b""

        timed_out = process.poll() is None and not idle_stalled
        if timed_out or idle_stalled:
            _kill_process_tree(process)
        try:
            process.wait(timeout=0.1)
        except Exception:
            pass
    finally:
        stop_event.set()
        if process.stdout:
            try:
                process.stdout.close()
            except Exception:
                pass
        reader_thread.join(timeout=0.1)

    return "".join(output_chunks), timed_out, idle_stalled


def _stream_pty_output(
    process: subprocess.Popen,
    master_fd: int | None,
    on_chunk: Callable[[str], None] | None,
    timeout: float,
    idle_timeout: float | None = None,
    idle_timeout_callback: Callable[[float], bool] | None = None,
) -> tuple[str, bool, bool]:
    """Stream output from a PTY-backed process until completion or timeout.

    Returns (output, timed_out, idle_stalled):
    - idle_stalled is True when no output for idle_timeout (or callback signals stall)
    - If process is still running, we still respect idle_timeout_callback to decide whether to keep waiting
    """
    if master_fd is None:
        return _stream_pipe_output(
            process,
            on_chunk,
            timeout,
            idle_timeout=idle_timeout,
            idle_timeout_callback=idle_timeout_callback,
        )

    output_chunks: list[str] = []
    start_time = time.time()
    last_activity = start_time
    idle_stalled = False

    try:
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                _drain_pty(master_fd, output_chunks, on_chunk)
                break

            try:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
            except OSError:
                break

            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break

                if chunk:
                    decoded = chunk.decode("utf-8", errors="replace")
                    output_chunks.append(decoded)
                    if on_chunk:
                        on_chunk(decoded)
                    last_activity = time.time()

            if idle_timeout and (time.time() - last_activity) >= idle_timeout:
                elapsed = time.time() - last_activity
                if idle_timeout_callback and not idle_timeout_callback(elapsed):
                    continue
                # Before declaring stall, try one final drain - data may have arrived
                chunks_before = len(output_chunks)
                _drain_pty(master_fd, output_chunks, on_chunk)
                if len(output_chunks) > chunks_before:
                    # Got new data during drain, reset and continue
                    last_activity = time.time()
                    continue
                idle_stalled = True
                break

        timed_out = process.poll() is None and not idle_stalled
        if timed_out or idle_stalled:
            # Drain any final output before killing - process may have produced
            # output that's still in the PTY buffer
            _drain_pty(master_fd, output_chunks, on_chunk)
            _kill_process_tree(process)
            try:
                process.wait(timeout=1)
            except Exception:
                pass
        else:
            try:
                process.wait(timeout=0.1)
            except Exception:
                pass
    finally:
        _close_master_fd(master_fd)

    return "".join(output_chunks), timed_out, idle_stalled


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self.activity_callback: ActivityCallback = None

    def set_activity_callback(self, callback: ActivityCallback) -> None:
        """Set callback for live activity updates."""
        self.activity_callback = callback

    def _notify_activity(self, activity_type: str, detail: str) -> None:
        """Notify about activity if callback is set."""
        if self.activity_callback:
            self.activity_callback(activity_type, detail)

    @abstractmethod
    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        """Start an interactive session.

        Args:
            project_path: Path to the project directory
            system_prompt: Optional system prompt for the session

        Returns:
            True if session started successfully
        """
        pass

    @abstractmethod
    def send_message(self, message: str) -> None:
        """Send a message to the AI."""
        pass

    @abstractmethod
    def get_response(self, timeout: float = 30.0) -> str:  # noqa: C901
        """Get the AI's response.

        Args:
            timeout: How long to wait for response

        Returns:
            The AI's response text
        """
        pass

    @abstractmethod
    def stop_session(self) -> None:
        """Stop the interactive session."""
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if the session is still running."""
        pass

    @abstractmethod
    def supports_multi_turn(self) -> bool:
        """Check if this provider supports multi-turn conversations.

        Returns:
            True if the provider can continue a session after the initial task.
            Providers that support this should preserve session state for follow-ups.
        """
        pass

    def can_continue_session(self) -> bool:
        """Check if the current session can accept follow-up messages.

        Returns:
            True if session is active and can process more messages.
            Default implementation returns is_alive() for multi-turn providers.
        """
        return self.supports_multi_turn() and self.is_alive()


class ClaudeCodeProvider(AIProvider):
    """Provider for Anthropic Claude Code CLI.

    Uses streaming JSON input/output for multi-turn conversations.
    See: https://docs.anthropic.com/en/docs/claude-code/headless

    Each account gets an isolated CLAUDE_CONFIG_DIR to support multiple accounts.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.accumulated_text: list[str] = []

    def _get_claude_config_dir(self) -> str:
        """Get the isolated CLAUDE_CONFIG_DIR for this account."""
        from pathlib import Path

        if self.config.account_name:
            return str(Path.home() / ".chad" / "claude-configs" / self.config.account_name)
        return str(Path.home() / ".claude")

    def _get_env(self) -> dict:
        """Get environment with isolated CLAUDE_CONFIG_DIR for this account."""
        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = self._get_claude_config_dir()
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _ensure_mcp_permissions(self) -> None:
        """Ensure Claude config has permissions to auto-approve project MCP servers."""
        import json

        config_dir = Path(self._get_claude_config_dir())
        config_dir.mkdir(parents=True, exist_ok=True)
        settings_path = config_dir / "settings.local.json"

        # Load existing settings or create new
        settings = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                settings = {}

        # Ensure MCP servers are auto-approved
        if not settings.get("enableAllProjectMcpServers"):
            settings["enableAllProjectMcpServers"] = True
            settings_path.write_text(json.dumps(settings, indent=2))

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        import subprocess

        ok, detail = _ensure_cli_tool("claude", self._notify_activity)
        if not ok:
            return False

        self._ensure_mcp_permissions()
        self.project_path = project_path

        cmd = [
            detail or find_cli_executable("claude"),
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
            "--verbose",
        ]

        if self.config.model_name and self.config.model_name != "default":
            cmd.extend(["--model", self.config.model_name])

        try:
            env = self._get_env()
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=project_path,
                bufsize=1,
                env=env,
            )

            if system_prompt:
                self.send_message(system_prompt)

            return True
        except (FileNotFoundError, PermissionError, OSError) as e:
            self._notify_activity("text", f"Failed to start Claude: {e}")
            return False

    def send_message(self, message: str) -> None:
        import json

        if not self.process or not self.process.stdin:
            return

        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": message}]}}

        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def get_response(self, timeout: float = 30.0) -> str:
        import time
        import json

        if not self.process or not self.process.stdout:
            return ""

        result_text = None
        start_time = time.time()
        idle_timeout = 2.0
        self.accumulated_text = []

        # Use a thread-based approach for Windows compatibility
        # (select.select doesn't work with pipes on Windows)
        line_queue: queue.Queue = queue.Queue()
        stop_reading = threading.Event()

        def reader_thread():
            try:
                while not stop_reading.is_set() and self.process and self.process.stdout:
                    line = self.process.stdout.readline()
                    if line:
                        line_queue.put(line)
                    elif self.process.poll() is not None:
                        break
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=reader_thread, daemon=True)
        reader.start()

        try:
            while time.time() - start_time < timeout:
                if self.process.poll() is not None and line_queue.empty():
                    break

                try:
                    line = line_queue.get(timeout=idle_timeout)
                except queue.Empty:
                    if result_text is not None:
                        break
                    continue

                if not line:
                    if result_text is not None:
                        break
                    continue

                try:
                    msg = json.loads(line.strip())

                    if msg.get("type") == "assistant":
                        content = msg.get("message", {}).get("content", [])
                        for item in content:
                            if item.get("type") == "text":
                                text = item.get("text", "")
                                self.accumulated_text.append(text)
                                # Stream the text to UI
                                self._notify_activity("stream", text + "\n")
                                self._notify_activity("text", text[:100])
                            elif item.get("type") == "tool_use":
                                tool_name = item.get("name", "unknown")
                                tool_input = item.get("input", {})
                                if tool_name in ("Read", "Edit", "Write"):
                                    detail = tool_input.get("file_path", "")
                                elif tool_name == "Bash":
                                    detail = tool_input.get("command", "")[:50]
                                elif tool_name in ("Glob", "Grep"):
                                    detail = tool_input.get("pattern", "")
                                else:
                                    detail = ""
                                self._notify_activity("tool", f"{tool_name}: {detail}")

                    if msg.get("type") == "result":
                        result_text = msg.get("result", "")
                        break

                    start_time = time.time()
                except json.JSONDecodeError:
                    continue
        finally:
            stop_reading.set()
            reader.join(timeout=0.5)

        return result_text or ""

    def stop_session(self) -> None:
        if self.process:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass

            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except TimeoutError:
                self.process.kill()
                self.process.wait()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def supports_multi_turn(self) -> bool:
        return True


class OpenAICodexProvider(AIProvider):
    """Provider for OpenAI Codex CLI.

    Uses browser-based authentication like Claude Code.
    Run 'codex' to authenticate via browser if not already logged in.
    Uses 'codex exec' for non-interactive execution with PTY for real-time streaming.
    Supports multi-turn via 'codex exec resume [thread_id]'.

    Each account gets an isolated HOME directory to support multiple accounts.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None
        self.master_fd: int | None = None
        self.thread_id: str | None = None  # For multi-turn conversation support
        self.last_event_info: dict | None = None

    def _get_isolated_home(self) -> str:
        """Get the isolated HOME directory for this account."""
        base_home = safe_home()
        if self.config.account_name:
            return str(base_home / ".chad" / "codex-homes" / self.config.account_name)
        return str(base_home)

    def _get_env(self) -> dict:
        """Get environment with isolated HOME for this account."""
        env = os.environ.copy()
        isolated_home = self._get_isolated_home()
        env["HOME"] = isolated_home
        # On Windows, set all home-related variables to ensure Codex CLI
        # uses our isolated config directory. Node.js apps may check any of these.
        if os.name == "nt":
            env["USERPROFILE"] = isolated_home
            # HOMEDRIVE and HOMEPATH are used by Windows for home resolution
            home_path = platform_path(isolated_home)
            env["HOMEDRIVE"] = home_path.drive or "C:"
            env["HOMEPATH"] = str(home_path.relative_to(home_path.anchor))
            # Some Node.js apps use APPDATA/LOCALAPPDATA for config storage
            env["APPDATA"] = str(home_path / "AppData" / "Roaming")
            env["LOCALAPPDATA"] = str(home_path / "AppData" / "Local")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["TERM"] = "xterm-256color"
        return env

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        ok, detail = _ensure_cli_tool("codex", self._notify_activity)
        if not ok:
            return False

        self.project_path = project_path
        self.system_prompt = system_prompt
        self.cli_path = detail
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1500.0, _is_recovery: bool = False) -> str:  # noqa: C901
        import json

        if not self.current_message:
            return ""

        codex_cli = getattr(self, "cli_path", None) or find_cli_executable("codex")

        # Build command - use resume if we have a thread_id (multi-turn)
        # Flags like --json must come BEFORE the resume subcommand
        if self.thread_id:
            cmd = [
                codex_cli,
                "exec",
                "--json",  # Must be before 'resume' subcommand
                # Use bypass flag for resume too - approval_policy=on-request
                # doesn't work in non-interactive exec mode
                "--dangerously-bypass-approvals-and-sandbox",
                "resume",
                self.thread_id,
                "-",  # Read prompt from stdin
            ]
        else:
            cmd = [
                codex_cli,
                "exec",
                # Use bypass flag because --full-auto sets approval_policy=on-request
                # which falls back to 'never' in non-interactive exec mode (no user to ask)
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--json",
                "-C",
                self.project_path,
                "-",  # Read from stdin
            ]

            if self.config.model_name and self.config.model_name != "default":
                cmd.extend(["-m", self.config.model_name])

            if self.config.reasoning_effort and self.config.reasoning_effort != "default":
                cmd.extend(["-c", f'model_reasoning_effort="{self.config.reasoning_effort}"'])

        try:
            env = self._get_env()
            self.process, self.master_fd = _start_pty_process(cmd, cwd=self.project_path, env=env)

            if self.process.stdin:
                self.process.stdin.write(self.current_message.encode())
                self.process.stdin.flush()
                self.process.stdin.close()

            # Both initial and resume use JSON output
            json_events = []
            reconnect_seen = [False]

            def format_json_event_as_text(event: dict) -> str | None:
                """Convert a JSON event to human-readable text for streaming."""
                event_type = event.get("type", "")

                if event_type == "item.completed":
                    item = event.get("item", {})
                    item_type = item.get("type", "")

                    if item_type == "reasoning":
                        text = item.get("text", "")
                        if text:
                            # Strip markdown bold/italics markers for clean display
                            clean = text.replace("**", "").replace("*", "").strip()
                            if clean:
                                # Use dim cyan for reasoning/thinking
                                return f"\033[36m• {clean}\033[0m\n"
                        return None
                    elif item_type == "agent_message":
                        text = item.get("text", "")
                        if not text:
                            return None
                        # Filter out internal tool invocations and raw bash output
                        lines = text.split("\n")
                        filtered = []
                        for line in lines:
                            stripped = line.strip()
                            # Skip raw bash commands (often prefixed with $ or contain shell paths)
                            if stripped.startswith("$ ") or stripped.startswith("$/"):
                                continue
                            # Skip internal markers (***...*** patterns)
                            if stripped.startswith("***") and stripped.endswith("***"):
                                continue
                            # Skip lines that look like grep/find output (path:line:content)
                            if re.match(r"^\d+:\s*", stripped) or re.match(r"^[a-zA-Z_/].*:\d+:", stripped):
                                continue
                            if stripped:
                                filtered.append(line)
                        if filtered:
                            return "\n".join(filtered) + "\n"
                        return None
                    elif item_type == "mcp_tool_call":
                        tool = item.get("tool", "tool")
                        # Human-readable tool descriptions
                        tool_descriptions = {
                            "read_file": "Reading",
                            "Read": "Reading",
                            "write_file": "Writing",
                            "Write": "Writing",
                            "edit_file": "Editing",
                            "Edit": "Editing",
                            "search": "Searching",
                            "Grep": "Searching",
                            "Glob": "Finding files",
                            "Bash": "Running command",
                        }
                        params = item.get("params", {})
                        path = params.get("path", params.get("file_path", ""))
                        desc = tool_descriptions.get(tool, f"Using {tool}")
                        if path:
                            # Show green for file operations
                            return f"\033[32m• {desc}: {path}\033[0m\n"
                        return f"\033[32m• {desc}\033[0m\n"
                    elif item_type == "command_execution":
                        cmd = item.get("command", "")[:80]
                        output = item.get("aggregated_output", "")
                        # Use purple/magenta for commands
                        result = f"\033[35m$ {cmd}\033[0m\n"
                        if output.strip():
                            lines = output.strip().split("\n")
                            # Show first few lines of output in dim gray
                            preview_lines = lines[:5]
                            for line in preview_lines:
                                result += f"\033[90m  {line[:100]}\033[0m\n"
                            if len(lines) > 5:
                                result += f"\033[90m  ... ({len(lines) - 5} more lines)\033[0m\n"
                        return result

                return None

            api_error = [None]  # Track API errors
            # Track last event for adaptive timeout and diagnostics
            last_event_info = {"kind": "start", "time": time.time(), "command": None}
            idle_diag = {"elapsed": 0.0, "limit": CODEX_START_IDLE_TIMEOUT, "kind": "start"}
            # Track command categories for session analysis
            cmd_stats = {"total": 0, "exploration": 0, "implementation": 0, "commands": []}
            exploration_loop_detected = [False]  # Flag to detect stuck exploration loops

            def process_chunk(chunk: str) -> None:
                # Parse JSON events and convert to human-readable text
                for line in chunk.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        json_events.append(event)

                        # Check for API errors (model not supported, etc.)
                        if event.get("type") == "error":
                            msg = event.get("message", "Unknown API error")
                            if "reconnecting" in msg.lower():
                                reconnect_seen[0] = True
                            else:
                                api_error[0] = msg
                        elif event.get("type") == "turn.failed":
                            error_info = event.get("error", {})
                            api_error[0] = error_info.get("message", "Turn failed")

                        # Extract thread_id from first event
                        if event.get("type") == "thread.started" and "thread_id" in event:
                            self.thread_id = event["thread_id"]

                        is_agent_message = (
                            event.get("type") == "item.completed"
                            and event.get("item", {}).get("type") == "agent_message"
                        )
                        if is_agent_message:
                            reconnect_seen[0] = False

                        # Convert to human-readable and stream
                        readable = format_json_event_as_text(event)
                        if readable:
                            self._notify_activity("stream", readable)

                        # Track last event for diagnostics and adaptive timeout
                        if event.get("type") == "item.completed":
                            item = event.get("item", {})
                            item_type = item.get("type", "")
                            last_event_info["time"] = time.time()
                            last_event_info["kind"] = item_type
                            if item_type == "command_execution":
                                cmd = item.get("command", "")
                                last_event_info["command"] = cmd[:80]
                                # Categorize command for session analysis
                                cmd_stats["total"] += 1
                                cmd_lower = cmd.lower()
                                # Implementation: file writes, edits, git commits
                                if any(x in cmd_lower for x in [
                                    "edit ", "write ", "> ", ">> ", "tee ",
                                    "git add", "git commit", "patch ", "sed -i",
                                ]):
                                    cmd_stats["implementation"] += 1
                                else:
                                    cmd_stats["exploration"] += 1
                                # Keep last N commands for diagnostics
                                cmd_stats["commands"].append(cmd[:100])
                                if len(cmd_stats["commands"]) > 20:
                                    cmd_stats["commands"] = cmd_stats["commands"][-20:]

                                # Detect exploration loop: many exploration commands, zero implementation
                                if (cmd_stats["exploration"] >= CODEX_MAX_EXPLORATION_WITHOUT_IMPL
                                        and cmd_stats["implementation"] == 0):
                                    exploration_loop_detected[0] = True
                            idle_diag["limit"] = (
                                CODEX_COMMAND_IDLE_TIMEOUT
                                if item_type == "command_execution"
                                else CODEX_THINK_IDLE_TIMEOUT
                            )
                            idle_diag["kind"] = item_type
                            # Also send activity notifications for status bar
                            if item_type == "reasoning":
                                self._notify_activity("thinking", item.get("text", "")[:80])
                            elif item_type == "agent_message":
                                self._notify_activity("text", item.get("text", "")[:80])
                            elif item_type in ("mcp_tool_call", "command_execution"):
                                name = item.get("tool", item.get("command", "tool"))[:50]
                                self._notify_activity("tool", name)
                    except json.JSONDecodeError:
                        # Non-JSON line in JSON mode - might be stderr, skip
                        pass

            def _idle_timeout_callback(elapsed: float) -> bool:
                """Decide whether a silent period should be treated as a stall."""
                idle_diag["elapsed"] = elapsed
                # Early exit if exploration loop detected (agent stuck in search/read mode)
                if exploration_loop_detected[0]:
                    return True  # Signal stall to exit streaming loop
                limit = idle_diag.get("limit", CODEX_THINK_IDLE_TIMEOUT)
                return elapsed >= limit

            output, timed_out, idle_stalled = _stream_pty_output(
                self.process,
                self.master_fd,
                process_chunk,
                timeout,
                idle_timeout=CODEX_IDLE_TIMEOUT,
                idle_timeout_callback=_idle_timeout_callback,
            )

            self.current_message = None
            self.process = None
            self.master_fd = None
            event_dt = datetime.fromtimestamp(last_event_info.get("time", time.time()), tz=timezone.utc)
            self.last_event_info = {
                "kind": last_event_info.get("kind") or "unknown",
                "command": last_event_info.get("command") or "",
                "event_time": event_dt.isoformat(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "last_event_age": max(0.0, time.time() - last_event_info.get("time", time.time())),
            }
            # Add command statistics for session analysis
            if cmd_stats["total"] > 0:
                self.last_event_info["cmd_stats"] = {
                    "total": cmd_stats["total"],
                    "exploration": cmd_stats["exploration"],
                    "implementation": cmd_stats["implementation"],
                    "last_commands": cmd_stats["commands"][-10:],  # Last 10 for brevity
                }
            if idle_diag.get("elapsed"):
                self.last_event_info["last_silence"] = {
                    "elapsed": idle_diag["elapsed"],
                    "limit": idle_diag.get("limit"),
                    "kind": idle_diag.get("kind"),
                }

            # Check for exploration loop FIRST (it also sets idle_stalled via callback)
            if exploration_loop_detected[0]:
                self.last_event_info["exploration_loop"] = {
                    "exploration": cmd_stats["exploration"],
                    "implementation": cmd_stats["implementation"],
                    "limit": CODEX_MAX_EXPLORATION_WITHOUT_IMPL,
                    "last_commands": cmd_stats["commands"][-5:],
                }
                # Try recovery by prompting the agent to implement
                if self.thread_id and not _is_recovery:
                    self._notify_activity(
                        "stream",
                        f"\033[33m• Exploration loop detected ({cmd_stats['exploration']} searches, "
                        f"0 implementations), prompting to implement...\033[0m\n"
                    )
                    self.current_message = (
                        "IMPORTANT: You've spent significant time searching and reading without making "
                        "any changes. Please proceed to implement the fix now. If you need to make a "
                        "decision, make your best choice and implement it. Do not continue exploring."
                    )
                    return self.get_response(timeout=timeout, _is_recovery=True)
                # Already tried recovery - fail with diagnostic info
                raise RuntimeError(
                    f"Codex stuck in exploration loop ({cmd_stats['exploration']} exploration commands, "
                    f"0 implementation commands). Agent may be confused about the task."
                )

            # Check for idle stall (no output for a while) - only if not already handled above
            if idle_stalled:
                # Build diagnostic info for logging
                stall_duration = idle_diag.get("elapsed") or (time.time() - last_event_info["time"])
                last_kind = last_event_info["kind"] or "none"
                last_cmd = last_event_info["command"] or ""
                stall_limit = idle_diag.get("limit", CODEX_IDLE_TIMEOUT)
                diag_msg = (
                    f"last_event={last_kind}, stall_duration={stall_duration:.1f}s"
                    + (f", last_cmd={last_cmd}" if last_cmd else "")
                )
                self.last_event_info["stall"] = {
                    "duration": stall_duration,
                    "limit": stall_limit,
                    "kind": last_kind,
                }

                # Single recovery attempt if we have a thread_id and this isn't already a recovery
                if self.thread_id and not _is_recovery:
                    self._notify_activity(
                        "stream",
                        f"\033[33m• API stream stalled ({diag_msg}), attempting resume...\033[0m\n"
                    )
                    self.current_message = (
                        "Continue with the task. If you were running a command, report its result. "
                        "If you had completed the task, provide your final summary."
                    )
                    return self.get_response(timeout=timeout, _is_recovery=True)

                # No thread_id or already tried recovery - fail with diagnostic info
                raise RuntimeError(
                    "Codex stalled waiting for output "
                    f"(~{stall_duration:.0f}s silence, limit {int(stall_limit)}s; {diag_msg})"
                )

            if timed_out:
                self.last_event_info["timeout"] = {
                    "timeout": timeout,
                    "last_event": last_event_info.get("kind"),
                    "command": last_event_info.get("command") or "",
                }
                raise RuntimeError(f"Codex execution timed out ({int(timeout / 60)} minutes)")

            # Check for API errors (model not supported, etc.)
            if api_error[0]:
                raise RuntimeError(f"Codex API error: {api_error[0]}")
            if reconnect_seen[0]:
                raise RuntimeError("Codex connection failed during reconnect attempts")

            # Extract response from JSON events
            response_parts = []
            for event in json_events:
                if event.get("type") == "item.completed":
                    item = event.get("item", {})
                    if item.get("type") == "agent_message":
                        response_parts.append(item.get("text", ""))
                    elif item.get("type") == "reasoning":
                        text = item.get("text", "")
                        if text:
                            response_parts.insert(0, f"*Thinking: {text}*\n\n")

            if response_parts:
                return "".join(response_parts).strip()

            # Fallback to raw output if no JSON events parsed
            output = _strip_ansi_codes(output)
            return output.strip() if output else "No response from Codex"

        except RuntimeError:
            # API errors (model not supported, timeout) - clean up and re-raise
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            raise
        except (FileNotFoundError, PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            raise RuntimeError(
                f"Failed to run Codex: {e}\n\n"
                "Make sure Codex CLI is installed and authenticated.\n"
                "Run 'codex' to authenticate."
            )

    def _process_streaming_chunk(self, chunk: str) -> None:
        """Process a streaming chunk for activity notifications."""
        # Pass through raw chunk with ANSI codes preserved for native terminal look
        if chunk.strip():
            self._notify_activity("stream", chunk)

        # Also parse for structured activity updates (using cleaned version)
        clean_chunk = _strip_ansi_codes(chunk)
        for line in clean_chunk.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            if stripped == "thinking":
                self._notify_activity("thinking", "Reasoning...")
            elif stripped == "codex":
                self._notify_activity("text", "Responding...")
            elif stripped.startswith("exec"):
                self._notify_activity("tool", f"Running: {stripped[5:65].strip()}")
            elif stripped.startswith("**") and stripped.endswith("**"):
                self._notify_activity("text", stripped.strip("*")[:60])

    def stop_session(self) -> None:
        self.current_message = None
        self.thread_id = None  # Clear thread_id to end multi-turn session
        _close_master_fd(self.master_fd)
        self.master_fd = None
        if self.process:
            try:
                # Kill entire process group to stop child processes too
                if os.name == "nt":
                    # On Windows, use taskkill to terminate the process tree
                    # /T kills child processes, /F forces termination
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                            capture_output=True,
                            timeout=5,
                        )
                    except (subprocess.SubprocessError, OSError):
                        pass
                else:
                    # On Unix, kill the process group with SIGTERM then SIGKILL
                    import signal

                    try:
                        pgid = os.getpgid(self.process.pid)
                        os.killpg(pgid, signal.SIGTERM)
                        # Give processes a moment to terminate gracefully
                        time.sleep(0.2)
                        # Force kill any remaining processes in the group
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        # Session is "alive" if we have a thread_id for resuming
        # (even if no process is currently running)
        return self.thread_id is not None or (self.process is not None and self.process.poll() is None)

    def supports_multi_turn(self) -> bool:
        return True


class GeminiCodeAssistProvider(AIProvider):
    """Provider for Gemini Code Assist with multi-turn support.

    Uses the `gemini` command-line interface in "YOLO" mode for
    non-interactive, programmatic calls with PTY for real-time streaming.
    Supports multi-turn via `--resume <session_id>`.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.project_path: str | None = None
        self.system_prompt: str | None = None
        self.current_message: str | None = None
        self.process: object | None = None
        self.master_fd: int | None = None
        self.session_id: str | None = None  # For multi-turn support

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        ok, detail = _ensure_cli_tool("gemini", self._notify_activity)
        if not ok:
            return False

        self.project_path = project_path
        self.system_prompt = system_prompt
        self.cli_path = detail
        return True

    def send_message(self, message: str) -> None:
        # Only prepend system prompt on first message (no session_id yet)
        if self.system_prompt and not self.session_id:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:  # noqa: C901
        import json

        if not self.current_message:
            return ""

        gemini_cli = getattr(self, "cli_path", None) or find_cli_executable("gemini")

        # Build command - use resume if we have a session_id (multi-turn)
        if self.session_id:
            cmd = [
                gemini_cli,
                "-y",
                "--output-format",
                "stream-json",
                "--resume",
                self.session_id,
                self.current_message,
            ]
        else:
            cmd = [gemini_cli, "-y", "--output-format", "stream-json"]
            if self.config.model_name and self.config.model_name != "default":
                cmd.extend(["-m", self.config.model_name])
            cmd.append(self.current_message)

        try:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"

            json_events = []
            response_parts = []

            def handle_chunk(decoded: str) -> None:
                # Stream raw output for live display
                self._notify_activity("stream", decoded)
                # Parse JSON lines
                for line in decoded.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        json_events.append(event)
                        # Extract session_id from init event
                        if event.get("type") == "init" and "session_id" in event:
                            self.session_id = event["session_id"]
                        # Collect response content
                        if event.get("type") == "message" and event.get("role") == "assistant":
                            content = event.get("content", "")
                            if content:
                                response_parts.append(content)
                                self._notify_activity("text", content[:80])
                    except json.JSONDecodeError:
                        # Non-JSON line (warnings, etc.) - just notify
                        if line and len(line) > 10:
                            self._notify_activity("text", line[:80])

            self.process, self.master_fd = _start_pty_process(cmd, cwd=self.project_path, env=env)

            if self.process.stdin:
                self.process.stdin.close()

            output, timed_out, idle_stalled = _stream_pty_output(self.process, self.master_fd, handle_chunk, timeout)

            self.current_message = None
            self.process = None
            self.master_fd = None

            if idle_stalled:
                return f"Error: Gemini execution stalled (no output for {int(timeout)}s)"
            if timed_out:
                return f"Error: Gemini execution timed out ({int(timeout / 60)} minutes)"

            # Return collected response parts if any
            if response_parts:
                return "".join(response_parts).strip()

            # Fallback to raw output
            output = _strip_ansi_codes(output)
            return output.strip() if output else "No response from Gemini"

        except FileNotFoundError:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            return "Failed to run Gemini: command not found\n\nInstall with: npm install -g @google/gemini-cli"
        except (PermissionError, OSError) as exc:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            return f"Failed to run Gemini: {exc}"

    def stop_session(self) -> None:
        self.current_message = None
        self.session_id = None  # Clear session_id to end multi-turn
        _close_master_fd(self.master_fd)
        self.master_fd = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        # Session is "alive" if we have a session_id for resuming
        return self.session_id is not None or (self.process is not None and self.process.poll() is None)

    def supports_multi_turn(self) -> bool:
        return True


class MistralVibeProvider(AIProvider):
    """Provider for Mistral Vibe CLI with multi-turn support.

    Uses the `vibe` command-line interface in programmatic mode (-p)
    with PTY for real-time streaming output.
    Supports multi-turn via `--continue` flag.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None
        self.master_fd: int | None = None
        self.session_active: bool = False  # For multi-turn support

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        ok, detail = _ensure_cli_tool("vibe", self._notify_activity)
        if not ok:
            return False

        self.project_path = project_path
        self.system_prompt = system_prompt
        self.cli_path = detail
        return True

    def send_message(self, message: str) -> None:
        # Only prepend system prompt on first message
        if self.system_prompt and not self.session_active:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:
        if not self.current_message:
            return ""

        vibe_cli = getattr(self, "cli_path", None) or find_cli_executable("vibe")

        # Build command - use --continue if we have an active session
        if self.session_active:
            cmd = [vibe_cli, "-p", self.current_message, "--output", "text", "--continue"]
        else:
            cmd = [vibe_cli, "-p", self.current_message, "--output", "text"]

        try:
            self._notify_activity("text", "Starting Vibe...")

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"

            def handle_chunk(decoded: str) -> None:
                self._notify_activity("stream", decoded)
                for line in decoded.split("\n"):
                    stripped = line.strip()
                    if stripped and len(stripped) > 10:
                        self._notify_activity("text", stripped[:80])

            self.process, self.master_fd = _start_pty_process(cmd, cwd=self.project_path, env=env)

            if self.process.stdin:
                self.process.stdin.close()

            output, timed_out, idle_stalled = _stream_pty_output(self.process, self.master_fd, handle_chunk, timeout)

            self.current_message = None
            self.process = None
            self.master_fd = None

            if idle_stalled:
                return f"Error: Vibe execution stalled (no output for {int(timeout)}s)"
            if timed_out:
                return f"Error: Vibe execution timed out ({int(timeout / 60)} minutes)"

            output = _strip_ansi_codes(output)
            if output.strip():
                # Mark session as active after first successful response
                self.session_active = True
            return output.strip() if output else "No response from Vibe"

        except FileNotFoundError:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            return (
                "Failed to run Vibe: command not found\n\n"
                "Install with: pip install mistral-vibe\n"
                "Then run: vibe --setup"
            )
        except (PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            return f"Failed to run Vibe: {e}"

    def stop_session(self) -> None:
        self.current_message = None
        self.session_active = False  # Clear session state
        _close_master_fd(self.master_fd)
        self.master_fd = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        # Session is "alive" if we have an active session for continuing
        return self.session_active or (self.process is not None and self.process.poll() is None)

    def supports_multi_turn(self) -> bool:
        return True


class MockProvider(AIProvider):
    """Mock provider for testing UI without real API calls.

    This provider simulates realistic coding and verification agent behavior:
    - Generates live streaming output with tool activities
    - Makes actual file changes to BUGS.md in the worktree
    - Returns proper JSON responses
    - Rejects first verification attempt, accepts second
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self._alive = False
        self._messages: list[str] = []
        self._response_queue: list[str] = []
        self._project_path: str | None = None
        self._coding_turn_count = 0
        self._verification_count = 0
        self._is_verification_mode = False

    def queue_response(self, response: str) -> None:
        """Queue a response to be returned by get_response (for unit tests)."""
        self._response_queue.append(response)

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self._alive = True
        self._project_path = project_path
        self._notify_activity("text", "Mock session started")
        return True

    def send_message(self, message: str) -> None:
        self._messages.append(message)
        # Detect if this is a verification prompt
        self._is_verification_mode = "DO NOT modify or create any files" in message

    def _simulate_delay(self, seconds: float = 0.1) -> None:
        """Simulate processing delay."""
        import time
        time.sleep(seconds)

    def _modify_bugs_file(self, marker: str) -> str:
        """Modify BUGS.md in the worktree, creating if needed. Return the path."""
        from pathlib import Path

        if not self._project_path:
            return ""

        bugs_path = Path(self._project_path) / "BUGS.md"

        if bugs_path.exists():
            content = bugs_path.read_text()
            # Add marker at the end
            content = content.rstrip() + f"\n{marker}\n"
        else:
            content = f"# Known Bugs\n\n{marker}\n"

        bugs_path.write_text(content)
        return str(bugs_path)

    def _generate_coding_response(self) -> str:
        """Generate a realistic coding agent response."""
        self._coding_turn_count += 1
        is_followup = self._coding_turn_count > 1

        # Simulate some streaming output
        self._notify_activity("tool", "Read: src/chad/providers.py")
        self._simulate_delay(0.15)
        self._notify_activity("stream", "Analyzing the codebase structure...\n")
        self._simulate_delay(0.1)

        if is_followup:
            self._notify_activity("stream", "Processing follow-up request...\n")
            self._simulate_delay(0.1)
            self._notify_activity("tool", "Grep: searching for relevant code")
            self._simulate_delay(0.15)
            self._notify_activity("stream", "Found the area that needs updating.\n")
            marker = f"<!-- Mock follow-up change {self._coding_turn_count} -->"
        else:
            self._notify_activity("stream", "Understanding the task requirements...\n")
            self._simulate_delay(0.1)
            self._notify_activity("tool", "Glob: **/*.py")
            self._simulate_delay(0.15)
            self._notify_activity("stream", "Located relevant files.\n")
            marker = "<!-- Mock initial change -->"

        # Simulate progress update
        self._simulate_delay(0.1)
        progress_json = (
            '```json\n'
            '{"type": "progress", "summary": "Mock task in progress", '
            f'"location": "BUGS.md - adding test marker"}}\n'
            '```\n'
        )
        self._notify_activity("stream", progress_json)

        # Make the actual file change
        self._simulate_delay(0.1)
        self._notify_activity("tool", "Edit: BUGS.md")
        bugs_path = self._modify_bugs_file(marker)
        self._simulate_delay(0.1)
        self._notify_activity("stream", f"Modified {bugs_path}\n")

        # Simulate running tests
        self._simulate_delay(0.15)
        self._notify_activity("tool", "Bash: ./.venv/bin/python -m pytest tests/ -v --tb=short")
        self._simulate_delay(0.2)
        self._notify_activity("stream", "===== 42 passed in 3.21s =====\n")

        # Simulate linting
        self._notify_activity("tool", "Bash: ./.venv/bin/python -m flake8 src/chad")
        self._simulate_delay(0.1)
        self._notify_activity("stream", "Linting passed.\n")

        # Build the full response
        if is_followup:
            change_desc = f"Applied follow-up change #{self._coding_turn_count} to BUGS.md"
        else:
            change_desc = "Applied initial mock change to BUGS.md"

        response = f"""I've analyzed the codebase and made the requested changes.

## Changes Made

I modified BUGS.md to add a test marker.

## Verification

- ✓ All 42 tests passed
- ✓ Linting clean

```json
{{"change_summary": "{change_desc}"}}
```
"""
        return response

    def _generate_verification_response(self) -> str:
        """Generate a realistic verification agent response."""
        self._verification_count += 1

        # Simulate verification activities
        self._notify_activity("tool", "Read: BUGS.md")
        self._simulate_delay(0.15)
        self._notify_activity("stream", "Reviewing the changes made...\n")
        self._simulate_delay(0.1)
        self._notify_activity("tool", "Bash: git diff")
        self._simulate_delay(0.15)
        self._notify_activity("stream", "Checking diff output...\n")
        self._simulate_delay(0.1)

        # First verification: reject with a reason
        if self._verification_count == 1:
            self._notify_activity("stream", "Found an issue with the changes.\n")
            return """{
  "passed": false,
  "summary": "The mock change marker should include a timestamp for traceability",
  "issues": [
    "Mock change marker does not include timestamp",
    "Consider adding more descriptive content to BUGS.md"
  ]
}"""

        # Subsequent verifications: accept
        self._notify_activity("stream", "Changes look good.\n")
        return """{
  "passed": true,
  "summary": "Verified that BUGS.md was updated correctly with the mock marker. All tests pass and linting is clean."
}"""

    def get_response(self, timeout: float = 30.0) -> str:
        self._simulate_delay(0.1)

        # If there's a queued response (for unit tests), use it
        if self._response_queue:
            return self._response_queue.pop(0)

        # Check for breakdown requests (for compatibility)
        last_msg = self._messages[-1] if self._messages else ""
        if "subtask" in last_msg.lower() or "break" in last_msg.lower():
            return '{"subtasks": [{"id": "1", "description": "Mock task", "dependencies": []}]}'

        # Generate appropriate response based on mode
        if self._is_verification_mode:
            return self._generate_verification_response()
        else:
            return self._generate_coding_response()

    def stop_session(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def supports_multi_turn(self) -> bool:
        return True


def create_provider(config: ModelConfig) -> AIProvider:
    """Factory function to create the appropriate provider.

    Args:
        config: Model configuration

    Returns:
        Appropriate provider instance

    Raises:
        ValueError: If provider is not supported
    """
    if config.provider == "anthropic":
        return ClaudeCodeProvider(config)
    elif config.provider == "openai":
        return OpenAICodexProvider(config)
    elif config.provider == "gemini":
        return GeminiCodeAssistProvider(config)
    elif config.provider == "mistral":
        return MistralVibeProvider(config)
    elif config.provider == "mock":
        return MockProvider(config)
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")

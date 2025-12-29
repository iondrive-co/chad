"""Generic AI provider interface for supporting multiple models."""

import glob
import os
import pty
import re
import select
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .installer import AIToolInstaller
from .installer import DEFAULT_TOOLS_DIR
from .mcp_config import ensure_global_mcp_config


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
    home = os.path.expanduser('~')
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

    lines = raw_output.split('\n')
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
        if line.startswith('OpenAI Codex') or line.startswith('--------'):
            i += 1
            continue

        # Skip metadata lines
        if any(stripped.startswith(prefix) for prefix in [
            'workdir:', 'model:', 'provider:', 'approval:', 'sandbox:',
            'reasoning effort:', 'reasoning summaries:', 'session id:',
            'mcp startup:', 'tokens used'
        ]):
            i += 1
            continue

        # Skip standalone numbers (token counts) - including comma-separated like "4,481"
        if stripped.replace(',', '').isdigit() and len(stripped) <= 10:
            i += 1
            continue

        # Skip 'user' marker lines
        if stripped == 'user':
            i += 1
            continue

        # Handle exec blocks - skip until next known marker
        if stripped.startswith('exec'):
            in_exec = True
            # Save current thinking section before exec
            if in_thinking and current_section:
                result_parts.append(('thinking', '\n'.join(current_section)))
                current_section = []
            in_thinking = False
            i += 1
            continue

        # End exec block on next marker
        if in_exec:
            if stripped in ('thinking', 'codex'):
                in_exec = False
                # Fall through to handle the marker
            else:
                i += 1
                continue

        # Capture thinking sections
        if stripped == 'thinking':
            # Save previous section if any
            if current_section:
                section_type = 'response' if in_response else 'thinking'
                result_parts.append((section_type, '\n'.join(current_section)))
            in_thinking = True
            in_response = False
            current_section = []
            i += 1
            continue

        # Capture codex response (final answer)
        if stripped == 'codex':
            # Save previous section if any
            if current_section:
                section_type = 'response' if in_response else 'thinking'
                result_parts.append((section_type, '\n'.join(current_section)))
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
        section_type = 'response' if in_response else 'thinking'
        result_parts.append((section_type, '\n'.join(current_section)))

    # Format output - consolidate thinking, preserve response formatting
    thinking_parts = []
    response_parts = []

    for section_type, content in result_parts:
        if section_type == 'thinking':
            # Collect all thinking for a compact summary
            thinking_parts.append(content.replace('\n', ' ').strip())
        else:
            response_parts.append(content)

    formatted = []

    # Add consolidated thinking as a compact italic block
    if thinking_parts:
        # Show last few thinking steps, not all
        recent_thoughts = thinking_parts[-5:] if len(thinking_parts) > 5 else thinking_parts
        thinking_summary = ' → '.join(recent_thoughts)
        formatted.append(f"*Thinking: {thinking_summary}*")

    # Add response content with preserved formatting
    for content in response_parts:
        # Clean up excessive blank lines but preserve structure
        lines = content.split('\n')
        cleaned_lines = []
        for i, line in enumerate(lines):
            if line.strip() or (i > 0 and lines[i - 1].strip()):
                cleaned_lines.append(line)
        cleaned = '\n'.join(cleaned_lines)
        if cleaned.strip():
            formatted.append(cleaned.strip())

    return '\n\n'.join(formatted) if formatted else raw_output


def extract_final_codex_response(raw_output: str | None) -> str:
    """Extract only the final 'codex' response from Codex output.

    This is useful for isolating the final instruction text
    without all the context it was given.
    """
    if not raw_output:
        return ""

    lines = raw_output.split('\n')
    last_codex_index = -1

    # Find the last 'codex' marker
    for i, line in enumerate(lines):
        if line.strip() == 'codex':
            last_codex_index = i

    if last_codex_index == -1:
        return raw_output

    # Collect everything after the last 'codex' marker until we hit a marker or end
    final_response = []
    for i in range(last_codex_index + 1, len(lines)):
        stripped = lines[i].strip()

        # Stop at next section marker
        if stripped in ('thinking', 'codex', 'exec'):
            break

        # Skip token counts and metadata - including comma-separated like "4,481"
        if stripped.startswith('tokens used') or (stripped.replace(',', '').isdigit() and len(stripped) <= 10):
            continue

        if stripped:
            final_response.append(stripped)

    return '\n'.join(final_response) if final_response else raw_output


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


_ANSI_ESCAPE = re.compile(r'[\x1b\u241b]\[[0-9;]*[a-zA-Z]?')
CLI_INSTALLER = AIToolInstaller()


def _ensure_cli_tool(tool_key: str, activity_cb: ActivityCallback = None) -> tuple[bool, str]:
    """Ensure a provider CLI is installed; optionally notify activity on failure."""
    ok, detail = CLI_INSTALLER.ensure_tool(tool_key)
    if not ok and activity_cb:
        activity_cb('text', detail)
    return ok, detail


def _strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from streamed output.

    Handles both actual escape char (0x1b) and Unicode escape symbol (␛ U+241B).
    Also removes partial/incomplete escape sequences.
    """
    text = _ANSI_ESCAPE.sub('', text)
    # Also remove incomplete escape sequences that got split across chunks
    text = re.sub(r'[\x1b\u241b]\[?[0-9;]*$', '', text)
    return text


def _close_master_fd(master_fd: int | None) -> None:
    """Safely close a master PTY file descriptor."""
    if master_fd is None:
        return
    try:
        os.close(master_fd)
    except OSError:
        pass


def _start_pty_process(cmd: list[str], cwd: str | None = None, env: dict | None = None) -> tuple[subprocess.Popen, int]:
    """Start a subprocess with a PTY attached for streaming output."""
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=cwd,
        env=env,
        close_fds=True,
        start_new_session=True  # Run in own process group for clean termination
    )
    os.close(slave_fd)
    return process, master_fd


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
            decoded = chunk.decode('utf-8', errors='replace')
            output_chunks.append(decoded)
            if on_chunk:
                on_chunk(decoded)
    except OSError:
        return


def _stream_pty_output(
    process: subprocess.Popen,
    master_fd: int,
    on_chunk: Callable[[str], None] | None,
    timeout: float
) -> tuple[str, bool]:
    """Stream output from a PTY-backed process until completion or timeout."""
    output_chunks: list[str] = []
    start_time = time.time()

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
                    decoded = chunk.decode('utf-8', errors='replace')
                    output_chunks.append(decoded)
                    if on_chunk:
                        on_chunk(decoded)
                    start_time = time.time()

        timed_out = process.poll() is None
        if timed_out:
            process.kill()
            process.wait()
        else:
            try:
                process.wait(timeout=0.1)
            except Exception:
                pass
    finally:
        _close_master_fd(master_fd)

    return ''.join(output_chunks), timed_out


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
                settings = json.loads(settings_path.read_text())
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
            detail or find_cli_executable('claude'),
            '-p',
            '--input-format', 'stream-json',
            '--output-format', 'stream-json',
            '--permission-mode', 'bypassPermissions',
            '--verbose'
        ]

        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['--model', self.config.model_name])

        try:
            env = self._get_env()
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project_path,
                bufsize=1,
                env=env
            )

            if system_prompt:
                self.send_message(system_prompt)

            return True
        except (FileNotFoundError, PermissionError, OSError) as e:
            self._notify_activity('text', f"Failed to start Claude: {e}")
            return False

    def send_message(self, message: str) -> None:
        import json

        if not self.process or not self.process.stdin:
            return

        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": message}]
            }
        }

        try:
            self.process.stdin.write(json.dumps(msg) + '\n')
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

        while time.time() - start_time < timeout:
            if self.process.poll() is not None:
                break

            ready, _, _ = select.select([self.process.stdout], [], [], idle_timeout)

            if not ready:
                if result_text is not None:
                    break
                continue

            line = self.process.stdout.readline()
            if not line:
                if result_text is not None:
                    break
                continue

            try:
                msg = json.loads(line.strip())

                if msg.get('type') == 'assistant':
                    content = msg.get('message', {}).get('content', [])
                    for item in content:
                        if item.get('type') == 'text':
                            text = item.get('text', '')
                            self.accumulated_text.append(text)
                            # Stream the text to UI
                            self._notify_activity('stream', text + '\n')
                            self._notify_activity('text', text[:100])
                        elif item.get('type') == 'tool_use':
                            tool_name = item.get('name', 'unknown')
                            tool_input = item.get('input', {})
                            if tool_name in ('Read', 'Edit', 'Write'):
                                detail = tool_input.get('file_path', '')
                            elif tool_name == 'Bash':
                                detail = tool_input.get('command', '')[:50]
                            elif tool_name in ('Glob', 'Grep'):
                                detail = tool_input.get('pattern', '')
                            else:
                                detail = ''
                            self._notify_activity('tool', f"{tool_name}: {detail}")

                if msg.get('type') == 'result':
                    result_text = msg.get('result', '')
                    break

                start_time = time.time()
            except json.JSONDecodeError:
                continue

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

    def _get_isolated_home(self) -> str:
        """Get the isolated HOME directory for this account."""
        from pathlib import Path
        if self.config.account_name:
            return str(Path.home() / ".chad" / "codex-homes" / self.config.account_name)
        return str(Path.home())

    def _get_env(self) -> dict:
        """Get environment with isolated HOME for this account."""
        env = os.environ.copy()
        env['HOME'] = self._get_isolated_home()
        env['PYTHONUNBUFFERED'] = '1'
        env['TERM'] = 'xterm-256color'
        return env

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        ok, detail = _ensure_cli_tool("codex", self._notify_activity)
        if not ok:
            return False

        ensure_global_mcp_config(home=Path(self._get_isolated_home()))
        self.project_path = project_path
        self.system_prompt = system_prompt
        self.cli_path = detail
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:  # noqa: C901
        import json
        if not self.current_message:
            return ""

        codex_cli = getattr(self, "cli_path", None) or find_cli_executable('codex')

        # Build command - use resume if we have a thread_id (multi-turn)
        # Flags like --json must come BEFORE the resume subcommand
        if self.thread_id:
            cmd = [
                codex_cli, 'exec',
                '--json',  # Must be before 'resume' subcommand
                '-c', 'sandbox_mode="workspace-write"',  # Match --full-auto sandbox mode
                '-c', 'approval_policy="on-request"',   # Match --full-auto approval policy
                '-c', 'network_access="enabled"',
                'resume', self.thread_id,
                '-',  # Read prompt from stdin
            ]
        else:
            cmd = [
                codex_cli, 'exec',
                '--full-auto',
                '--skip-git-repo-check',
                '--json',
                '-c', 'network_access="enabled"',
                '-C', self.project_path,
                '-',  # Read from stdin
            ]

            if self.config.model_name and self.config.model_name != 'default':
                cmd.extend(['-m', self.config.model_name])

            if self.config.reasoning_effort and self.config.reasoning_effort != 'default':
                cmd.extend(['-c', f'model_reasoning_effort="{self.config.reasoning_effort}"'])

        try:
            env = self._get_env()
            self.process, self.master_fd = _start_pty_process(cmd, cwd=self.project_path, env=env)

            if self.process.stdin:
                self.process.stdin.write(self.current_message.encode())
                self.process.stdin.close()

            # Both initial and resume use JSON output
            json_events = []

            def format_json_event_as_text(event: dict) -> str | None:
                """Convert a JSON event to human-readable text for streaming."""
                event_type = event.get('type', '')

                if event_type == 'item.completed':
                    item = event.get('item', {})
                    item_type = item.get('type', '')

                    if item_type == 'reasoning':
                        text = item.get('text', '')
                        if text:
                            return f"*{text}*\n"
                    elif item_type == 'agent_message':
                        return item.get('text', '') + '\n'
                    elif item_type == 'mcp_tool_call':
                        tool = item.get('tool', 'tool')
                        result = item.get('result', {})
                        if result:
                            content = result.get('content', [])
                            if content and isinstance(content, list):
                                text = content[0].get('text', '')[:200] if content else ''
                                return f"[{tool}]: {text}...\n" if len(text) >= 200 else f"[{tool}]: {text}\n"
                        return f"[{tool}]\n"
                    elif item_type == 'command_execution':
                        cmd = item.get('command', '')[:60]
                        output = item.get('aggregated_output', '')
                        # Show command and abbreviated output
                        lines = output.strip().split('\n')
                        preview = '\n'.join(lines[:3]) if lines else ''
                        if len(lines) > 3:
                            preview += f'\n... ({len(lines) - 3} more lines)'
                        return f"$ {cmd}\n{preview}\n" if preview else f"$ {cmd}\n"

                return None

            def process_chunk(chunk: str) -> None:
                # Parse JSON events and convert to human-readable text
                for line in chunk.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        json_events.append(event)

                        # Extract thread_id from first event
                        if event.get('type') == 'thread.started' and 'thread_id' in event:
                            self.thread_id = event['thread_id']

                        # Convert to human-readable and stream
                        readable = format_json_event_as_text(event)
                        if readable:
                            self._notify_activity('stream', readable)

                        # Also send activity notifications for status bar
                        if event.get('type') == 'item.completed':
                            item = event.get('item', {})
                            if item.get('type') == 'reasoning':
                                self._notify_activity('thinking', item.get('text', '')[:80])
                            elif item.get('type') == 'agent_message':
                                self._notify_activity('text', item.get('text', '')[:80])
                            elif item.get('type') in ('mcp_tool_call', 'command_execution'):
                                name = item.get('tool', item.get('command', 'tool'))[:50]
                                self._notify_activity('tool', name)
                    except json.JSONDecodeError:
                        # Non-JSON line in JSON mode - might be stderr, skip
                        pass

            output, timed_out = _stream_pty_output(
                self.process,
                self.master_fd,
                process_chunk,
                timeout
            )

            self.current_message = None
            self.process = None
            self.master_fd = None

            if timed_out:
                return f"Error: Codex execution timed out ({int(timeout / 60)} minutes)"

            # Extract response from JSON events
            response_parts = []
            for event in json_events:
                if event.get('type') == 'item.completed':
                    item = event.get('item', {})
                    if item.get('type') == 'agent_message':
                        response_parts.append(item.get('text', ''))
                    elif item.get('type') == 'reasoning':
                        text = item.get('text', '')
                        if text:
                            response_parts.insert(0, f"*Thinking: {text}*\n\n")

            if response_parts:
                return ''.join(response_parts).strip()

            # Fallback to raw output if no JSON events parsed
            output = _strip_ansi_codes(output)
            return output.strip() if output else "No response from Codex"

        except (FileNotFoundError, PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            _close_master_fd(self.master_fd)
            self.master_fd = None
            return (
                f"Failed to run Codex: {e}\n\n"
                "Make sure Codex CLI is installed and authenticated.\n"
                "Run 'codex' to authenticate."
            )

    def _process_streaming_chunk(self, chunk: str) -> None:
        """Process a streaming chunk for activity notifications."""
        # Pass through raw chunk with ANSI codes preserved for native terminal look
        if chunk.strip():
            self._notify_activity('stream', chunk)

        # Also parse for structured activity updates (using cleaned version)
        clean_chunk = _strip_ansi_codes(chunk)
        for line in clean_chunk.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue

            if stripped == 'thinking':
                self._notify_activity('thinking', 'Reasoning...')
            elif stripped == 'codex':
                self._notify_activity('text', 'Responding...')
            elif stripped.startswith('exec'):
                self._notify_activity('tool', f"Running: {stripped[5:65].strip()}")
            elif stripped.startswith('**') and stripped.endswith('**'):
                self._notify_activity('text', stripped.strip('*')[:60])

    def stop_session(self) -> None:
        self.current_message = None
        self.thread_id = None  # Clear thread_id to end multi-turn session
        _close_master_fd(self.master_fd)
        self.master_fd = None
        if self.process:
            try:
                # Kill entire process group to stop child processes too
                import signal
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
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

        gemini_cli = getattr(self, "cli_path", None) or find_cli_executable('gemini')

        # Build command - use resume if we have a session_id (multi-turn)
        if self.session_id:
            cmd = [gemini_cli, '-y', '--output-format', 'stream-json',
                   '--resume', self.session_id, self.current_message]
        else:
            cmd = [gemini_cli, '-y', '--output-format', 'stream-json']
            if self.config.model_name and self.config.model_name != 'default':
                cmd.extend(['-m', self.config.model_name])
            cmd.append(self.current_message)

        try:
            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'

            json_events = []
            response_parts = []

            def handle_chunk(decoded: str) -> None:
                # Stream raw output for live display
                self._notify_activity('stream', decoded)
                # Parse JSON lines
                for line in decoded.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        json_events.append(event)
                        # Extract session_id from init event
                        if event.get('type') == 'init' and 'session_id' in event:
                            self.session_id = event['session_id']
                        # Collect response content
                        if event.get('type') == 'message' and event.get('role') == 'assistant':
                            content = event.get('content', '')
                            if content:
                                response_parts.append(content)
                                self._notify_activity('text', content[:80])
                    except json.JSONDecodeError:
                        # Non-JSON line (warnings, etc.) - just notify
                        if line and len(line) > 10:
                            self._notify_activity('text', line[:80])

            self.process, self.master_fd = _start_pty_process(
                cmd,
                cwd=self.project_path,
                env=env
            )

            if self.process.stdin:
                self.process.stdin.close()

            output, timed_out = _stream_pty_output(
                self.process,
                self.master_fd,
                handle_chunk,
                timeout
            )

            self.current_message = None
            self.process = None
            self.master_fd = None

            if timed_out:
                return f"Error: Gemini execution timed out ({int(timeout / 60)} minutes)"

            # Return collected response parts if any
            if response_parts:
                return ''.join(response_parts).strip()

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

        vibe_cli = getattr(self, "cli_path", None) or find_cli_executable('vibe')

        # Build command - use --continue if we have an active session
        if self.session_active:
            cmd = [vibe_cli, '-p', self.current_message, '--output', 'text', '--continue']
        else:
            cmd = [vibe_cli, '-p', self.current_message, '--output', 'text']

        try:
            self._notify_activity('text', 'Starting Vibe...')

            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'

            def handle_chunk(decoded: str) -> None:
                self._notify_activity('stream', decoded)
                for line in decoded.split('\n'):
                    stripped = line.strip()
                    if stripped and len(stripped) > 10:
                        self._notify_activity('text', stripped[:80])

            self.process, self.master_fd = _start_pty_process(
                cmd,
                cwd=self.project_path,
                env=env
            )

            if self.process.stdin:
                self.process.stdin.close()

            output, timed_out = _stream_pty_output(
                self.process,
                self.master_fd,
                handle_chunk,
                timeout
            )

            self.current_message = None
            self.process = None
            self.master_fd = None

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
    """Mock provider for testing. Returns predictable responses."""

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self._alive = False
        self._messages = []
        self._response_queue = []

    def queue_response(self, response: str) -> None:
        """Queue a response to be returned by get_response."""
        self._response_queue.append(response)

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self._alive = True
        self._notify_activity('text', 'Mock session started')
        return True

    def send_message(self, message: str) -> None:
        self._messages.append(message)
        self._notify_activity('tool', 'MockTool: processing')

    def get_response(self, timeout: float = 30.0) -> str:
        import time
        time.sleep(0.1)  # Simulate some processing
        self._notify_activity('text', 'Mock response ready')

        if self._response_queue:
            return self._response_queue.pop(0)

        # Default response for breakdown requests
        last_msg = self._messages[-1] if self._messages else ""
        if "subtask" in last_msg.lower() or "break" in last_msg.lower():
            return '{"subtasks": [{"id": "1", "description": "Mock task", "dependencies": []}]}'

        # Default response for other requests
        return "Mock response: Task completed successfully."

    def stop_session(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def supports_multi_turn(self) -> bool:
        return True  # Mock provider supports multi-turn for testing


def create_provider(config: ModelConfig) -> AIProvider:
    """Factory function to create the appropriate provider.

    Args:
        config: Model configuration

    Returns:
        Appropriate provider instance

    Raises:
        ValueError: If provider is not supported
    """
    if config.provider == 'anthropic':
        return ClaudeCodeProvider(config)
    elif config.provider == 'openai':
        return OpenAICodexProvider(config)
    elif config.provider == 'gemini':
        return GeminiCodeAssistProvider(config)
    elif config.provider == 'mistral':
        return MistralVibeProvider(config)
    elif config.provider == 'mock':
        return MockProvider(config)
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")

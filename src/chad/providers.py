"""Generic AI provider interface for supporting multiple models."""

import os
import pty
import select
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


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
        if in_thinking or in_response:
            if stripped:
                current_section.append(stripped)

        i += 1

    # Add final section
    if current_section:
        section_type = 'response' if in_response else 'thinking'
        result_parts.append((section_type, '\n'.join(current_section)))

    # Format output
    formatted = []
    for section_type, content in result_parts:
        if section_type == 'thinking':
            formatted.append(f"*Thinking: {content}*")
        else:
            formatted.append(content)

    return '\n\n'.join(formatted) if formatted else raw_output


def extract_final_codex_response(raw_output: str | None) -> str:
    """Extract only the final 'codex' response from Codex output.

    This is useful for getting just the management AI's instruction
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


# Callback type for activity updates: (activity_type, detail)
# activity_type: 'tool', 'thinking', 'text', 'stream' (for raw streaming chunks)
ActivityCallback = Callable[[str, str], None] | None


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


class ClaudeCodeProvider(AIProvider):
    """Provider for Anthropic Claude Code CLI.

    Uses streaming JSON input/output for multi-turn conversations.
    See: https://docs.anthropic.com/en/docs/claude-code/headless
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.accumulated_text: list[str] = []

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        import subprocess

        self.project_path = project_path

        cmd = [
            'claude',
            '-p',
            '--input-format', 'stream-json',
            '--output-format', 'stream-json',
            '--permission-mode', 'bypassPermissions',
            '--verbose'
        ]

        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['--model', self.config.model_name])

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project_path,
                bufsize=1
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
                            self._notify_activity('stream', f"[Tool: {tool_name}]\n")

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


class OpenAICodexProvider(AIProvider):
    """Provider for OpenAI Codex CLI.

    Uses browser-based authentication like Claude Code.
    Run 'codex' to authenticate via browser if not already logged in.
    Uses 'codex exec' for non-interactive execution with PTY for real-time streaming.

    Each account gets an isolated HOME directory to support multiple accounts.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None
        self.master_fd: int | None = None

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
        self.project_path = project_path
        self.system_prompt = system_prompt
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:  # noqa: C901
        import subprocess
        import time

        if not self.current_message:
            return ""

        cmd = [
            'codex',
            'exec',
            '--full-auto',
            '--skip-git-repo-check',
            '-C', self.project_path,
            '-',  # Read from stdin
        ]

        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['-m', self.config.model_name])

        try:
            env = self._get_env()

            # Use PTY for real-time unbuffered output
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True
            )

            os.close(slave_fd)

            # Write input and close stdin
            if self.process.stdin:
                self.process.stdin.write(self.current_message.encode())
                self.process.stdin.close()

            # Read output in real-time using PTY
            output_chunks = []
            start_time = time.time()

            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
                    # Process finished, read any remaining output
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
                            self._process_streaming_chunk(decoded)
                    except OSError:
                        pass
                    break

                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        chunk = os.read(master_fd, 4096)
                        if chunk:
                            decoded = chunk.decode('utf-8', errors='replace')
                            output_chunks.append(decoded)
                            self._process_streaming_chunk(decoded)
                            # Keep extending timeout while we're receiving data
                            start_time = time.time()
                except OSError:
                    break

            os.close(master_fd)
            self.master_fd = None

            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
                self.current_message = None
                self.process = None
                return f"Error: Codex execution timed out ({int(timeout / 60)} minutes)"

            self.current_message = None
            self.process = None

            output = ''.join(output_chunks)
            # Strip ANSI escape codes for clean output
            import re
            output = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', output)
            return output.strip() if output else "No response from Codex"

        except (FileNotFoundError, PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            if self.master_fd:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            return (
                f"Failed to run Codex: {e}\n\n"
                "Make sure Codex CLI is installed and authenticated.\n"
                "Run 'codex' to authenticate."
            )

    def _process_streaming_chunk(self, chunk: str) -> None:
        """Process a streaming chunk for activity notifications."""
        # Send raw stream to UI for live display
        self._notify_activity('stream', chunk)

        # Also parse for structured activity updates
        for line in chunk.split('\n'):
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
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        return self.process is None or self.process.poll() is None


class GeminiCodeAssistProvider(AIProvider):
    """Provider for Gemini Code Assist (one-shot per prompt).

    Uses the `gemini` command-line interface in "YOLO" mode for
    non-interactive, programmatic calls with PTY for real-time streaming.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.project_path: str | None = None
        self.system_prompt: str | None = None
        self.current_message: str | None = None
        self.process: object | None = None
        self.master_fd: int | None = None

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self.project_path = project_path
        self.system_prompt = system_prompt
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:
        import subprocess
        import time
        import re

        if not self.current_message:
            return ""

        cmd = ['gemini', '-y', '--output-format', 'text']

        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['-m', self.config.model_name])

        cmd.extend(['-p', self.current_message])

        try:
            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'

            # Use PTY for real-time streaming
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.project_path,
                env=env,
                close_fds=True
            )

            os.close(slave_fd)

            if self.process.stdin:
                self.process.stdin.close()

            output_chunks = []
            start_time = time.time()

            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
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
                            self._notify_activity('stream', decoded)
                    except OSError:
                        pass
                    break

                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        chunk = os.read(master_fd, 4096)
                        if chunk:
                            decoded = chunk.decode('utf-8', errors='replace')
                            output_chunks.append(decoded)
                            self._notify_activity('stream', decoded)
                            # Parse for activity updates
                            for line in decoded.split('\n'):
                                stripped = line.strip()
                                if stripped and len(stripped) > 10:
                                    self._notify_activity('text', stripped[:80])
                            start_time = time.time()
                except OSError:
                    break

            os.close(master_fd)
            self.master_fd = None

            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
                self.current_message = None
                self.process = None
                return f"Error: Gemini execution timed out ({int(timeout / 60)} minutes)"

            self.current_message = None
            self.process = None

            output = ''.join(output_chunks)
            output = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', output)
            return output.strip() if output else "No response from Gemini"

        except FileNotFoundError:
            self.current_message = None
            self.process = None
            if self.master_fd:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            return "Failed to run Gemini: command not found\n\nInstall with: npm install -g @google/gemini-cli"
        except (PermissionError, OSError) as exc:
            self.current_message = None
            self.process = None
            if self.master_fd:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            return f"Failed to run Gemini: {exc}"

    def stop_session(self) -> None:
        self.current_message = None
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        return self.process is None or self.process.poll() is None


class MistralVibeProvider(AIProvider):
    """Provider for Mistral Vibe CLI.

    Uses the `vibe` command-line interface in programmatic mode (-p)
    with PTY for real-time streaming output.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None
        self.master_fd: int | None = None

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self.project_path = project_path
        self.system_prompt = system_prompt
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:
        import subprocess
        import time
        import re

        if not self.current_message:
            return ""

        cmd = [
            'vibe',
            '-p', self.current_message,
            '--output', 'text',
        ]

        try:
            self._notify_activity('text', 'Starting Vibe...')

            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'

            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.project_path,
                env=env,
                close_fds=True
            )

            os.close(slave_fd)

            if self.process.stdin:
                self.process.stdin.close()

            output_chunks = []
            start_time = time.time()

            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
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
                            self._notify_activity('stream', decoded)
                    except OSError:
                        pass
                    break

                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        chunk = os.read(master_fd, 4096)
                        if chunk:
                            decoded = chunk.decode('utf-8', errors='replace')
                            output_chunks.append(decoded)
                            self._notify_activity('stream', decoded)
                            for line in decoded.split('\n'):
                                stripped = line.strip()
                                if stripped and len(stripped) > 10:
                                    self._notify_activity('text', stripped[:80])
                            start_time = time.time()
                except OSError:
                    break

            os.close(master_fd)
            self.master_fd = None

            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
                self.current_message = None
                self.process = None
                return f"Error: Vibe execution timed out ({int(timeout / 60)} minutes)"

            self.current_message = None
            self.process = None

            output = ''.join(output_chunks)
            output = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', output)
            return output.strip() if output else "No response from Vibe"

        except FileNotFoundError:
            self.current_message = None
            self.process = None
            if self.master_fd:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            return (
                "Failed to run Vibe: command not found\n\n"
                "Install with: pip install mistral-vibe\n"
                "Then run: vibe --setup"
            )
        except (PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            if self.master_fd:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            return f"Failed to run Vibe: {e}"

    def stop_session(self) -> None:
        self.current_message = None
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        return self.process is None or self.process.poll() is None


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
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")

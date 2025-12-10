"""Generic AI provider interface for supporting multiple models."""

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


def parse_codex_output(raw_output: str | None) -> str:
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


from typing import Callable


@dataclass
class ModelConfig:
    """Configuration for an AI model."""

    provider: str  # 'anthropic', 'openai', etc.
    model_name: str  # 'claude-3-5-sonnet-20241022', 'gpt-4', etc.
    account_name: str | None = None  # Account identifier (not an API key)
    base_url: str | None = None


# Callback type for activity updates: (activity_type, detail)
# activity_type: 'tool', 'thinking', 'text'
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
    def get_response(self, timeout: float = 30.0) -> str:
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

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        import subprocess
        import json

        self.project_path = project_path

        # Use streaming JSON I/O mode for multi-turn conversations
        # This allows programmatic usage without interactive prompts
        cmd = [
            'claude',
            '-p',  # Print mode (non-interactive)
            '--input-format', 'stream-json',
            '--output-format', 'stream-json',
            '--permission-mode', 'bypassPermissions',  # Skip all permission prompts
            '--verbose'  # Required for stream-json output
        ]

        # Add model if specified and not default
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
                bufsize=1  # Line buffered
            )

            # Send initial message if provided
            if system_prompt:
                self.send_message(system_prompt)

            return True
        except (FileNotFoundError, PermissionError, OSError) as e:
            print(f"Failed to start Claude: {e}")
            return False

    def send_message(self, message: str) -> None:
        import json

        if not self.process or not self.process.stdin:
            return

        # Format as streaming JSON message
        # https://docs.anthropic.com/en/docs/claude-code/headless#streaming-json-input
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
        import select
        import json
        import sys

        if not self.process or not self.process.stdout:
            return ""

        result_text = None
        start_time = time.time()
        idle_timeout = 2.0

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

                # Print assistant messages in real-time for visibility
                if msg.get('type') == 'assistant':
                    content = msg.get('message', {}).get('content', [])
                    for item in content:
                        if item.get('type') == 'text':
                            text = item.get('text', '')
                            print(text, flush=True)
                            self._notify_activity('text', text[:100])
                        elif item.get('type') == 'tool_use':
                            tool_name = item.get('name', 'unknown')
                            tool_input = item.get('input', {})
                            # Extract meaningful detail from tool input
                            if tool_name in ('Read', 'Edit', 'Write'):
                                detail = tool_input.get('file_path', '')
                            elif tool_name == 'Bash':
                                detail = tool_input.get('command', '')[:50]
                            elif tool_name in ('Glob', 'Grep'):
                                detail = tool_input.get('pattern', '')
                            else:
                                detail = ''
                            print(f"[Using tool: {tool_name}]", flush=True)
                            self._notify_activity('tool', f"{tool_name}: {detail}")

                # The 'result' message contains the final response text
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
    Uses 'codex exec' for non-interactive execution.

    Each account gets an isolated HOME directory to support multiple accounts.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None

    def _get_isolated_home(self) -> str:
        """Get the isolated HOME directory for this account."""
        from pathlib import Path
        if self.config.account_name:
            return str(Path.home() / ".chad" / "codex-homes" / self.config.account_name)
        return str(Path.home())

    def _get_env(self) -> dict:
        """Get environment with isolated HOME for this account."""
        import os
        env = os.environ.copy()
        env['HOME'] = self._get_isolated_home()
        return env

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self.project_path = project_path
        self.system_prompt = system_prompt  # Store for prepending to each message
        return True

    def send_message(self, message: str) -> None:
        # Prepend system prompt to each message since exec mode is stateless
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:
        import subprocess
        import time
        import threading
        import os

        if not self.current_message:
            return ""

        # Use 'codex exec' for non-interactive execution
        cmd = [
            'codex',
            'exec',
            '--full-auto',  # Automatic execution without approval prompts
            '--skip-git-repo-check',  # Allow execution in non-git directories
            '-C', self.project_path,  # Set working directory
        ]

        # Add model if specified and not default
        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['-m', self.config.model_name])

        # Send the prompt via stdin to avoid OS argument length limits
        cmd.append('-')

        try:
            # Use unbuffered output by setting PYTHONUNBUFFERED and using os.pipe
            env = self._get_env()
            env['PYTHONUNBUFFERED'] = '1'

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=0  # Unbuffered
            )

            # Write input and close stdin
            if self.process.stdin:
                self.process.stdin.write(self.current_message)
                self.process.stdin.close()

            # Read output in a separate thread to avoid blocking
            output_lines = []
            read_complete = threading.Event()

            def read_output():
                import sys
                try:
                    if self.process and self.process.stdout:
                        for line in iter(self.process.stdout.readline, ''):
                            if not line:
                                break
                            output_lines.append(line)
                            stripped = line.strip()

                            # Debug: print each line we receive
                            print(f"[Codex] {stripped}", file=sys.stderr, flush=True)

                            # Parse Codex output for activity updates
                            if stripped == 'thinking':
                                self._notify_activity('text', 'Thinking...')
                            elif stripped.startswith('**') and stripped.endswith('**'):
                                self._notify_activity('text', stripped.strip('*')[:60])
                            elif stripped == 'codex':
                                self._notify_activity('text', 'Responding...')
                            elif stripped.startswith('exec'):
                                self._notify_activity('tool', f"exec: {stripped[5:65]}")
                            elif stripped == 'user':
                                self._notify_activity('text', 'Processing input...')
                            elif stripped and not stripped.replace(',', '').isdigit() and stripped not in ('mcp startup: no servers', 'tokens used') and not stripped.startswith('--------') and not stripped.startswith('OpenAI Codex') and not stripped.startswith('workdir:') and not stripped.startswith('model:') and not stripped.startswith('provider:') and not stripped.startswith('approval:') and not stripped.startswith('sandbox:') and not stripped.startswith('reasoning') and not stripped.startswith('session id:'):
                                if len(stripped) > 10:
                                    self._notify_activity('text', stripped[:80])
                except Exception as e:
                    print(f"[Codex reader error: {e}]", file=sys.stderr, flush=True)
                finally:
                    read_complete.set()

            reader_thread = threading.Thread(target=read_output, daemon=True)
            reader_thread.start()

            # Wait for process to complete or timeout
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
                    break
                time.sleep(0.1)

            # Check for timeout
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
                self.current_message = None
                self.process = None
                timeout_mins = int(timeout / 60)
                return f"Error: Codex execution timed out ({timeout_mins} minutes)"

            # Wait for reader thread to finish (with short timeout)
            read_complete.wait(timeout=2.0)

            # Read any stderr
            stderr = self.process.stderr.read() if self.process.stderr else ""

            self.current_message = None
            self.process = None

            output = ''.join(output_lines)
            if stderr:
                output += f"\n{stderr}"
            return output.strip() if output else "No response from Codex"
        except (FileNotFoundError, PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            return f"Failed to run Codex: {e}\n\nMake sure Codex CLI is installed and authenticated.\nRun 'codex' to authenticate."

    def stop_session(self) -> None:
        self.current_message = None
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def is_alive(self) -> bool:
        # With exec mode, we're "alive" if there's no active process or one is running
        return self.process is None or self.process.poll() is None


class GeminiCodeAssistProvider(AIProvider):
    """Provider for Gemini Code Assist (one-shot per prompt).

    Uses the `gemini` command-line interface in "YOLO" mode for
    non-interactive, programmatic calls.
    Authentication typically uses Application Default Credentials (ADC),
    set up via `gcloud auth application-default login`.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.project_path: str | None = None
        self.system_prompt: str | None = None
        self.current_message: str | None = None
        self._debug_enabled = bool(os.environ.get("CHAD_GEMINI_DEBUG"))

    def _debug(self, message: str) -> None:
        if self._debug_enabled:
            import sys
            print(f"[Gemini debug] {message}", file=sys.stderr, flush=True)

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self.project_path = project_path
        self.system_prompt = system_prompt
        return True

    def send_message(self, message: str) -> None:
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 30.0) -> str:
        import subprocess

        if not self.current_message:
            return ""

        cmd = ['gemini', '-y', '--output-format', 'text']

        if self.config.model_name and self.config.model_name != 'default':
            cmd.extend(['-m', self.config.model_name])

        cmd.extend(['-p', self.current_message])

        try:
            self._debug(f"Running gemini with {len(self.current_message)} chars")
            completed = subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                cwd=self.project_path,
                timeout=timeout
            )
        except subprocess.TimeoutExpired:
            self.current_message = None
            return f"Error: Gemini execution timed out ({int(timeout/60)} minutes)"
        except FileNotFoundError:
            self.current_message = None
            return "Failed to run Gemini: command not found\n\nInstall with: sudo npm install -g @google/gemini-cli"
        except (PermissionError, OSError) as exc:
            self.current_message = None
            return f"Failed to run Gemini: {exc}"

        self.current_message = None

        stdout_text = (completed.stdout or "").strip()
        stderr_text = (completed.stderr or "").strip()

        if stdout_text:
            return stdout_text
        if stderr_text:
            return stderr_text
        return "No response from Gemini"

    def stop_session(self) -> None:
        self.current_message = None

    def is_alive(self) -> bool:
        # Stateless execution – always available unless explicitly shut down
        return True


class MistralVibeProvider(AIProvider):
    """Provider for Mistral Vibe CLI.

    Uses the `vibe` command-line interface in programmatic mode (-p).
    Authentication is set up via `vibe --setup`.
    """

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.process: object | None = None
        self.project_path: str | None = None
        self.current_message: str | None = None
        self.system_prompt: str | None = None

    def start_session(self, project_path: str, system_prompt: str | None = None) -> bool:
        self.project_path = project_path
        self.system_prompt = system_prompt
        return True

    def send_message(self, message: str) -> None:
        # Prepend system prompt since programmatic mode is stateless
        if self.system_prompt:
            self.current_message = f"{self.system_prompt}\n\n---\n\n{message}"
        else:
            self.current_message = message

    def get_response(self, timeout: float = 1800.0) -> str:
        import subprocess

        if not self.current_message:
            return ""

        # Use vibe in programmatic mode (-p already auto-approves)
        cmd = [
            'vibe',
            '-p', self.current_message,
            '--output', 'text',  # text output is clean; streaming outputs verbose JSON
        ]

        try:
            self._notify_activity('text', 'Starting Vibe...')

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.project_path,
            )

            # Use communicate() with timeout - simpler and handles buffering correctly
            try:
                stdout, stderr = self.process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
                self.current_message = None
                self.process = None
                return f"Error: Vibe execution timed out ({int(timeout/60)} minutes)"

            self.current_message = None
            self.process = None

            output = stdout.strip() if stdout else ""
            if stderr and stderr.strip():
                output += f"\n{stderr.strip()}"
            return output if output else "No response from Vibe"

        except FileNotFoundError:
            self.current_message = None
            self.process = None
            return "Failed to run Vibe: command not found\n\nInstall with: pip install mistral-vibe\nThen run: vibe --setup"
        except (PermissionError, OSError) as e:
            self.current_message = None
            self.process = None
            return f"Failed to run Vibe: {e}"

    def stop_session(self) -> None:
        self.current_message = None
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

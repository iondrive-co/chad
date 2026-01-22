"""PTY passthrough for running agent CLIs directly in the terminal."""

import os
import select
import sys
import termios
import tty
from pathlib import Path


def run_agent_pty(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    initial_input: str | None = None,
) -> int:
    """Run agent CLI with terminal attached, return exit code.

    This function forks a PTY and connects the agent CLI directly to the
    user's terminal, allowing full interactive use of the agent's TUI.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory for the agent
        env: Additional environment variables
        initial_input: Optional input to send after agent starts

    Returns:
        Exit code from the agent process
    """
    import pty

    # Merge provided env with current environment
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    # Save terminal state
    old_settings = None
    try:
        old_settings = termios.tcgetattr(sys.stdin)
    except termios.error:
        pass  # Not a TTY

    try:
        # Fork with PTY
        pid, master_fd = pty.fork()

        if pid == 0:
            # Child process: exec the agent
            os.chdir(cwd)
            os.execvpe(cmd[0], cmd, full_env)
        else:
            # Parent process: relay I/O between terminal and agent
            try:
                # Set terminal to raw mode for passthrough
                if old_settings:
                    tty.setraw(sys.stdin.fileno())

                # Send initial input if provided
                if initial_input:
                    os.write(master_fd, initial_input.encode())

                # Bidirectional relay
                while True:
                    try:
                        rlist, _, _ = select.select([sys.stdin, master_fd], [], [], 0.1)
                    except (ValueError, OSError):
                        break

                    if sys.stdin in rlist:
                        # User input -> agent
                        try:
                            data = os.read(sys.stdin.fileno(), 1024)
                            if data:
                                os.write(master_fd, data)
                        except OSError:
                            break

                    if master_fd in rlist:
                        # Agent output -> user
                        try:
                            data = os.read(master_fd, 1024)
                            if data:
                                os.write(sys.stdout.fileno(), data)
                            else:
                                break  # Agent closed
                        except OSError:
                            break

            finally:
                # Wait for child and get exit status
                _, status = os.waitpid(pid, 0)
                return os.WEXITSTATUS(status)

    finally:
        # Restore terminal state
        if old_settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    return 1  # Fallback error code


def build_agent_command(
    provider: str,
    account_name: str,
    project_path: Path,
) -> tuple[list[str], dict[str, str]]:
    """Build CLI command and environment for a provider.

    Args:
        provider: Provider type (anthropic, openai, gemini, qwen, mistral)
        account_name: Account name for provider-specific paths
        project_path: Path to the project/worktree

    Returns:
        Tuple of (command_list, environment_dict)
    """
    env: dict[str, str] = {}

    if provider == "anthropic":
        # Claude Code CLI
        config_dir = Path.home() / ".chad" / "claude-configs" / account_name
        cmd = [
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
        ]
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

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

    elif provider == "gemini":
        # Gemini CLI in YOLO mode
        cmd = ["gemini", "-y"]

    elif provider == "qwen":
        # Qwen Code CLI
        cmd = ["qwen", "-y"]

    elif provider == "mistral":
        # Vibe CLI (Mistral)
        cmd = ["vibe"]

    elif provider == "mock":
        # Mock provider for testing - creates a file to simulate agent work
        mock_script = f'''
import datetime
bugs_file = "{project_path}/BUGS.md"
with open(bugs_file, "a") as f:
    f.write(f"\\n## Mock Task - {{datetime.datetime.now().isoformat()}}\\n")
    f.write("- Fixed a simulated bug\\n")
    f.write("- Added mock improvements\\n")
print("Mock agent: Created changes in BUGS.md")
'''
        cmd = ["python3", "-c", mock_script]

    else:
        # Fallback - try running provider name as command
        cmd = [provider]

    return cmd, env

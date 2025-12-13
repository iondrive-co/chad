"""Main installer logic for OpenAI Codex and Claude Code."""
import webbrowser
from pathlib import Path

from .utils import run_command, ensure_directory, is_tool_installed


class AIToolInstaller:
    """Handles installation of AI coding tools."""

    CLAUDE_LOGIN_URL = "https://claude.ai/login"
    OPENAI_LOGIN_URL = "https://platform.openai.com/login"

    def __init__(self, install_dir: Path | None = None):
        self.install_dir = install_dir or Path.home() / ".local" / "bin"
        self.config_dir = Path.home() / ".config" / "chad"
        ensure_directory(self.config_dir)

    def _check_node_npm(self) -> bool:
        return is_tool_installed('node') and is_tool_installed('npm')

    def install_codex(self) -> bool:
        print("Installing OpenAI Codex CLI...")

        if not self._check_node_npm():
            print("Error: Node.js and npm are required but not installed.")
            print("Please install Node.js from: https://nodejs.org/")
            return False

        returncode, stdout, stderr = run_command(
            ['npm', 'install', '-g', '@openai/openai-cli']
        )

        if returncode != 0:
            print(f"Warning: npm install returned {returncode}")
            print(f"stderr: {stderr}")

        return self._verify_codex()

    def install_claude_code(self) -> bool:
        print("Installing Anthropic Claude Code...")

        if not self._check_node_npm():
            print("Error: Node.js and npm are required but not installed.")
            print("Please install Node.js from: https://nodejs.org/")
            return False

        returncode, stdout, stderr = run_command(
            ['npm', 'install', '-g', '@anthropics/claude-code']
        )

        if returncode != 0:
            print(f"Warning: npm install returned {returncode}")
            print(f"stderr: {stderr}")

        return self._verify_claude_code()

    def _verify_codex(self) -> bool:
        return is_tool_installed('openai')

    def _verify_claude_code(self) -> bool:
        return is_tool_installed('claude-code')

    def open_login_page(self, tool: str) -> None:
        """Open login page and explain browser authentication.

        Args:
            tool: Either 'claude' or 'openai'
        """
        url = self.OPENAI_LOGIN_URL if tool == 'openai' else self.CLAUDE_LOGIN_URL
        print(f"\n{'='*70}")
        print(f"BROWSER AUTHENTICATION FOR {tool.upper()}")
        print(f"{'='*70}")
        print(f"Opening login page for {tool}...")
        print(f"URL: {url}")

        try:
            webbrowser.open(url)
            print("\nLogin page opened in your browser.")
        except Exception as e:
            print(f"\nCould not open browser automatically: {e}")
            print(f"Please manually open: {url}")

        print(f"\n{'='*70}")
        if tool == 'claude':
            print("After logging in to Claude.ai:")
            print("1. You can use your Claude.ai account (Pro/web subscription)")
            print("2. No API key needed - usage tracked against your account")
            print("3. Authenticate the CLI by running: claude-code login")
        else:  # openai
            print("After logging in to OpenAI:")
            print("1. Authenticate the CLI by running: openai login")
            print("2. Follow the prompts to complete authentication")
            print("3. No API key needed - uses your OpenAI account")
        print(f"{'='*70}\n")

    def authenticate_tool(self, tool: str) -> bool:
        """Attempt to authenticate with the tool's CLI.

        Args:
            tool: Either 'claude' or 'openai'

        Returns:
            True if authentication successful
        """
        if tool == 'claude':
            print("\nAuthenticating Claude Code...")
            print("Run: claude-code login")
            print("This will open a browser window to authenticate.")
        else:  # openai
            print("\nAuthenticating OpenAI CLI...")
            print("Run: openai login")
            print("Follow the prompts to complete authentication.")

        return True

    def verify_installation(self) -> dict[str, bool]:
        return {
            'codex': self._verify_codex(),
            'claude_code': self._verify_claude_code()
        }

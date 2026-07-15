"""Shared provider login / authorization logic.

Single source of truth for: installing a provider's CLI, running its interactive
login (browser OAuth or API-key entry), and checking whether an account is
authenticated. Used by both the CLI (`chad.ui.cli.app`) and the web API
(`chad.server.api.routes.providers`) so the two front-ends behave identically.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from chad.util.installer import AIToolInstaller
from chad.util.providers import is_mistral_configured

_INSTALLER = AIToolInstaller()

# Providers whose login launches an interactive terminal UI (Ink/raw-mode) and
# therefore needs a real TTY. From a detached server thread these must open in a
# new terminal window; the CLI front-end runs them in its own terminal instead.
TTY_LOGIN_PROVIDERS = frozenset({"anthropic", "gemini", "qwen", "kimi"})

# Provider type -> installer tool key
PROVIDER_TOOL_KEYS: dict[str, str] = {
    "openai": "codex",
    "anthropic": "claude",
    "gemini": "gemini",
    "qwen": "qwen",
    "local": "qwen",
    "mistral": "vibe",
    "kimi": "kimi",
}

# Providers that authenticate with a pasted API key rather than browser OAuth.
API_KEY_PROVIDERS = frozenset({"mistral"})

_LOGIN_TIMEOUT_SECS = 120


# ── Isolated home / config paths ──

def codex_home(account_name: str) -> Path:
    """Isolated HOME directory for a Codex account."""
    return Path.home() / ".chad" / "codex-homes" / account_name


def claude_config_dir(account_name: str) -> Path:
    """Isolated CLAUDE_CONFIG_DIR for a Claude account."""
    return Path.home() / ".chad" / "claude-configs" / account_name


def kimi_home(account_name: str) -> Path:
    """Isolated HOME directory for a Kimi account."""
    return Path.home() / ".chad" / "kimi-homes" / account_name


def write_kimi_default_config(config_file: Path) -> None:
    """Write default Kimi config when creds exist but config wasn't populated.

    A partial login leaves credentials but an empty config, causing "LLM not set".
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        'default_model = "kimi-code/kimi-k2.5"\n\n'
        '[models."kimi-code/kimi-k2.5"]\n'
        'provider = "managed:kimi-code"\n'
        'model = "kimi-k2.5"\n'
        'max_context_size = 131072\n\n'
        '[providers."managed:kimi-code"]\n'
        'type = "kimi"\n'
        'base_url = "https://api.kimi.com/coding/v1"\n'
        'api_key = ""\n\n'
        '[providers."managed:kimi-code".oauth]\n'
        'storage = "file"\n'
        'key = "kimi-code"\n',
        encoding="utf-8",
    )


# ── CLI installation ──

def ensure_cli(provider: str) -> tuple[bool, str]:
    """Ensure the provider's CLI is installed in Chad's managed tools dir.

    Returns (success, resolved_path_or_error). For providers with no managed CLI
    (e.g. mock) returns (True, "").
    """
    tool_key = PROVIDER_TOOL_KEYS.get(provider)
    if not tool_key:
        return True, ""
    return _INSTALLER.ensure_tool(tool_key)


def _resolve_cli(provider: str) -> str | None:
    """Resolve the path to an already-installed provider CLI, or None."""
    tool_key = PROVIDER_TOOL_KEYS.get(provider)
    if not tool_key:
        return None
    resolved = _INSTALLER.resolve_tool_path(tool_key)
    return str(resolved) if resolved else None


# ── Login status ──

def _codex_authenticated(account_name: str) -> bool:
    auth_file = codex_home(account_name) / ".codex" / "auth.json"
    if not auth_file.exists():
        return False
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        return bool(data.get("tokens", {}).get("access_token"))
    except (json.JSONDecodeError, OSError):
        return False


def is_logged_in(provider: str, account_name: str) -> bool:
    """Return True if the account has valid credentials on disk."""
    try:
        if provider == "openai":
            return _codex_authenticated(account_name)

        if provider == "anthropic":
            return (claude_config_dir(account_name) / ".credentials.json").exists()

        if provider == "gemini":
            return (Path.home() / ".gemini" / "oauth_creds.json").exists()

        if provider == "qwen":
            return (Path.home() / ".qwen" / "oauth_creds.json").exists()

        if provider == "local":
            # No credentials needed — ready once the Qwen Code CLI is installed.
            return _resolve_cli(provider) is not None

        if provider == "mistral":
            return is_mistral_configured(Path.home() / ".vibe")

        if provider == "kimi":
            creds_file = kimi_home(account_name) / ".kimi" / "credentials" / "kimi-code.json"
            global_creds = Path.home() / ".kimi" / "credentials" / "kimi-code.json"
            if not (creds_file.exists() or global_creds.exists()):
                return False
            config_file = kimi_home(account_name) / ".kimi" / "config.toml"
            if not (config_file.exists() and "[models." in config_file.read_text(encoding="utf-8")):
                write_kimi_default_config(config_file)
            return True

        if provider == "mock":
            return True

        return False
    except OSError:
        return False


# ── Login ──

def run_login(
    provider: str, account_name: str, api_key: str = "", new_terminal: bool = False
) -> tuple[bool, str]:
    """Install the CLI if needed and run the provider's login flow.

    - Codex (openai) logs in via a browser and needs no terminal.
    - Claude/Gemini/Qwen/Kimi use interactive terminal UIs: pass
      ``new_terminal=True`` (the web server) to open them in a new terminal
      window; leave it False (the CLI) to run them in the current terminal.
    - API-key providers write the supplied key to disk.

    Returns (success, message).
    """
    cli_ok, cli_detail = ensure_cli(provider)
    if not cli_ok:
        return False, cli_detail
    cli_path = cli_detail or PROVIDER_TOOL_KEYS.get(provider, provider)

    # Already authenticated (also repairs a partial kimi config as a side effect).
    if is_logged_in(provider, account_name):
        return True, "Already logged in"

    if provider == "local":
        # CLI is installed (ensure_cli above); a local server needs no login.
        return True, "Ready"

    if provider in API_KEY_PROVIDERS:
        return _login_api_key(provider, api_key)

    if provider == "openai":
        home = codex_home(account_name)
        (home / ".codex").mkdir(parents=True, exist_ok=True)
        env = _isolated_env(str(home))
        try:
            # Clear any partial session so re-login is reliable.
            subprocess.run([cli_path, "logout"], env=env, capture_output=True, timeout=10)
            subprocess.run([cli_path, "login"], env=env, timeout=_LOGIN_TIMEOUT_SECS)
        except FileNotFoundError:
            return False, "Codex CLI not found after install"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        if _codex_authenticated(account_name):
            return True, "Login successful"
        return False, "Login failed or was cancelled"

    if provider in TTY_LOGIN_PROVIDERS:
        cmd, extra_env = _tty_login_command(provider, account_name, cli_path)
        return _run_tty_login(provider, account_name, cmd, extra_env, new_terminal)

    return False, f"Unsupported provider: {provider}"


def _tty_login_command(provider: str, account_name: str, cli_path: str) -> tuple[list[str], dict]:
    """Build the command + isolated env for a terminal-interactive login."""
    if provider == "anthropic":
        config_dir = claude_config_dir(account_name)
        config_dir.mkdir(parents=True, exist_ok=True)
        return [cli_path], {"CLAUDE_CONFIG_DIR": str(config_dir)}
    if provider == "kimi":
        home = kimi_home(account_name)
        home.mkdir(parents=True, exist_ok=True)
        return [cli_path, "login"], {"HOME": str(home)}
    # gemini / qwen authenticate against their global home in YOLO mode.
    return [cli_path, "-y"], {}


def _run_tty_login(
    provider: str, account_name: str, cmd: list[str], extra_env: dict, new_terminal: bool
) -> tuple[bool, str]:
    if new_terminal:
        if _spawn_terminal(cmd, extra_env):
            return True, "Login started — finish signing in in the terminal window that opened."
        return False, (
            "Could not open a terminal window for login. Open a terminal and run: "
            + " ".join(shlex.quote(c) for c in cmd)
        )

    env = os.environ.copy()
    env.update(extra_env)
    try:
        subprocess.run(cmd, env=env, timeout=_LOGIN_TIMEOUT_SECS)
    except FileNotFoundError:
        return False, f"{provider} CLI not found after install"
    except subprocess.TimeoutExpired:
        return False, "Login timed out"
    if is_logged_in(provider, account_name):
        return True, "Login successful"
    return False, "Login failed or was cancelled"


def _login_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    if provider == "mistral":
        vibe_dir = Path.home() / ".vibe"
        if is_mistral_configured(vibe_dir):
            return True, "Already logged in"
        if not api_key:
            return False, "Mistral requires an API key"
        vibe_dir.mkdir(parents=True, exist_ok=True)
        (vibe_dir / ".env").write_text(f"MISTRAL_API_KEY='{api_key}'\n", encoding="utf-8")
        return True, "Login successful"

    return False, f"Unsupported provider: {provider}"


def _isolated_env(home: str) -> dict:
    """Environment with HOME pointed at an isolated account directory."""
    env = os.environ.copy()
    env["HOME"] = home
    if os.name == "nt":
        env["USERPROFILE"] = home
    return env


# Terminal emulators tried in order on Linux, with the flag that precedes the command.
_LINUX_TERMINALS: list[tuple[str, list[str]]] = [
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-e"]),
    ("xterm", ["-e"]),
]


def _write_login_script(cmd: list[str], extra_env: dict) -> str:
    """Write a temp shell script that sets env, runs cmd, and waits for a keypress."""
    lines = ["#!/usr/bin/env bash", ""]
    for key, value in extra_env.items():
        lines.append(f"export {key}={shlex.quote(value)}")
    lines.append(" ".join(shlex.quote(c) for c in cmd))
    lines.append("echo")
    lines.append('read -n 1 -s -r -p "Login finished — press any key to close this window."')
    fd, path = tempfile.mkstemp(prefix="chad-login-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(path, 0o755)
    return path


def _spawn_terminal(cmd: list[str], extra_env: dict) -> bool:
    """Launch cmd in a new visible terminal window. Returns True if one was opened."""
    if os.name == "nt":
        env = os.environ.copy()
        env.update(extra_env)
        CREATE_NEW_CONSOLE = 0x00000010
        try:
            subprocess.Popen(cmd, env=env, creationflags=CREATE_NEW_CONSOLE)
            return True
        except OSError:
            return False

    script = _write_login_script(cmd, extra_env)
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-a", "Terminal", script])
            return True
        except OSError:
            return False

    for name, flag in _LINUX_TERMINALS:
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            subprocess.Popen([exe, *flag, "bash", script])
            return True
        except OSError:
            continue
    return False

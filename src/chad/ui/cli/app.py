"""Simple CLI for Chad - minimal terminal UI using API streaming."""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import tty
from pathlib import Path

from chad.ui.client import APIClient
from chad.ui.client.stream_client import SyncStreamClient, decode_terminal_data
from chad.util.providers import is_mistral_configured


def _get_codex_home(account_name: str) -> Path:
    """Get the isolated HOME directory for a Codex account."""
    return Path.home() / ".chad" / "codex-homes" / account_name


def _get_claude_config_dir(account_name: str) -> Path:
    """Get the isolated CLAUDE_CONFIG_DIR for a Claude account."""
    return Path.home() / ".chad" / "claude-configs" / account_name


def _write_kimi_default_config(config_file: Path) -> None:
    """Write default Kimi config when creds exist but config wasn't populated."""
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


def _run_provider_oauth(provider: str, account_name: str) -> tuple[bool, str]:
    """Run the OAuth flow for a provider.

    Args:
        provider: Provider type (anthropic, openai, gemini, qwen, mistral, opencode, kimi)
        account_name: Name for the new account

    Returns:
        Tuple of (success, message)
    """
    if provider == "openai":
        # Codex uses isolated HOME directory
        codex_home = _get_codex_home(account_name)
        codex_home.mkdir(parents=True, exist_ok=True)
        auth_file = codex_home / ".codex" / "auth.json"

        env = os.environ.copy()
        env["HOME"] = str(codex_home)

        print("Starting Codex login... (browser will open)")
        print()
        try:
            result = subprocess.run(
                ["codex", "login"],
                env=env,
                timeout=120,
            )
            if result.returncode == 0 and auth_file.exists():
                try:
                    with open(auth_file, encoding="utf-8") as f:
                        auth_data = json.load(f)
                    if auth_data.get("tokens", {}).get("access_token"):
                        return True, "Login successful"
                except (json.JSONDecodeError, OSError):
                    pass
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            shutil.rmtree(codex_home, ignore_errors=True)
            return False, "Codex CLI not found. Install with: npm install -g @openai/codex"
        except subprocess.TimeoutExpired:
            shutil.rmtree(codex_home, ignore_errors=True)
            return False, "Login timed out"
        except Exception as e:
            shutil.rmtree(codex_home, ignore_errors=True)
            return False, f"Login error: {e}"

    elif provider == "anthropic":
        # Claude uses isolated config directory
        config_dir = _get_claude_config_dir(account_name)
        config_dir.mkdir(parents=True, exist_ok=True)
        creds_file = config_dir / ".credentials.json"

        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        print("Starting Claude login... (browser will open)")
        print()
        try:
            # Run claude which will handle the OAuth flow
            result = subprocess.run(
                ["claude"],
                env=env,
                timeout=120,
            )
            # Check for credentials file
            if creds_file.exists():
                try:
                    with open(creds_file, encoding="utf-8") as f:
                        creds_data = json.load(f)
                    if creds_data.get("claudeAiOauth", {}).get("accessToken"):
                        return True, "Login successful"
                except (json.JSONDecodeError, OSError):
                    pass
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            shutil.rmtree(config_dir, ignore_errors=True)
            return False, "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        except subprocess.TimeoutExpired:
            shutil.rmtree(config_dir, ignore_errors=True)
            return False, "Login timed out"
        except Exception as e:
            shutil.rmtree(config_dir, ignore_errors=True)
            return False, f"Login error: {e}"

    elif provider == "gemini":
        creds_file = Path.home() / ".gemini" / "oauth_creds.json"

        print("Starting Gemini login... (browser will open)")
        print()
        try:
            result = subprocess.run(
                ["gemini", "-y"],
                timeout=120,
            )
            if result.returncode == 0 and creds_file.exists():
                return True, "Login successful"
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            return False, "Gemini CLI not found"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        except Exception as e:
            return False, f"Login error: {e}"

    elif provider == "qwen":
        print("Starting Qwen login... (browser will open)")
        print()
        try:
            result = subprocess.run(
                ["qwen", "-y"],
                timeout=120,
            )
            if result.returncode == 0:
                return True, "Login successful"
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            return False, "Qwen CLI not found"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        except Exception as e:
            return False, f"Login error: {e}"

    elif provider == "mistral":
        vibe_dir = Path.home() / ".vibe"
        if is_mistral_configured(vibe_dir):
            return True, "Already logged in"

        import webbrowser
        print("Mistral requires an API key.")
        print("Opening https://console.mistral.ai/codestral/cli ...")
        webbrowser.open("https://console.mistral.ai/codestral/cli")
        print()
        api_key = input("Paste your MISTRAL_API_KEY: ").strip()
        if not api_key:
            return False, "No API key provided"

        vibe_dir.mkdir(parents=True, exist_ok=True)
        env_file = vibe_dir / ".env"
        env_file.write_text(f"MISTRAL_API_KEY='{api_key}'\n", encoding="utf-8")
        return True, "Login successful"

    elif provider == "opencode":
        # OpenCode stores credentials at ~/.local/share/opencode/auth.json
        auth_file = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if auth_file.exists():
            try:
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                if data:
                    return True, "Already logged in"
            except (json.JSONDecodeError, OSError):
                pass

        print("OpenCode requires an API key.")
        print("Get one at https://opencode.ai/auth")
        print()
        try:
            api_key = input("Paste your API key (or press Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            api_key = ""
        if api_key:
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            auth_data = {"opencode": {"type": "api", "key": api_key}}
            auth_file.write_text(json.dumps(auth_data), encoding="utf-8")
            return True, "API key stored"
        return False, "No API key provided"

    elif provider == "kimi":
        # Check isolated credentials for this account
        kimi_home = Path.home() / ".chad" / "kimi-homes" / account_name
        creds_file = kimi_home / ".kimi" / "credentials" / "kimi-code.json"
        global_creds = Path.home() / ".kimi" / "credentials" / "kimi-code.json"
        config_file = kimi_home / ".kimi" / "config.toml"
        # Only consider fully logged in if creds exist AND config has models populated.
        # A partial login leaves creds but empty config, causing "LLM not set".
        if creds_file.exists() or global_creds.exists():
            if config_file.exists() and "[models." in config_file.read_text(encoding="utf-8"):
                return True, "Already logged in"
            # Creds exist but config wasn't populated — write config directly
            # rather than re-doing OAuth (which fails with "already approved").
            _write_kimi_default_config(config_file)
            return True, "Already logged in"

        # Run interactive kimi login in the terminal
        kimi_cli = shutil.which("kimi")
        if not kimi_cli:
            return False, "Kimi CLI not found. Install with: pip install kimi-cli"

        kimi_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(kimi_home)

        print("Starting Kimi login...")
        print()
        try:
            result = subprocess.run(
                [kimi_cli, "login"],
                env=env,
                timeout=120,
            )
            if result.returncode == 0 and creds_file.exists():
                return True, "Logged in successfully"
            # Kimi may persist credentials before a non-fatal model listing error.
            # Accept this as logged in and repair config if needed.
            if creds_file.exists() or global_creds.exists():
                if not (config_file.exists() and "[models." in config_file.read_text(encoding="utf-8")):
                    _write_kimi_default_config(config_file)
                return True, "Logged in successfully"
            return False, "Kimi login did not complete"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        except Exception as e:
            return False, f"Login error: {e}"

    else:
        return False, f"Unsupported provider: {provider}"


def get_terminal_size() -> tuple[int, int]:
    """Get the current terminal size (rows, cols).

    Returns:
        Tuple of (rows, cols) or (24, 80) as fallback
    """
    try:
        size = shutil.get_terminal_size()
        return (size.lines, size.columns)
    except Exception:
        return (24, 80)


def clear_screen():
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def print_header():
    """Print the Chad CLI header."""
    print("=" * 50)
    print("  CHAD - CLI Mode")
    print("=" * 50)
    print()


def select_from_list(prompt: str, options: list[tuple[str, str]], default_idx: int = 0) -> str | None:
    """Simple numbered selection from a list.

    Args:
        prompt: The prompt to display
        options: List of (label, value) tuples
        default_idx: Default selection index

    Returns:
        Selected value or None if cancelled
    """
    if not options:
        print("No options available.")
        return None

    print(prompt)
    for i, (label, _) in enumerate(options):
        marker = "*" if i == default_idx else " "
        print(f"  {marker}[{i + 1}] {label}")
    print()

    while True:
        try:
            choice = input(f"Select [1-{len(options)}] (Enter for default, q to cancel): ").strip()
            if choice.lower() == "q":
                return None
            if choice == "":
                return options[default_idx][1]
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx][1]
            print(f"Please enter a number between 1 and {len(options)}")
        except ValueError:
            print("Please enter a valid number")
        except (EOFError, KeyboardInterrupt):
            return None


def _format_action_settings(settings: list[dict]) -> str:
    """Format action settings for display."""
    lines = []
    for s in settings:
        event = s.get("event", "?")
        threshold = s.get("threshold", 90)
        action = s.get("action", "notify")
        target = s.get("target_account", "")
        label = event.replace("_", " ").title()
        suffix = f" -> {target}" if action == "switch_provider" and target else ""
        lines.append(f"    {label}: {threshold}% {action}{suffix}")
    return "\n".join(lines) if lines else "    (none)"


def run_settings_menu(client: APIClient) -> None:
    """Run the settings submenu.

    Args:
        client: API client instance
    """
    while True:
        clear_screen()
        print("=" * 50)
        print("  CHAD - Settings")
        print("=" * 50)
        print()

        # Get current settings from API
        accounts = client.list_accounts()
        cleanup = client.get_cleanup_settings()
        preferences = client.get_preferences()
        verification_agent_name = client.get_verification_agent()
        verification_model = client.get_preferred_verification_model()
        max_verification_attempts = client.get_max_verification_attempts()
        action_settings = client.get_action_settings()
        try:
            slack_settings = client.get_slack_settings()
        except Exception:
            slack_settings = {"enabled": False, "channel": None, "has_token": False}
        # Find coding agent from roles
        coding_agent = None
        for acc in accounts:
            if acc.role == "CODING":
                coding_agent = acc.name

        print("Current Settings:")
        print(f"  Accounts:           {len(accounts)} configured")
        print(f"  Cleanup:            {cleanup.retention_days} days")
        print(f"  UI Mode:            {preferences.ui_mode}")
        print(f"  Coding Agent:       {coding_agent or '(not set)'}")
        print(f"  Verification Agent: {verification_agent_name or '(not set)'}")
        print(f"  Verification Model: {verification_model or '(auto)'}")
        print(f"  Max Verif Attempts: {max_verification_attempts}")
        print("  Action Rules:")
        print(_format_action_settings(action_settings))
        slack_status = "enabled" if slack_settings.get("enabled") else "disabled"
        slack_ch = slack_settings.get("channel") or "(not set)"
        print(f"  Slack:              {slack_status}, channel={slack_ch}")
        print()

        print("Settings Menu:")
        print("  [1] Manage accounts")
        print("  [2] Set cleanup days")
        print("  [3] Set verification agent")
        print("  [4] Set verification model")
        print("  [5] Set max verification attempts")
        print("  [6] Action rules")
        print("  [7] Set UI mode")
        print("  [8] Slack integration")
        print("  [b] Back to main menu")
        print()

        try:
            choice = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "b":
            break

        elif choice == "1":
            run_accounts_menu(client)

        elif choice == "2":
            print()
            current_days = cleanup.retention_days
            print(f"Current cleanup: {current_days} days")
            try:
                new_days = input("New cleanup days (1-365): ").strip()
                if new_days:
                    days = int(new_days)
                    if 1 <= days <= 365:
                        client.set_cleanup_settings(retention_days=days)
                        print(f"Cleanup set to {days} days")
                    else:
                        print("Please enter a number between 1 and 365")
            except ValueError:
                print("Invalid number")
            input("Press Enter to continue...")

        elif choice == "3":
            print()
            if not accounts:
                print("No accounts configured.")
            else:
                options = [("None (disabled)", None)] + [
                    (f"{acc.name} ({acc.provider})", acc.name)
                    for acc in accounts
                ]
                default_idx = 0
                if verification_agent_name:
                    for i, (_, val) in enumerate(options):
                        if val == verification_agent_name:
                            default_idx = i
                            break

                selected = select_from_list("Select verification agent:", options, default_idx)
                if selected is not None or (selected is None and default_idx == 0):
                    client.set_verification_agent(selected)
                    if selected:
                        print(f"Verification agent set to: {selected}")
                    else:
                        print("Verification agent disabled")
            input("Press Enter to continue...")

        elif choice == "4":
            # Set verification model
            print()
            print(f"Current verification model: {verification_model or '(auto)'}")
            print()
            if not verification_agent_name:
                print("No verification agent set. Set one first.")
            else:
                # Get available models for the verification agent
                try:
                    models = client.get_account_models(verification_agent_name)
                    if models:
                        options = [("Auto (default)", None)] + [
                            (model, model) for model in models
                        ]
                        default_idx = 0
                        if verification_model:
                            for i, (_, val) in enumerate(options):
                                if val == verification_model:
                                    default_idx = i
                                    break
                        selected = select_from_list("Select verification model:", options, default_idx)
                        if selected is not None or default_idx > 0:
                            client.set_preferred_verification_model(selected)
                            if selected:
                                print(f"Verification model set to: {selected}")
                            else:
                                print("Verification model set to auto")
                    else:
                        print("No models available for verification agent.")
                except Exception as e:
                    print(f"Error getting models: {e}")
            input("Press Enter to continue...")

        elif choice == "5":
            # Set max verification attempts
            print()
            print(f"Current max verification attempts: {max_verification_attempts}")
            print("Number of times to retry verification before giving up.")
            try:
                new_attempts = input("New max attempts (1-20): ").strip()
                if new_attempts:
                    attempts = int(new_attempts)
                    if 1 <= attempts <= 20:
                        client.set_max_verification_attempts(attempts)
                        print(f"Max verification attempts set to {attempts}")
                    else:
                        print("Please enter a number between 1 and 20")
            except ValueError:
                print("Invalid number")
            input("Press Enter to continue...")

        elif choice == "6":
            # Action rules
            print()
            print("Action Rules")
            print("-" * 30)
            print(_format_action_settings(action_settings))
            print()
            print("  [a] Add rule")
            if action_settings:
                print("  [e] Edit rule")
                print("  [d] Delete rule")
            print()
            try:
                sub = input("Choice (or Enter to skip): ").strip().lower()
                event_names = ["session_usage", "weekly_usage", "context_usage"]
                all_actions = ["notify", "switch_provider", "await_reset"]

                if sub == "a":
                    print("Event types: " + ", ".join(event_names))
                    event_key = input("Event type: ").strip()
                    if event_key not in event_names:
                        print("Invalid event type")
                    else:
                        cur_threshold = int(input("Threshold (0-100): ").strip())
                        avail_actions = [a for a in all_actions if not (a == "await_reset" and event_key == "context_usage")]
                        print(f"Actions: {', '.join(avail_actions)}")
                        cur_action = input("Action: ").strip()
                        if cur_action not in avail_actions:
                            print("Invalid action")
                        else:
                            target = None
                            if cur_action == "switch_provider":
                                print("Available accounts:", ", ".join(acc.name for acc in accounts) if accounts else "(none)")
                                target = input("Target account: ").strip() or None
                            new_entry = {"event": event_key, "threshold": cur_threshold, "action": cur_action}
                            if target:
                                new_entry["target_account"] = target
                            new_settings = list(action_settings) + [new_entry]
                            try:
                                client.set_action_settings(new_settings)
                                print("Rule added.")
                            except Exception as e:
                                print(f"Error: {e}")

                elif sub == "e" and action_settings:
                    for i, s in enumerate(action_settings, 1):
                        ev = s.get("event", "?")
                        print(f"  [{i}] {ev} >= {s.get('threshold', 90)}% -> {s.get('action', 'notify')}")
                    idx = int(input("Rule number: ").strip()) - 1
                    if 0 <= idx < len(action_settings):
                        current = action_settings[idx]
                        event_key = current.get("event", "session_usage")
                        cur_threshold = current.get("threshold", 90)
                        cur_action = current.get("action", "notify")

                        new_thr = input(f"Threshold (0-100, current {cur_threshold}): ").strip()
                        if new_thr:
                            cur_threshold = int(new_thr)

                        avail_actions = [a for a in all_actions if not (a == "await_reset" and event_key == "context_usage")]
                        print(f"Actions: {', '.join(avail_actions)} (current: {cur_action})")
                        new_action = input("Action: ").strip()
                        if new_action and new_action in avail_actions:
                            cur_action = new_action

                        target = None
                        if cur_action == "switch_provider":
                            print("Available accounts:", ", ".join(acc.name for acc in accounts) if accounts else "(none)")
                            target = input("Target account: ").strip() or None

                        new_entry = {"event": event_key, "threshold": cur_threshold, "action": cur_action}
                        if target:
                            new_entry["target_account"] = target
                        new_settings = list(action_settings)
                        new_settings[idx] = new_entry
                        try:
                            client.set_action_settings(new_settings)
                            print("Rule updated.")
                        except Exception as e:
                            print(f"Error: {e}")

                elif sub == "d" and action_settings:
                    for i, s in enumerate(action_settings, 1):
                        ev = s.get("event", "?")
                        print(f"  [{i}] {ev} >= {s.get('threshold', 90)}% -> {s.get('action', 'notify')}")
                    idx = int(input("Rule to delete: ").strip()) - 1
                    if 0 <= idx < len(action_settings):
                        new_settings = [s for j, s in enumerate(action_settings) if j != idx]
                        try:
                            client.set_action_settings(new_settings)
                            print("Rule deleted.")
                        except Exception as e:
                            print(f"Error: {e}")
            except ValueError:
                print("Invalid input")
            input("Press Enter to continue...")

        elif choice == "7":
            # Set UI mode
            print()
            print(f"Current UI mode: {preferences.ui_mode}")
            options = [
                ("Gradio (web interface)", "gradio"),
                ("CLI (terminal interface)", "cli"),
            ]
            default_idx = 0 if preferences.ui_mode == "gradio" else 1
            selected = select_from_list("Select UI mode:", options, default_idx)
            if selected is not None:
                client.set_preferences(ui_mode=selected)
                print(f"UI mode set to: {selected}")
            input("Press Enter to continue...")

        elif choice == "8":
            # Slack integration
            print()
            print("Slack Integration")
            print("-" * 30)
            print(f"  Enabled:   {slack_settings.get('enabled', False)}")
            print(f"  Token:     {'(set)' if slack_settings.get('has_token') else '(not set)'}")
            print(f"  Signing:   {'(set)' if slack_settings.get('has_signing_secret') else '(not set)'}")
            print(f"  Channel:   {slack_settings.get('channel') or '(not set)'}")
            print()
            print("  [e] Toggle enabled")
            print("  [t] Set bot token")
            print("  [g] Set signing secret")
            print("  [c] Set channel ID")
            print("  [s] Send test message")
            print()
            try:
                sub = input("Choice (or Enter to skip): ").strip().lower()
                if sub == "e":
                    new_val = not slack_settings.get("enabled", False)
                    client.set_slack_settings(enabled=new_val)
                    print(f"Slack {'enabled' if new_val else 'disabled'}")
                elif sub == "t":
                    token = input("Bot token (xoxb-...): ").strip()
                    if token:
                        client.set_slack_settings(bot_token=token)
                        print("Bot token saved")
                elif sub == "g":
                    secret = input("Signing secret: ").strip()
                    if secret:
                        client.set_slack_settings(signing_secret=secret)
                        print("Signing secret saved")
                elif sub == "c":
                    channel = input("Channel ID (e.g. C0123456789): ").strip()
                    if channel:
                        client.set_slack_settings(channel=channel)
                        print(f"Channel set to {channel}")
                elif sub == "s":
                    result = client.test_slack_connection()
                    if result.get("ok"):
                        print("Test message sent to Slack")
                    else:
                        print(f"Failed: {result.get('error', 'unknown error')}")
            except (ValueError, EOFError):
                pass
            input("Press Enter to continue...")


def run_accounts_menu(client: APIClient) -> None:
    """Run the accounts management submenu.

    Args:
        client: API client instance
    """
    while True:
        clear_screen()
        print("=" * 50)
        print("  CHAD - Manage Accounts")
        print("=" * 50)
        print()

        accounts = client.list_accounts()
        coding_agent = None
        for acc in accounts:
            if acc.role == "CODING":
                coding_agent = acc.name

        if accounts:
            print("Configured Accounts:")
            for acc in accounts:
                role_marker = f" [{acc.role}]" if acc.role else ""
                print(f"  - {acc.name} ({acc.provider}){role_marker}")
            print()
        else:
            print("No accounts configured.")
            print()

        print("Account Menu:")
        print("  [1] Add account")
        print("  [2] Delete account")
        print("  [3] Set as coding agent")
        print("  [b] Back to settings")
        print()

        try:
            choice = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "b":
            break

        elif choice == "1":
            print()
            providers = client.list_providers()
            print("Supported providers:")
            for i, p in enumerate(providers, 1):
                print(f"  {i}. {p['type']:10} - {p['name']}")
            print()

            try:
                provider_choice = input(f"Select provider [1-{len(providers)}]: ").strip()
                if not provider_choice.isdigit() or not (1 <= int(provider_choice) <= len(providers)):
                    print("Invalid selection")
                    input("Press Enter to continue...")
                    continue

                provider = providers[int(provider_choice) - 1]["type"]

                account_name = input(f"Account name (e.g., my-{provider}): ").strip()
                if not account_name:
                    print("Account name is required")
                    input("Press Enter to continue...")
                    continue

                # Check if account exists
                existing = [acc.name for acc in accounts]
                if account_name in existing:
                    print(f"Account '{account_name}' already exists")
                    input("Press Enter to continue...")
                    continue

                print()
                # Run OAuth flow for the provider
                success, message = _run_provider_oauth(provider, account_name)

                if success:
                    # Register the account via API
                    try:
                        client.create_account(account_name, provider)
                        print(f"✓ Account '{account_name}' created successfully!")
                    except Exception as e:
                        print(f"✗ Failed to register account: {e}")
                else:
                    print(f"✗ {message}")

            except (EOFError, KeyboardInterrupt):
                pass

            input("Press Enter to continue...")

        elif choice == "2":
            print()
            if not accounts:
                print("No accounts to delete.")
            else:
                options = [(f"{acc.name} ({acc.provider})", acc.name) for acc in accounts]
                selected = select_from_list("Select account to delete:", options)
                if selected:
                    confirm = input(f"Delete '{selected}'? [y/N]: ").strip().lower()
                    if confirm == "y":
                        client.delete_account(selected)
                        print(f"Account '{selected}' deleted")
            input("Press Enter to continue...")

        elif choice == "3":
            print()
            if not accounts:
                print("No accounts configured.")
            else:
                options = [(f"{acc.name} ({acc.provider})", acc.name) for acc in accounts]
                default_idx = 0
                if coding_agent:
                    for i, (_, val) in enumerate(options):
                        if val == coding_agent:
                            default_idx = i
                            break
                selected = select_from_list("Select coding agent:", options, default_idx)
                if selected:
                    client.set_account_role(selected, "CODING")
                    print(f"Coding agent set to: {selected}")
            input("Press Enter to continue...")


def run_task_with_streaming(
    client: APIClient,
    stream_client: SyncStreamClient,
    session_id: str,
    project_path: str,
    task_description: str,
    coding_account: str,
    verification_account: str | None = None,
) -> int:
    """Run a task with PTY streaming via API.

    Args:
        client: API client for REST calls
        stream_client: Streaming client for SSE
        session_id: Session ID
        project_path: Project path
        task_description: Task description
        coding_account: Account to use for coding
        verification_account: Optional account to use for verification

    Returns:
        Exit code from the agent
    """
    # Get actual terminal size for PTY
    rows, cols = get_terminal_size()

    # Start the task with current terminal dimensions
    client.start_task(
        session_id=session_id,
        project_path=project_path,
        task_description=task_description,
        coding_agent=coding_account,
        verification_agent=verification_account,
        terminal_rows=rows,
        terminal_cols=cols,
    )

    milestone_since_seq = 0
    milestone_poll_stop = threading.Event()

    def _emit_milestones_once() -> None:
        nonlocal milestone_since_seq
        try:
            milestones = client.get_milestones(session_id, since_seq=milestone_since_seq)
        except Exception:
            return

        if not isinstance(milestones, list):
            return

        max_seq = milestone_since_seq
        for milestone in milestones:
            if not isinstance(milestone, dict):
                continue

            seq = milestone.get("seq", 0)
            try:
                seq_int = int(seq)
            except (TypeError, ValueError):
                seq_int = 0
            if seq_int > max_seq:
                max_seq = seq_int

            summary = str(milestone.get("summary", "")).strip()
            if not summary:
                continue

            title = str(milestone.get("title", "")).strip()
            line = f"\r\n[MILESTONE] {title}: {summary}\r\n"
            os.write(sys.stdout.fileno(), line.encode("utf-8", errors="replace"))

        milestone_since_seq = max_seq

    def _milestone_poll_loop() -> None:
        while not milestone_poll_stop.is_set():
            _emit_milestones_once()
            milestone_poll_stop.wait(0.5)

    _emit_milestones_once()
    milestone_poll_thread = threading.Thread(target=_milestone_poll_loop, daemon=True)
    milestone_poll_thread.start()

    # Save terminal state
    old_settings = None
    try:
        old_settings = termios.tcgetattr(sys.stdin)
    except termios.error:
        pass

    exit_code = 0

    # Track if we need to send resize
    resize_pending = False

    def handle_sigwinch(signum, frame):
        """Handle terminal resize signal."""
        nonlocal resize_pending
        resize_pending = True

    # Set up SIGWINCH handler for terminal resize
    old_sigwinch = None
    try:
        old_sigwinch = signal.signal(signal.SIGWINCH, handle_sigwinch)
    except (ValueError, OSError):
        pass  # SIGWINCH not available (not Unix or not main thread)

    try:
        # Set terminal to raw mode for passthrough
        if old_settings:
            tty.setraw(sys.stdin.fileno())

        # Stream events and relay I/O
        for event in stream_client.stream_events(session_id, include_terminal=True):
            # Check for pending resize
            if resize_pending:
                resize_pending = False
                new_rows, new_cols = get_terminal_size()
                try:
                    stream_client.resize_terminal(session_id, new_rows, new_cols)
                except Exception:
                    pass  # Best effort resize

            if event.event_type == "terminal":
                # Write terminal output
                data = decode_terminal_data(
                    event.data.get("data", ""),
                    is_text=event.data.get("text", False),
                )
                os.write(sys.stdout.fileno(), data)

            elif event.event_type == "complete":
                exit_code = event.data.get("exit_code", 0)
                break

            elif event.event_type == "error":
                # Restore terminal before printing error
                if old_settings:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                print(f"\nError: {event.data.get('error', 'Unknown error')}")
                exit_code = 1
                break

            # Check for user input (non-blocking)
            if old_settings:
                rlist, _, _ = select.select([sys.stdin], [], [], 0)
                if sys.stdin in rlist:
                    try:
                        input_data = os.read(sys.stdin.fileno(), 1024)
                        if input_data:
                            stream_client.send_input(session_id, input_data)
                    except OSError:
                        pass

    finally:
        milestone_poll_stop.set()
        milestone_poll_thread.join(timeout=1.0)
        _emit_milestones_once()

        # Restore SIGWINCH handler
        if old_sigwinch is not None:
            try:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            except (ValueError, OSError):
                pass

        # Restore terminal state
        if old_settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    return exit_code


def run_cli(client: APIClient) -> None:
    """Run the simple CLI interface.

    Args:
        client: API client instance
    """
    stream_client = SyncStreamClient(base_url=client.base_url)

    try:
        while True:
            clear_screen()
            print_header()

            # Load accounts and preferences from API
            accounts = client.list_accounts()
            prefs = client.get_preferences()
            default_project = prefs.last_project_path or ""

            # Find coding agent
            coding_account = None
            coding_provider = None
            for acc in accounts:
                if acc.role == "CODING":
                    coding_account = acc.name
                    coding_provider = acc.provider
                    break

            # Get verification agent from config
            verification_account = client.get_verification_agent()

            if not accounts:
                print("No accounts configured.")
                print("Press [s] to open settings and add an account.")
                print()

            # Show current settings
            print(f"Project: {default_project or '(not set)'}")
            if coding_account:
                print(f"Agent:   {coding_account} ({coding_provider})")
            print()

            # Main menu
            print("What would you like to do?")
            print("  [1] Start a task")
            print("  [2] Change project path")
            print("  [3] Change agent")
            print("  [s] Settings")
            print("  [q] Quit")
            print()

            try:
                choice = input("Choice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if choice == "q":
                break

            elif choice == "s":
                run_settings_menu(client)

            elif choice == "2":
                # Change project path
                print()
                new_path = input(f"Project path [{default_project}]: ").strip()
                if new_path:
                    expanded = str(Path(new_path).expanduser().resolve())
                    if Path(expanded).exists():
                        client.set_preferences(last_project_path=expanded)
                        print(f"Project path set to: {expanded}")
                    else:
                        print(f"Path does not exist: {expanded}")
                input("Press Enter to continue...")

            elif choice == "3":
                # Change agent
                print()
                if not accounts:
                    print("No accounts configured.")
                else:
                    options = [(f"{acc.name} ({acc.provider})", acc.name) for acc in accounts]
                    default_idx = 0
                    if coding_account:
                        for i, (_, val) in enumerate(options):
                            if val == coding_account:
                                default_idx = i
                                break

                    selected = select_from_list("Select coding agent:", options, default_idx)
                    if selected:
                        client.set_account_role(selected, "CODING")
                        print(f"Agent set to: {selected}")
                input("Press Enter to continue...")

            elif choice == "1":
                # Start a task
                if not default_project:
                    print("\nPlease set a project path first.")
                    input("Press Enter to continue...")
                    continue

                project_path = str(Path(default_project).expanduser().resolve())
                if not Path(project_path).exists():
                    print(f"\nProject path does not exist: {project_path}")
                    input("Press Enter to continue...")
                    continue

                git_dir = Path(project_path) / ".git"
                if not git_dir.exists():
                    print(f"\nProject is not a git repository: {project_path}")
                    input("Press Enter to continue...")
                    continue

                if not coding_account or not coding_provider:
                    print("\nPlease select a coding agent first.")
                    input("Press Enter to continue...")
                    continue

                # Get task description
                print()
                print("Enter task description (Ctrl+D or empty line to finish):")
                print("-" * 40)

                lines = []
                try:
                    while True:
                        line = input()
                        if not line and lines:
                            break
                        lines.append(line)
                except EOFError:
                    pass

                task_description = "\n".join(lines).strip()
                if not task_description:
                    print("\nNo task description provided.")
                    input("Press Enter to continue...")
                    continue

                # Create session via API
                print()
                print("Creating session...")
                session = client.create_session(
                    project_path=project_path,
                    name=f"Task: {task_description[:30]}...",
                )

                # Run with streaming
                clear_screen()
                print(f"Starting {coding_provider} agent...")
                print(f"Project: {project_path}")
                print(f"Task: {task_description[:80]}...")
                print("-" * 60)
                print()

                exit_code = run_task_with_streaming(
                    client=client,
                    stream_client=stream_client,
                    session_id=session.id,
                    project_path=project_path,
                    task_description=task_description,
                    coding_account=coding_account,
                    verification_account=verification_account,
                )

                print()
                print("-" * 60)
                print(f"Agent exited with code: {exit_code}")

                # Check for changes via API
                try:
                    worktree = client.get_worktree_status(session.id)
                    if worktree.exists and worktree.has_changes:
                        diff = client.get_diff_summary(session.id)
                        print()
                        print("Changes detected:")
                        print(f"  {diff.files_changed} files changed, "
                              f"+{diff.insertions} -{diff.deletions}")
                        print()
                        print("What would you like to do?")
                        print("  [m] Merge changes to main branch")
                        print("  [d] View diff")
                        print("  [x] Discard changes")
                        print("  [k] Keep worktree for later")

                        while True:
                            try:
                                action = input("Choice: ").strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                action = "k"
                                break

                            if action == "d":
                                # Show diff
                                full_diff = client.get_full_diff(session.id)
                                print()
                                for file_info in full_diff.get("files", []):
                                    print(f"--- {file_info.get('path', 'unknown')}")
                                    for hunk in file_info.get("hunks", []):
                                        print(hunk.get("content", ""))
                                continue

                            elif action == "m":
                                # Merge to main
                                print("\nMerging changes...")
                                result = client.merge_worktree(session.id)
                                if result.success:
                                    print("Merge successful!")
                                else:
                                    print(f"Merge failed: {result.message}")
                                    if result.conflicts:
                                        print("Conflicts:")
                                        for c in result.conflicts:
                                            print(f"  - {c}")
                                break

                            elif action == "x":
                                # Discard
                                print("\nDiscarding changes...")
                                client.reset_worktree(session.id)
                                client.delete_worktree(session.id)
                                print("Changes discarded.")
                                break

                            elif action == "k":
                                # Keep for later
                                print(f"\nWorktree kept: {worktree.path}")
                                print(f"Branch: {worktree.branch}")
                                break

                            else:
                                print("Please enter m, d, x, or k")

                    else:
                        print("\nNo changes made by agent.")
                        # Clean up session
                        try:
                            client.delete_session(session.id)
                        except Exception:
                            pass

                except Exception as e:
                    print(f"\nError checking worktree: {e}")

                input("\nPress Enter to continue...")

    finally:
        stream_client.close()


def launch_cli_ui(api_base_url: str = "http://localhost:8000", password: str | None = None) -> None:
    """Launch the Chad CLI UI.

    Args:
        api_base_url: Base URL of the Chad API server
        password: Optional pre-authenticated password (unused, kept for compatibility)
    """
    client = APIClient(base_url=api_base_url)

    try:
        # Verify server is available
        status = client.get_status()
        print(f"Connected to Chad server v{status.get('version', 'unknown')}")
    except Exception as e:
        print(f"Error: Cannot connect to Chad server at {api_base_url}")
        print(f"  {e}")
        print("\nMake sure the server is running (chad --mode server)")
        sys.exit(1)

    try:
        run_cli(client)
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    finally:
        client.close()

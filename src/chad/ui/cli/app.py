"""Simple CLI for Chad - minimal terminal UI using API streaming."""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import tty
from pathlib import Path

from chad.ui.client import APIClient
from chad.ui.client.stream_client import SyncStreamClient, decode_terminal_data


def _get_codex_home(account_name: str) -> Path:
    """Get the isolated HOME directory for a Codex account."""
    return Path.home() / ".chad" / "codex-homes" / account_name


def _get_claude_config_dir(account_name: str) -> Path:
    """Get the isolated CLAUDE_CONFIG_DIR for a Claude account."""
    return Path.home() / ".chad" / "claude-configs" / account_name


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
        vibe_config = Path.home() / ".vibe" / "config.toml"
        if vibe_config.exists():
            return True, "Already logged in"

        print("Starting Vibe setup...")
        print()
        try:
            result = subprocess.run(
                ["vibe", "--setup"],
                timeout=120,
            )
            if result.returncode == 0 and vibe_config.exists():
                return True, "Login successful"
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            return False, "Vibe CLI not found"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        except Exception as e:
            return False, f"Login error: {e}"

    elif provider == "opencode":
        # OpenCode uses browser OAuth via `opencode auth login`
        auth_file = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if auth_file.exists():
            try:
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                if data:
                    return True, "Already logged in"
            except (json.JSONDecodeError, OSError):
                pass

        opencode_cli = shutil.which("opencode")
        if not opencode_cli:
            return False, "OpenCode CLI not found"

        print("Opening browser for OpenCode login...")
        try:
            result = subprocess.run(
                [opencode_cli, "auth", "login"],
                timeout=120,
            )
            if result.returncode == 0 and auth_file.exists():
                return True, "Login successful"
            return False, "Login failed or was cancelled"
        except FileNotFoundError:
            return False, "OpenCode CLI not found"
        except subprocess.TimeoutExpired:
            return False, "Login timed out"
        except Exception as e:
            return False, f"Login error: {e}"

    elif provider == "kimi":
        # Check isolated credentials for this account
        kimi_home = Path.home() / ".chad" / "kimi-homes" / account_name
        creds_file = kimi_home / ".kimi" / "credentials" / "kimi-code.json"
        global_creds = Path.home() / ".kimi" / "credentials" / "kimi-code.json"
        if creds_file.exists() or global_creds.exists():
            return True, "Already logged in"

        # Run interactive kimi login in the terminal
        kimi_cli = shutil.which("kimi")
        if not kimi_cli:
            return False, "Kimi CLI not found. Install with: npm install -g @anthropic-ai/kimi-code"

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
        fallback_order = client.get_provider_fallback_order()
        usage_threshold = client.get_usage_switch_threshold()
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
        fallback_str = " -> ".join(fallback_order) if fallback_order else "(none)"
        print(f"  Fallback Order:     {fallback_str}")
        print(f"  Usage Threshold:    {usage_threshold}%")
        print()

        print("Settings Menu:")
        print("  [1] Manage accounts")
        print("  [2] Set cleanup days")
        print("  [3] Set verification agent")
        print("  [4] Set verification model")
        print("  [5] Set max verification attempts")
        print("  [6] Set provider fallback order")
        print("  [7] Set usage threshold")
        print("  [8] Set UI mode")
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
            # Set provider fallback order
            print()
            print("Provider Fallback Order")
            print("-" * 30)
            if fallback_order:
                print("Current order:")
                for i, name in enumerate(fallback_order, 1):
                    print(f"  {i}. {name}")
            else:
                print("No fallback order set.")
            print()
            print("Enter account names separated by commas, in priority order.")
            print("Available accounts:", ", ".join(acc.name for acc in accounts) if accounts else "(none)")
            print()
            try:
                order_input = input("New order (or Enter to keep current): ").strip()
                if order_input:
                    new_order = [name.strip() for name in order_input.split(",") if name.strip()]
                    # Validate account names
                    valid_names = {acc.name for acc in accounts}
                    invalid = [name for name in new_order if name not in valid_names]
                    if invalid:
                        print(f"Unknown accounts: {', '.join(invalid)}")
                    else:
                        client.set_provider_fallback_order(new_order)
                        print(f"Fallback order set: {' -> '.join(new_order)}")
            except Exception as e:
                print(f"Error: {e}")
            input("Press Enter to continue...")

        elif choice == "7":
            # Set usage threshold
            print()
            print(f"Current usage threshold: {usage_threshold}%")
            print("When provider usage exceeds this %, auto-switch to next fallback.")
            print("Set to 100 to disable usage-based switching.")
            try:
                new_threshold = input("New threshold (0-100): ").strip()
                if new_threshold:
                    threshold = int(new_threshold)
                    if 0 <= threshold <= 100:
                        client.set_usage_switch_threshold(threshold)
                        print(f"Usage threshold set to {threshold}%")
                    else:
                        print("Please enter a number between 0 and 100")
            except ValueError:
                print("Invalid number")
            input("Press Enter to continue...")

        elif choice == "8":
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

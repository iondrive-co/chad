"""Simple CLI for Chad - minimal terminal UI using API streaming."""

import os
import select
import shutil
import signal
import sys
import termios
import tty
from pathlib import Path

from chad.ui.client import APIClient
from chad.ui.client.stream_client import SyncStreamClient, decode_terminal_data


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

        # Find coding and verification agents
        coding_agent = None
        verification_agent = None
        for acc in accounts:
            if acc.role == "CODING":
                coding_agent = acc.name
            if acc.role == "VERIFICATION":
                verification_agent = acc.name

        print("Current Settings:")
        print(f"  Accounts:     {len(accounts)} configured")
        print(f"  Cleanup:      {cleanup.retention_days} days")
        print(f"  Coding Agent: {coding_agent or '(not set)'}")
        print(f"  Verification: {verification_agent or '(not set)'}")
        print()

        print("Settings Menu:")
        print("  [1] Manage accounts")
        print("  [2] Set cleanup days")
        print("  [3] Set verification agent")
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
                options = [("None (disabled)", "")] + [
                    (f"{acc.name} ({acc.provider})", acc.name)
                    for acc in accounts
                ]
                default_idx = 0
                if verification_agent:
                    for i, (_, val) in enumerate(options):
                        if val == verification_agent:
                            default_idx = i
                            break

                selected = select_from_list("Select verification agent:", options, default_idx)
                if selected is not None:
                    if selected == "":
                        # Clear verification role - need to find who has it
                        for acc in accounts:
                            if acc.role == "VERIFICATION":
                                client.set_account_role(acc.name, "")
                        print("Verification agent disabled")
                    else:
                        client.set_account_role(selected, "VERIFICATION")
                        print(f"Verification agent set to: {selected}")
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
                print("Account creation requires authentication.")

                if provider == "anthropic":
                    print("For Claude Code, run: claude auth login")
                    print("Then the account will use ~/.claude/.credentials.json")
                elif provider == "openai":
                    home_dir = Path.home() / ".chad" / "codex-homes" / account_name
                    print(f"For Codex, set CODEX_HOME={home_dir}")
                    print("Then run: codex auth")
                elif provider == "gemini":
                    print("For Gemini, run: gemini auth")
                    print("Then the account will use ~/.gemini/oauth_creds.json")
                elif provider == "qwen":
                    print("For Qwen, run: qwen auth")
                elif provider == "mistral":
                    print("For Vibe, run: vibe auth")

                print()
                print("Note: Account creation via API requires OAuth flow.")
                print("Use the Gradio UI for full account setup, or manually")
                print("configure credentials and add to ~/.chad.conf")

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
) -> int:
    """Run a task with PTY streaming via API.

    Args:
        client: API client for REST calls
        stream_client: Streaming client for SSE
        session_id: Session ID
        project_path: Project path
        task_description: Task description
        coding_account: Account to use

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

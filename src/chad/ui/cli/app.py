"""Simple CLI for Chad - minimal terminal UI using API."""

import os
import subprocess
import sys
from pathlib import Path

from chad.ui.client import APIClient


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
        print(f"  Cleanup:      {cleanup.get('cleanup_days', 7)} days")
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
            current_days = cleanup.get("cleanup_days", 7)
            print(f"Current cleanup: {current_days} days")
            try:
                new_days = input("New cleanup days (1-365): ").strip()
                if new_days:
                    days = int(new_days)
                    if 1 <= days <= 365:
                        client.update_cleanup_settings(cleanup_days=days)
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


def run_cli(client: APIClient) -> None:
    """Run the simple CLI interface.

    Args:
        client: API client instance
    """
    from chad.ui.cli.pty_runner import build_agent_command, run_agent_pty
    from chad.util.git_worktree import GitWorktreeManager

    while True:
        clear_screen()
        print_header()

        # Load accounts and preferences from API
        accounts = client.list_accounts()
        prefs = client.get_preferences()
        default_project = prefs.get("last_project_path", "")

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
                    client.update_preferences(last_project_path=expanded)
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

            # Create worktree
            print()
            print("Creating worktree...")
            import uuid
            task_id = uuid.uuid4().hex[:8]
            worktree_manager = GitWorktreeManager(project_path)
            worktree_path, branch = worktree_manager.create_worktree(task_id)
            print(f"Created worktree: {branch}")
            print(f"Working in: {worktree_path}")

            # Build agent command
            cmd, env = build_agent_command(coding_provider, coding_account, Path(worktree_path))

            # Clear and hand off to agent
            clear_screen()
            print(f"Handing off to {coding_provider} agent...")
            print(f"Working in: {worktree_path}")
            print(f"Task: {task_description[:80]}...")
            print("-" * 60)
            print()

            # For claude, send prompt via stdin
            initial_input = None
            if coding_provider == "anthropic":
                initial_input = task_description + "\n"

            # Run agent with PTY passthrough
            exit_code = run_agent_pty(
                cmd=cmd,
                cwd=Path(worktree_path),
                env=env,
                initial_input=initial_input,
            )

            print()
            print("-" * 60)
            print(f"Agent exited with code: {exit_code}")

            # Check for changes
            result = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            if result.stdout.strip():
                # There are changes - offer merge options
                print()
                print("Changes detected:")
                print(result.stdout)
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
                        subprocess.run(["git", "diff", "HEAD"], cwd=worktree_path)
                        continue

                    elif action == "m":
                        # Merge to main
                        print("\nMerging changes...")
                        try:
                            # Get main branch name
                            main_result = subprocess.run(
                                ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                                cwd=project_path,
                                capture_output=True,
                                text=True,
                            )
                            if main_result.returncode == 0:
                                main_branch = main_result.stdout.strip().replace("origin/", "")
                            else:
                                main_branch = "main"

                            # Commit if needed
                            subprocess.run(["git", "add", "-A"], cwd=worktree_path)
                            subprocess.run(
                                ["git", "commit", "-m", f"Task: {task_description[:50]}"],
                                cwd=worktree_path,
                            )

                            # Merge
                            subprocess.run(
                                ["git", "checkout", main_branch],
                                cwd=project_path,
                            )
                            merge_result = subprocess.run(
                                ["git", "merge", "--no-ff", branch, "-m", f"Merge {branch}"],
                                cwd=project_path,
                                capture_output=True,
                                text=True,
                            )

                            if merge_result.returncode == 0:
                                print("Merge successful!")
                                worktree_manager.delete_worktree(task_id)
                            else:
                                print("Merge failed:")
                                print(merge_result.stderr)
                                print("\nWorktree kept for manual resolution.")

                        except Exception as e:
                            print(f"Error during merge: {e}")

                        break

                    elif action == "x":
                        # Discard
                        print("\nDiscarding changes...")
                        worktree_manager.delete_worktree(task_id)
                        print("Worktree removed.")
                        break

                    elif action == "k":
                        # Keep for later
                        print(f"\nWorktree kept at: {worktree_path}")
                        print(f"Branch: {branch}")
                        break

                    else:
                        print("Please enter m, d, x, or k")

            else:
                print("\nNo changes made by agent.")
                worktree_manager.delete_worktree(task_id)

            input("\nPress Enter to continue...")


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

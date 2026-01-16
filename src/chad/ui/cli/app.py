"""Simple CLI for Chad - minimal terminal UI."""

import os
import subprocess
import sys
from pathlib import Path


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


def run_cli(config_manager, password: str | None = None) -> None:
    """Run the simple CLI interface.

    Args:
        config_manager: ConfigManager instance
        password: Main password (already verified)
    """
    from chad.ui.cli.pty_runner import build_agent_command, run_agent_pty
    from chad.util.git_worktree import GitWorktreeManager

    while True:
        clear_screen()
        print_header()

        # Load accounts and preferences
        accounts = config_manager.list_accounts()
        prefs = config_manager.load_preferences() or {}
        default_project = prefs.get("project_path", "")
        coding_account = config_manager.get_role_assignment("CODING")

        if not accounts:
            print("No accounts configured.")
            print("Please run Chad in Gradio mode to set up accounts:")
            print("  chad --ui gradio")
            print()
            input("Press Enter to exit...")
            return

        # Show current settings
        print(f"Project: {default_project or '(not set)'}")
        if coding_account and coding_account in accounts:
            provider = accounts[coding_account]
            print(f"Agent:   {coding_account} ({provider})")
        print()

        # Main menu
        print("What would you like to do?")
        print("  [1] Start a task")
        print("  [2] Change project path")
        print("  [3] Change agent")
        print("  [s] Settings (opens Gradio)")
        print("  [q] Quit")
        print()

        try:
            choice = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "q":
            break

        elif choice == "s":
            print("\nTo access settings, run: chad --ui gradio")
            input("Press Enter to continue...")

        elif choice == "2":
            # Change project path
            print()
            new_path = input(f"Project path [{default_project}]: ").strip()
            if new_path:
                expanded = str(Path(new_path).expanduser().resolve())
                if Path(expanded).exists():
                    config_manager.save_preferences(expanded)
                    print(f"Project path set to: {expanded}")
                else:
                    print(f"Path does not exist: {expanded}")
            input("Press Enter to continue...")

        elif choice == "3":
            # Change agent
            print()
            options = [(f"{name} ({provider})", name) for name, provider in accounts.items()]
            default_idx = 0
            if coding_account:
                for i, (_, val) in enumerate(options):
                    if val == coding_account:
                        default_idx = i
                        break

            selected = select_from_list("Select coding agent:", options, default_idx)
            if selected:
                config_manager.assign_role(selected, "CODING")
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

            if not coding_account or coding_account not in accounts:
                print("\nPlease select a coding agent first.")
                input("Press Enter to continue...")
                continue

            provider = accounts[coding_account]

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
            cmd, env = build_agent_command(provider, coding_account, Path(worktree_path))

            # Clear and hand off to agent
            clear_screen()
            print(f"Handing off to {provider} agent...")
            print(f"Working in: {worktree_path}")
            print(f"Task: {task_description[:80]}...")
            print("-" * 60)
            print()

            # For claude, send prompt via stdin
            initial_input = None
            if provider == "anthropic":
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


def launch_cli_ui(config_manager=None, password: str | None = None) -> None:
    """Launch the Chad CLI UI.

    Args:
        config_manager: Optional ConfigManager instance
        password: Optional pre-authenticated password
    """
    from chad.util.config_manager import ConfigManager

    if config_manager is None:
        config_manager = ConfigManager()

    try:
        run_cli(config_manager, password)
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)

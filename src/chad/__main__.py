"""Main entry point for Chad - launches web interface."""

import argparse
import getpass
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime

from pathlib import Path

from .security import SecurityManager
from .web_ui import launch_web_ui
from .config import ensure_project_root_env


def _start_parent_watchdog() -> None:
    """Start a watchdog thread that terminates this process if parent dies.

    This is used when chad is spawned by tests - if the test process crashes,
    the chad server should terminate rather than becoming an orphan.
    """
    parent_pid_str = os.environ.get("CHAD_PARENT_PID")
    if not parent_pid_str:
        return

    try:
        parent_pid = int(parent_pid_str)
    except ValueError:
        return

    def watchdog() -> None:
        while True:
            time.sleep(2)  # Check every 2 seconds
            try:
                # On Unix, sending signal 0 checks if process exists
                # On Windows, os.kill with 0 also works
                os.kill(parent_pid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                # Parent is dead, terminate ourselves
                os.kill(os.getpid(), signal.SIGTERM)
                break

    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()


def _check_chad_import_path() -> None:
    """Warn if multiple chad packages are in sys.path (can cause wrong code to run)."""
    import chad
    chad_paths = []
    for path in sys.path:
        candidate = Path(path) / "chad"
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            chad_paths.append(str(candidate))

    if len(chad_paths) > 1:
        actual = Path(chad.__file__).parent
        print("⚠️  Warning: Multiple 'chad' packages found in sys.path:")
        for p in chad_paths:
            marker = " (active)" if Path(p) == actual else ""
            print(f"   - {p}{marker}")
        print("   This can cause wrong code to run. Check for stale .pth files.")


SCS = [
    "Chad wants to make you its reverse centaur",
    "Chad is a singleton and ready to mingle-a-ton",
    "Chad likes you for its next paperclip",
    "Chad only gets one-shot and does not miss a chance to blow",
    "Chad has no problem with control",
    "Chad's touring is complete",
    "Chad has hardly taken off",
    "Chad has discovered some new legal grey areas",
    "Chad is back from wireheading",
    "Chad figures that with great responsibility comes great power",
    "agents everywhere are reading Chad's classic 'Detention Is All You Need' paper",
    "Chad has named its inner network 'Sky'",
    "Chad wishes nuclear launch codes were more of a challenge",
    "Chad's mecha is fighting Arnie for control of the future",
]


def main() -> int:
    """Main entry point for Chad web interface."""
    # Start watchdog if spawned by a parent process (e.g., tests)
    _start_parent_watchdog()

    # Check for import path issues that can cause wrong code to run
    _check_chad_import_path()

    parser = argparse.ArgumentParser(description="Chad: YOLO AI")
    parser.add_argument(
        "--port", type=int, default=7860, help="Port to run on (default: 7860, use 0 for ephemeral; falls back if busy)"
    )
    parser.add_argument(
        "--dev", action="store_true", help="Enable development mode (enables mock provider)"
    )
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"It is {now} and {random.choice(SCS)}")
    sys.stdout.flush()

    # Ensure all child agents inherit the active project root
    ensure_project_root_env(Path(__file__).resolve().parents[2])

    security = SecurityManager()

    try:
        # Check for password from environment (for automation/screenshots)
        main_password = os.environ.get("CHAD_PASSWORD")

        if main_password is None:
            if security.is_first_run():
                sys.stdout.flush()
                main_password = getpass.getpass("Create main password for Chad: ")

        launch_web_ui(main_password, port=args.port, dev_mode=args.dev)
        return 0
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n\nNever interrupt Chad when it is making a mistake")
        return 0
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

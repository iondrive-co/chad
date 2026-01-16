"""Main entry point for Chad - launches web interface or server."""

import argparse
import atexit
import getpass
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Literal

from pathlib import Path

from .util.cleanup import cleanup_on_startup, cleanup_on_shutdown
from .util.config_manager import ConfigManager
from .ui.gradio.web_ui import launch_web_ui
from .util.config import ensure_project_root_env

# Supported run modes
RunMode = Literal["unified", "server", "ui"]


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


def find_free_port() -> int:
    """Find a free port by binding to port 0."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def run_server(host: str = "0.0.0.0", port: int = 0) -> None:
    """Run the Chad API server.

    Args:
        host: Host to bind to
        port: Port to run on (0 for ephemeral)
    """
    import uvicorn
    from chad.server.main import create_app

    if port == 0:
        port = find_free_port()

    app = create_app()
    print(f"Starting Chad API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


def run_unified(main_password: str | None, ui_port: int, api_port: int, dev_mode: bool) -> None:
    """Run both server and UI in one process.

    The API server runs in a background thread while the UI runs in the main thread.

    Args:
        main_password: Main password for config encryption
        ui_port: Port for Gradio UI (0 for ephemeral)
        api_port: Port for API server (0 for ephemeral)
        dev_mode: Enable development mode
    """
    import uvicorn
    from chad.server.main import create_app

    # Use ephemeral port if 0
    if api_port == 0:
        api_port = find_free_port()

    # Start API server in background thread
    app = create_app()
    server_config = uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
    server = uvicorn.Server(server_config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Give server a moment to start
    time.sleep(0.5)
    print(f"API server running on http://127.0.0.1:{api_port}")

    # Run UI in main thread (blocking) - Gradio handles ephemeral ports itself
    launch_web_ui(main_password, port=ui_port, dev_mode=dev_mode)


def main() -> int:
    """Main entry point for Chad web interface."""
    # Start watchdog if spawned by a parent process (e.g., tests)
    _start_parent_watchdog()

    # Check for import path issues that can cause wrong code to run
    _check_chad_import_path()

    parser = argparse.ArgumentParser(description="Chad: YOLO AI")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["unified", "server", "ui"],
        default="unified",
        help="Run mode: unified (default, both server+UI), server (API only), ui (Gradio only)",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Port for UI (default: 0 = ephemeral)"
    )
    parser.add_argument(
        "--api-port", type=int, default=0, help="Port for API server (default: 0 = ephemeral)"
    )
    parser.add_argument(
        "--api-host", type=str, default="0.0.0.0", help="Host for API server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--server-url", type=str, default=None, help="Server URL for UI mode (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--dev", action="store_true", help="Enable development mode (enables mock provider)"
    )
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"It is {now} and {random.choice(SCS)}")
    sys.stdout.flush()

    # Ensure all child agents inherit the active project root
    project_root = Path(__file__).resolve().parents[2]
    ensure_project_root_env(project_root)

    # Run startup cleanup (worktrees, logs, screenshots older than N days)
    config_mgr = ConfigManager()
    cleanup_days = config_mgr.get_cleanup_days()
    cleanup_results = cleanup_on_startup(project_root, cleanup_days)
    if cleanup_results:
        total = sum(len(items) for items in cleanup_results.values())
        print(f"Cleaned up {total} old files/directories (>{cleanup_days} days old)")

    # Register shutdown cleanup
    atexit.register(cleanup_on_shutdown)

    try:
        # Server-only mode - no password needed
        if args.mode == "server":
            run_server(host=args.api_host, port=args.api_port)
            return 0

        # UI modes need password
        main_password = os.environ.get("CHAD_PASSWORD")

        if main_password is None:
            if config_mgr.is_first_run():
                sys.stdout.flush()
                main_password = getpass.getpass("Create main password for Chad: ")

        if args.mode == "unified":
            # Run both server and UI
            run_unified(main_password, ui_port=args.port, api_port=args.api_port, dev_mode=args.dev)
        else:
            # UI-only mode (connects to external server)
            if args.server_url:
                os.environ["CHAD_SERVER_URL"] = args.server_url
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

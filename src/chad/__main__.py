"""Main entry point for Chad - launches web interface or server."""

import argparse
import atexit
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
    "Chad is hyping its parameters",
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


def get_chad_dir() -> Path:
    """Get the Chad data directory, creating it if needed.

    Uses CHAD_DIR env var if set, otherwise ~/.chad.
    """
    env_dir = os.environ.get("CHAD_DIR")
    if env_dir:
        chad_dir = Path(env_dir)
    else:
        chad_dir = Path.home() / ".chad"
    chad_dir.mkdir(parents=True, exist_ok=True)
    return chad_dir


def write_server_port(port: int) -> None:
    """Write the server port to a file for autodiscovery."""
    port_file = get_chad_dir() / "server.port"
    port_file.write_text(f"{port}\n")


def read_server_port() -> int | None:
    """Read the server port from the autodiscovery file.

    Returns:
        The port number, or None if not available or invalid.
    """
    port_file = get_chad_dir() / "server.port"
    if not port_file.exists():
        return None
    try:
        return int(port_file.read_text().strip())
    except (ValueError, OSError):
        return None


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

    # Write port for autodiscovery by other clients
    write_server_port(port)

    app = create_app()
    print(f"Starting Chad API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


def run_unified(
    main_password: str | None,
    ui_port: int,
    api_port: int,
    dev_mode: bool,
    ui_mode: str = "gradio",
    server_url: str | None = None,
) -> None:
    """Run UI, optionally with a local API server.

    If server_url is provided, connects to that server. Otherwise starts a local
    API server in a background thread.

    Args:
        main_password: Main password for config encryption
        ui_port: Port for Gradio UI (0 for ephemeral)
        api_port: Port for API server (0 for ephemeral)
        dev_mode: Enable development mode
        ui_mode: UI mode - "gradio" or "cli"
        server_url: External server URL to connect to (skips local server)
    """
    if server_url:
        # Connect to existing server
        api_base_url = server_url
        print(f"Connecting to API server at {api_base_url}")
    else:
        # Start local API server
        import uvicorn
        from chad.server.main import create_app

        if api_port == 0:
            api_port = find_free_port()

        app = create_app()
        server_config = uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
        server = uvicorn.Server(server_config)

        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        time.sleep(0.5)
        api_base_url = f"http://127.0.0.1:{api_port}"
        # Write port for autodiscovery by other clients
        write_server_port(api_port)
        print(f"API server running on {api_base_url}")

    # Run UI in main thread (blocking)
    if ui_mode == "cli":
        from chad.ui.cli import launch_cli_ui
        launch_cli_ui(api_base_url=api_base_url, password=main_password)
    else:
        from chad.ui.gradio.gradio_ui import launch_web_ui
        launch_web_ui(api_base_url=api_base_url, port=ui_port, dev_mode=dev_mode)


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
        choices=["unified", "server"],
        default="unified",
        help="Run mode: unified (default, UI + local server), server (API only)",
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
        "--server-url", type=str, default=None,
        help="Connect to existing API server (use 'auto' to autodiscover port from ~/.chad/server.port)"
    )
    parser.add_argument(
        "--dev", action="store_true", help="Enable development mode (enables mock provider)"
    )
    parser.add_argument(
        "--ui",
        type=str,
        choices=["gradio", "cli"],
        default=None,
        help="UI mode: gradio (web) or cli (terminal). Overrides config preference.",
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

        # Handle server URL autodiscovery early so we can skip password prompt when connecting
        server_url = args.server_url
        if server_url == "auto":
            discovered_port = read_server_port()
            if discovered_port is None:
                print("❌ No running Chad server found (check ~/.chad/server.port)")
                return 1
            server_url = f"http://127.0.0.1:{discovered_port}"
            print(f"Autodiscovered server at port {discovered_port}")

        # Determine UI mode from args or config
        ui_mode = args.ui if args.ui else config_mgr.get_ui_mode()

        # UI modes need password only when starting a local API server
        main_password = None
        needs_password = server_url is None

        if needs_password:
            main_password = os.environ.get("CHAD_PASSWORD")

            if main_password is None:
                if config_mgr.is_first_run():
                    main_password = config_mgr.setup_main_password()
                else:
                    main_password = config_mgr.verify_main_password()

        # Run UI with optional local server (--server-url skips local server)
        run_unified(
            main_password,
            ui_port=args.port,
            api_port=args.api_port,
            dev_mode=args.dev,
            ui_mode=ui_mode,
            server_url=server_url,
        )

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

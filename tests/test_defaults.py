from __future__ import annotations

import inspect
from pathlib import Path


DEFAULT_PORT = 3184


def test_default_ports_are_consistent():
    import chad.__main__ as chad_main
    from chad.ui.client.api_client import APIClient
    from chad.ui.client.stream_client import StreamClient, SyncStreamClient
    from chad.ui.client.ws_client import WSClient
    from chad.ui.cli import app as cli_app

    assert chad_main.CHAD_DEFAULT_PORT == DEFAULT_PORT

    api_client = APIClient()
    try:
        assert api_client.base_url == f"http://localhost:{DEFAULT_PORT}"
    finally:
        api_client.close()

    stream_client = StreamClient()
    assert stream_client.base_url == f"http://localhost:{DEFAULT_PORT}"

    sync_stream = SyncStreamClient()
    try:
        assert sync_stream.base_url == f"http://localhost:{DEFAULT_PORT}"
    finally:
        sync_stream.close()

    ws_client = WSClient()
    assert ws_client.base_url == f"ws://localhost:{DEFAULT_PORT}"

    launch_sig = inspect.signature(cli_app.launch_cli_ui)
    assert launch_sig.parameters["api_base_url"].default == f"http://localhost:{DEFAULT_PORT}"


def test_ui_connection_default_is_localhost_3184():
    text = Path("ui/src/App.tsx").read_text()
    assert "127.0.0.1:3184" in text
    assert "localhost:3814" not in text


def test_ui_project_path_prefers_preferences_over_cwd():
    text = Path("ui/src/App.tsx").read_text()
    assert "status.cwd" not in text
    assert "getPreferences" in text

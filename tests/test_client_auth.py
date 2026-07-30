"""Tests for client-side authentication against a tunneled (token-protected) server.

Covers:
- APIClient bearer header + ChadAuthError on 401
- StreamClient / SyncStreamClient bearer header
- WSClient / AsyncWSClient single-use ticket minting
- CLI threading of the parsed pairing token
- Config export/import passphrase contract
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from chad.ui.client import APIClient, ChadAuthError
from chad.ui.client.api_client import CleanupSettings, Preferences
from chad.ui.client.stream_client import StreamClient, SyncStreamClient
from chad.ui.client.ws_client import AsyncWSClient, WSClient


def _api_client(handler, token=None):
    """APIClient whose HTTP layer is a mock transport (keeps default headers)."""
    api = APIClient(base_url="http://test", token=token)
    api._client._transport = httpx.MockTransport(handler)
    return api


class TestAPIClientAuth:
    """APIClient sends the bearer header and surfaces 401 as ChadAuthError."""

    def test_bearer_header_sent_on_api_requests(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"accounts": []})

        api = _api_client(handler, token="sekret")
        api.list_accounts()
        assert seen["auth"] == "Bearer sekret"

    def test_no_auth_header_without_token(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"accounts": []})

        api = _api_client(handler)
        api.list_accounts()
        assert seen["auth"] is None

    def test_get_status_works_with_token(self):
        def handler(request):
            assert request.url.path == "/status"
            return httpx.Response(200, json={"status": "healthy", "version": "1.0"})

        api = _api_client(handler, token="sekret")
        assert api.get_status()["status"] == "healthy"

    def test_401_raises_chad_auth_error(self):
        def handler(request):
            return httpx.Response(401, json={"detail": "Invalid or missing authentication token"})

        api = _api_client(handler)
        with pytest.raises(ChadAuthError, match="authentication token"):
            api.list_sessions()

    def test_chad_auth_error_is_runtime_error(self):
        assert issubclass(ChadAuthError, RuntimeError)

    def test_non_401_errors_stay_httpx_errors(self):
        def handler(request):
            return httpx.Response(500, json={"detail": "boom"})

        api = _api_client(handler, token="sekret")
        with pytest.raises(httpx.HTTPStatusError):
            api.list_sessions()


class TestConfigTransferContract:
    """export_config/import_config follow the passphrase POST contract."""

    def test_export_config_posts_passphrase(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"credentials_included": True})

        api = _api_client(handler, token="sekret")
        data = api.export_config("hunter2")
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/config/export"
        assert seen["body"] == {"passphrase": "hunter2"}
        assert data["credentials_included"] is True

    def test_export_config_without_passphrase_sends_null(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"credentials_included": False})

        api = _api_client(handler)
        data = api.export_config()
        assert seen["body"] == {"passphrase": None}
        assert data["credentials_included"] is False

    def test_import_config_posts_config_and_passphrase(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "message": "imported"})

        api = _api_client(handler)
        config = {"accounts": {}, "provider_auth_encrypted": "abc", "provider_auth_salt": "s"}
        result = api.import_config(config, "hunter2")
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/config/import"
        assert seen["body"] == {"config": config, "passphrase": "hunter2"}
        assert result["ok"] is True

    def test_import_config_wrong_passphrase_is_http_400(self):
        def handler(request):
            return httpx.Response(400, json={"detail": "wrong passphrase"})

        api = _api_client(handler)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            api.import_config({"provider_auth_encrypted": "abc"}, "wrong")
        assert exc_info.value.response.status_code == 400


class TestStreamClientAuth:
    """Stream clients carry the bearer header and surface 401s."""

    def test_sync_stream_client_sets_bearer_header(self):
        client = SyncStreamClient("http://test", token="sekret")
        assert client._sync_client.headers["Authorization"] == "Bearer sekret"
        client.close()

    def test_sync_stream_client_no_header_without_token(self):
        client = SyncStreamClient("http://test")
        assert "Authorization" not in client._sync_client.headers
        client.close()

    def test_async_stream_client_sets_bearer_header(self):
        stream = StreamClient("http://test", token="sekret")

        async def check():
            client = await stream._get_async_client()
            header = client.headers["Authorization"]
            await stream.close()
            return header

        assert asyncio.run(check()) == "Bearer sekret"

    def test_sync_stream_events_401_raises_auth_error(self):
        def handler(request):
            return httpx.Response(401, json={"detail": "nope"})

        client = SyncStreamClient("http://test", token="stale")
        client._sync_client._transport = httpx.MockTransport(handler)
        with pytest.raises(ChadAuthError):
            for _ in client.stream_events("sess-1"):
                pass
        client.close()


class TestWSClientTickets:
    """WSClient mints a fresh single-use ticket for every connection."""

    def test_connect_mints_ticket_and_puts_it_in_url(self, monkeypatch):
        posts = []
        connected = []

        def fake_post(url, headers=None, timeout=None):
            posts.append((url, dict(headers or {})))
            return httpx.Response(
                200,
                json={"ticket": f"tick-{len(posts)}", "expires_in": 60},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr("chad.ui.client.ws_client.httpx.post", fake_post)
        monkeypatch.setattr(
            "chad.ui.client.ws_client.sync_connect",
            lambda url: connected.append(url) or MagicMock(),
        )

        ws = WSClient("http://test", token="sekret")
        ws.connect("sess-1", since_seq=5)

        assert posts[0][0] == "http://test/api/v1/ws-ticket/sess-1"
        assert posts[0][1]["Authorization"] == "Bearer sekret"
        assert connected[0] == "ws://test/api/v1/ws/sess-1?since_seq=5&ticket=tick-1"

    def test_reconnect_mints_fresh_ticket(self, monkeypatch):
        """Tickets are single-use server-side: every connect must mint a new one."""
        posts = []
        connected = []

        def fake_post(url, headers=None, timeout=None):
            posts.append(url)
            return httpx.Response(
                200,
                json={"ticket": f"tick-{len(posts)}", "expires_in": 60},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr("chad.ui.client.ws_client.httpx.post", fake_post)
        monkeypatch.setattr(
            "chad.ui.client.ws_client.sync_connect",
            lambda url: connected.append(url) or MagicMock(),
        )

        ws = WSClient("http://test", token="sekret")
        ws.connect("sess-1")
        ws.connect("sess-1", since_seq=42)

        assert len(posts) == 2
        assert connected[0].endswith("ticket=tick-1")
        assert connected[1] == "ws://test/api/v1/ws/sess-1?since_seq=42&ticket=tick-2"

    def test_connect_without_token_skips_ticket(self, monkeypatch):
        connected = []

        def fail_post(*args, **kwargs):
            raise AssertionError("must not mint a ticket without a token")

        monkeypatch.setattr("chad.ui.client.ws_client.httpx.post", fail_post)
        monkeypatch.setattr(
            "chad.ui.client.ws_client.sync_connect",
            lambda url: connected.append(url) or MagicMock(),
        )

        ws = WSClient("http://test")
        ws.connect("sess-1", since_seq=3)
        assert connected[0] == "ws://test/api/v1/ws/sess-1?since_seq=3"

    def test_ticket_mint_401_raises_auth_error(self, monkeypatch):
        def fake_post(url, headers=None, timeout=None):
            return httpx.Response(
                401,
                json={"detail": "nope"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr("chad.ui.client.ws_client.httpx.post", fake_post)

        ws = WSClient("http://test", token="stale")
        with pytest.raises(ChadAuthError):
            ws.connect("sess-1")

    def test_async_connect_mints_ticket(self, monkeypatch):
        posts = []
        connected = []

        def handler(request):
            posts.append((str(request.url), request.headers.get("authorization")))
            return httpx.Response(200, json={"ticket": "tick-async", "expires_in": 60})

        real_async_client = httpx.AsyncClient

        def fake_async_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(**kwargs)

        async def fake_connect(url):
            connected.append(url)
            return MagicMock()

        monkeypatch.setattr("chad.ui.client.ws_client.httpx.AsyncClient", fake_async_client)
        monkeypatch.setattr("chad.ui.client.ws_client.websockets.connect", fake_connect)

        ws = AsyncWSClient("https://test", token="sekret")
        asyncio.run(ws.connect("sess-9", since_seq=7))

        assert posts[0] == ("https://test/api/v1/ws-ticket/sess-9", "Bearer sekret")
        assert connected[0] == "wss://test/api/v1/ws/sess-9?since_seq=7&ticket=tick-async"


class _FakeCLIClient:
    """Minimal APIClient stand-in for driving run_cli."""

    def __init__(self, base_url="http://test", token="tok"):
        self.base_url = base_url
        self.token = token
        self.closed = False

    def get_status(self):
        return {"version": "1.0", "status": "healthy"}

    def list_accounts(self):
        return []

    def get_preferences(self):
        return Preferences(last_project_path="", ui_mode="cli")

    def get_cleanup_settings(self):
        return CleanupSettings(retention_days=7, auto_cleanup=True)

    def get_verification_agent(self):
        return None

    def list_sessions(self):
        return []

    def close(self):
        self.closed = True


class TestCLIPairingToken:
    """The CLI keeps the parsed pairing token and threads it into every client."""

    def test_launch_cli_ui_threads_pairing_token(self, monkeypatch):
        from chad.ui.cli.app import launch_cli_ui

        created = []

        def fake_api_client(base_url="", token=None):
            client = _FakeCLIClient(base_url=base_url, token=token)
            created.append(client)
            return client

        monkeypatch.setattr("chad.ui.cli.app.APIClient", fake_api_client)
        monkeypatch.setattr("chad.ui.cli.app.run_cli", lambda client: None)

        launch_cli_ui(api_base_url="my-tunnel:sekret")

        assert created[0].base_url == "https://my-tunnel.trycloudflare.com"
        assert created[0].token == "sekret"

    def test_disconnected_menu_threads_token(self, monkeypatch):
        from chad.ui.cli.app import _run_disconnected_menu

        created = []

        class FailingClient(_FakeCLIClient):
            def __init__(self, base_url="", token=None):
                super().__init__(base_url=base_url, token=token)
                created.append(self)

            def get_status(self):
                raise ConnectionError("still down")

        monkeypatch.setattr("chad.ui.cli.app.APIClient", FailingClient)
        inputs = iter(["1", "my-tunnel:sekret", "q"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        _run_disconnected_menu("http://localhost:3184")

        assert created[0].base_url == "https://my-tunnel.trycloudflare.com"
        assert created[0].token == "sekret"

    def test_disconnected_menu_retry_reuses_original_token(self, monkeypatch):
        from chad.ui.cli.app import _run_disconnected_menu

        created = []

        class FailingClient(_FakeCLIClient):
            def __init__(self, base_url="", token=None):
                super().__init__(base_url=base_url, token=token)
                created.append(self)

            def get_status(self):
                raise ConnectionError("still down")

        monkeypatch.setattr("chad.ui.cli.app.APIClient", FailingClient)
        inputs = iter(["2", "q"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        _run_disconnected_menu("https://x.trycloudflare.com", "sekret")

        assert created[0].base_url == "https://x.trycloudflare.com"
        assert created[0].token == "sekret"

    def test_run_cli_threads_token_to_stream_client(self, monkeypatch):
        from chad.ui.cli.app import run_cli

        captured = {}

        class FakeStream:
            def __init__(self, base_url="", token=None):
                captured["base_url"] = base_url
                captured["token"] = token

            def close(self):
                pass

        monkeypatch.setattr("chad.ui.cli.app.SyncStreamClient", FakeStream)
        monkeypatch.setattr("os.system", lambda _cmd: None)
        inputs = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        run_cli(_FakeCLIClient(base_url="http://test", token="sekret"))

        assert captured["base_url"] == "http://test"
        assert captured["token"] == "sekret"

    def test_run_cli_auth_error_shows_repair_message(self, monkeypatch, capsys):
        from chad.ui.cli.app import run_cli

        class AuthFailingClient(_FakeCLIClient):
            def list_accounts(self):
                raise ChadAuthError()

        class FakeStream:
            def __init__(self, base_url="", token=None):
                pass

            def close(self):
                pass

        monkeypatch.setattr("chad.ui.cli.app.SyncStreamClient", FakeStream)
        monkeypatch.setattr("os.system", lambda _cmd: None)

        # Must not raise a traceback out of the menu loop
        run_cli(AuthFailingClient(token=None))

        out = capsys.readouterr().out
        assert "authentication token" in out
        assert "chad --tunnel" in out


class TestCLIConfigTransferFlow:
    """Settings menu option 9 uses the passphrase export/import contract."""

    def _settings_client(self):
        client = MagicMock()
        client.list_accounts.return_value = []
        client.get_cleanup_settings.return_value = CleanupSettings(
            retention_days=7, auto_cleanup=True
        )
        client.get_preferences.return_value = Preferences(
            last_project_path="", ui_mode="cli"
        )
        client.get_verification_agent.return_value = None
        client.get_preferred_verification_model.return_value = None
        client.get_max_verification_attempts.return_value = 5
        client.get_local_endpoint.return_value = "http://localhost:8000"
        client.get_action_settings.return_value = []
        client.get_slack_settings.return_value = {
            "enabled": False,
            "channel": None,
            "has_token": False,
        }
        return client

    def test_export_prompts_for_passphrase(self, monkeypatch, tmp_path):
        from chad.ui.cli.app import run_settings_menu

        client = self._settings_client()
        client.export_config.return_value = {"credentials_included": True}

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("os.system", lambda _cmd: None)
        inputs = iter(["9", "e", "hunter2", "", "b"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        run_settings_menu(client)

        client.export_config.assert_called_once_with("hunter2")
        assert (tmp_path / "chad-config.json").exists()

    def test_export_blank_passphrase_excludes_credentials(self, monkeypatch, tmp_path):
        from chad.ui.cli.app import run_settings_menu

        client = self._settings_client()
        client.export_config.return_value = {"credentials_included": False}

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("os.system", lambda _cmd: None)
        inputs = iter(["9", "e", "", "", "b"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        run_settings_menu(client)

        client.export_config.assert_called_once_with(None)

    def test_import_prompts_passphrase_only_for_encrypted_export(self, monkeypatch, tmp_path):
        from chad.ui.cli.app import run_settings_menu

        client = self._settings_client()
        client.import_config.return_value = {"ok": True, "message": "imported"}
        config = {"accounts": {}, "provider_auth_encrypted": "abc", "provider_auth_salt": "s"}
        config_file = tmp_path / "export.json"
        config_file.write_text(json.dumps(config))

        monkeypatch.setattr("os.system", lambda _cmd: None)
        inputs = iter(["9", "i", str(config_file), "hunter2", "", "b"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        run_settings_menu(client)

        client.import_config.assert_called_once_with(config, "hunter2")

    def test_import_plain_export_skips_passphrase_prompt(self, monkeypatch, tmp_path):
        from chad.ui.cli.app import run_settings_menu

        client = self._settings_client()
        client.import_config.return_value = {"ok": True, "message": "imported"}
        config = {"accounts": {}}
        config_file = tmp_path / "export.json"
        config_file.write_text(json.dumps(config))

        monkeypatch.setattr("os.system", lambda _cmd: None)
        # No passphrase input in the sequence: prompting would exhaust the iterator
        inputs = iter(["9", "i", str(config_file), "", "b"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        run_settings_menu(client)

        client.import_config.assert_called_once_with(config, None)

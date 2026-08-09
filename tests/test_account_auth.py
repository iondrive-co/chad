"""Logged-out accounts must be visible, refused early, and never shown as 0% usage.

Regression cover for session ff391be9: a Claude account whose OAuth token had
expired 24 days earlier reported "Ready", was accepted for a task, and left the
user watching a blank terminal for 3m14s while the CLI retried a token refresh
that could never succeed — then reported 0% session/weekly usage on top.

The rules these tests pin down, for any account whose token is dead:
  * the account list says logged out, not ready;
  * starting a task is refused immediately, before any agent is spawned;
  * usage reports "logged out" rather than a fabricated 0%.

An *expired* token is not by itself logged out — the CLI refreshes tokens on use,
so an account idle overnight is normal. Logged out means the refresh itself was
refused.
"""

import json
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.state import get_config_manager, reset_state
from chad.util import provider_login, providers


HOUR_MS = 60 * 60 * 1000


def write_claude_credentials(
    home: Path,
    account: str,
    *,
    expires_in_ms: int | None = HOUR_MS,
    refresh_token: str | None = "refresh-tok",
    access_token: str = "access-tok",
) -> Path:
    """Write a Claude credentials file for an isolated account config dir."""
    config_dir = home / ".chad" / "claude-configs" / account
    config_dir.mkdir(parents=True, exist_ok=True)
    oauth: dict = {"accessToken": access_token, "subscriptionType": "pro"}
    if expires_in_ms is not None:
        oauth["expiresAt"] = int(time.time() * 1000) + expires_in_ms
    if refresh_token is not None:
        oauth["refreshToken"] = refresh_token
    creds = config_dir / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
    return creds


@pytest.fixture(autouse=True)
def clear_refresh_cache():
    """Refusal caching is per-process; keep tests independent of each other."""
    providers.clear_claude_auth_cache()
    yield
    providers.clear_claude_auth_cache()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every credential lookup at a temp home."""
    monkeypatch.setattr("chad.util.providers.safe_home", lambda: tmp_path)
    monkeypatch.setattr("chad.util.provider_login.safe_home", lambda: tmp_path)
    return tmp_path


def _refresh_response(status_code: int, body: dict | None = None) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = body or {}
    return response


class TestClaudeReadiness:
    """`is_logged_in` must reflect whether the token can still be used."""

    def test_missing_credentials_report_logged_out(self, isolated_home):
        assert provider_login.is_logged_in("anthropic", "never-logged-in") is False

    def test_unexpired_token_reports_ready_without_network(self, isolated_home):
        write_claude_credentials(isolated_home, "fresh", expires_in_ms=HOUR_MS)

        with patch("requests.post", side_effect=AssertionError("must not refresh")):
            assert provider_login.is_logged_in("anthropic", "fresh") is True

    def test_expired_token_that_still_refreshes_reports_ready(self, isolated_home):
        """Overnight expiry is normal — the refresh succeeds and the account is fine."""
        creds = write_claude_credentials(isolated_home, "stale", expires_in_ms=-HOUR_MS)
        ok = _refresh_response(200, {"access_token": "new-tok", "expires_in": 28800})

        with patch("requests.post", return_value=ok):
            assert provider_login.is_logged_in("anthropic", "stale") is True

        # The refreshed token is persisted, so the next check needs no network.
        oauth = json.loads(creds.read_text())["claudeAiOauth"]
        assert oauth["accessToken"] == "new-tok"
        assert oauth["expiresAt"] > time.time() * 1000

    def test_expired_token_with_rejected_refresh_reports_logged_out(self, isolated_home):
        """The ff391be9 case: credentials on disk, but the refresh is refused."""
        write_claude_credentials(isolated_home, "dead", expires_in_ms=-24 * HOUR_MS)

        with patch("requests.post", return_value=_refresh_response(400)):
            assert provider_login.is_logged_in("anthropic", "dead") is False

    def test_credentials_without_refresh_token_report_logged_out(self, isolated_home):
        """What the Claude CLI leaves behind after it gives up: no refresh token."""
        write_claude_credentials(
            isolated_home, "wiped", expires_in_ms=None, refresh_token=None
        )

        with patch("requests.post", side_effect=AssertionError("nothing to refresh")):
            assert provider_login.is_logged_in("anthropic", "wiped") is False

    def test_rejected_refresh_is_not_retried_on_every_poll(self, isolated_home):
        """The UI polls the account list; a dead account must not re-hit the network."""
        write_claude_credentials(isolated_home, "dead", expires_in_ms=-HOUR_MS)

        with patch("requests.post", return_value=_refresh_response(400)) as post:
            for _ in range(5):
                assert provider_login.is_logged_in("anthropic", "dead") is False

        assert post.call_count == 1, "refusal should be cached, not re-attempted per poll"

    def test_concurrent_checks_refresh_the_token_only_once(self, isolated_home):
        """Anthropic refresh tokens are single-use — spending one twice logs the
        account out. Parallel readiness checks must not race on the same file."""
        write_claude_credentials(isolated_home, "stale", expires_in_ms=-HOUR_MS)
        calls: list[int] = []

        def slow_refresh(*_args, **_kwargs):
            calls.append(1)
            time.sleep(0.2)
            return _refresh_response(200, {"access_token": "new-tok", "expires_in": 28800})

        results: list[bool] = []
        with patch("requests.post", side_effect=slow_refresh):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        provider_login.is_logged_in("anthropic", "stale")
                    )
                )
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert results == [True] * 4
        assert len(calls) == 1, "the refresh token must be spent exactly once"

    def test_named_account_does_not_borrow_the_default_claude_login(self, isolated_home):
        """A logged-out account must not report the machine owner's ~/.claude session."""
        default_dir = isolated_home / ".claude"
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / ".credentials.json").write_text(
            json.dumps({
                "claudeAiOauth": {
                    "accessToken": "someone-elses",
                    "expiresAt": int(time.time() * 1000) + HOUR_MS,
                }
            }),
            encoding="utf-8",
        )

        assert provider_login.is_logged_in("anthropic", "logged-out-account") is False


class TestUsageForLoggedOutAccount:
    """Usage must say "logged out", never a fabricated 0%."""

    def test_logged_out_account_reports_no_usage_percentage(self, isolated_home):
        write_claude_credentials(isolated_home, "dead", expires_in_ms=-HOUR_MS)
        provider = providers.ClaudeCodeProvider(
            providers.ModelConfig(
                provider="anthropic", model_name="default", account_name="dead"
            )
        )

        with patch("requests.post", return_value=_refresh_response(400)), \
                patch("requests.get", return_value=_refresh_response(401)):
            assert provider.get_session_usage_percentage() is None
            assert provider.get_weekly_usage_percentage() is None

    def test_usage_endpoint_marks_the_account_logged_out(self, api_client, isolated_home):
        seed_account(api_client, "claude-dead", "anthropic")
        write_claude_credentials(isolated_home, "claude-dead", expires_in_ms=-HOUR_MS)

        with patch("requests.post", return_value=_refresh_response(400)), \
                patch("requests.get", return_value=_refresh_response(401)):
            resp = api_client.get("/api/v1/accounts/claude-dead/usage")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["logged_out"] is True
        assert body["session_usage_pct"] is None
        assert body["weekly_usage_pct"] is None

    def test_account_list_reports_logged_out_account_as_not_ready(
        self, api_client, isolated_home
    ):
        seed_account(api_client, "claude-dead", "anthropic")
        write_claude_credentials(isolated_home, "claude-dead", expires_in_ms=-HOUR_MS)

        with patch("requests.post", return_value=_refresh_response(400)):
            resp = api_client.get("/api/v1/accounts")

        assert resp.status_code == 200, resp.text
        account = next(a for a in resp.json()["accounts"] if a["name"] == "claude-dead")
        assert account["ready"] is False


class TestTaskStartPreflight:
    """A logged-out account is refused before an agent is ever spawned."""

    def test_task_start_is_refused_and_nothing_is_spawned(
        self, api_client, isolated_home, git_project, monkeypatch
    ):
        seed_account(api_client, "claude-dead", "anthropic")
        write_claude_credentials(isolated_home, "claude-dead", expires_in_ms=-HOUR_MS)

        def no_spawn(*_args, **_kwargs):
            raise AssertionError("agent must not be spawned for a logged-out account")

        monkeypatch.setattr(
            "chad.server.services.task_executor.get_pty_stream_service", no_spawn
        )

        session_id = api_client.post(
            "/api/v1/sessions", json={"name": "auth-preflight"}
        ).json()["id"]

        started = time.monotonic()
        with patch("requests.post", return_value=_refresh_response(400)):
            resp = api_client.post(
                f"/api/v1/sessions/{session_id}/tasks",
                json={
                    "project_path": str(git_project),
                    "task_description": "do a thing",
                    "coding_agent": "claude-dead",
                },
            )
        elapsed = time.monotonic() - started

        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "claude-dead" in detail
        assert "logged out" in detail.lower()
        assert "log in" in detail.lower()
        assert elapsed < 10, f"refusal took {elapsed:.1f}s; it must be immediate"

    def test_local_model_server_needs_no_login(
        self, api_client, isolated_home, git_project, monkeypatch
    ):
        """A local server has no account to be logged out of — don't block it."""
        seed_account(api_client, "local-box", "local")
        monkeypatch.setattr("chad.util.provider_login._resolve_cli", lambda _p: None)
        monkeypatch.setattr(
            "chad.server.services.task_executor.TaskExecutor._run_task",
            lambda self, task, *a, **k: None,
        )

        session_id = api_client.post(
            "/api/v1/sessions", json={"name": "local-ok"}
        ).json()["id"]
        resp = api_client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_project),
                "task_description": "do a thing",
                "coding_agent": "local-box",
            },
        )

        assert resp.status_code == 201, resp.text

    def test_task_start_is_allowed_for_a_live_account(
        self, api_client, isolated_home, git_project, monkeypatch
    ):
        """The preflight must not block accounts that are actually usable."""
        seed_account(api_client, "claude-live", "anthropic")
        write_claude_credentials(isolated_home, "claude-live", expires_in_ms=HOUR_MS)

        started: list[str] = []
        monkeypatch.setattr(
            "chad.server.services.task_executor.TaskExecutor._run_task",
            lambda self, task, *a, **k: started.append(task.id),
        )

        session_id = api_client.post(
            "/api/v1/sessions", json={"name": "auth-ok"}
        ).json()["id"]
        resp = api_client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_project),
                "task_description": "do a thing",
                "coding_agent": "claude-live",
            },
        )

        assert resp.status_code == 201, resp.text


class TestLoggedOutIsVisibleInTheFrontEnds:
    """The user has to be able to see the problem and act on it."""

    def test_api_client_surfaces_the_server_message_not_the_status_code(self):
        """"HTTP 400" told the user nothing; the refusal explains itself."""
        api_ts = Path(__file__).parent.parent / "client" / "src" / "api.ts"
        content = api_ts.read_text(encoding="utf-8")

        error_class = content[content.index("export class ChadAPIError"):]
        error_class = error_class[: error_class.index("\n}\n") + 3]
        assert "detail" in error_class, "error message should come from the response detail"
        assert "super(`HTTP ${status}`)" not in error_class

    def test_providers_panel_offers_a_login_for_a_logged_out_account(self):
        panel = Path(__file__).parent.parent / "ui" / "src" / "components" / "ProvidersPanel.tsx"
        content = panel.read_text(encoding="utf-8")

        assert "Logged out" in content
        assert "!a.ready && (" in content, "logged-out accounts must show the login control"
        assert "Log in" in content

    def test_cli_shows_logged_out_accounts_and_can_re_authorize_them(self, monkeypatch, capsys):
        """The CLI's only route back from an expired login was delete-and-re-add."""
        from dataclasses import dataclass

        from chad.ui.cli import app as cli_app

        @dataclass
        class Acct:
            name: str
            provider: str
            model: str | None = None
            reasoning: str | None = None
            role: str | None = None
            ready: bool = True

        client = Mock()
        client.list_accounts.return_value = [Acct(name="claude-dead", provider="anthropic", ready=False)]

        logins: list[tuple[str, str]] = []
        monkeypatch.setattr(
            cli_app, "_run_provider_oauth",
            lambda provider, name: (logins.append((provider, name)), (True, "Login successful"))[1],
        )
        monkeypatch.setattr(cli_app, "select_from_list", lambda *a, **k: "claude-dead")
        monkeypatch.setattr(cli_app, "clear_screen", lambda: None)
        monkeypatch.setattr(cli_app, "_pause", lambda *a, **k: None)
        monkeypatch.setattr("builtins.input", lambda *a: next(choices))
        choices = iter(["4", "b"])

        cli_app.run_accounts_menu(client)

        out = capsys.readouterr().out
        assert "logged out" in out, out
        assert "Log in to an account" in out
        assert logins == [("anthropic", "claude-dead")]


# ── Shared fixtures ──

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """API client with isolated config, logs, and stubbed CLI installation."""
    monkeypatch.setenv("CHAD_CONFIG", str(tmp_path / "test_chad.conf"))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    from chad.util.installer import AIToolInstaller
    monkeypatch.setattr(
        AIToolInstaller, "ensure_tool", lambda self, key: (True, f"/fake/bin/{key}")
    )

    reset_session_manager()
    reset_task_executor()
    reset_state()

    with TestClient(create_app()) as client:
        yield client

    reset_session_manager()
    reset_task_executor()
    reset_state()


def seed_account(client, name: str, provider: str) -> None:
    """Register an account through the API."""
    get_config_manager().save_config(
        {"password_hash": "", "encryption_salt": "dGVzdHNhbHQ=", "accounts": {}}
    )
    resp = client.post("/api/v1/accounts", json={"name": name, "provider": provider})
    assert resp.status_code == 201, resp.text


@pytest.fixture
def git_project(tmp_path):
    """A real git repo, since task start validates one."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)
    return project

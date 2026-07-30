"""Regression tests for bugs found in the 2026-07 autonomous bug hunt.

Covers API validation, role handling, task lifecycle edge cases, uploads,
and event-loop semantics that the original suite missed.
"""

import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with isolated config (mirrors test_end_to_end)."""
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    temp_config.write_text(json.dumps({
        "encryption_salt": "dGVzdHNhbHQ=",
        "password_hash": "",
        "accounts": {},
    }))

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()

    app = create_app()
    with TestClient(app) as c:
        yield c

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


@pytest.fixture
def git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
        env={**subprocess.os.environ, "GIT_AUTHOR_NAME": "test",
             "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "test",
             "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return repo


def _mock_account(client, name="reg-mock"):
    resp = client.post("/api/v1/accounts", json={"name": name, "provider": "mock"})
    assert resp.status_code == 201, resp.text
    return name


def _wait_terminal(client, session_id, task_id, timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/v1/sessions/{session_id}/tasks/{task_id}")
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.15)
    raise TimeoutError(f"task {task_id} never finished")


class TestTaskValidation:
    def test_empty_task_description_rejected(self, client, git_repo):
        _mock_account(client)
        session = client.post("/api/v1/sessions", json={}).json()
        resp = client.post(f"/api/v1/sessions/{session['id']}/tasks", json={
            "project_path": str(git_repo),
            "task_description": "",
            "coding_agent": "reg-mock",
        })
        assert resp.status_code == 422, resp.text

    def test_account_name_path_traversal_rejected(self, client):
        for bad in ("../evil", "..", ".hidden", "a/b", ""):
            resp = client.post("/api/v1/accounts", json={"name": bad, "provider": "mock"})
            assert resp.status_code == 422, f"{bad!r}: {resp.status_code} {resp.text}"


class TestRoleEndpoint:
    def test_coding_role_roundtrips(self, client):
        _mock_account(client, "role-mock")
        resp = client.put("/api/v1/accounts/role-mock/role", json={"role": "CODING"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "CODING"

    def test_verification_role_rejected(self, client):
        """VERIFICATION was silently swallowed before; now it's a 422."""
        _mock_account(client, "role-mock2")
        resp = client.put("/api/v1/accounts/role-mock2/role", json={"role": "VERIFICATION"})
        assert resp.status_code == 422, resp.text

    def test_null_role_clears_assignment(self, client):
        _mock_account(client, "role-mock3")
        client.put("/api/v1/accounts/role-mock3/role", json={"role": "CODING"})
        resp = client.put("/api/v1/accounts/role-mock3/role", json={"role": None})
        assert resp.status_code == 200
        assert resp.json()["role"] is None


class TestConversationWithoutTask:
    def test_conversation_empty_not_404(self, client):
        session = client.post("/api/v1/sessions", json={}).json()
        resp = client.get(f"/api/v1/sessions/{session['id']}/conversation")
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []


class TestDeleteRunningSession:
    def test_delete_cancels_running_task(self, client, git_repo):
        """Deleting a session mid-task must cancel it, not report success."""
        _mock_account(client, "del-mock")
        client.put("/api/v1/config/mock-run-duration",
                   json={"account_name": "del-mock", "seconds": 20})
        session = client.post("/api/v1/sessions", json={}).json()
        task = client.post(f"/api/v1/sessions/{session['id']}/tasks", json={
            "project_path": str(git_repo),
            "task_description": "long task",
            "coding_agent": "del-mock",
        }).json()
        time.sleep(1.5)  # let the PTY spin up

        resp = client.delete(f"/api/v1/sessions/{session['id']}")
        assert resp.status_code == 204

        # The task object survives in the executor; it must NOT be COMPLETED
        from chad.server.services import get_task_executor
        executor = get_task_executor()
        deadline = time.time() + 15
        task_obj = executor.get_task(task["task_id"])
        assert task_obj is not None
        while time.time() < deadline and task_obj.state.value == "running":
            time.sleep(0.2)
        assert task_obj.state.value == "cancelled", task_obj.state


class TestFailedTaskSurfacesResult:
    def test_quota_exhausted_task_reports_error(self, client, git_repo):
        _mock_account(client, "quota-mock")
        client.put("/api/v1/config/mock-remaining-usage",
                   json={"account_name": "quota-mock", "remaining": 0.0})
        session = client.post("/api/v1/sessions", json={}).json()
        task = client.post(f"/api/v1/sessions/{session['id']}/tasks", json={
            "project_path": str(git_repo),
            "task_description": "doomed task",
            "coding_agent": "quota-mock",
        }).json()
        final = _wait_terminal(client, session["id"], task["task_id"])
        assert final["status"] == "failed"
        # result must carry a human-readable reason (was null before)
        assert final["result"], final


class TestMockTaskSinglePass:
    def test_mock_task_completes_without_continuations(self, client, git_repo):
        """The mock agent emits a completion summary, so no continuation runs."""
        _mock_account(client, "single-mock")
        session = client.post("/api/v1/sessions", json={}).json()
        task = client.post(f"/api/v1/sessions/{session['id']}/tasks", json={
            "project_path": str(git_repo),
            "task_description": "single pass",
            "coding_agent": "single-mock",
        }).json()
        final = _wait_terminal(client, session["id"], task["task_id"])
        assert final["status"] == "completed"

        events = client.get(
            f"/api/v1/sessions/{session['id']}/events"
        ).json()["events"]
        statuses = [e.get("status", "") for e in events if e.get("type") == "status"]
        assert not any("continuing" in s for s in statuses), statuses


class TestMaxVerificationAttemptsWired:
    def test_config_value_reaches_event_loop(self, client, git_repo, monkeypatch):
        """max-verification-attempts config must reach SessionEventLoop."""
        client.put("/api/v1/config/max-verification-attempts", json={"attempts": 2})

        captured = {}
        from chad.server.services.session_event_loop import SessionEventLoop

        original_init = SessionEventLoop.__init__

        def spy_init(self, *args, **kwargs):
            captured["max_attempts"] = kwargs.get("max_verification_attempts")
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(SessionEventLoop, "__init__", spy_init)

        _mock_account(client, "wire-mock")
        session = client.post("/api/v1/sessions", json={}).json()
        task = client.post(f"/api/v1/sessions/{session['id']}/tasks", json={
            "project_path": str(git_repo),
            "task_description": "wiring check",
            "coding_agent": "wire-mock",
        }).json()
        _wait_terminal(client, session["id"], task["task_id"])
        assert captured.get("max_attempts") == 2


class TestUploads:
    def test_upload_roundtrip_via_url(self, client):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        resp = client.post(
            "/api/v1/uploads",
            files={"file": ("shot.png", io.BytesIO(png), "image/png")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["url"].startswith("/api/v1/uploads/")
        fetched = client.get(data["url"])
        assert fetched.status_code == 200
        assert fetched.content == png

    def test_upload_path_traversal_blocked(self, client):
        resp = client.get("/api/v1/uploads/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)


class TestAwaitResetSwitchProviderTuple:
    def test_switch_after_await_reset_returns_tuple(self):
        """The await_reset→switch_provider path crashed with a TypeError
        (6-tuple concatenated to str) before the fix."""
        from chad.server.services.session_event_loop import SessionEventLoop

        emitted = []

        class FakeLog:
            def log(self, event):
                pass

        task = type("T", (), {"cancel_requested": False, "stream_id": None})()

        usage_values = iter([10.0])  # immediately below threshold → resume

        loop = SessionEventLoop(
            session_id="t",
            event_log=FakeLog(),
            task=task,
            run_phase_fn=None,
            emit_fn=lambda *a, **k: emitted.append((a, k)),
            worktree_path=None,
            get_session_usage_fn=lambda: next(usage_values, 10.0),
            action_settings=[{"event": "session_usage", "threshold": 90, "action": "await_reset"}],
            get_account_info_fn=lambda name: None,  # switch target missing → early return path
        )
        loop._running = True

        def fake_run_phase(**kwargs):
            # After the continuation, plant a pending switch action
            loop._pending_action = {
                "action": "switch_provider",
                "target_account": "missing-account",
                "label": "session",
            }
            return 0, "continuation output"

        loop._run_phase_fn = fake_run_phase

        # monkeypatch sleep so the wait loop is instant
        import chad.server.services.session_event_loop as sel
        original_sleep = sel.time.sleep
        sel.time.sleep = lambda s: original_sleep(0)
        try:
            exit_code, output = loop._handle_await_reset(
                action={"event": "session_usage", "threshold": 90,
                        "action": "await_reset", "label": "session"},
                session=None,
                task_description="x",
                previous_output="",
                screenshots=None,
                rows=24, cols=80,
                git_mgr=None,
                coding_account="a", coding_provider="mock",
                coding_model=None, coding_reasoning=None,
            )
        finally:
            sel.time.sleep = original_sleep

        assert isinstance(exit_code, int)
        assert isinstance(output, str)
        assert "continuation output" in output


class TestVerificationLoopCancellation:
    def test_cancel_stops_verification_loop(self):
        from chad.server.services.session_event_loop import SessionEventLoop

        task = type("T", (), {"cancel_requested": True, "stream_id": None})()
        calls = []

        loop = SessionEventLoop(
            session_id="t",
            event_log=None,
            task=task,
            run_phase_fn=lambda **kw: calls.append(1) or (0, ""),
            emit_fn=lambda *a, **k: None,
            worktree_path=".",
        )
        loop._running = True
        loop._run_verification_loop(
            session=None,
            task_description="x",
            coding_account="a",
            coding_provider="mock",
            rows=24, cols=80,
            git_mgr=None,
            verification_config={"verification_account": "a"},
        )
        assert calls == []  # no verifier or revision agents spawned


class TestHardening:
    """Regression tests for the tunnel/remote-access hardening pass."""

    def test_ws_ticket_is_single_use(self):
        from chad.server.auth import (
            generate_token, mint_browser_ticket, validate_browser_ticket,
        )

        secret = generate_token()
        ticket = mint_browser_ticket(secret, purpose="ws", resource="sess1", ttl_seconds=60)

        assert validate_browser_ticket(secret, ticket, "ws", "sess1") is True
        # Replay inside the TTL must be refused
        assert validate_browser_ticket(secret, ticket, "ws", "sess1") is False

    def test_ticket_rejects_wrong_resource_and_signature(self):
        from chad.server.auth import (
            generate_token, mint_browser_ticket, validate_browser_ticket,
        )

        secret = generate_token()
        ticket = mint_browser_ticket(secret, purpose="ws", resource="sess1", ttl_seconds=60)
        assert validate_browser_ticket(secret, ticket, "ws", "other") is False
        assert validate_browser_ticket("wrong-secret", ticket, "ws", "sess1") is False
        # A rejected ticket must not have consumed its nonce
        assert validate_browser_ticket(secret, ticket, "ws", "sess1") is True

    def test_auth_middleware_enforces_when_token_set_at_runtime(self, tmp_path, monkeypatch):
        """Auth is dynamic: setting app.state.auth_token starts enforcing."""
        import json as json_mod
        temp_config = tmp_path / "c.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
        monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))
        temp_config.write_text(json_mod.dumps({
            "encryption_salt": "dGVzdHNhbHQ=", "password_hash": "", "accounts": {},
        }))
        reset_session_manager()
        reset_task_executor()
        reset_pty_stream_service()
        reset_state()

        app = create_app()
        with TestClient(app) as c:
            assert c.get("/api/v1/sessions").status_code == 200
            app.state.auth_token = "secret-token"
            assert c.get("/api/v1/sessions").status_code == 401
            ok = c.get("/api/v1/sessions", headers={"Authorization": "Bearer secret-token"})
            assert ok.status_code == 200
            # /status stays public so pairing can discover the server
            assert c.get("/status").status_code == 200

        reset_session_manager()
        reset_task_executor()
        reset_pty_stream_service()
        reset_state()

    def test_tunnel_start_requires_and_mints_token(self, client, monkeypatch):
        """A tunnel must never publish an unauthenticated server."""
        started = {}

        class FakeTunnel:
            def start(self, port):
                started["port"] = port

            def stop(self):
                pass

            def status(self):
                return {"running": True, "url": "https://x.trycloudflare.com",
                        "subdomain": "x", "error": None}

        from chad.server.services import tunnel_service
        monkeypatch.setattr(tunnel_service, "get_tunnel_service", lambda: FakeTunnel())

        resp = client.post("/api/v1/tunnel/start")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["token"], "start must return the minted auth token"
        assert data["pairing_code"] == f"x:{data['token']}"
        assert started["port"]
        # …and the server now demands it
        assert client.get("/api/v1/sessions").status_code == 401

    def test_upload_rejects_non_image_bytes(self, client):
        resp = client.post(
            "/api/v1/uploads",
            files={"file": ("evil.png", io.BytesIO(b"#!/bin/sh\nrm -rf /\n"), "image/png")},
        )
        assert resp.status_code == 400, resp.text

    def test_upload_extension_comes_from_bytes(self, client):
        gif = b"GIF89a" + b"\x00" * 32
        resp = client.post(
            "/api/v1/uploads",
            files={"file": ("notreally.png", io.BytesIO(gif), "image/png")},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["url"].endswith(".gif")


class TestConfigExportEncryption:
    def test_export_without_passphrase_omits_credentials(self, client):
        resp = client.post("/api/v1/config/export", json={})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["credentials_included"] is False
        assert "provider_auth" not in data
        assert "provider_auth_encrypted" not in data

    def test_export_with_passphrase_encrypts_credentials(self, client):
        resp = client.post("/api/v1/config/export", json={"passphrase": "hunter2"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["credentials_included"] is True
        assert "provider_auth" not in data, "credentials must never be exported in the clear"
        assert data["provider_auth_encrypted"]
        assert data["provider_auth_salt"]

    def test_import_rejects_wrong_passphrase(self, client):
        exported = client.post(
            "/api/v1/config/export", json={"passphrase": "right"}
        ).json()
        resp = client.post(
            "/api/v1/config/import", json={"config": exported, "passphrase": "wrong"}
        )
        assert resp.status_code == 400
        assert "passphrase" in resp.json()["detail"].lower()

    def test_import_roundtrips_with_correct_passphrase(self, client, tmp_path):
        from chad.util.config_manager import ConfigManager
        from pathlib import Path

        # Give an account a credential file to carry across
        client.post("/api/v1/accounts", json={"name": "carry-me", "provider": "anthropic"})
        auth_path = Path.home() / ".chad" / "claude-configs" / "carry-me" / ".claude.json"
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_text('{"token": "abc"}')
        try:
            exported = client.post(
                "/api/v1/config/export", json={"passphrase": "s3cret"}
            ).json()
            mgr = ConfigManager(tmp_path / "dest.conf")
            mgr.import_config(exported, passphrase="s3cret")
            assert "carry-me" in mgr.list_accounts()
        finally:
            auth_path.unlink(missing_ok=True)


class TestCredentialIsolation:
    """A never-logged-in account must never inherit the real home's login."""

    def test_kimi_account_does_not_inherit_global_credentials(self, tmp_path, monkeypatch):
        from chad.util import provider_login

        # Real home has a kimi login; the isolated account home does not
        real_home = tmp_path / "real"
        (real_home / ".kimi" / "credentials").mkdir(parents=True)
        (real_home / ".kimi" / "credentials" / "kimi-code.json").write_text("{}")
        monkeypatch.setattr(provider_login.Path, "home", classmethod(lambda cls: real_home))

        chad_home = tmp_path / "chadhome"
        monkeypatch.setattr(provider_login, "safe_home", lambda: chad_home)
        monkeypatch.setattr(provider_login, "_resolve_cli", lambda provider: "/fake/kimi")

        assert provider_login.is_logged_in("kimi", "fresh-account") is False

    def test_kimi_account_with_own_credentials_is_ready(self, tmp_path, monkeypatch):
        from chad.util import provider_login

        chad_home = tmp_path / "chadhome"
        monkeypatch.setattr(provider_login, "safe_home", lambda: chad_home)
        monkeypatch.setattr(provider_login, "_resolve_cli", lambda provider: "/fake/kimi")

        creds = chad_home / ".chad" / "kimi-homes" / "mine" / ".kimi" / "credentials"
        creds.mkdir(parents=True)
        (creds / "kimi-code.json").write_text("{}")

        assert provider_login.is_logged_in("kimi", "mine") is True

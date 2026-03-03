"""Multi-client server tests.

Tests that multiple clients can interact with the same server,
sharing sessions and seeing each other's events.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create a FastAPI app with isolated config."""
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    initial_config = {
        "encryption_salt": "dGVzdHNhbHQ=",
        "password_hash": "",
        "accounts": {},
    }
    temp_config.write_text(json.dumps(initial_config))

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()

    yield create_app()

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


class TestMultiClientSharing:
    """Test that multiple clients can share sessions via the same server."""

    def test_client_b_sees_client_a_session(self, app):
        """Client B should see sessions created by Client A."""
        with TestClient(app) as client_a, TestClient(app) as client_b:
            # Client A creates a session
            resp = client_a.post("/api/v1/sessions", json={"name": "Shared"})
            assert resp.status_code in (200, 201)
            session_id = resp.json()["id"]

            # Client B lists sessions and sees it
            resp = client_b.get("/api/v1/sessions")
            assert resp.status_code == 200
            sessions = resp.json()["sessions"]
            session_ids = [s["id"] for s in sessions]
            assert session_id in session_ids

    def test_client_b_gets_client_a_session_details(self, app):
        """Client B should be able to get full session details."""
        with TestClient(app) as client_a, TestClient(app) as client_b:
            # Client A creates a session
            resp = client_a.post(
                "/api/v1/sessions", json={"name": "DetailTest"}
            )
            session_id = resp.json()["id"]

            # Client B gets session details
            resp = client_b.get(f"/api/v1/sessions/{session_id}")
            assert resp.status_code == 200
            assert resp.json()["name"] == "DetailTest"

    def test_multiple_sessions_visible_to_all(self, app):
        """Sessions created by either client should be visible to both."""
        with TestClient(app) as client_a, TestClient(app) as client_b:
            # Client A creates a session
            resp_a = client_a.post(
                "/api/v1/sessions", json={"name": "SessionA"}
            )
            assert resp_a.status_code in (200, 201)
            id_a = resp_a.json()["id"]

            # Client B creates a session
            resp_b = client_b.post(
                "/api/v1/sessions", json={"name": "SessionB"}
            )
            assert resp_b.status_code in (200, 201)
            id_b = resp_b.json()["id"]

            # Both clients see both sessions
            for client in (client_a, client_b):
                resp = client.get("/api/v1/sessions")
                session_ids = [s["id"] for s in resp.json()["sessions"]]
                assert id_a in session_ids
                assert id_b in session_ids

    def test_mock_task_events_shared(self, app, tmp_path):
        """Client B should see events from Client A's task."""
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

        with TestClient(app) as client_a, TestClient(app) as client_b:
            # Create a mock account
            client_a.post(
                "/api/v1/accounts",
                json={"name": "multi-mock", "provider": "mock"},
            )

            # Client A creates a session
            resp = client_a.post(
                "/api/v1/sessions", json={"name": "EventTest"}
            )
            assert resp.status_code in (200, 201)
            session_id = resp.json()["id"]

            # Client A starts a task
            resp = client_a.post(
                f"/api/v1/sessions/{session_id}/tasks",
                json={
                    "project_path": str(repo),
                    "task_description": "test task",
                    "coding_agent": "multi-mock",
                },
            )
            assert resp.status_code in (200, 201)
            task_id = resp.json()["task_id"]

            # Wait for the mock task to complete
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = client_a.get(
                    f"/api/v1/sessions/{session_id}/tasks/{task_id}"
                )
                status = resp.json()["status"]
                if status in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.2)

            # Client B should be able to get events from the task
            resp = client_b.get(f"/api/v1/sessions/{session_id}/events")
            assert resp.status_code == 200
            events = resp.json()["events"]
            assert len(events) > 0


class TestMultiClientWithAuth:
    """Test multi-client access with authentication enabled."""

    def test_both_clients_need_auth(self, tmp_path, monkeypatch):
        """Both clients must provide a valid token."""
        from chad.server.auth import generate_token

        temp_config = tmp_path / "auth_test.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
        monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

        initial_config = {
            "encryption_salt": "dGVzdHNhbHQ=",
            "password_hash": "",
            "accounts": {},
        }
        temp_config.write_text(json.dumps(initial_config))

        reset_session_manager()
        reset_task_executor()
        reset_pty_stream_service()
        reset_state()

        token = generate_token()
        app = create_app(auth_token=token)
        headers = {"Authorization": f"Bearer {token}"}

        try:
            with TestClient(app) as client_a, TestClient(app) as client_b:
                # Without token: both get 401
                assert client_a.get("/api/v1/sessions").status_code == 401
                assert client_b.get("/api/v1/sessions").status_code == 401

                # With token: both succeed
                resp_a = client_a.post(
                    "/api/v1/sessions",
                    json={"name": "AuthA"},
                    headers=headers,
                )
                assert resp_a.status_code in (200, 201)

                resp_b = client_b.get("/api/v1/sessions", headers=headers)
                assert resp_b.status_code == 200
                sessions = resp_b.json()["sessions"]
                assert any(s["name"] == "AuthA" for s in sessions)
        finally:
            reset_session_manager()
            reset_task_executor()
            reset_pty_stream_service()
            reset_state()

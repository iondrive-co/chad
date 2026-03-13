"""Tests for bearer token authentication middleware."""

import json

import pytest
from fastapi.testclient import TestClient

from chad.server.auth import generate_token
from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state


@pytest.fixture
def auth_token():
    """Generate a test auth token."""
    return generate_token()


@pytest.fixture
def client_with_auth(tmp_path, monkeypatch, auth_token):
    """Create a test client with auth enabled."""
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

    app = create_app(auth_token=auth_token)
    with TestClient(app) as c:
        yield c

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


@pytest.fixture
def client_no_auth(tmp_path, monkeypatch):
    """Create a test client without auth (local mode)."""
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

    app = create_app()
    with TestClient(app) as c:
        yield c

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


class TestAuthMiddleware:
    """Test that auth middleware correctly protects API routes."""

    def test_api_without_token_returns_401(self, client_with_auth):
        """API requests without a token should be rejected."""
        resp = client_with_auth.get("/api/v1/sessions")
        assert resp.status_code == 401

    def test_api_with_correct_token_returns_200(
        self, client_with_auth, auth_token
    ):
        """API requests with the correct token should succeed."""
        resp = client_with_auth.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 200

    def test_api_with_wrong_token_returns_401(self, client_with_auth):
        """API requests with an incorrect token should be rejected."""
        resp = client_with_auth.get(
            "/api/v1/sessions",
            headers={"Authorization": "Bearer wrong-token-here"},
        )
        assert resp.status_code == 401

    def test_health_check_without_token_succeeds(self, client_with_auth):
        """GET /status should work without a token (health check)."""
        resp = client_with_auth.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "cwd" not in data

    def test_post_api_without_token_returns_401(self, client_with_auth):
        """POST to API without token should be rejected."""
        resp = client_with_auth.post(
            "/api/v1/sessions", json={"name": "test"}
        )
        assert resp.status_code == 401

    def test_post_api_with_correct_token_succeeds(
        self, client_with_auth, auth_token
    ):
        """POST to API with correct token should succeed."""
        resp = client_with_auth.post(
            "/api/v1/sessions",
            json={"name": "test"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code in (200, 201)

    def test_multiple_api_routes_protected(
        self, client_with_auth, auth_token
    ):
        """Various API routes should all require auth."""
        protected_routes = [
            "/api/v1/sessions",
            "/api/v1/accounts",
            "/api/v1/providers",
            "/api/v1/config/preferences",
        ]
        for route in protected_routes:
            # Without token: 401
            resp = client_with_auth.get(route)
            assert resp.status_code == 401, f"{route} should require auth"

            # With token: not 401 (may be 200 or other success code)
            resp = client_with_auth.get(
                route,
                headers={"Authorization": f"Bearer {auth_token}"},
            )
            assert resp.status_code != 401, f"{route} should allow valid token"

    def test_non_api_docs_routes_are_protected(self, client_with_auth, auth_token):
        """Docs and schema routes should not bypass auth when tunnel auth is enabled."""
        protected_routes = [
            "/docs",
            "/redoc",
            "/openapi.json",
        ]
        for route in protected_routes:
            resp = client_with_auth.get(route)
            assert resp.status_code == 401, f"{route} should require auth"

            resp = client_with_auth.get(
                route,
                headers={"Authorization": f"Bearer {auth_token}"},
            )
            assert resp.status_code == 200, f"{route} should allow valid token"

    def test_root_serves_security_headers(self, client_with_auth):
        """The public UI should send hardening headers."""
        resp = client_with_auth.get("/")
        assert resp.status_code == 200
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["referrer-policy"] == "same-origin"
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp


class TestNoAuthMode:
    """Test that local mode (no auth) works as before."""

    def test_api_works_without_token(self, client_no_auth):
        """In local mode, API should work without any token."""
        resp = client_no_auth.get("/api/v1/sessions")
        assert resp.status_code == 200

    def test_health_check_works(self, client_no_auth):
        """Health check should work in local mode."""
        resp = client_no_auth.get("/status")
        assert resp.status_code == 200


class TestWebSocketAuth:
    """Test WebSocket authentication via query parameter."""

    def test_ws_with_valid_ticket_connects(
        self, client_with_auth, auth_token
    ):
        """WebSocket with a minted ticket should connect successfully."""
        # First create a session (with auth)
        resp = client_with_auth.post(
            "/api/v1/sessions",
            json={"name": "ws-test"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        session_id = resp.json()["id"]

        ticket_resp = client_with_auth.post(
            f"/api/v1/ws-ticket/{session_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert ticket_resp.status_code == 200
        ticket = ticket_resp.json()["ticket"]

        with client_with_auth.websocket_connect(
            f"/api/v1/ws/{session_id}?ticket={ticket}"
        ) as ws:
            # Send ping and expect pong
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"

    def test_ws_without_ticket_rejected(self, client_with_auth, auth_token):
        """WebSocket without a ticket should be rejected with code 4001."""
        # Create a session first
        resp = client_with_auth.post(
            "/api/v1/sessions",
            json={"name": "ws-test"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        session_id = resp.json()["id"]

        # Connect WebSocket without token — should close with 4001
        with pytest.raises(Exception):
            with client_with_auth.websocket_connect(
                f"/api/v1/ws/{session_id}"
            ) as ws:
                ws.receive_json()

    def test_ws_with_invalid_ticket_rejected(
        self, client_with_auth, auth_token
    ):
        """WebSocket with a forged ticket should be rejected."""
        # Create a session first
        resp = client_with_auth.post(
            "/api/v1/sessions",
            json={"name": "ws-test"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        session_id = resp.json()["id"]

        with pytest.raises(Exception):
            with client_with_auth.websocket_connect(
                f"/api/v1/ws/{session_id}?ticket=wrong-ticket"
            ) as ws:
                ws.receive_json()

    def test_ws_ticket_requires_api_auth(self, client_with_auth, auth_token):
        """Minting a WebSocket ticket should require the bearer token."""
        resp = client_with_auth.post(
            "/api/v1/sessions",
            json={"name": "ws-test"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        session_id = resp.json()["id"]

        unauth = client_with_auth.post(f"/api/v1/ws-ticket/{session_id}")
        assert unauth.status_code == 401


class TestTokenGeneration:
    """Test token generation utility."""

    def test_generate_token_returns_string(self):
        """generate_token() should return a non-empty string."""
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_token_unique(self):
        """Each call should produce a unique token."""
        tokens = {generate_token() for _ in range(10)}
        assert len(tokens) == 10

"""Tests for Chad server API endpoints."""

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.state import get_config_manager, reset_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client for the API with isolated config."""
    # Use temporary config file to avoid reading real accounts
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))

    # Reset all state before each test
    reset_session_manager()
    reset_task_executor()
    reset_state()

    app = create_app()
    with TestClient(app) as client:
        yield client

    # Reset after test
    reset_session_manager()
    reset_task_executor()
    reset_state()


class TestStatusEndpoint:
    """Tests for status endpoint."""

    def test_status(self, client):
        """Status endpoint returns health, version, and uptime."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime_seconds" in data


class TestSessionEndpoints:
    """Tests for session management endpoints."""

    def test_create_session(self, client):
        """Can create a new session."""
        response = client.post("/api/v1/sessions", json={"name": "Test Session"})
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test Session"
        assert data["active"] is False

    def test_create_session_with_project_path(self, client):
        """Can create session with project path."""
        response = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": "/tmp/test-project"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_path"] == "/tmp/test-project"

    def test_list_sessions(self, client):
        """Can list all sessions."""
        # Create a session first
        client.post("/api/v1/sessions", json={"name": "Session 1"})
        client.post("/api/v1/sessions", json={"name": "Session 2"})

        response = client.get("/api/v1/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["sessions"]) == 2

    def test_get_session(self, client):
        """Can get a specific session."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id

    def test_get_session_not_found(self, client):
        """Returns 404 for non-existent session."""
        response = client.get("/api/v1/sessions/nonexistent")
        assert response.status_code == 404

    def test_delete_session(self, client):
        """Can delete a session."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        # Delete it
        response = client.delete(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 404


class TestProviderEndpoints:
    """Tests for provider management endpoints."""

    def test_list_providers(self, client):
        """Can list all supported providers."""
        response = client.get("/api/v1/providers")
        assert response.status_code == 200
        data = response.json()
        providers = data["providers"]
        assert len(providers) >= 5  # anthropic, openai, gemini, qwen, mistral

        provider_types = [p["type"] for p in providers]
        assert "anthropic" in provider_types
        assert "openai" in provider_types
        assert "gemini" in provider_types
        assert "opencode" in provider_types
        assert "kimi" in provider_types

    @pytest.mark.parametrize("provider", ["opencode", "kimi"])
    def test_create_account_accepts_new_provider_types(self, client, provider):
        """Account create API should accept all provider types exposed in the setup UI."""
        config_mgr = get_config_manager()
        config_mgr.save_config(
            {
                "password_hash": "",
                "encryption_salt": "dGVzdHNhbHQ=",
                "accounts": {},
            }
        )

        response = client.post(
            "/api/v1/accounts",
            json={"name": f"{provider}-test", "provider": provider},
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["name"] == f"{provider}-test"
        assert data["provider"] == provider

    def test_list_accounts_empty(self, client):
        """List accounts returns empty list when no accounts configured."""
        response = client.get("/api/v1/accounts")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["accounts"] == []


class TestConfigEndpoints:
    """Tests for config management endpoints."""

    def test_get_cleanup_settings(self, client):
        """Can get cleanup settings."""
        response = client.get("/api/v1/config/cleanup")
        assert response.status_code == 200
        data = response.json()
        assert "cleanup_days" in data
        assert data["cleanup_days"] >= 1

    def test_get_preferences(self, client):
        """Can get user preferences."""
        response = client.get("/api/v1/config/preferences")
        assert response.status_code == 200
        data = response.json()
        assert "dark_mode" in data

    def test_get_verification_settings(self, client):
        """Can get verification settings."""
        response = client.get("/api/v1/config/verification")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "auto_run" in data

    def test_get_verification_agent_default(self, client):
        """Returns None when no verification agent is set."""
        response = client.get("/api/v1/config/verification-agent")
        assert response.status_code == 200
        data = response.json()
        assert "account_name" in data
        # Default is None (same as coding agent)
        assert data["account_name"] is None

    def test_set_verification_agent_none_marker(self, client):
        """Setting verification agent to VERIFICATION_NONE marker persists correctly."""
        # Set to the special marker value
        response = client.put(
            "/api/v1/config/verification-agent",
            json={"account_name": "__verification_none__"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["account_name"] == "__verification_none__"

        # Verify it persists when we get it back
        get_response = client.get("/api/v1/config/verification-agent")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["account_name"] == "__verification_none__"

    def test_set_verification_agent_none_clears(self, client):
        """Setting verification agent to None clears the setting."""
        # First set to the marker
        client.put(
            "/api/v1/config/verification-agent",
            json={"account_name": "__verification_none__"},
        )

        # Then clear by setting to None
        response = client.put(
            "/api/v1/config/verification-agent",
            json={"account_name": None},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["account_name"] is None

        # Verify it's cleared
        get_response = client.get("/api/v1/config/verification-agent")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["account_name"] is None

    def test_set_action_settings_invalid_account_returns_400(self, client):
        """Action settings with invalid switch target should return 400."""
        response = client.put(
            "/api/v1/config/action-settings",
            json={"settings": [
                {"event": "session_usage", "threshold": 90, "action": "switch_provider", "target_account": "nonexistent"},
            ]},
        )

        assert response.status_code == 400
        assert "valid target_account" in response.json()["detail"]

    def test_mock_run_duration_endpoints(self, client):
        """Can get/set per-account mock run duration."""
        get_default = client.get("/api/v1/config/mock-run-duration/mock-runner")
        assert get_default.status_code == 200
        assert get_default.json()["seconds"] == 0

        set_resp = client.put(
            "/api/v1/config/mock-run-duration",
            json={"account_name": "mock-runner", "seconds": 60},
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["seconds"] == 60

        get_after = client.get("/api/v1/config/mock-run-duration/mock-runner")
        assert get_after.status_code == 200
        assert get_after.json()["seconds"] == 60


class TestWorktreeEndpoints:
    """Tests for worktree endpoints (require valid session)."""

    def test_get_worktree_no_session(self, client):
        """Returns 404 for worktree on non-existent session."""
        response = client.get("/api/v1/sessions/nonexistent/worktree")
        assert response.status_code == 404

    def test_get_worktree_no_project(self, client):
        """Returns appropriate status when session has no worktree."""
        # Create session without project path
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.get(f"/api/v1/sessions/{session_id}/worktree")
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False

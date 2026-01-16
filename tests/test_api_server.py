"""Tests for Chad server API endpoints."""

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.state import reset_state


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

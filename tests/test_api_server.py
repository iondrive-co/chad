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

    def test_update_verification_settings_partial(self, client):
        """Can partially update verification settings and disable verification."""
        # Disable verification only
        resp = client.put("/api/v1/config/verification", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        # auto_run should remain default True
        assert data["auto_run"] is True

        # Disable auto_run while enabled already false
        resp2 = client.put("/api/v1/config/verification", json={"auto_run": False})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["enabled"] is False
        assert data2["auto_run"] is False

        # GET should reflect latest values
        resp3 = client.get("/api/v1/config/verification")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["enabled"] is False
        assert data3["auto_run"] is False

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

    def test_merge_request_accepts_commit_message(self, client, tmp_path, monkeypatch):
        """Merge request should accept optional commit_message field."""
        # Create a test git repo
        import subprocess
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
        (project_dir / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=project_dir, capture_output=True)

        # Create session with project path
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": str(project_dir)},
        )
        session_id = create_resp.json()["id"]

        # Create worktree
        wt_resp = client.post(f"/api/v1/sessions/{session_id}/worktree")
        assert wt_resp.status_code == 201

        # Merge request with commit message should be accepted
        merge_resp = client.post(
            f"/api/v1/sessions/{session_id}/worktree/merge",
            json={"target_branch": None, "commit_message": "Custom merge commit"},
        )
        # Should not fail due to unknown field
        assert merge_resp.status_code in [200, 400]  # 400 is ok if no changes

    def test_get_branches_endpoint(self, client, tmp_path):
        """GET /worktree/branches should return branch list."""
        # Create a test git repo
        import subprocess
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
        (project_dir / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=project_dir, capture_output=True)

        # Create session with project path
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": str(project_dir)},
        )
        session_id = create_resp.json()["id"]

        # Create worktree
        client.post(f"/api/v1/sessions/{session_id}/worktree")

        # Get branches
        response = client.get(f"/api/v1/sessions/{session_id}/worktree/branches")
        assert response.status_code == 200
        data = response.json()
        assert "branches" in data
        assert "default" in data
        assert isinstance(data["branches"], list)

    def test_resolve_conflicts_endpoint(self, client, tmp_path):
        """POST /worktree/resolve-conflicts should exist."""
        # Create a test git repo
        import subprocess
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
        (project_dir / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=project_dir, capture_output=True)

        # Create session with project path
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": str(project_dir)},
        )
        session_id = create_resp.json()["id"]

        # Create worktree
        client.post(f"/api/v1/sessions/{session_id}/worktree")

        # Test endpoint exists (even without actual conflicts it should respond)
        response = client.post(
            f"/api/v1/sessions/{session_id}/worktree/resolve-conflicts",
            json={"use_incoming": True},
        )
        # Should return 200 or 400, not 404 (endpoint must exist)
        assert response.status_code != 404

    def test_abort_merge_endpoint(self, client, tmp_path):
        """POST /worktree/abort-merge should exist."""
        # Create a test git repo
        import subprocess
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
        (project_dir / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=project_dir, capture_output=True)

        # Create session with project path
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": str(project_dir)},
        )
        session_id = create_resp.json()["id"]

        # Create worktree
        client.post(f"/api/v1/sessions/{session_id}/worktree")

        # Test endpoint exists
        response = client.post(f"/api/v1/sessions/{session_id}/worktree/abort-merge")
        # Should return 200 or 400, not 404 (endpoint must exist)
        assert response.status_code != 404

    def test_merge_cleans_up_session_state(self, client, tmp_path):
        """Successful merge should clear session worktree state."""
        import subprocess
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
        (project_dir / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=project_dir, capture_output=True)

        # Create session with project path
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "Test", "project_path": str(project_dir)},
        )
        session_id = create_resp.json()["id"]

        # Create worktree
        wt_resp = client.post(f"/api/v1/sessions/{session_id}/worktree")
        assert wt_resp.status_code == 201
        wt_data = wt_resp.json()
        worktree_path = wt_data["path"]

        # Make a change in the worktree
        import os
        (tmp_path / "project" / ".chad-worktrees").mkdir(exist_ok=True)
        if os.path.exists(worktree_path):
            with open(os.path.join(worktree_path, "new_file.txt"), "w") as f:
                f.write("new content")
            subprocess.run(["git", "add", "."], cwd=worktree_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "change"], cwd=worktree_path, capture_output=True)

        # Merge
        merge_resp = client.post(
            f"/api/v1/sessions/{session_id}/worktree/merge",
            json={"target_branch": None},
        )

        # After successful merge, worktree should not exist
        if merge_resp.status_code == 200 and merge_resp.json().get("success"):
            wt_status = client.get(f"/api/v1/sessions/{session_id}/worktree")
            assert wt_status.status_code == 200
            assert wt_status.json()["exists"] is False


class TestMockAccountUsage:
    """Tests for get_mock_account_usage() fixture function."""

    def test_openai_account_returns_session_and_weekly(self):
        """OpenAI accounts map primary→session, secondary→weekly."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("codex-work")
        assert result["account_name"] == "codex-work"
        assert result["provider"] == "openai"
        assert result["session_usage_pct"] == 15.0
        assert result["weekly_usage_pct"] == 42.0
        assert result["session_reset_eta"] is not None
        assert result["weekly_reset_eta"] is not None

    def test_openai_free_no_weekly(self):
        """OpenAI free plan has no secondary usage → weekly is None."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("codex-free")
        assert result["session_usage_pct"] == 95.0
        assert result["weekly_usage_pct"] is None
        assert result["weekly_reset_eta"] is None

    def test_anthropic_account_returns_session_and_weekly(self):
        """Anthropic accounts map five_hour→session, seven_day→weekly."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("claude-pro")
        assert result["provider"] == "anthropic"
        assert result["session_usage_pct"] == 23.0
        assert result["weekly_usage_pct"] == 55.0

    def test_gemini_account_returns_session_only(self):
        """Gemini maps request count to session pct, no weekly."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("gemini-advanced")
        assert result["provider"] == "gemini"
        assert result["session_usage_pct"] is not None
        assert result["session_usage_pct"] > 0
        assert result["weekly_usage_pct"] is None

    def test_mistral_account_returns_session_only(self):
        """Mistral maps token count to session pct, no weekly."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("vibe-pro")
        assert result["provider"] == "mistral"
        assert result["session_usage_pct"] is not None
        assert result["session_usage_pct"] > 0
        assert result["weekly_usage_pct"] is None

    def test_unknown_account_returns_all_none(self):
        """Unknown accounts return all-None usage fields."""
        from chad.ui.gradio.verification.screenshot_fixtures import get_mock_account_usage
        result = get_mock_account_usage("nonexistent-account")
        assert result["account_name"] == "nonexistent-account"
        assert result["session_usage_pct"] is None
        assert result["weekly_usage_pct"] is None
        assert result["session_reset_eta"] is None
        assert result["weekly_reset_eta"] is None


class TestScreenshotModeUsageEndpoint:
    """Tests for usage endpoint with CHAD_SCREENSHOT_MODE=1."""

    @staticmethod
    def _init_config_with_accounts(config_mgr):
        """Initialize config with encryption salt and register mock accounts."""
        import base64
        import bcrypt
        from chad.ui.gradio.verification.screenshot_fixtures import setup_mock_accounts
        password = ""
        password_hash = config_mgr.hash_password(password)
        encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
        config_mgr.save_config({
            "password_hash": password_hash,
            "encryption_salt": encryption_salt,
            "accounts": {},
        })
        setup_mock_accounts(config_mgr, password)

    def test_usage_returns_mock_data_in_screenshot_mode(self, client, monkeypatch):
        """Usage endpoint returns fixture data when CHAD_SCREENSHOT_MODE=1."""
        monkeypatch.setenv("CHAD_SCREENSHOT_MODE", "1")

        config_mgr = get_config_manager()
        self._init_config_with_accounts(config_mgr)

        response = client.get("/api/v1/accounts/claude-pro/usage")
        assert response.status_code == 200
        data = response.json()
        assert data["account_name"] == "claude-pro"
        assert data["provider"] == "anthropic"
        assert data["session_usage_pct"] == 23.0
        assert data["weekly_usage_pct"] == 55.0

    def test_usage_returns_mock_for_openai_in_screenshot_mode(self, client, monkeypatch):
        """OpenAI usage endpoint returns fixture data in screenshot mode."""
        monkeypatch.setenv("CHAD_SCREENSHOT_MODE", "1")

        config_mgr = get_config_manager()
        self._init_config_with_accounts(config_mgr)

        response = client.get("/api/v1/accounts/codex-work/usage")
        assert response.status_code == 200
        data = response.json()
        assert data["session_usage_pct"] == 15.0
        assert data["weekly_usage_pct"] == 42.0

    def test_usage_404_for_nonexistent_account_in_screenshot_mode(self, client, monkeypatch):
        """Non-existent accounts still return 404 in screenshot mode."""
        monkeypatch.setenv("CHAD_SCREENSHOT_MODE", "1")
        response = client.get("/api/v1/accounts/nonexistent/usage")
        assert response.status_code == 404

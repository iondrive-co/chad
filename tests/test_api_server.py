"""Tests for Chad server API endpoints."""

import json
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app, _resolve_ui_paths
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.state import get_config_manager, reset_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client for the API with isolated config."""
    # Use temporary config file to avoid reading real accounts
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    # Isolate log directory so load_from_logs() doesn't pick up real sessions
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

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
        assert data["paused"] is False  # New paused field

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

    def test_startup_skips_completed_historical_sessions_without_changes(self, tmp_path, monkeypatch):
        """Startup should not surface cleanly completed historical sessions."""
        temp_config = tmp_path / "test_chad.conf"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        (log_dir / "historical.jsonl").write_text(
            "\n".join([
                json.dumps({
                    "type": "session_started",
                    "seq": 1,
                    "ts": now,
                    "task_description": "Old finished task",
                    "project_path": "/tmp/project",
                    "coding_account": "codex-main",
                    "coding_provider": "openai",
                }),
                json.dumps({
                    "type": "session_ended",
                    "seq": 2,
                    "ts": now,
                    "success": True,
                    "reason": "completed",
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        reset_session_manager()
        reset_task_executor()
        reset_state()

        app = create_app()
        with TestClient(app) as isolated_client:
            response = isolated_client.get("/api/v1/sessions")

        data = response.json()
        assert response.status_code == 200
        assert data["total"] == 0
        assert data["sessions"] == []

    def test_startup_restores_historical_sessions_when_resume_enabled(self, tmp_path, monkeypatch):
        """Startup restores prior sessions only when resume mode is enabled."""
        temp_config = tmp_path / "test_chad.conf"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        now = datetime.now(timezone.utc).isoformat()
        (log_dir / "historical.jsonl").write_text(
            "\n".join([
                json.dumps({
                    "type": "session_started",
                    "seq": 1,
                    "ts": now,
                    "task_description": "Old finished task",
                    "project_path": "/tmp/project",
                    "coding_account": "codex-main",
                    "coding_provider": "openai",
                }),
                json.dumps({
                    "type": "session_ended",
                    "seq": 2,
                    "ts": now,
                    "success": True,
                    "reason": "completed",
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        reset_session_manager()
        reset_task_executor()
        reset_state()

        app = create_app(resume_sessions=True)
        with TestClient(app) as isolated_client:
            response = isolated_client.get("/api/v1/sessions")

        data = response.json()
        assert response.status_code == 200
        assert data["total"] == 1
        assert data["sessions"][0]["id"] == "historical"

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

    def test_resume_session_not_paused(self, client):
        """Resume endpoint returns resumed=False if session is not paused."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        # Try to resume when not paused
        response = client.post(f"/api/v1/sessions/{session_id}/resume")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["resumed"] is False
        assert "not paused" in data["message"]

    def test_resume_session_not_found(self, client):
        """Resume endpoint returns 404 for non-existent session."""
        response = client.post("/api/v1/sessions/nonexistent/resume")
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
        assert "ui_mode" in data

    def test_export_config(self, client):
        """Can export config."""
        response = client.get("/api/v1/config/export")
        assert response.status_code == 200
        data = response.json()
        assert "password_hash" in data or data.keys() <= {"provider_auth"}

    def test_import_config_rejects_invalid(self, client):
        """Import rejects config without required fields."""
        response = client.post(
            "/api/v1/config/import",
            json={"config": {"accounts": {}}},
        )
        assert response.status_code == 400

    def test_import_config_accepts_valid(self, client):
        """Import accepts valid config."""
        response = client.post(
            "/api/v1/config/import",
            json={
                "config": {
                    "password_hash": "test",
                    "encryption_salt": "test",
                    "accounts": {},
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_import_config_installs_provider_tools(self, client, monkeypatch):
        """Import triggers CLI tool installation for each provider."""
        installed = []

        def fake_ensure_tool(self, tool_key):
            installed.append(tool_key)
            return True, f"/fake/bin/{tool_key}"

        from chad.util.installer import AIToolInstaller
        monkeypatch.setattr(AIToolInstaller, "ensure_tool", fake_ensure_tool)

        response = client.post(
            "/api/v1/config/import",
            json={
                "config": {
                    "password_hash": "test",
                    "encryption_salt": "test",
                    "accounts": {
                        "my-claude": {"provider": "anthropic", "key": "x", "model": "default", "reasoning": "default"},
                        "my-codex": {"provider": "openai", "key": "x", "model": "default", "reasoning": "default"},
                        "my-gemini": {"provider": "gemini", "key": "x", "model": "default", "reasoning": "default"},
                    },
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert sorted(installed) == ["claude", "codex", "gemini"]

    def test_import_config_reports_install_errors(self, client, monkeypatch):
        """Import reports tool installation failures without failing the import."""
        def fake_ensure_tool(self, tool_key):
            if tool_key == "codex":
                return False, "npm not found"
            return True, f"/fake/bin/{tool_key}"

        from chad.util.installer import AIToolInstaller
        monkeypatch.setattr(AIToolInstaller, "ensure_tool", fake_ensure_tool)

        response = client.post(
            "/api/v1/config/import",
            json={
                "config": {
                    "password_hash": "test",
                    "encryption_salt": "test",
                    "accounts": {
                        "acct-claude": {"provider": "anthropic", "key": "x", "model": "default", "reasoning": "default"},
                        "acct-codex": {"provider": "openai", "key": "x", "model": "default", "reasoning": "default"},
                    },
                }
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "install_errors" in data
        assert "codex" in data["install_errors"]


class TestUIServing:
    """Ensure the packaged React UI is served correctly."""

    def test_root_serves_index_html(self, client):
        index, _assets = _resolve_ui_paths()
        if index is None:
            pytest.skip("UI assets not built (no vite/npm in this environment)")
        response = client.get("/")
        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "<div id=\"root\"></div>" in body

    def test_static_assets_are_served(self, client):
        _index, assets_dir = _resolve_ui_paths()
        if assets_dir is None:
            pytest.skip("UI assets not built (no vite/npm in this environment)")

        js_files = [p for p in assets_dir.iterdir() if p.suffix == ".js"]
        assert js_files, "Expected at least one JS asset in the resolved UI assets directory"

        asset_name = js_files[0].name
        response = client.get(f"/assets/{asset_name}")
        assert response.status_code == 200
        assert response.content == (assets_dir / asset_name).read_bytes()

    def test_packaged_ui_package_exists(self):
        with resources.as_file(resources.files("chad.ui_dist")) as ui_root:
            assert (ui_root / "__init__.py").is_file()

    def test_ui_resolver_autobuilds_when_packaged_assets_are_missing(self, tmp_path, monkeypatch):
        project_root = tmp_path / "project"
        (project_root / "ui" / "src").mkdir(parents=True)
        (project_root / "client" / "src").mkdir(parents=True)

        built = {"called": False}

        def fake_autobuild(root):
            built["called"] = True
            dist = root / "ui" / "dist"
            assets = dist / "assets"
            assets.mkdir(parents=True)
            (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
            (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")

        monkeypatch.setattr("chad.server.main._source_project_root", lambda: project_root)
        monkeypatch.setattr("chad.server.main._package_ui_paths", lambda: (None, None))
        monkeypatch.setattr("chad.server.main._autobuild_ui_from_source", fake_autobuild)

        index, assets = _resolve_ui_paths()

        assert built["called"] is True
        assert index == project_root / "ui" / "dist" / "index.html"
        assert assets == project_root / "ui" / "dist" / "assets"

    def test_get_verification_settings(self, client):
        """Can get verification settings."""
        response = client.get("/api/v1/config/verification")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data

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

    def test_update_verification_settings(self, client):
        """Can update verification settings and disable verification."""
        # Disable verification
        resp = client.put("/api/v1/config/verification", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

        # GET should reflect latest value
        resp2 = client.get("/api/v1/config/verification")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["enabled"] is False

        # Re-enable verification
        resp3 = client.put("/api/v1/config/verification", json={"enabled": True})
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["enabled"] is True

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
        assert "current" in data  # Current worktree branch
        assert isinstance(data["branches"], list)
        # Current branch is the worktree branch - it might be a chad-task-* branch
        # that isn't in the main branches list if it was just created for the worktree
        assert data["current"]  # Just verify it's not empty

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


class TestProjectSettingsEndpoints:
    """Tests for project settings API endpoints."""

    def test_get_project_settings_no_project(self, client):
        """Returns 400 when no project path provided."""
        response = client.get("/api/v1/config/project")
        assert response.status_code == 400

    def test_get_project_settings_nonexistent(self, client, tmp_path):
        """Returns default settings for new project."""
        project_path = str(tmp_path / "new-project")
        response = client.get(f"/api/v1/config/project?project_path={project_path}")
        assert response.status_code == 200
        data = response.json()
        assert data["project_path"] == project_path
        assert data["lint_command"] is None
        assert data["test_command"] is None

    def test_set_project_settings(self, client, tmp_path):
        """Can save project settings."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        project_path = str(project_dir)

        response = client.put(
            "/api/v1/config/project",
            json={
                "project_path": project_path,
                "lint_command": "flake8 .",
                "test_command": "pytest tests/",
                "instructions_paths": ["AGENTS.md"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["lint_command"] == "flake8 ."
        assert data["test_command"] == "pytest tests/"
        assert data["instructions_paths"] == ["AGENTS.md"]

    def test_get_project_settings_after_save(self, client, tmp_path):
        """Saved project settings are returned on GET."""
        project_dir = tmp_path / "test-project2"
        project_dir.mkdir()
        project_path = str(project_dir)

        # Save settings
        client.put(
            "/api/v1/config/project",
            json={
                "project_path": project_path,
                "lint_command": "npm run lint",
                "test_command": "npm test",
            },
        )

        # Retrieve settings
        response = client.get(f"/api/v1/config/project?project_path={project_path}")
        assert response.status_code == 200
        data = response.json()
        assert data["lint_command"] == "npm run lint"
        assert data["test_command"] == "npm test"

    def test_get_session_log_path(self, client):
        """Can get session log file path."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.get(f"/api/v1/sessions/{session_id}/log")
        assert response.status_code == 200
        data = response.json()
        assert "log_path" in data
        assert "log_exists" in data
        assert data["session_id"] == session_id
        # Log file may not exist until a task is started
        # If log_path is set, it should contain the session id
        if data["log_path"]:
            assert session_id in data["log_path"]


class TestConnectionLogging:
    """Tests for connection logging on SSE/WebSocket endpoints."""

    def test_websocket_logs_connection(self, client, capsys):
        """WebSocket endpoint should log connection events."""
        # Create a session first
        create_resp = client.post("/api/v1/sessions", json={"name": "Test WS"})
        session_id = create_resp.json()["id"]

        # Connect to WebSocket
        with client.websocket_connect(f"/api/v1/ws/{session_id}") as websocket:
            # Send a ping to establish connection
            websocket.send_json({"type": "ping"})
            response = websocket.receive_json()
            assert response["type"] == "pong"

        # Check that connection was logged
        captured = capsys.readouterr()
        assert f"WebSocket client connected to session {session_id}" in captured.out

    def test_websocket_logs_disconnection(self, client, capsys):
        """WebSocket endpoint should log disconnection events."""
        # Create a session first
        create_resp = client.post("/api/v1/sessions", json={"name": "Test Disconnect"})
        session_id = create_resp.json()["id"]

        # Connect and disconnect
        with client.websocket_connect(f"/api/v1/ws/{session_id}") as websocket:
            websocket.send_json({"type": "ping"})
            websocket.receive_json()
        # websocket is now disconnected

        # Check that disconnection was logged
        captured = capsys.readouterr()
        assert f"WebSocket client disconnected from session {session_id}" in captured.out


class TestHistoricalSessionEvents:
    """Tests for viewing historical events from finished sessions."""

    def test_events_endpoint_returns_historical_events_from_disk(self, client, tmp_path, monkeypatch):
        """Events endpoint should return events from persisted JSONL even without active task."""
        from chad.util.event_log import (
            EventLog,
            SessionStartedEvent,
            TerminalOutputEvent,
            SessionEndedEvent,
        )

        # Set up log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Historical Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Manually create a persisted event log (simulating a finished session)
        event_log = EventLog(session_id, base_dir=log_dir)
        event_log.log(SessionStartedEvent(
            task_description="Test task",
            project_path="/test/path",
            coding_provider="mock",
            coding_account="test-account",
        ))
        event_log.log(TerminalOutputEvent(data="Hello from terminal"))
        event_log.log(SessionEndedEvent(
            success=True,
            reason="completed",
            total_tool_calls=5,
            total_turns=3,
        ))
        event_log.close()

        # Now query the events endpoint - should return the persisted events
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        assert response.status_code == 200
        data = response.json()

        # Should have events from the persisted log
        assert len(data["events"]) == 3
        assert data["latest_seq"] == 3

        # Check event types are present
        event_types = [e["type"] for e in data["events"]]
        assert "session_started" in event_types
        assert "terminal_output" in event_types
        assert "session_ended" in event_types

        # Check terminal output content
        terminal_events = [e for e in data["events"] if e["type"] == "terminal_output"]
        assert len(terminal_events) == 1
        assert terminal_events[0]["data"] == "Hello from terminal"

    def test_events_endpoint_filters_by_type_for_historical(self, client, tmp_path, monkeypatch):
        """Events endpoint should support filtering by type for historical events."""
        from chad.util.event_log import (
            EventLog,
            SessionStartedEvent,
            TerminalOutputEvent,
            SessionEndedEvent,
        )

        # Set up log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Filter Test"})
        session_id = create_resp.json()["id"]

        # Create persisted event log
        event_log = EventLog(session_id, base_dir=log_dir)
        event_log.log(SessionStartedEvent(
            task_description="Test task",
            project_path="/test/path",
            coding_provider="mock",
            coding_account="test-account",
        ))
        event_log.log(TerminalOutputEvent(data="Line 1"))
        event_log.log(TerminalOutputEvent(data="Line 2"))
        event_log.log(SessionEndedEvent(success=True, reason="done"))
        event_log.close()

        # Filter to only terminal_output events
        response = client.get(
            f"/api/v1/sessions/{session_id}/events",
            params={"event_types": "terminal_output"},
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) == 2
        for event in data["events"]:
            assert event["type"] == "terminal_output"

    def test_events_endpoint_with_since_seq_for_historical(self, client, tmp_path, monkeypatch):
        """Events endpoint should support since_seq filtering for historical events."""
        from chad.util.event_log import EventLog, TerminalOutputEvent

        # Set up log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Seq Test"})
        session_id = create_resp.json()["id"]

        # Create persisted event log with multiple events
        event_log = EventLog(session_id, base_dir=log_dir)
        for i in range(5):
            event_log.log(TerminalOutputEvent(data=f"Event {i}"))
        event_log.close()

        # Get events after seq 2
        response = client.get(
            f"/api/v1/sessions/{session_id}/events",
            params={"since_seq": 2},
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) == 3  # seqs 3, 4, 5
        seqs = [e["seq"] for e in data["events"]]
        assert seqs == [3, 4, 5]


class TestConversationEndpoint:
    """Tests for the conversation timeline endpoint."""

    def test_conversation_returns_latest_task_only(self, client, tmp_path, monkeypatch):
        """Conversation should include only the latest task's items."""
        from chad.util.event_log import (
            EventLog,
            SessionStartedEvent,
            UserMessageEvent,
            MilestoneEvent,
            AssistantMessageEvent,
        )

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        create_resp = client.post("/api/v1/sessions", json={"name": "Conversation Test"})
        session_id = create_resp.json()["id"]

        event_log = EventLog(session_id, base_dir=log_dir)

        # Task 1
        event_log.log(SessionStartedEvent(
            task_description="Old task",
            project_path="/test/path",
            coding_provider="mock",
            coding_account="acc-old",
        ))
        event_log.log(UserMessageEvent(content="First message"))
        event_log.log(MilestoneEvent(
            milestone_type="exploration",
            title="Discovery",
            summary="Explored project",
        ))

        # Task 2 (latest)
        event_log.log(SessionStartedEvent(
            task_description="New task",
            project_path="/test/path",
            coding_provider="mock",
            coding_account="acc-new",
        ))
        event_log.log(UserMessageEvent(content="Second message"))
        event_log.log(MilestoneEvent(
            milestone_type="coding_complete",
            title="Coding Complete",
            summary="Finished coding",
        ))
        event_log.log(AssistantMessageEvent(blocks=[{"kind": "text", "content": "All done"}]))
        event_log.close()

        resp = client.get(f"/api/v1/sessions/{session_id}/conversation")
        assert resp.status_code == 200
        data = resp.json()

        assert data["task"]["task_description"] == "New task"
        item_types = [item["type"] for item in data["items"]]
        assert item_types == ["user", "milestone", "assistant"]
        assert data["items"][0]["content"] == "Second message"
        assert "Finished coding" in data["items"][1]["summary"]
        assert "All done" in data["items"][2]["content"]

    def test_conversation_since_seq_filters_items(self, client, tmp_path, monkeypatch):
        """Conversation should honor since_seq for incremental fetches."""
        from chad.util.event_log import (
            EventLog,
            SessionStartedEvent,
            UserMessageEvent,
            MilestoneEvent,
        )

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        create_resp = client.post("/api/v1/sessions", json={"name": "Conversation Incremental"})
        session_id = create_resp.json()["id"]

        event_log = EventLog(session_id, base_dir=log_dir)
        event_log.log(SessionStartedEvent(
            task_description="Incremental",
            project_path="/test/path",
            coding_provider="mock",
            coding_account="acc-new",
        ))
        event_log.log(UserMessageEvent(content="Start"))
        first_seq = event_log._seq
        event_log.log(MilestoneEvent(
            milestone_type="coding_complete",
            title="Done",
            summary="Finished",
        ))
        event_log.close()

        resp_all = client.get(f"/api/v1/sessions/{session_id}/conversation")
        assert resp_all.status_code == 200
        assert len(resp_all.json()["items"]) == 2

        resp_filtered = client.get(
            f"/api/v1/sessions/{session_id}/conversation",
            params={"since_seq": first_seq},
        )
        assert resp_filtered.status_code == 200
        filtered_items = resp_filtered.json()["items"]
        assert len(filtered_items) == 1
        assert filtered_items[0]["type"] == "milestone"


class TestUploadEndpoint:
    """Tests for file upload functionality."""

    def test_upload_screenshot(self, client, tmp_path):
        """Can upload a screenshot file."""
        # Create a test image file (PNG-like bytes)
        png_header = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        files = {"file": ("test.png", png_header, "image/png")}

        response = client.post("/api/v1/uploads", files=files)
        assert response.status_code == 201
        data = response.json()
        assert "path" in data
        assert data["filename"] == "test.png"
        # Verify the file was saved
        assert Path(data["path"]).exists()

    def test_upload_multiple_screenshots(self, client, tmp_path):
        """Can upload multiple screenshot files."""
        png_header = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100

        # Upload first file
        response1 = client.post(
            "/api/v1/uploads",
            files={"file": ("screenshot1.png", png_header, "image/png")}
        )
        assert response1.status_code == 201

        # Upload second file
        response2 = client.post(
            "/api/v1/uploads",
            files={"file": ("screenshot2.png", png_header, "image/png")}
        )
        assert response2.status_code == 201

        # Paths should be different
        assert response1.json()["path"] != response2.json()["path"]

    def test_upload_rejects_non_image(self, client):
        """Rejects non-image file types."""
        response = client.post(
            "/api/v1/uploads",
            files={"file": ("malware.exe", b"bad content", "application/octet-stream")}
        )
        assert response.status_code == 400
        assert "image" in response.json()["detail"].lower()

    def test_upload_accepts_jpeg(self, client):
        """Accepts JPEG images."""
        jpeg_header = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        response = client.post(
            "/api/v1/uploads",
            files={"file": ("photo.jpg", jpeg_header, "image/jpeg")}
        )
        assert response.status_code == 201

    def test_upload_accepts_webp(self, client):
        """Accepts WebP images."""
        webp_header = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 100
        response = client.post(
            "/api/v1/uploads",
            files={"file": ("image.webp", webp_header, "image/webp")}
        )
        assert response.status_code == 201

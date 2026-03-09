"""Tests for the preview tunnel service and API endpoints."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from chad.server.services.preview_tunnel_service import (
    PreviewTunnelService,
    get_preview_tunnel_service,
    reset_preview_tunnel_service,
)


class TestPreviewTunnelService:
    """Tests for PreviewTunnelService."""

    def setup_method(self):
        reset_preview_tunnel_service()

    def test_initial_status(self):
        svc = PreviewTunnelService()
        status = svc.status()
        assert status["running"] is False
        assert status["url"] is None
        assert status["port"] is None
        assert status["error"] is None

    def test_not_running_initially(self):
        svc = PreviewTunnelService()
        assert svc.is_running is False

    def test_stop_when_not_running(self):
        """Stopping when not running should be a no-op."""
        svc = PreviewTunnelService()
        svc.stop()
        assert svc.is_running is False

    @patch("chad.server.services.preview_tunnel_service.AIToolInstaller")
    def test_start_fails_when_cloudflared_not_installed(self, mock_installer_cls):
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (False, "not found")
        mock_installer_cls.return_value = mock_installer

        svc = PreviewTunnelService()
        result = svc.start(3000)

        assert result is None
        assert svc.status()["error"] == "not found"

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    @patch("chad.server.services.preview_tunnel_service.AIToolInstaller")
    def test_start_success(self, mock_installer_cls, mock_registry_fn):
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (True, "/usr/bin/cloudflared")
        mock_installer_cls.return_value = mock_installer

        mock_registry = MagicMock()
        mock_registry_fn.return_value = mock_registry

        # Simulate cloudflared outputting a URL on stderr
        stderr_content = (
            b"2024-01-01 INF Requesting new quick Tunnel\n"
            b"2024-01-01 INF +----------------------------+\n"
            b"2024-01-01 INF |  https://test-preview.trycloudflare.com  |\n"
        )

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_proc.stderr = iter(stderr_content.split(b"\n"))

        with patch("chad.server.services.preview_tunnel_service.subprocess.Popen", return_value=mock_proc):
            svc = PreviewTunnelService()
            result = svc.start(3000)

        assert result == "https://test-preview.trycloudflare.com"
        status = svc.status()
        assert status["running"] is True
        assert status["url"] == "https://test-preview.trycloudflare.com"
        assert status["port"] == 3000

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    @patch("chad.server.services.preview_tunnel_service.AIToolInstaller")
    def test_start_returns_existing_url_for_same_port(self, mock_installer_cls, mock_registry_fn):
        """Starting again with the same port returns the existing URL."""
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (True, "/usr/bin/cloudflared")
        mock_installer_cls.return_value = mock_installer
        mock_registry_fn.return_value = MagicMock()

        stderr_content = b"https://existing-tunnel.trycloudflare.com\n"
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_proc.stderr = iter(stderr_content.split(b"\n"))

        with patch("chad.server.services.preview_tunnel_service.subprocess.Popen", return_value=mock_proc):
            svc = PreviewTunnelService()
            url1 = svc.start(3000)

        # Second call with same port should return existing
        url2 = svc.start(3000)
        assert url1 == url2

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    def test_stop_terminates_process(self, mock_registry_fn):
        mock_registry = MagicMock()
        mock_registry_fn.return_value = mock_registry

        svc = PreviewTunnelService()
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        svc._proc = mock_proc
        svc._url = "https://test.trycloudflare.com"
        svc._port = 3000

        svc.stop()

        mock_registry.terminate.assert_called_once_with(99999)
        assert svc._proc is None
        assert svc._url is None
        assert svc._port is None

    def test_singleton(self):
        reset_preview_tunnel_service()
        svc1 = get_preview_tunnel_service()
        svc2 = get_preview_tunnel_service()
        assert svc1 is svc2

    def test_reset_singleton(self):
        svc1 = get_preview_tunnel_service()
        reset_preview_tunnel_service()
        svc2 = get_preview_tunnel_service()
        assert svc1 is not svc2


class TestPreviewTunnelAPI:
    """Tests for preview tunnel API endpoints."""

    @pytest.fixture
    def client(self):
        from chad.server.main import create_app
        from fastapi.testclient import TestClient

        app = create_app(debug=True)
        return TestClient(app)

    def test_get_status(self, client):
        reset_preview_tunnel_service()
        resp = client.get("/api/v1/preview-tunnel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["url"] is None
        assert data["port"] is None

    def test_stop_when_not_running(self, client):
        reset_preview_tunnel_service()
        resp = client.post("/api/v1/preview-tunnel/stop")
        assert resp.status_code == 200
        assert resp.json()["running"] is False

    def test_start_requires_port(self, client):
        resp = client.post("/api/v1/preview-tunnel/start", json={})
        assert resp.status_code == 422  # validation error


class TestAutoconfigureAPI:
    """Tests for autoconfigure API endpoints."""

    @pytest.fixture
    def client(self):
        from chad.server.main import create_app
        from fastapi.testclient import TestClient

        app = create_app(debug=True)
        return TestClient(app)

    def test_autoconfigure_requires_project_path(self, client):
        resp = client.post("/api/v1/config/project/autoconfigure", json={
            "coding_agent": "test",
        })
        assert resp.status_code == 422

    def test_autoconfigure_requires_coding_agent(self, client):
        resp = client.post("/api/v1/config/project/autoconfigure", json={
            "project_path": "/tmp",
        })
        assert resp.status_code == 422

    def test_autoconfigure_result_not_found(self, client):
        resp = client.get("/api/v1/config/project/autoconfigure/nonexistent")
        assert resp.status_code == 404

    def test_cancel_not_found(self, client):
        resp = client.post("/api/v1/config/project/autoconfigure/nonexistent/cancel")
        assert resp.status_code == 404


class TestAutoconfigureService:
    """Tests for the autoconfigure service."""

    def test_extract_json_from_code_block(self):
        from chad.server.services.autoconfigure_service import _extract_json

        text = 'Some text\n```json\n{"lint_command": "npm run lint"}\n```\nMore text'
        result = _extract_json(text)
        assert result == {"lint_command": "npm run lint"}

    def test_extract_json_bare(self):
        from chad.server.services.autoconfigure_service import _extract_json

        text = '{"lint_command": "flake8 .", "test_command": "pytest", "preview_port": null, "instructions_paths": []}'
        result = _extract_json(text)
        assert result is not None
        assert result["lint_command"] == "flake8 ."

    def test_extract_json_none_when_no_json(self):
        from chad.server.services.autoconfigure_service import _extract_json

        result = _extract_json("no json here at all")
        assert result is None

    def test_build_command_anthropic(self):
        from chad.server.services.autoconfigure_service import _build_command

        cmd, env, stdin_input = _build_command(
            "anthropic", "test-account", "/tmp", "test prompt"
        )
        assert "claude" in cmd[0] or cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "text" in cmd
        assert "--max-turns" in cmd
        assert stdin_input is None

    def test_build_command_openai(self):
        from chad.server.services.autoconfigure_service import _build_command

        cmd, env, stdin_input = _build_command(
            "openai", "test-account", "/tmp", "test prompt"
        )
        assert "codex" in cmd[0] or cmd[0] == "codex"
        assert "exec" in cmd
        assert stdin_input is not None

    def test_build_command_mock(self):
        import json
        from chad.server.services.autoconfigure_service import _build_command

        cmd, env, stdin_input = _build_command(
            "mock", "test-account", "/tmp", "test prompt"
        )
        assert cmd[0] == "echo"
        # Should be valid JSON
        json.loads(cmd[1])

    def test_job_lifecycle(self):
        from chad.server.services.autoconfigure_service import (
            start_autoconfigure, get_job, cleanup_job,
        )
        import time

        # Mock provider returns instantly via echo
        job_id = start_autoconfigure("mock", "test", "/tmp")
        assert job_id.startswith("autoconf-")

        # Wait for completion
        for _ in range(20):
            job = get_job(job_id)
            if job and job.status != "running":
                break
            time.sleep(0.1)

        job = get_job(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.result is not None

        cleanup_job(job_id)
        assert get_job(job_id) is None


class TestProjectConfigPreviewPort:
    """Tests for preview_port in project config."""

    def test_project_config_preview_port_roundtrip(self, tmp_path):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig(preview_port=3000)
        data = config.to_dict()
        assert data["preview_port"] == 3000

        restored = ProjectConfig.from_dict(data)
        assert restored.preview_port == 3000

    def test_project_config_preview_port_none_by_default(self):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig()
        assert config.preview_port is None
        assert config.to_dict()["preview_port"] is None

    def test_project_config_from_dict_missing_preview_port(self):
        from chad.util.project_setup import ProjectConfig

        data = {"version": "1.0", "project_type": "python"}
        config = ProjectConfig.from_dict(data)
        assert config.preview_port is None

    def test_save_project_settings_with_preview_port(self, tmp_path):
        """save_project_settings should persist preview_port."""
        from unittest.mock import patch as mock_patch
        from chad.util.project_setup import save_project_settings

        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        stored = {}

        def mock_get(path):
            return stored.get(str(path))

        def mock_set(path, data):
            stored[str(path)] = data

        mock_cm = MagicMock()
        mock_cm.get_project_config.side_effect = mock_get
        mock_cm.set_project_config.side_effect = mock_set

        with mock_patch("chad.util.config_manager.ConfigManager", return_value=mock_cm):
            config = save_project_settings(project_dir, preview_port=8080)
            assert config.preview_port == 8080

            # Verify the stored data includes preview_port
            saved_data = stored[str(project_dir.resolve())]
            assert saved_data["preview_port"] == 8080

    def test_save_project_settings_preview_port_unchanged_when_omitted(self, tmp_path):
        """preview_port should be unchanged when not passed (sentinel ...)."""
        from unittest.mock import patch as mock_patch
        from chad.util.project_setup import save_project_settings, ProjectConfig

        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        existing = ProjectConfig(preview_port=5000)
        stored = {str(project_dir.resolve()): existing.to_dict()}

        def mock_get(path):
            data = stored.get(str(path))
            if data:
                return data
            return None

        def mock_set(path, data):
            stored[str(path)] = data

        mock_cm = MagicMock()
        mock_cm.get_project_config.side_effect = mock_get
        mock_cm.set_project_config.side_effect = mock_set

        with mock_patch("chad.util.config_manager.ConfigManager", return_value=mock_cm):
            # Call without preview_port (uses sentinel default)
            config = save_project_settings(project_dir)
            assert config.preview_port == 5000

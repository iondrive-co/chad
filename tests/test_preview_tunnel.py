"""Tests for the preview tunnel service and API endpoints."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from chad.server.services.preview_tunnel_service import (
    PreviewTunnelService,
    create_preview_access_url,
    create_preview_proxy_app,
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

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    def test_start_app_only_local(self, mock_registry_fn, tmp_path):
        """Starting without tunnel should start the app and return localhost URL."""
        mock_registry_fn.return_value = MagicMock()

        mock_proc = MagicMock()
        mock_proc.pid = 111
        mock_proc.poll.return_value = None

        with patch("chad.server.services.preview_tunnel_service.subprocess.Popen", return_value=mock_proc):
            svc = PreviewTunnelService()
            result = svc.start(3000, command="npm run dev", cwd=str(tmp_path))

        assert result == "http://localhost:3000"
        assert svc.is_running is True
        assert svc.has_tunnel is False
        status = svc.status()
        assert status["running"] is True
        assert status["url"] is None  # no tunnel
        assert status["port"] == 3000

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    @patch("chad.server.services.preview_tunnel_service.AIToolInstaller")
    def test_start_with_tunnel(self, mock_installer_cls, mock_registry_fn, tmp_path):
        """Starting with tunnel=True should start app + cloudflared."""
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (True, "/usr/bin/cloudflared")
        mock_installer_cls.return_value = mock_installer
        mock_registry_fn.return_value = MagicMock()

        stderr_content = b"https://test-preview.trycloudflare.com\n"

        mock_app_proc = MagicMock(spec=subprocess.Popen)
        mock_app_proc.pid = 111
        mock_app_proc.poll.return_value = None

        mock_tunnel_proc = MagicMock(spec=subprocess.Popen)
        mock_tunnel_proc.pid = 222
        mock_tunnel_proc.poll.return_value = None
        mock_tunnel_proc.stderr = iter(stderr_content.split(b"\n"))

        call_count = [0]

        def make_proc(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_app_proc
            return mock_tunnel_proc

        with patch("chad.server.services.preview_tunnel_service.subprocess.Popen", side_effect=make_proc), \
             patch.object(PreviewTunnelService, "_start_proxy", return_value=43123):
            svc = PreviewTunnelService()
            result = svc.start(
                3000,
                command="npm run dev",
                cwd=str(tmp_path),
                tunnel=True,
                auth_token="main-auth-token",
            )

        assert result == "https://test-preview.trycloudflare.com"
        assert svc.is_running is True
        assert svc.has_tunnel is True

    @patch("chad.server.services.preview_tunnel_service.AIToolInstaller")
    def test_start_tunnel_fails_when_cloudflared_not_installed(self, mock_installer_cls, tmp_path):
        """If cloudflared can't be installed, return localhost URL instead."""
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (False, "not found")
        mock_installer_cls.return_value = mock_installer

        svc = PreviewTunnelService()
        # No command, just tunnel request — should still handle gracefully
        with patch.object(PreviewTunnelService, "_start_proxy", return_value=43123):
            result = svc.start(3000, tunnel=True, auth_token="main-auth-token")

        assert result == "http://localhost:3000"
        assert svc.status()["error"] == "not found"

    def test_start_tunnel_requires_main_auth(self):
        """Preview tunnels should not start without the main tunnel auth secret."""
        svc = PreviewTunnelService()
        result = svc.start(3000, tunnel=True)
        assert result == "http://localhost:3000"
        assert "requires authenticated main tunnel" in svc.status()["error"]

    def test_start_no_command_no_tunnel(self):
        """Starting without command or tunnel should just return localhost URL."""
        svc = PreviewTunnelService()
        result = svc.start(3000)

        assert result == "http://localhost:3000"
        assert svc.status()["error"] is None

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    def test_start_returns_existing_for_same_config(self, mock_registry_fn, tmp_path):
        """Starting again with same port/cwd returns existing URL."""
        mock_registry_fn.return_value = MagicMock()

        mock_proc = MagicMock()
        mock_proc.pid = 111
        mock_proc.poll.return_value = None

        with patch("chad.server.services.preview_tunnel_service.subprocess.Popen", return_value=mock_proc):
            svc = PreviewTunnelService()
            url1 = svc.start(3000, command="npm dev", cwd=str(tmp_path))

        url2 = svc.start(3000, cwd=str(tmp_path))
        assert url1 == url2

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    def test_stop_terminates_both_processes(self, mock_registry_fn):
        mock_registry = MagicMock()
        mock_registry_fn.return_value = mock_registry

        svc = PreviewTunnelService()
        mock_app = MagicMock()
        mock_app.pid = 111
        mock_tunnel = MagicMock()
        mock_tunnel.pid = 222
        svc._app_proc = mock_app
        svc._tunnel_proc = mock_tunnel
        svc._url = "https://test.trycloudflare.com"
        svc._port = 3000

        svc.stop()

        assert mock_registry.terminate.call_count == 2
        mock_registry.terminate.assert_any_call(111)
        mock_registry.terminate.assert_any_call(222)
        assert svc._app_proc is None
        assert svc._tunnel_proc is None
        assert svc._url is None
        assert svc._port is None

    def test_start_invalid_cwd(self):
        """Starting with a non-existent cwd should fail."""
        svc = PreviewTunnelService()
        result = svc.start(3000, command="npm dev", cwd="/nonexistent/path/xyz")
        assert result is None
        assert "does not exist" in svc.status()["error"]

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

    def test_start_without_port_or_command_returns_error(self, client):
        """Starting without port or command should return an error status."""
        reset_preview_tunnel_service()
        resp = client.post("/api/v1/preview-tunnel/start", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["error"] is not None

    def test_start_local_no_command(self, client):
        """Starting with just a port (no command, no tunnel) should succeed."""
        reset_preview_tunnel_service()
        resp = client.post("/api/v1/preview-tunnel/start", json={"port": 3000})
        assert resp.status_code == 200
        # App not started (no command), but endpoint succeeds
        data = resp.json()
        assert data["port"] is None  # no app process running

    def test_start_remote_preview_returns_bootstrap_url(self):
        from chad.server.main import create_app

        app = create_app(auth_token="main-auth-token")
        client = TestClient(app)
        with patch("chad.server.api.routes.preview_tunnel.preview_tunnel_service.get_preview_tunnel_service") as mock_get:
            mock_service = MagicMock()
            mock_service.start.return_value = "https://preview.trycloudflare.com"
            mock_service.status.return_value = {
                "running": True,
                "url": "https://preview.trycloudflare.com",
                "port": 3000,
                "error": None,
            }
            mock_get.return_value = mock_service

            resp = client.post(
                "/api/v1/preview-tunnel/start",
                json={"port": 3000, "tunnel": True},
                headers={"Authorization": "Bearer main-auth-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["url"].startswith("https://preview.trycloudflare.com?preview_token=")


class TestPreviewCwdResolution:
    """Tests for working directory resolution from session."""

    def test_resolve_cwd_no_session(self):
        from chad.server.api.routes.preview_tunnel import _resolve_cwd
        assert _resolve_cwd(None) is None

    def test_resolve_cwd_missing_session(self):
        from chad.server.api.routes.preview_tunnel import _resolve_cwd
        with patch("chad.server.api.routes.preview_tunnel.get_session_manager") as mock_mgr:
            mock_mgr.return_value.get_session.return_value = None
            assert _resolve_cwd("nonexistent") is None

    def test_resolve_cwd_prefers_worktree(self, tmp_path):
        from chad.server.api.routes.preview_tunnel import _resolve_cwd

        wt_path = tmp_path / "worktree"
        wt_path.mkdir()

        mock_session = MagicMock()
        mock_session.worktree_path = str(wt_path)
        mock_session.project_path = "/some/project"

        with patch("chad.server.api.routes.preview_tunnel.get_session_manager") as mock_mgr:
            mock_mgr.return_value.get_session.return_value = mock_session
            result = _resolve_cwd("sess1")
            assert result == str(wt_path)

    def test_resolve_cwd_falls_back_to_project_path(self):
        from chad.server.api.routes.preview_tunnel import _resolve_cwd

        mock_session = MagicMock()
        mock_session.worktree_path = None
        mock_session.project_path = "/some/project"

        with patch("chad.server.api.routes.preview_tunnel.get_session_manager") as mock_mgr:
            mock_mgr.return_value.get_session.return_value = mock_session
            result = _resolve_cwd("sess1")
            assert result == "/some/project"


class TestPreviewProxyAuth:
    """Tests for the authenticated preview proxy."""

    def test_access_url_carries_scoped_ticket(self):
        url = create_preview_access_url("https://preview.trycloudflare.com", "main-auth-token")
        assert url.startswith("https://preview.trycloudflare.com?preview_token=")

    def test_proxy_rejects_request_without_ticket(self):
        app = create_preview_proxy_app(target_port=3000, auth_token="main-auth-token")
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 401


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


class TestProjectConfigPreviewFields:
    """Tests for preview_port and preview_command in project config."""

    def test_project_config_preview_port_roundtrip(self):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig(preview_port=3000)
        data = config.to_dict()
        assert data["preview_port"] == 3000

        restored = ProjectConfig.from_dict(data)
        assert restored.preview_port == 3000

    def test_project_config_preview_command_roundtrip(self):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig(preview_command="npm run dev")
        data = config.to_dict()
        assert data["preview_command"] == "npm run dev"

        restored = ProjectConfig.from_dict(data)
        assert restored.preview_command == "npm run dev"

    def test_project_config_defaults_none(self):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig()
        assert config.preview_port is None
        assert config.preview_command is None
        data = config.to_dict()
        assert data["preview_port"] is None
        assert data["preview_command"] is None

    def test_project_config_from_dict_missing_preview_fields(self):
        from chad.util.project_setup import ProjectConfig

        data = {"version": "1.0", "project_type": "python"}
        config = ProjectConfig.from_dict(data)
        assert config.preview_port is None
        assert config.preview_command is None

    def test_save_project_settings_with_preview_fields(self, tmp_path):
        """save_project_settings should persist preview_port and preview_command."""
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
            config = save_project_settings(
                project_dir, preview_port=8080, preview_command="python -m http.server 8080"
            )
            assert config.preview_port == 8080
            assert config.preview_command == "python -m http.server 8080"

            saved_data = stored[str(project_dir.resolve())]
            assert saved_data["preview_port"] == 8080
            assert saved_data["preview_command"] == "python -m http.server 8080"

    def test_save_project_settings_preview_unchanged_when_omitted(self, tmp_path):
        """preview_port and preview_command should be unchanged when not passed (sentinel ...)."""
        from unittest.mock import patch as mock_patch
        from chad.util.project_setup import save_project_settings, ProjectConfig

        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        existing = ProjectConfig(preview_port=5000, preview_command="npm start")
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
            # Call without preview fields (uses sentinel default)
            config = save_project_settings(project_dir)
            assert config.preview_port == 5000
            assert config.preview_command == "npm start"


class TestPreviewPortMode:
    """Tests for preview_port_mode in project config."""

    def test_default_mode_is_disabled(self):
        from chad.util.project_setup import ProjectConfig

        config = ProjectConfig()
        assert config.preview_port_mode == "disabled"
        data = config.to_dict()
        assert data["preview_port_mode"] == "disabled"

    def test_mode_roundtrip(self):
        from chad.util.project_setup import ProjectConfig

        for mode in ("disabled", "auto", "manual"):
            config = ProjectConfig(preview_port_mode=mode)
            data = config.to_dict()
            assert data["preview_port_mode"] == mode
            restored = ProjectConfig.from_dict(data)
            assert restored.preview_port_mode == mode

    def test_legacy_migration_port_set_becomes_manual(self):
        """Old configs without preview_port_mode but with preview_port should migrate to manual."""
        from chad.util.project_setup import ProjectConfig

        data = {"version": "1.0", "project_type": "python", "preview_port": 3000}
        config = ProjectConfig.from_dict(data)
        assert config.preview_port_mode == "manual"
        assert config.preview_port == 3000

    def test_legacy_migration_no_port_becomes_disabled(self):
        """Old configs without preview_port_mode and no preview_port stay disabled."""
        from chad.util.project_setup import ProjectConfig

        data = {"version": "1.0", "project_type": "python"}
        config = ProjectConfig.from_dict(data)
        assert config.preview_port_mode == "disabled"

    def test_save_project_settings_with_mode(self, tmp_path):
        from unittest.mock import patch as mock_patch, MagicMock
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
            config = save_project_settings(
                project_dir,
                preview_port_mode="auto",
                preview_command="npm run dev",
            )
            assert config.preview_port_mode == "auto"
            assert config.preview_command == "npm run dev"

            saved_data = stored[str(project_dir.resolve())]
            assert saved_data["preview_port_mode"] == "auto"


class TestPortAutodetection:
    """Tests for the port autodetection functionality."""

    def test_detect_port_from_stdout(self):
        """Should detect port from a process that prints a URL to stdout."""
        from chad.server.services.preview_tunnel_service import detect_listening_port

        proc = subprocess.Popen(
            ["echo", "Server running at http://localhost:4567/"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        port = detect_listening_port(proc, timeout=5.0)
        assert port == 4567

    def test_detect_port_from_stdout_without_psutil(self):
        """Should still import and detect from stdout when psutil is unavailable."""
        from chad.server.services import preview_tunnel_service

        original_psutil = preview_tunnel_service.psutil
        preview_tunnel_service.psutil = None
        try:
            proc = subprocess.Popen(
                ["echo", "Server running at http://localhost:4568/"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            port = preview_tunnel_service.detect_listening_port(proc, timeout=5.0)
            assert port == 4568
        finally:
            preview_tunnel_service.psutil = original_psutil

    def test_detect_port_from_listening_socket(self):
        """Should detect port from a process that opens a listening socket."""
        import sys
        from chad.server.services import preview_tunnel_service

        if preview_tunnel_service.psutil is None:
            pytest.skip("listening-socket autodetection requires psutil")

        # Start a Python subprocess that listens on a random port
        proc = subprocess.Popen(
            [
                sys.executable, "-c",
                "import socket, time; "
                "s = socket.socket(); s.bind(('127.0.0.1', 0)); "
                "s.listen(1); print(f'port={s.getsockname()[1]}'); "
                "time.sleep(10)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = preview_tunnel_service.detect_listening_port(proc, timeout=10.0)
            assert port is not None
            assert 1 <= port <= 65535
        finally:
            proc.terminate()
            proc.wait()

    def test_detect_port_returns_none_on_exit(self):
        """Should return None when process exits without opening a port."""
        from chad.server.services.preview_tunnel_service import detect_listening_port

        proc = subprocess.Popen(
            ["echo", "no port here"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        port = detect_listening_port(proc, timeout=2.0)
        assert port is None

    @patch("chad.server.services.preview_tunnel_service.get_global_registry")
    def test_service_autodetect_mode(self, mock_registry_fn, tmp_path):
        """PreviewTunnelService should autodetect port when autodetect_port=True."""
        import sys
        from chad.server.services.preview_tunnel_service import PreviewTunnelService

        mock_registry_fn.return_value = MagicMock()

        # Write a tiny server script that prints a URL and listens
        script = tmp_path / "serve.py"
        script.write_text(
            "import http.server, sys\n"
            "s = http.server.HTTPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)\n"
            "print(f'http://localhost:{s.server_address[1]}/')\n"
            "sys.stdout.flush()\n"
            "s.serve_forever()\n"
        )

        svc = PreviewTunnelService()
        try:
            result = svc.start(
                command=f"{sys.executable} {script}",
                cwd=str(tmp_path),
                autodetect_port=True,
            )
            assert result is not None
            assert "localhost" in result
            assert svc._port is not None
            assert svc._port > 0
        finally:
            if svc._app_proc and svc._app_proc.poll() is None:
                svc._app_proc.terminate()
                svc._app_proc.wait(timeout=5)

    def test_url_patterns(self):
        """Test the URL regex matches common dev server output patterns."""
        from chad.server.services.preview_tunnel_service import _DEV_SERVER_URL_RE

        cases = [
            ("  Local: http://localhost:5173/", 5173),
            ("Starting development server at http://127.0.0.1:8000/", 8000),
            (" * Running on http://127.0.0.1:5000", 5000),
            ("http://localhost:8080", 8080),
            ("Server running at http://0.0.0.0:3000/", 3000),
        ]
        for text, expected_port in cases:
            m = _DEV_SERVER_URL_RE.search(text)
            assert m is not None, f"No match for: {text}"
            assert int(m.group(1)) == expected_port

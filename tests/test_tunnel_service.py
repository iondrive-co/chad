"""Tests for Cloudflare tunnel service and API endpoints."""

import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestTunnelService:
    """Test TunnelService with mocked subprocess and installer."""

    def test_start_parses_url(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()

        # Mock installer to return a fake cloudflared path
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (True, "/usr/bin/cloudflared")

        # Create a mock process whose stderr emits a tunnel URL
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None  # running

        # Simulate stderr output with the tunnel URL
        url_line = b"2024-01-01 INF +-------------------------------------------+\n"
        url_line2 = b"2024-01-01 INF | https://some-words-here.trycloudflare.com |\n"
        mock_proc.stderr = iter([url_line, url_line2])

        with patch("chad.server.services.tunnel_service.AIToolInstaller", return_value=mock_installer), \
             patch("chad.server.services.tunnel_service.subprocess.Popen", return_value=mock_proc), \
             patch("chad.server.services.tunnel_service.get_global_registry") as mock_registry:
            mock_registry.return_value = MagicMock()
            result = svc.start(8000)

        assert result == "https://some-words-here.trycloudflare.com"
        assert svc._subdomain == "some-words-here"
        assert svc.is_running

    def test_start_timeout_returns_none(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()
        svc._start_timeout = 0.1  # Very short timeout for test

        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (True, "/usr/bin/cloudflared")

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None

        # Stderr that never emits the URL pattern
        mock_proc.stderr = iter([b"starting tunnel...\n"])

        with patch("chad.server.services.tunnel_service.AIToolInstaller", return_value=mock_installer), \
             patch("chad.server.services.tunnel_service.subprocess.Popen", return_value=mock_proc), \
             patch("chad.server.services.tunnel_service.get_global_registry") as mock_registry:
            mock_registry.return_value = MagicMock()
            result = svc.start(8000)

        assert result is None
        assert svc._error == "Timed out waiting for tunnel URL"

    def test_stop_terminates_process(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None
        svc._proc = mock_proc
        svc._url = "https://test.trycloudflare.com"
        svc._subdomain = "test"

        with patch("chad.server.services.tunnel_service.get_global_registry") as mock_registry:
            mock_reg = MagicMock()
            mock_registry.return_value = mock_reg
            svc.stop()

        mock_reg.terminate.assert_called_once_with(99999)
        assert svc._proc is None
        assert svc._url is None
        assert svc._subdomain is None

    def test_status_when_idle(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()
        status = svc.status()
        assert status == {"running": False, "url": None, "subdomain": None, "error": None}

    def test_double_start_returns_existing(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None  # running
        svc._proc = mock_proc
        svc._url = "https://existing.trycloudflare.com"
        svc._subdomain = "existing"

        result = svc.start(8000)
        assert result == "https://existing.trycloudflare.com"

    def test_installer_failure(self):
        from chad.server.services.tunnel_service import TunnelService

        svc = TunnelService()
        mock_installer = MagicMock()
        mock_installer.ensure_tool.return_value = (False, "Download failed")

        with patch("chad.server.services.tunnel_service.AIToolInstaller", return_value=mock_installer):
            result = svc.start(8000)

        assert result is None
        assert svc._error == "Download failed"


class TestTunnelEndpoints:
    """Test tunnel API endpoints with mocked service."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from chad.server.main import create_app
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_get_tunnel_default_status(self):
        resp = self.client.get("/api/v1/tunnel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["url"] is None

    def test_post_start_stop_lifecycle(self):
        # The conftest NoOpTunnelService makes start a no-op, so running stays False.
        # This tests the endpoint wiring, not the real cloudflared.
        resp = self.client.post("/api/v1/tunnel/start")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data

        resp = self.client.post("/api/v1/tunnel/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False


class TestCloudflaredInstaller:
    """Verify cloudflared spec is registered."""

    def test_spec_exists(self):
        from chad.util.installer import AIToolInstaller

        installer = AIToolInstaller()
        assert "cloudflared" in installer.tool_specs

    def test_spec_is_binary_type(self):
        from chad.util.installer import AIToolInstaller

        installer = AIToolInstaller()
        spec = installer.tool_specs["cloudflared"]
        assert spec.installer == "binary"
        assert spec.binary == "cloudflared"

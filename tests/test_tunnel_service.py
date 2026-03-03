"""Tests for Cloudflare tunnel service and API endpoints."""

import os
import subprocess
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
             patch("chad.server.services.tunnel_service.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("chad.server.services.tunnel_service.get_global_registry") as mock_registry:
            mock_registry.return_value = MagicMock()
            result = svc.start(8000)

        assert result == "https://some-words-here.trycloudflare.com"
        assert svc._subdomain == "some-words-here"
        assert svc.is_running

        # Verify cloudflared is called with --protocol http2
        args_passed = mock_popen.call_args[0][0]
        assert "--protocol" in args_passed
        assert "http2" in args_passed

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


class TestPairingQR:
    """Test QR code generation for tunnel pairing."""

    def test_print_pairing_qr_produces_output(self, capsys):
        from chad.util.qr import print_pairing_qr

        print_pairing_qr("https://test-tunnel.trycloudflare.com/#pair=test-tunnel:abc123")
        captured = capsys.readouterr()
        # Should produce multi-line Unicode output (half-block characters)
        lines = captured.out.strip().split("\n")
        assert len(lines) > 5, f"Expected multi-line QR output, got {len(lines)} lines"
        # Should contain block characters used by segno terminal output
        assert any("\u2588" in line or "\u2580" in line or "\u2584" in line for line in lines)

    def test_save_pairing_qr_creates_png(self, tmp_path):
        from chad.util.qr import save_pairing_qr

        png_path = tmp_path / "test-qr.png"
        save_pairing_qr("https://test-tunnel.trycloudflare.com/#pair=test-tunnel:abc123", png_path)

        assert png_path.exists()
        data = png_path.read_bytes()
        # PNG magic bytes
        assert data[:4] == b"\x89PNG"
        # Should be a reasonable size (not empty/trivial)
        assert len(data) > 100

    def test_stop_cleans_pairing_artifacts(self, tmp_path):
        from chad.server.services.tunnel_service import TunnelService

        # Create pairing files in a temp CHAD_DIR
        pairing_url = tmp_path / "pairing-url"
        pairing_qr = tmp_path / "pairing-qr.png"
        pairing_url.write_text("https://test.trycloudflare.com/#pair=test:tok")
        pairing_qr.write_bytes(b"\x89PNG fake")

        svc = TunnelService()

        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}), \
             patch("chad.server.services.tunnel_service.get_global_registry") as mock_registry:
            mock_registry.return_value = MagicMock()
            svc.stop()

        assert not pairing_url.exists(), "pairing-url should be deleted on stop"
        assert not pairing_qr.exists(), "pairing-qr.png should be deleted on stop"

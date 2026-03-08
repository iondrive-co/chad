"""Preview tunnel service for tunneling a local dev server via Cloudflare."""

import logging
import re
import subprocess
import threading

from chad.util.installer import AIToolInstaller
from chad.util.process_registry import get_global_registry

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")


class PreviewTunnelService:
    """Manages a second cloudflared quick-tunnel for previewing a local dev server."""

    _start_timeout: float = 15.0

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._port: int | None = None
        self._error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, port: int) -> str | None:
        """Start a cloudflared quick-tunnel pointing to the given local port.

        Returns the tunnel URL on success, or None on failure.
        If already tunneling this port, returns the existing URL.
        """
        if self.is_running and self._url and self._port == port:
            return self._url

        # Stop any existing tunnel (port may have changed)
        self.stop()

        installer = AIToolInstaller()
        ok, path_or_error = installer.ensure_tool("cloudflared")
        if not ok:
            self._error = path_or_error
            logger.error("Failed to install cloudflared: %s", path_or_error)
            return None

        try:
            self._proc = subprocess.Popen(
                [
                    path_or_error, "tunnel",
                    "--url", f"http://localhost:{port}",
                    "--protocol", "http2",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            self._error = str(exc)
            logger.error("Failed to start preview tunnel: %s", exc)
            return None

        self._port = port
        registry = get_global_registry()
        registry.register(self._proc, description="cloudflared preview tunnel")

        url_found = threading.Event()

        def _read_stderr():
            assert self._proc is not None
            assert self._proc.stderr is not None
            for raw_line in self._proc.stderr:
                line = raw_line.decode("utf-8", errors="replace")
                m = _URL_RE.search(line)
                if m:
                    self._url = f"https://{m.group(1)}"
                    url_found.set()

        reader = threading.Thread(target=_read_stderr, daemon=True)
        reader.start()

        if url_found.wait(timeout=self._start_timeout):
            self._error = None
            return self._url

        self._error = "Timed out waiting for preview tunnel URL"
        self.stop()
        return None

    def stop(self) -> None:
        """Stop the running preview tunnel."""
        if self._proc is not None:
            pid = self._proc.pid
            registry = get_global_registry()
            registry.terminate(pid)
            self._proc = None
        self._url = None
        self._port = None

    def status(self) -> dict:
        """Return current preview tunnel status."""
        return {
            "running": self.is_running,
            "url": self._url if self.is_running else None,
            "port": self._port if self.is_running else None,
            "error": self._error,
        }


# Global singleton
_preview_tunnel_service: PreviewTunnelService | None = None


def get_preview_tunnel_service() -> PreviewTunnelService:
    """Get the global PreviewTunnelService instance."""
    global _preview_tunnel_service
    if _preview_tunnel_service is None:
        _preview_tunnel_service = PreviewTunnelService()
    return _preview_tunnel_service


def reset_preview_tunnel_service() -> None:
    """Reset the global PreviewTunnelService singleton (for testing)."""
    global _preview_tunnel_service
    _preview_tunnel_service = None

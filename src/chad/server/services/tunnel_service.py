"""Cloudflare tunnel integration for remote access via trycloudflare.com."""

import logging
import re
import subprocess
import threading

from chad.util.installer import AIToolInstaller
from chad.util.process_registry import get_global_registry

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")


class TunnelService:
    """Manages a cloudflared quick-tunnel for remote access."""

    _start_timeout: float = 15.0

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._subdomain: str | None = None
        self._error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, port: int) -> str | None:
        """Start a cloudflared quick-tunnel pointing to the given port.

        Returns the tunnel URL on success, or None on failure.
        If a tunnel is already running, returns the existing URL.
        """
        if self.is_running and self._url:
            return self._url

        # Stop any stale process
        self.stop()

        installer = AIToolInstaller()
        ok, path_or_error = installer.ensure_tool("cloudflared")
        if not ok:
            self._error = path_or_error
            logger.error("Failed to install cloudflared: %s", path_or_error)
            return None

        try:
            self._proc = subprocess.Popen(
                [path_or_error, "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            self._error = str(exc)
            logger.error("Failed to start cloudflared: %s", exc)
            return None

        registry = get_global_registry()
        registry.register(self._proc, description="cloudflared tunnel")

        # Parse URL from stderr in a background thread
        url_found = threading.Event()

        def _read_stderr():
            assert self._proc is not None
            assert self._proc.stderr is not None
            for raw_line in self._proc.stderr:
                line = raw_line.decode("utf-8", errors="replace")
                m = _URL_RE.search(line)
                if m:
                    self._url = f"https://{m.group(1)}"
                    # Extract subdomain (everything before .trycloudflare.com)
                    full_host = m.group(1)
                    self._subdomain = full_host.replace(".trycloudflare.com", "")
                    url_found.set()

        reader = threading.Thread(target=_read_stderr, daemon=True)
        reader.start()

        if url_found.wait(timeout=self._start_timeout):
            self._error = None
            return self._url

        # Timed out waiting for URL
        self._error = "Timed out waiting for tunnel URL"
        self.stop()
        return None

    def stop(self) -> None:
        """Stop the running tunnel."""
        if self._proc is not None:
            pid = self._proc.pid
            registry = get_global_registry()
            registry.terminate(pid)
            self._proc = None
        self._url = None
        self._subdomain = None

    def status(self) -> dict:
        """Return current tunnel status."""
        return {
            "running": self.is_running,
            "url": self._url if self.is_running else None,
            "subdomain": self._subdomain if self.is_running else None,
            "error": self._error,
        }


# Global singleton
_tunnel_service: TunnelService | None = None


def get_tunnel_service() -> TunnelService:
    """Get the global TunnelService instance."""
    global _tunnel_service
    if _tunnel_service is None:
        _tunnel_service = TunnelService()
    return _tunnel_service


def reset_tunnel_service() -> None:
    """Reset the global TunnelService singleton (for testing)."""
    global _tunnel_service
    _tunnel_service = None

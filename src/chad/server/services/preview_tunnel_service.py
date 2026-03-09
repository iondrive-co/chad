"""Preview service for starting a project's dev server and optionally tunneling it."""

import logging
import re
import subprocess
import threading
from pathlib import Path

from chad.util.installer import AIToolInstaller
from chad.util.process_registry import get_global_registry

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")


class PreviewTunnelService:
    """Manages a project dev server process and an optional cloudflared tunnel."""

    _start_timeout: float = 15.0

    def __init__(self) -> None:
        self._tunnel_proc: subprocess.Popen | None = None
        self._app_proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._port: int | None = None
        self._command: str | None = None
        self._cwd: str | None = None
        self._error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._app_proc is not None and self._app_proc.poll() is None

    @property
    def has_tunnel(self) -> bool:
        return self._tunnel_proc is not None and self._tunnel_proc.poll() is None

    def start(
        self,
        port: int,
        command: str | None = None,
        cwd: str | None = None,
        tunnel: bool = False,
    ) -> str | None:
        """Start the preview app and optionally a cloudflared tunnel.

        Args:
            port: The port the app listens on.
            command: Shell command to start the app (e.g. "npm run dev").
            cwd: Working directory for the app command.
            tunnel: Whether to also start a cloudflared tunnel (for remote access).

        Returns:
            The tunnel URL if tunnel=True and successful, otherwise
            the localhost URL, or None on failure.
        """
        # If already running the same config, return existing URL
        if self.is_running and self._port == port and self._cwd == cwd:
            if tunnel and self._url:
                return self._url
            return f"http://localhost:{port}"

        # Stop any existing preview
        self.stop()

        self._port = port
        self._command = command
        self._cwd = cwd

        # Start the app process
        if command:
            resolved_cwd = cwd or "."
            if not Path(resolved_cwd).is_dir():
                self._error = f"Working directory does not exist: {resolved_cwd}"
                logger.error(self._error)
                return None

            try:
                self._app_proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=resolved_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as exc:
                self._error = str(exc)
                logger.error("Failed to start preview app: %s", exc)
                return None

            registry = get_global_registry()
            registry.register(self._app_proc, description=f"preview app: {command}")

        if not tunnel:
            self._error = None
            return f"http://localhost:{port}"

        # Start cloudflared tunnel
        installer = AIToolInstaller()
        ok, path_or_error = installer.ensure_tool("cloudflared")
        if not ok:
            self._error = path_or_error
            logger.error("Failed to install cloudflared: %s", path_or_error)
            return f"http://localhost:{port}"

        try:
            self._tunnel_proc = subprocess.Popen(
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
            return f"http://localhost:{port}"

        registry = get_global_registry()
        registry.register(self._tunnel_proc, description="cloudflared preview tunnel")

        url_found = threading.Event()

        def _read_stderr():
            assert self._tunnel_proc is not None
            assert self._tunnel_proc.stderr is not None
            for raw_line in self._tunnel_proc.stderr:
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
        return f"http://localhost:{port}"

    def stop(self) -> None:
        """Stop the app process and tunnel."""
        registry = get_global_registry()
        if self._tunnel_proc is not None:
            registry.terminate(self._tunnel_proc.pid)
            self._tunnel_proc = None
        if self._app_proc is not None:
            registry.terminate(self._app_proc.pid)
            self._app_proc = None
        self._url = None
        self._port = None
        self._command = None
        self._cwd = None

    def status(self) -> dict:
        """Return current preview status."""
        return {
            "running": self.is_running,
            "url": self._url if self.has_tunnel else None,
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

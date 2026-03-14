"""Preview service for starting a project's dev server behind an authenticated proxy."""

import asyncio
import logging
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import PlainTextResponse, RedirectResponse

from chad.server.auth import mint_browser_ticket, validate_browser_ticket
from chad.util.installer import AIToolInstaller
from chad.util.process_registry import get_global_registry

logger = logging.getLogger(__name__)

try:
    import psutil
except ModuleNotFoundError:
    psutil = None

# Regex to match common dev server URL announcements in stdout/stderr
_DEV_SERVER_URL_RE = re.compile(
    r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]):(\d+)',
)


def _get_listening_ports(pid: int) -> set[int]:
    """Get all TCP ports that a process tree is listening on."""
    ports: set[int] = set()
    if psutil is None:
        return ports
    try:
        proc = psutil.Process(pid)
        for child in [proc] + proc.children(recursive=True):
            try:
                for conn in child.net_connections(kind="tcp"):
                    if conn.status == "LISTEN":
                        ports.add(conn.laddr.port)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return ports


def detect_listening_port(
    proc: subprocess.Popen,
    timeout: float = 30.0,
    poll_interval: float = 0.3,
) -> int | None:
    """Detect the port a subprocess starts listening on.

    Uses two strategies in parallel:
    1. Parse stdout/stderr for common URL patterns (fast path)
    2. Poll the process tree for new TCP LISTEN ports (generic fallback)

    Returns the detected port or None if detection times out.
    """
    deadline = time.monotonic() + timeout
    stdout_port: list[int] = []
    found_event = threading.Event()

    def _scan_output(stream) -> None:
        """Read process output looking for URL patterns."""
        if stream is None:
            return
        try:
            for raw_line in stream:
                line = raw_line.decode("utf-8", errors="replace")
                m = _DEV_SERVER_URL_RE.search(line)
                if m:
                    port = int(m.group(1))
                    if port > 0:
                        stdout_port.append(port)
                        found_event.set()
                        return
        except (OSError, ValueError):
            pass

    # Start stdout/stderr scanners in background threads
    readers = []
    for stream in (proc.stdout, proc.stderr):
        if stream:
            t = threading.Thread(target=_scan_output, args=(stream,), daemon=True)
            t.start()
            readers.append(t)

    # Poll for new listening ports
    while time.monotonic() < deadline:
        if found_event.wait(timeout=poll_interval):
            break

        if proc.poll() is not None:
            # Process exited before we found a port
            break

        ports = _get_listening_ports(proc.pid)
        if ports:
            return min(ports)  # prefer lowest port (usually the main server)

    if stdout_port:
        return stdout_port[0]

    return None


_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")
_PREVIEW_COOKIE = "chad_preview_ticket"
_PREVIEW_TTL_SECONDS = 8 * 60 * 60
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _filter_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers
        if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "host"
    }


def _validated_preview_ticket(secret: str, ticket: str | None) -> bool:
    if not ticket:
        return False
    return validate_browser_ticket(
        secret=secret,
        ticket=ticket,
        purpose="preview",
        resource="preview",
    )


def create_preview_access_url(base_url: str, auth_token: str) -> str:
    """Create a preview URL that bootstraps an authenticated preview session."""
    ticket = mint_browser_ticket(
        secret=auth_token,
        purpose="preview",
        resource="preview",
        ttl_seconds=_PREVIEW_TTL_SECONDS,
    )
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'preview_token': ticket})}"


def create_preview_proxy_app(target_port: int, auth_token: str) -> FastAPI:
    """Create an authenticated reverse proxy for the preview app."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _target_url(request: Request, path: str, *, include_ticket: bool = False) -> str:
        params = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if include_ticket or key != "preview_token"
        ]
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        trimmed = path.lstrip("/")
        suffix = f"/{trimmed}" if trimmed else ""
        return f"http://127.0.0.1:{target_port}{suffix}{query}"

    def _clean_url(request: Request, path: str) -> str:
        params = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key != "preview_token"
        ]
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        trimmed = path.lstrip("/")
        suffix = f"/{trimmed}" if trimmed else ""
        return f"{request.base_url}{suffix.lstrip('/')}{query}"

    def _set_preview_cookie(response: Response, request: Request, ticket: str) -> None:
        response.set_cookie(
            key=_PREVIEW_COOKIE,
            value=ticket,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=_PREVIEW_TTL_SECONDS,
            path="/",
        )

    def _authorized_request(request: Request) -> tuple[bool, str | None]:
        cookie_ticket = request.cookies.get(_PREVIEW_COOKIE)
        if _validated_preview_ticket(auth_token, cookie_ticket):
            return True, None

        query_ticket = request.query_params.get("preview_token")
        if _validated_preview_ticket(auth_token, query_ticket):
            return True, query_ticket

        return False, None

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_http(request: Request, path: str) -> Response:
        authorized, bootstrap_ticket = _authorized_request(request)
        if not authorized:
            return PlainTextResponse("Preview authentication required", status_code=401)

        if bootstrap_ticket and request.method in {"GET", "HEAD"}:
            redirect = RedirectResponse(url=_clean_url(request, path), status_code=307)
            _set_preview_cookie(redirect, request, bootstrap_ticket)
            return redirect

        body = await request.body()
        upstream_headers = _filter_headers(request.headers.items())
        upstream_headers.pop("cookie", None)

        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            upstream = await client.request(
                method=request.method,
                url=_target_url(request, path),
                headers=upstream_headers,
                content=body,
            )

        response_headers = _filter_headers(upstream.headers.items())
        location = response_headers.get("location")
        local_origin = f"http://127.0.0.1:{target_port}"
        if location and location.startswith(local_origin):
            response_headers["location"] = location.replace(local_origin, str(request.base_url).rstrip("/"), 1)

        response = Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )
        if bootstrap_ticket:
            _set_preview_cookie(response, request, bootstrap_ticket)
        return response

    @app.websocket("/{path:path}")
    async def proxy_websocket(websocket: WebSocket, path: str) -> None:
        cookie_ticket = websocket.cookies.get(_PREVIEW_COOKIE)
        query_ticket = websocket.query_params.get("preview_token")
        ticket = cookie_ticket if _validated_preview_ticket(auth_token, cookie_ticket) else query_ticket
        if not _validated_preview_ticket(auth_token, ticket):
            await websocket.close(code=4401, reason="Preview authentication required")
            return

        params = [
            (key, value)
            for key, value in websocket.query_params.multi_items()
            if key != "preview_token"
        ]
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        trimmed = path.lstrip("/")
        suffix = f"/{trimmed}" if trimmed else ""
        upstream_url = f"ws://127.0.0.1:{target_port}{suffix}{query}"

        await websocket.accept()
        try:
            async with websockets.connect(upstream_url) as upstream:
                async def browser_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if "text" in message:
                            await upstream.send(message["text"])
                        elif "bytes" in message:
                            await upstream.send(message["bytes"])
                        else:
                            break

                async def upstream_to_browser() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                await asyncio.gather(browser_to_upstream(), upstream_to_browser())
        except Exception:
            await websocket.close(code=1011)

    return app


class PreviewTunnelService:
    """Manages a project dev server plus an authenticated Cloudflare preview tunnel."""

    _start_timeout: float = 15.0

    def __init__(self) -> None:
        self._tunnel_proc: subprocess.Popen | None = None
        self._app_proc: subprocess.Popen | None = None
        self._proxy_server: uvicorn.Server | None = None
        self._proxy_thread: threading.Thread | None = None
        self._proxy_port: int | None = None
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

    def _start_proxy(self, target_port: int, auth_token: str) -> int | None:
        proxy_port = _find_free_port()
        app = create_preview_proxy_app(target_port=target_port, auth_token=auth_token)
        config = uvicorn.Config(app, host="127.0.0.1", port=proxy_port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name="preview-proxy")
        thread.start()

        for _ in range(30):
            if getattr(server, "started", False):
                self._proxy_server = server
                self._proxy_thread = thread
                self._proxy_port = proxy_port
                return proxy_port
            thread.join(timeout=0.1)
            if not thread.is_alive():
                break

        self._error = "Timed out waiting for preview proxy to start"
        server.should_exit = True
        thread.join(timeout=1)
        return None

    def start(
        self,
        port: int | None = None,
        command: str | None = None,
        cwd: str | None = None,
        tunnel: bool = False,
        auth_token: str | None = None,
        autodetect_port: bool = False,
    ) -> str | None:
        """Start the preview app and optionally an authenticated cloudflared tunnel.

        When autodetect_port is True and port is None, the port is discovered
        by launching the command and detecting what it listens on.
        """
        if self.is_running and self._port == port and self._cwd == cwd and port is not None:
            if tunnel and self._url:
                return self._url
            return f"http://localhost:{port}"

        self.stop()

        self._command = command
        self._cwd = cwd

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

            if autodetect_port and port is None:
                detected = detect_listening_port(self._app_proc)
                if detected is None:
                    self._error = "Could not detect preview port from command output"
                    logger.error(self._error)
                    self.stop()
                    return None
                port = detected
                logger.info("Auto-detected preview port: %d", port)

        if port is None:
            self._error = "No port specified and no command to autodetect from"
            return None

        self._port = port

        if not tunnel:
            self._error = None
            return f"http://localhost:{port}"

        if not auth_token:
            self._error = "Preview tunnel requires authenticated main tunnel access"
            logger.error(self._error)
            return f"http://localhost:{port}"

        proxy_port = self._start_proxy(target_port=port, auth_token=auth_token)
        if proxy_port is None:
            return f"http://localhost:{port}"

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
                    "--url", f"http://localhost:{proxy_port}",
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

        def _read_stderr() -> None:
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
        """Stop the app process, authenticated proxy, and cloudflare tunnel."""
        registry = get_global_registry()
        if self._tunnel_proc is not None:
            registry.terminate(self._tunnel_proc.pid)
            self._tunnel_proc = None
        if self._app_proc is not None:
            registry.terminate(self._app_proc.pid)
            self._app_proc = None
        if self._proxy_server is not None:
            self._proxy_server.should_exit = True
        if self._proxy_thread is not None:
            self._proxy_thread.join(timeout=2)
        self._proxy_server = None
        self._proxy_thread = None
        self._proxy_port = None
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

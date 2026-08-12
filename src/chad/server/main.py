"""FastAPI application factory for Chad server."""

from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .state import init_start_time
from .api.routes import health, sessions, providers, worktree, config, ws, slack, tunnel, uploads, preview_tunnel


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply baseline browser hardening headers to Chad UI responses."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join([
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: blob:",
                "connect-src 'self' ws: wss: https:",
                "font-src 'self' data:",
                "object-src 'none'",
                "base-uri 'self'",
                "frame-ancestors 'none'",
            ]),
        )
        return response


def _source_project_root() -> Path:
    """Return the repository root when running from a source checkout."""
    return Path(__file__).resolve().parents[3]


def _repo_ui_paths(project_root: Path) -> tuple[Path | None, Path | None]:
    """Return the source-tree UI build if it exists."""
    repo_dist = project_root / "ui" / "dist" / "index.html"
    if repo_dist.is_file():
        assets = repo_dist.parent / "assets"
        return repo_dist, assets if assets.is_dir() else None
    return None, None


def _repo_ui_is_stale(project_root: Path) -> bool:
    """Return True when ``ui/dist`` exists but is older than its source.

    A git checkout/merge can leave a built ``ui/dist`` whose mtime predates a
    later source change. Without this check the resolver would serve that stale
    bundle forever (never triggering the autobuild, which only fired when
    ``ui/dist`` was absent), so new UI never reached the browser no matter how
    many times chad restarted.
    """
    ui_src = project_root / "ui" / "src"
    client_src = project_root / "client" / "src"
    ui_dist = project_root / "ui" / "dist" / "index.html"
    if not ui_src.is_dir() or not ui_dist.is_file():
        return False
    from chad.util.ui_build import _is_stale

    # The chad-client TS source is bundled into the UI, so a newer file in
    # client/src also makes ui/dist stale.
    return _is_stale([ui_src, client_src], ui_dist)


def _package_ui_paths() -> tuple[Path | None, Path | None]:
    """Return packaged UI assets bundled in the Python package if present."""
    try:
        package_dist = resources.files("chad.ui_dist")
        index = Path(package_dist) / "index.html"
        if index.is_file():
            assets = index.parent / "assets"
            return index, assets if assets.is_dir() else None
    except Exception:
        pass
    return None, None


def _autobuild_ui_from_source(project_root: Path) -> None:
    """Materialize a build from source when committed package assets are absent."""
    if not (project_root / "ui" / "src").is_dir():
        return
    if not (project_root / "client" / "src").is_dir():
        return

    try:
        from chad.util.ui_build import ensure_ui_built
        ensure_ui_built(project_root=project_root, verbose=False)
    except Exception:
        pass


def _resolve_ui_paths() -> tuple[Path | None, Path | None]:
    """Return the UI index and assets directory if available."""
    project_root = _source_project_root()

    # Rebuild a stale source-tree bundle before serving it, otherwise an
    # out-of-date ui/dist (e.g. left by a git checkout/merge) would be served
    # forever and source changes never reach the browser.
    if _repo_ui_is_stale(project_root):
        _autobuild_ui_from_source(project_root)

    repo_index, repo_assets = _repo_ui_paths(project_root)
    if repo_index:
        return repo_index, repo_assets

    package_index, package_assets = _package_ui_paths()
    if package_index:
        return package_index, package_assets

    _autobuild_ui_from_source(project_root)

    repo_index, repo_assets = _repo_ui_paths(project_root)
    if repo_index:
        return repo_index, repo_assets

    return _package_ui_paths()


def start_provider_cli_updates() -> None:
    """Refresh stale provider CLIs in the background, once per server start.

    Provider CLIs are installed once and then never touched, so they silently
    fall months behind — an old Claude Code build spent three minutes retrying a
    dead OAuth refresh where the current one reports "log in again" in a second.
    Runs off the request path, at startup, before any task can be using them.
    """
    import threading

    from chad.util.installer import AIToolInstaller

    def run() -> None:
        try:
            updated = AIToolInstaller().update_stale_tools()
        except Exception:
            return  # A failed update must never stop the server from starting
        if updated:
            print(f"Updated provider CLI: {', '.join(sorted(updated))}")

    threading.Thread(target=run, daemon=True, name="provider-cli-update").start()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    init_start_time()

    # Always restore previous sessions from event logs on disk.
    from .services import get_session_manager
    from chad.util.config_manager import ConfigManager
    manager = get_session_manager()
    cleanup_days = ConfigManager().get_cleanup_days()
    restored = manager.load_from_logs(max_age_days=cleanup_days)
    if restored:
        print(f"Restored {restored} previous session(s)")

    yield

    # Shutdown: cleanup resources
    # TODO: Cleanup sessions, stop providers, etc.


def create_app(
    title: str = "Chad Server",
    debug: bool = False,
    cors_origins: list[str] | None = None,
    auth_token: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        title: Application title for OpenAPI docs
        debug: Enable debug mode
        cors_origins: List of allowed CORS origins (None = allow all)
        auth_token: Bearer token for API authentication (None = no auth)

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title=title,
        description="Backend API for Chad AI - multi-provider coding assistant",
        version=__version__,
        debug=debug,
        lifespan=lifespan,
    )

    # Store auth token on app state for WebSocket auth
    app.state.auth_token = auth_token

    # Configure CORS
    if cors_origins is None:
        # Default: allow all origins for development
        cors_origins = ["*"]

    # Add auth middleware before CORS so it runs after CORS (middleware order
    # is LIFO). Always installed: it no-ops while app.state.auth_token is None
    # and starts enforcing the moment a tunnel mints one.
    from .auth import BearerAuthMiddleware
    app.add_middleware(BearerAuthMiddleware)

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        # Auth uses bearer headers, never cookies — and wildcard origins with
        # credentials enabled would let any web page make credentialed
        # requests against the local server.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
    app.include_router(providers.router, prefix="/api/v1", tags=["Providers"])
    app.include_router(worktree.router, prefix="/api/v1/sessions", tags=["Worktree"])
    app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
    app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
    app.include_router(slack.router, prefix="/api/v1", tags=["Slack"])
    app.include_router(tunnel.router, prefix="/api/v1", tags=["Tunnel"])
    app.include_router(uploads.router, prefix="/api/v1/uploads", tags=["Uploads"])
    app.include_router(preview_tunnel.router, prefix="/api/v1", tags=["Preview Tunnel"])

    # Serve the single-file React UI if available (packaged or repo build).
    ui_index, ui_assets = _resolve_ui_paths()
    if ui_index:
        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(
                ui_index,
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        if ui_assets:
            app.mount("/assets", StaticFiles(directory=ui_assets), name="assets")

    return app


# Create default application instance
app = create_app()

"""FastAPI application factory for Chad server."""

from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .state import init_start_time
from .api.routes import health, sessions, providers, worktree, config, ws, slack, tunnel


def _resolve_ui_dist() -> Path | None:
    """Return the path to the packaged React build if available."""
    # Prefer packaged assets (bundled in wheel)
    try:
        package_dist = resources.files("chad.ui_dist")
        if package_dist.is_dir():
            return Path(package_dist)
    except Exception:
        pass

    # Fallback to repository build (useful in editable installs)
    repo_dist = Path(__file__).resolve().parents[3] / "ui" / "dist"
    if repo_dist.is_dir():
        return repo_dist
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    init_start_time()

    # Startup: initialize services
    # TODO: Initialize session manager, etc.

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

    # Add auth middleware before CORS so it runs after CORS (middleware order is LIFO)
    if auth_token:
        from .auth import BearerAuthMiddleware
        app.add_middleware(BearerAuthMiddleware, token=auth_token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
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

    # Serve React UI static files if available (packaged or repo build).
    ui_dist = _resolve_ui_dist()
    if ui_dist:
        from fastapi.responses import FileResponse

        # Serve index.html for the root path (SPA fallback)
        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(ui_dist / "index.html")

        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="ui-assets")

    return app


# Create default application instance
app = create_app()

"""FastAPI application factory for Chad server."""

from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .state import init_start_time
from .api.routes import health, sessions, providers, worktree, config, ws, slack, tunnel, uploads


def _resolve_ui_index() -> Path | None:
    """Return the path to the UI index.html if available.

    The UI is a single self-contained HTML file (portable build) that can be
    served without any additional asset files.
    """
    # Prefer packaged assets (bundled in wheel)
    try:
        package_dist = resources.files("chad.ui_dist")
        index = Path(package_dist) / "index.html"
        if index.is_file():
            return index
    except Exception:
        pass

    # Fallback to portable build in repository (useful in editable installs)
    repo_portable = Path(__file__).resolve().parents[3] / "ui" / "dist-portable" / "index.html"
    if repo_portable.is_file():
        return repo_portable
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
    app.include_router(uploads.router, prefix="/api/v1/uploads", tags=["Uploads"])

    # Serve the single-file React UI if available (packaged or repo build).
    ui_index = _resolve_ui_index()
    if ui_index:
        from fastapi.responses import FileResponse

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(ui_index)

    return app


# Create default application instance
app = create_app()

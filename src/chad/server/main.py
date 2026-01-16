"""FastAPI application factory for Chad server."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .state import init_start_time
from .api.routes import health, sessions, providers, worktree, config, ws


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
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        title: Application title for OpenAPI docs
        debug: Enable debug mode
        cors_origins: List of allowed CORS origins (None = allow all)

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

    # Configure CORS
    if cors_origins is None:
        # Default: allow all origins for development
        cors_origins = ["*"]

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

    return app


# Create default application instance
app = create_app()

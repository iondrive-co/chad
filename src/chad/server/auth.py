"""Bearer token authentication middleware for tunnel access."""

import secrets

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


def generate_token() -> str:
    """Generate a random bearer token for tunnel authentication."""
    return secrets.token_urlsafe(32)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that requires Bearer token on /api/ routes.

    Skips auth for:
    - GET /status (health check)
    - Static routes (/, /assets/)
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip auth for health check and static routes
        if path == "/status" or path == "/" or path.startswith("/assets"):
            return await call_next(request)

        # Only require auth on /api/ routes
        if not path.startswith("/api/"):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header == f"Bearer {self.token}":
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing authentication token"},
        )


def check_websocket_token(websocket: WebSocket, token: str) -> bool:
    """Check if a WebSocket connection has a valid token query parameter.

    WebSocket connections can't set custom headers from browser JS,
    so the token is passed as a ?token= query parameter.
    """
    ws_token = websocket.query_params.get("token", "")
    return ws_token == token

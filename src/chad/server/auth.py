"""Bearer token authentication middleware and browser tickets."""

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


def generate_token() -> str:
    """Generate a random bearer token for tunnel authentication."""
    return secrets.token_urlsafe(32)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that requires Bearer token on /api/ routes.

    Skips auth only for explicitly public routes:
    - GET /status (health check)
    - Static UI routes (/, /assets/)
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Always pass through OPTIONS — CORS preflight requests never carry
        # auth headers, so blocking them breaks cross-origin access entirely.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for health check and static routes
        if path == "/status" or path == "/" or path == "/assets" or path.startswith("/assets/"):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header == f"Bearer {self.token}":
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing authentication token"},
        )


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def mint_browser_ticket(
    secret: str,
    purpose: str,
    resource: str,
    ttl_seconds: int,
) -> str:
    """Mint a signed browser ticket scoped to a purpose and resource."""
    expires_at = int(time.time()) + ttl_seconds
    nonce = secrets.token_urlsafe(12)
    payload = f"{purpose}:{resource}:{expires_at}:{nonce}"
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{_urlsafe_b64encode(payload.encode('utf-8'))}.{_urlsafe_b64encode(signature)}"


def validate_browser_ticket(
    secret: str,
    ticket: str,
    purpose: str,
    resource: str,
) -> bool:
    """Validate a signed browser ticket."""
    try:
        encoded_payload, encoded_sig = ticket.split(".", 1)
        payload = _urlsafe_b64decode(encoded_payload).decode("utf-8")
        supplied_sig = _urlsafe_b64decode(encoded_sig)
        ticket_purpose, ticket_resource, expires_at, nonce = payload.split(":", 3)
        del nonce
    except Exception:
        return False

    if ticket_purpose != purpose or ticket_resource != resource:
        return False

    try:
        if int(expires_at) < int(time.time()):
            return False
    except ValueError:
        return False

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return hmac.compare_digest(expected_sig, supplied_sig)


def check_websocket_ticket(websocket: WebSocket, token: str, session_id: str) -> bool:
    """Check if a WebSocket connection has a valid one-time browser ticket."""
    ticket = websocket.query_params.get("ticket", "")
    return validate_browser_ticket(
        secret=token,
        ticket=ticket,
        purpose="ws",
        resource=session_id,
    )

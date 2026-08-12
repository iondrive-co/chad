"""Bearer token authentication middleware and browser tickets."""

import base64
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


def generate_token() -> str:
    """Generate a random bearer token for tunnel authentication."""
    return secrets.token_urlsafe(32)


class _TicketNonceStore:
    """Records redeemed ticket nonces so a ticket can only be used once.

    Tickets are signed and short-lived, but without this a leaked ticket
    (it travels in a URL, so it lands in logs and history) could be replayed
    for the rest of its TTL. Entries are dropped once the ticket they belong
    to has expired, so the store stays bounded by the ticket TTL.
    """

    def __init__(self) -> None:
        self._redeemed: dict[str, int] = {}
        self._lock = threading.Lock()

    def redeem(self, nonce: str, expires_at: int) -> bool:
        """Mark a nonce as used. Returns False if it was already redeemed."""
        now = int(time.time())
        with self._lock:
            for used_nonce, used_exp in list(self._redeemed.items()):
                if used_exp < now:
                    del self._redeemed[used_nonce]
            if nonce in self._redeemed:
                return False
            self._redeemed[nonce] = expires_at
            return True

    def clear(self) -> None:
        with self._lock:
            self._redeemed.clear()


_ticket_nonces = _TicketNonceStore()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that requires Bearer token on /api/ routes.

    The token is read from ``app.state.auth_token`` on every request rather
    than captured at construction, so enabling remote access at runtime (the
    tunnel endpoints mint a token when none exists) immediately starts
    enforcing auth. When that value is None the middleware is a no-op.

    Skips auth only for explicitly public routes:
    - GET /status (health check)
    - Static UI routes (/, /assets/)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = getattr(request.app.state, "auth_token", None)
        if not token:
            return await call_next(request)

        path = request.url.path

        # Always pass through OPTIONS — CORS preflight requests never carry
        # auth headers, so blocking them breaks cross-origin access entirely.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for health check and static routes
        if path == "/status" or path == "/" or path == "/assets" or path.startswith("/assets/"):
            return await call_next(request)

        # Check Authorization header (constant-time compare)
        auth_header = request.headers.get("authorization", "")
        if hmac.compare_digest(auth_header, f"Bearer {token}"):
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
    redeem: bool = True,
) -> bool:
    """Validate a signed browser ticket.

    Args:
        redeem: When True (the default) the ticket's nonce is consumed, so a
            second use of the same ticket fails even inside its TTL.
    """
    try:
        encoded_payload, encoded_sig = ticket.split(".", 1)
        payload = _urlsafe_b64decode(encoded_payload).decode("utf-8")
        supplied_sig = _urlsafe_b64decode(encoded_sig)
        ticket_purpose, ticket_resource, expires_at, nonce = payload.split(":", 3)
    except Exception:
        return False

    if ticket_purpose != purpose or ticket_resource != resource:
        return False

    try:
        expiry = int(expires_at)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_sig, supplied_sig):
        return False

    # Signature is good — burn the nonce last so an invalid ticket can't be
    # used to evict a pending valid one.
    if redeem:
        return _ticket_nonces.redeem(nonce, expiry)
    return True


def check_websocket_ticket(websocket: WebSocket, token: str, session_id: str) -> bool:
    """Check if a WebSocket connection has a valid one-time browser ticket."""
    ticket = websocket.query_params.get("ticket", "")
    return validate_browser_ticket(
        secret=token,
        ticket=ticket,
        purpose="ws",
        resource=session_id,
    )

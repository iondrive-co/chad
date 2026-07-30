"""Tunnel management endpoints for Cloudflare quick-tunnel remote access."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from chad.server.services import tunnel_service

router = APIRouter()


class TunnelStatus(BaseModel):
    """Current state of the Cloudflare tunnel."""

    running: bool = Field(description="Whether the tunnel is active")
    url: str | None = Field(default=None, description="Public tunnel URL")
    subdomain: str | None = Field(default=None, description="Tunnel subdomain (pairing code)")
    error: str | None = Field(default=None, description="Last error message")
    token: str | None = Field(
        default=None,
        description="Auth token required by the tunnelled server (returned on start)",
    )
    pairing_code: str | None = Field(
        default=None,
        description="Pairing code (subdomain:token) for the connect field or QR",
    )


def _status_with_auth(request: Request, include_token: bool = False) -> TunnelStatus:
    """Build a TunnelStatus, optionally exposing the auth token."""
    svc = tunnel_service.get_tunnel_service()
    status = TunnelStatus(**svc.status())
    token = getattr(request.app.state, "auth_token", None)
    if include_token and token:
        status.token = token
        if status.subdomain:
            status.pairing_code = f"{status.subdomain}:{token}"
    return status


@router.get("/tunnel", response_model=TunnelStatus)
async def get_tunnel_status(request: Request) -> TunnelStatus:
    """Get the current tunnel status."""
    return _status_with_auth(request)


@router.post("/tunnel/start", response_model=TunnelStatus)
async def start_tunnel(request: Request) -> TunnelStatus:
    """Start a Cloudflare quick-tunnel. Port is inferred from the server.

    A tunnel publishes this server to the internet, so it must never run
    unauthenticated: if the server has no auth token yet, one is minted here
    and returned so the caller can keep talking to the (now authenticated)
    server and share the pairing code.
    """
    if not getattr(request.app.state, "auth_token", None):
        from chad.server.auth import generate_token

        request.app.state.auth_token = generate_token()

    port = request.url.port or 8000
    svc = tunnel_service.get_tunnel_service()
    svc.start(port)
    return _status_with_auth(request, include_token=True)


@router.post("/tunnel/stop", response_model=TunnelStatus)
async def stop_tunnel(request: Request) -> TunnelStatus:
    """Stop the running tunnel."""
    svc = tunnel_service.get_tunnel_service()
    svc.stop()
    return _status_with_auth(request)

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


@router.get("/tunnel", response_model=TunnelStatus)
async def get_tunnel_status() -> TunnelStatus:
    """Get the current tunnel status."""
    svc = tunnel_service.get_tunnel_service()
    return TunnelStatus(**svc.status())


@router.post("/tunnel/start", response_model=TunnelStatus)
async def start_tunnel(request: Request) -> TunnelStatus:
    """Start a Cloudflare quick-tunnel. Port is inferred from the server."""
    port = request.url.port or 8000
    svc = tunnel_service.get_tunnel_service()
    svc.start(port)
    return TunnelStatus(**svc.status())


@router.post("/tunnel/stop", response_model=TunnelStatus)
async def stop_tunnel() -> TunnelStatus:
    """Stop the running tunnel."""
    svc = tunnel_service.get_tunnel_service()
    svc.stop()
    return TunnelStatus(**svc.status())

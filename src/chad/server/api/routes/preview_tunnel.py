"""Preview tunnel endpoints for tunneling a local dev server."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server.services import preview_tunnel_service

router = APIRouter()


class PreviewTunnelStatus(BaseModel):
    """Current state of the preview tunnel."""

    running: bool = Field(description="Whether the preview tunnel is active")
    url: str | None = Field(default=None, description="Public tunnel URL")
    port: int | None = Field(default=None, description="Local port being tunneled")
    error: str | None = Field(default=None, description="Last error message")


class PreviewTunnelStartRequest(BaseModel):
    """Request to start a preview tunnel."""

    port: int = Field(description="Local port to tunnel")


@router.get("/preview-tunnel", response_model=PreviewTunnelStatus)
async def get_preview_tunnel_status() -> PreviewTunnelStatus:
    """Get the current preview tunnel status."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    return PreviewTunnelStatus(**svc.status())


@router.post("/preview-tunnel/start", response_model=PreviewTunnelStatus)
async def start_preview_tunnel(request: PreviewTunnelStartRequest) -> PreviewTunnelStatus:
    """Start a preview tunnel to a local port."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    svc.start(request.port)
    return PreviewTunnelStatus(**svc.status())


@router.post("/preview-tunnel/stop", response_model=PreviewTunnelStatus)
async def stop_preview_tunnel() -> PreviewTunnelStatus:
    """Stop the running preview tunnel."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    svc.stop()
    return PreviewTunnelStatus(**svc.status())

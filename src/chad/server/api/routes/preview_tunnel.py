"""Preview tunnel endpoints for starting a project dev server and optionally tunneling it."""

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server.services import preview_tunnel_service, get_session_manager

router = APIRouter()


class PreviewTunnelStatus(BaseModel):
    """Current state of the preview."""

    running: bool = Field(description="Whether the preview app is active")
    url: str | None = Field(default=None, description="Public tunnel URL (remote only)")
    port: int | None = Field(default=None, description="Local port being served")
    error: str | None = Field(default=None, description="Last error message")


class PreviewStartRequest(BaseModel):
    """Request to start a preview."""

    port: int = Field(description="Local port the app listens on")
    command: str | None = Field(default=None, description="Shell command to start the app")
    session_id: str | None = Field(default=None, description="Session ID to resolve worktree cwd")
    tunnel: bool = Field(default=False, description="Whether to create a Cloudflare tunnel")


def _resolve_cwd(session_id: str | None) -> str | None:
    """Resolve the working directory for the preview app.

    Uses the session's worktree path if it exists, otherwise falls back
    to the session's project path.
    """
    if not session_id:
        return None

    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        return None

    # Prefer worktree (agent's isolated copy) over project path
    if session.worktree_path and Path(session.worktree_path).is_dir():
        return str(session.worktree_path)

    # Worktree was merged/deleted — use main project path
    if session.project_path:
        return str(session.project_path)

    return None


@router.get("/preview-tunnel", response_model=PreviewTunnelStatus)
async def get_preview_tunnel_status() -> PreviewTunnelStatus:
    """Get the current preview status."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    return PreviewTunnelStatus(**svc.status())


@router.post("/preview-tunnel/start", response_model=PreviewTunnelStatus)
async def start_preview_tunnel(request: PreviewStartRequest) -> PreviewTunnelStatus:
    """Start a preview app and optionally tunnel it."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    cwd = _resolve_cwd(request.session_id)
    svc.start(
        port=request.port,
        command=request.command,
        cwd=cwd,
        tunnel=request.tunnel,
    )
    return PreviewTunnelStatus(**svc.status())


@router.post("/preview-tunnel/stop", response_model=PreviewTunnelStatus)
async def stop_preview_tunnel() -> PreviewTunnelStatus:
    """Stop the running preview."""
    svc = preview_tunnel_service.get_preview_tunnel_service()
    svc.stop()
    return PreviewTunnelStatus(**svc.status())

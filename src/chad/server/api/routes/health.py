"""Status endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server import __version__
from chad.server.state import get_uptime

router = APIRouter()


class StatusResponse(BaseModel):
    """Response model for status endpoint."""

    status: str = Field(default="healthy", description="Server status")
    version: str = Field(description="Server version")
    uptime_seconds: float = Field(description="Server uptime in seconds")


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Status endpoint reporting server health, version, and uptime."""
    return StatusResponse(
        status="healthy",
        version=__version__,
        uptime_seconds=get_uptime(),
    )

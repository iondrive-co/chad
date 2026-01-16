"""Health and status endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server import __version__
from chad.server.state import get_uptime
from chad.server.api.schemas import HealthResponse

router = APIRouter()


class StatusResponse(BaseModel):
    """Response model for status endpoint."""

    version: str = Field(description="Server version")
    status: str = Field(default="running", description="Server status")
    uptime_seconds: float = Field(description="Server uptime in seconds")


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint.

    Returns server health status, version, and uptime.
    """
    return HealthResponse(
        status="healthy",
        version=__version__,
        uptime_seconds=get_uptime(),
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Status endpoint reporting current version and state."""
    return StatusResponse(
        version=__version__,
        status="running",
        uptime_seconds=get_uptime(),
    )

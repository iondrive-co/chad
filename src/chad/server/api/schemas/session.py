"""Session-related Pydantic schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """Request model for creating a new session."""

    name: str = Field(default="New Session", description="Human-readable session name")
    project_path: str | None = Field(
        default=None, description="Path to the project directory"
    )


class SessionResponse(BaseModel):
    """Response model for session details."""

    id: str = Field(description="Unique session identifier")
    name: str = Field(description="Human-readable session name")
    project_path: str | None = Field(
        default=None, description="Path to the project directory"
    )
    active: bool = Field(default=False, description="Whether a task is currently running")
    has_worktree: bool = Field(default=False, description="Whether a git worktree exists")
    has_changes: bool = Field(default=False, description="Whether there are uncommitted changes")
    created_at: datetime = Field(description="When the session was created")
    last_activity: datetime = Field(description="When the session was last active")


class SessionListResponse(BaseModel):
    """Response model for listing sessions."""

    sessions: list[SessionResponse] = Field(default_factory=list)
    total: int = Field(description="Total number of sessions")


class SessionCancelResponse(BaseModel):
    """Response model for cancellation request."""

    session_id: str
    cancel_requested: bool = True
    message: str = "Cancellation requested"

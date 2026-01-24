"""Task-related Pydantic schemas."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class TaskCreate(BaseModel):
    """Request model for starting a new task."""

    project_path: str = Field(description="Absolute path to the project directory")
    task_description: str = Field(description="Description of the task to perform")
    coding_agent: str = Field(description="Name of the coding agent account to use")
    coding_model: str | None = Field(default=None, description="Optional model override for coding agent")
    coding_reasoning: str | None = Field(default=None, description="Optional reasoning level override")
    verification_agent: str | None = Field(default=None, description="Name of the verification agent account")
    verification_model: str | None = Field(default=None, description="Optional model override for verification")
    verification_reasoning: str | None = Field(default=None, description="Optional reasoning level for verification")
    target_branch: str | None = Field(default=None, description="Optional target branch for changes")
    # Terminal dimensions for PTY - should match client's actual terminal size
    terminal_rows: int | None = Field(default=None, ge=10, le=500, description="Terminal rows (height)")
    terminal_cols: int | None = Field(default=None, ge=40, le=500, description="Terminal columns (width)")


class TaskStatusResponse(BaseModel):
    """Response model for task status."""

    task_id: str = Field(description="Unique task identifier")
    session_id: str = Field(description="Parent session identifier")
    status: TaskStatus = Field(description="Current task status")
    progress: str | None = Field(default=None, description="Progress message if available")
    result: str | None = Field(default=None, description="Result message if completed")
    started_at: datetime | None = Field(default=None, description="When the task started")
    completed_at: datetime | None = Field(default=None, description="When the task completed")


class TaskFollowupRequest(BaseModel):
    """Request model for sending a follow-up message."""

    message: str = Field(description="Follow-up message to send to the agent")


class TaskFollowupResponse(BaseModel):
    """Response model for follow-up message."""

    task_id: str
    message_sent: bool = True
    status: TaskStatus

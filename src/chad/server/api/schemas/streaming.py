"""WebSocket streaming Pydantic schemas."""

from typing import Any, Literal
from pydantic import BaseModel, Field


StreamMessageType = Literal[
    "stream",            # Raw AI output chunk
    "activity",          # Activity update (tool use, thinking)
    "status",            # Status message
    "message_start",     # AI message started
    "message_complete",  # AI message completed
    "progress",          # Progress update from agent
    "complete",          # Task completed
    "error",             # Error occurred
]


class StreamMessage(BaseModel):
    """WebSocket message format for streaming updates."""

    type: StreamMessageType = Field(description="Message type")
    session_id: str = Field(description="Session this message belongs to")
    data: dict[str, Any] = Field(default_factory=dict, description="Message payload")


class StreamChunkData(BaseModel):
    """Data payload for stream chunks."""

    chunk: str = Field(description="Text chunk from AI")


class ActivityData(BaseModel):
    """Data payload for activity updates."""

    activity_type: str = Field(description="Type of activity (tool, thinking, etc.)")
    detail: str = Field(description="Activity detail/description")


class StatusData(BaseModel):
    """Data payload for status updates."""

    status: str = Field(description="Status message")


class MessageStartData(BaseModel):
    """Data payload for message start."""

    speaker: str = Field(description="Who is speaking (CODING AI, VERIFICATION AI, etc.)")


class MessageCompleteData(BaseModel):
    """Data payload for message complete."""

    speaker: str = Field(description="Who spoke")
    content: str = Field(description="Full message content")


class ProgressData(BaseModel):
    """Data payload for progress updates."""

    summary: str = Field(description="Progress summary")
    location: str | None = Field(default=None, description="Code location")
    next_step: str | None = Field(default=None, description="What the agent plans to do next")
    before_screenshot: str | None = Field(default=None, description="Path to before screenshot")
    before_description: str | None = Field(default=None, description="Screenshot description")


class CompleteData(BaseModel):
    """Data payload for task completion."""

    success: bool = Field(description="Whether task succeeded")
    message: str = Field(description="Completion message")
    change_summary: str | None = Field(default=None, description="Summary of changes made")


class ErrorData(BaseModel):
    """Data payload for errors."""

    error: str = Field(description="Error message")
    details: str | None = Field(default=None, description="Additional error details")


# Client -> Server messages
class ClientMessage(BaseModel):
    """Message from client to server via WebSocket."""

    type: Literal["subscribe", "cancel", "ping"] = Field(description="Client message type")


class SubscribeMessage(ClientMessage):
    """Client message to subscribe to session updates."""

    type: Literal["subscribe"] = "subscribe"


class CancelMessage(ClientMessage):
    """Client message to request task cancellation."""

    type: Literal["cancel"] = "cancel"


class PingMessage(ClientMessage):
    """Client ping message for keepalive."""

    type: Literal["ping"] = "ping"

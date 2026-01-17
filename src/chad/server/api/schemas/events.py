"""Pydantic schemas for streaming events."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactRefSchema(BaseModel):
    """Reference to an artifact file."""

    path: str = Field(description="Relative path from logs directory")
    sha256: str = Field(description="SHA256 hash of content")
    size: int = Field(description="Size in bytes")


class MessageBlockSchema(BaseModel):
    """A block within an assistant message."""

    kind: Literal["text", "thinking", "tool_call", "tool_result", "error"] = Field(
        description="Block type"
    )
    content: str = Field(default="", description="Block content")
    tool: str | None = Field(default=None, description="Tool name for tool_call blocks")
    tool_call_id: str | None = Field(default=None, description="Tool call ID")
    args: dict[str, Any] | None = Field(default=None, description="Tool arguments")


# Base event fields
class EventBaseSchema(BaseModel):
    """Common fields for all events."""

    event_id: str = Field(description="Unique event identifier (UUID)")
    ts: datetime = Field(description="Event timestamp (ISO8601)")
    seq: int = Field(description="Monotonic sequence number")
    session_id: str = Field(description="Session identifier")
    turn_id: str | None = Field(default=None, description="Conversation turn grouping")
    type: str = Field(description="Event type")


class SessionStartedEventSchema(EventBaseSchema):
    """Session started event."""

    type: Literal["session_started"] = "session_started"
    task_description: str = Field(description="Task description")
    project_path: str = Field(description="Project path")
    coding_provider: str = Field(description="Provider type")
    coding_account: str = Field(description="Account name")
    coding_model: str | None = Field(default=None, description="Model name")


class ModelSelectedEventSchema(EventBaseSchema):
    """Model selected event."""

    type: Literal["model_selected"] = "model_selected"
    provider: str = Field(description="Provider type")
    model: str = Field(description="Model name")
    reasoning_effort: str | None = Field(default=None, description="Reasoning level")


class ProviderSwitchedEventSchema(EventBaseSchema):
    """Provider switched event."""

    type: Literal["provider_switched"] = "provider_switched"
    from_provider: str = Field(description="Previous provider")
    to_provider: str = Field(description="New provider")
    from_model: str = Field(description="Previous model")
    to_model: str = Field(description="New model")
    reason: str = Field(description="Reason for switch")


class UserMessageEventSchema(EventBaseSchema):
    """User message event."""

    type: Literal["user_message"] = "user_message"
    content: str = Field(description="Message content")


class AssistantMessageEventSchema(EventBaseSchema):
    """Assistant message event."""

    type: Literal["assistant_message"] = "assistant_message"
    blocks: list[MessageBlockSchema] = Field(description="Message blocks")


class ToolDeclaredEventSchema(EventBaseSchema):
    """Tool declared event."""

    type: Literal["tool_declared"] = "tool_declared"
    name: str = Field(description="Tool name")
    args_schema: dict[str, Any] = Field(description="Argument schema")
    version: str = Field(default="1.0", description="Tool version")


class ToolCallStartedEventSchema(EventBaseSchema):
    """Tool call started event."""

    type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: str = Field(description="Unique tool call ID")
    tool: str = Field(description="Tool name (bash, read, write, edit, mcp, etc.)")

    # For bash commands
    cwd: str | None = Field(default=None, description="Working directory")
    command: str | None = Field(default=None, description="Command to execute")
    env_redactions: list[str] | None = Field(
        default=None, description="Environment variables redacted"
    )
    timeout_s: float | None = Field(default=None, description="Timeout in seconds")

    # For file operations
    path: str | None = Field(default=None, description="File path")
    file_bytes: int | None = Field(default=None, description="File size in bytes")
    sha256: str | None = Field(default=None, description="File SHA256 hash")

    # For file edits
    before_sha256: str | None = Field(default=None, description="Hash before edit")

    # For MCP tools
    server: str | None = Field(default=None, description="MCP server name")
    tool_name: str | None = Field(default=None, description="MCP tool name")
    args: dict[str, Any] | None = Field(default=None, description="Tool arguments")


class ToolCallFinishedEventSchema(EventBaseSchema):
    """Tool call finished event."""

    type: Literal["tool_call_finished"] = "tool_call_finished"
    tool_call_id: str = Field(description="References ToolCallStartedEvent")
    exit_code: int | None = Field(default=None, description="Exit code for commands")
    duration_ms: int = Field(description="Duration in milliseconds")
    stdout_ref: ArtifactRefSchema | None = Field(
        default=None, description="Reference to stdout artifact"
    )
    stderr_ref: ArtifactRefSchema | None = Field(
        default=None, description="Reference to stderr artifact"
    )
    llm_summary: str = Field(description="Bounded summary for handover")

    # For file edits
    after_sha256: str | None = Field(default=None, description="Hash after edit")
    patch_ref: ArtifactRefSchema | None = Field(
        default=None, description="Reference to patch artifact"
    )


class VerificationAttemptEventSchema(EventBaseSchema):
    """Verification attempt event."""

    type: Literal["verification_attempt"] = "verification_attempt"
    attempt_number: int = Field(description="Attempt number")
    tool_call_refs: list[str] = Field(description="References to tool calls")
    passed: bool = Field(description="Whether verification passed")
    summary: str = Field(description="Verification summary")
    issues: list[str] = Field(default_factory=list, description="Issues found")


class ContextCondensedEventSchema(EventBaseSchema):
    """Context condensed event."""

    type: Literal["context_condensed"] = "context_condensed"
    replaces_seq_range: tuple[int, int] = Field(
        description="Sequence range being condensed"
    )
    summary_text: str = Field(description="Summary of condensed context")
    policy: str = Field(default="rolling_window", description="Condensation policy")


class TerminalOutputEventSchema(EventBaseSchema):
    """Terminal output event."""

    type: Literal["terminal_output"] = "terminal_output"
    data: str = Field(description="Base64 encoded terminal output")
    has_ansi: bool = Field(default=True, description="Whether output contains ANSI codes")


class SessionEndedEventSchema(EventBaseSchema):
    """Session ended event."""

    type: Literal["session_ended"] = "session_ended"
    success: bool = Field(description="Whether session succeeded")
    reason: str = Field(description="Reason for ending")
    total_tool_calls: int = Field(description="Total number of tool calls")
    total_turns: int = Field(description="Total conversation turns")


# Streaming message types
class SSEMessage(BaseModel):
    """Server-Sent Event message format."""

    event: str = Field(description="Event type: terminal, event, ping, error")
    data: dict[str, Any] = Field(description="Event data")
    id: str | None = Field(default=None, description="Event ID for reconnection")


class TerminalOutputData(BaseModel):
    """Data for terminal output SSE messages."""

    data: str = Field(description="Base64 encoded terminal bytes")
    seq: int = Field(description="Sequence number")


class PingData(BaseModel):
    """Data for ping SSE messages."""

    ts: datetime = Field(description="Server timestamp")


# Client -> Server messages
class InputMessage(BaseModel):
    """Client message to send input to PTY."""

    type: Literal["input"] = "input"
    data: str = Field(description="Base64 encoded input bytes")


class ResizeMessage(BaseModel):
    """Client message to resize terminal."""

    type: Literal["resize"] = "resize"
    rows: int = Field(description="Terminal rows")
    cols: int = Field(description="Terminal columns")


class CancelMessage(BaseModel):
    """Client message to cancel task."""

    type: Literal["cancel"] = "cancel"


class PingRequestMessage(BaseModel):
    """Client ping message."""

    type: Literal["ping"] = "ping"


# Request/Response schemas for new endpoints
class SendInputRequest(BaseModel):
    """Request body for sending input to PTY."""

    data: str = Field(description="Base64 encoded input bytes")


class ResizeTerminalRequest(BaseModel):
    """Request body for resizing terminal."""

    rows: int = Field(ge=1, le=500, description="Terminal rows")
    cols: int = Field(ge=1, le=500, description="Terminal columns")


class StreamEventsParams(BaseModel):
    """Query parameters for stream endpoint."""

    since_seq: int = Field(default=0, description="Return events after this sequence")
    include_terminal: bool = Field(default=True, description="Include raw PTY output")

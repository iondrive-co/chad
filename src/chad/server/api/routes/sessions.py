"""Session management endpoints."""

import base64

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from chad.server.api.schemas import (
    SessionCreate,
    SessionResponse,
    SessionListResponse,
    SessionCancelResponse,
    TaskCreate,
    TaskStatusResponse,
    TaskStatus,
)
from chad.server.api.schemas.events import (
    SendInputRequest,
    SendMessageRequest,
    ResizeTerminalRequest,
)
from chad.server.services import Session, get_session_manager, get_task_executor, TaskState
from chad.server.services.pty_stream import get_pty_stream_service
from chad.server.services.event_mux import EventMultiplexer, format_sse_event

router = APIRouter()


def _session_to_response(session: Session) -> SessionResponse:
    """Convert a Session object to a SessionResponse."""
    return SessionResponse(
        id=session.id,
        name=session.name,
        project_path=session.project_path,
        active=session.active,
        has_worktree=session.worktree_path is not None,
        has_changes=session.has_worktree_changes,
        created_at=session.created_at,
        last_activity=session.last_activity,
    )


def _task_state_to_status(state: TaskState) -> TaskStatus:
    """Convert TaskState enum to TaskStatus literal."""
    return state.value


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(request: SessionCreate) -> SessionResponse:
    """Create a new session.

    Sessions are isolated workspaces for running coding tasks.
    Each session can have its own git worktree and task state.
    """
    manager = get_session_manager()
    session = manager.create_session(
        project_path=request.project_path,
        name=request.name,
    )
    return _session_to_response(session)


@router.get("", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    """List all active sessions."""
    manager = get_session_manager()
    sessions = manager.list_sessions()
    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        total=len(sessions),
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    """Get details of a specific session."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return _session_to_response(session)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Delete a session and clean up its resources."""
    manager = get_session_manager()

    # Terminate any active PTY sessions
    pty_service = get_pty_stream_service()
    pty_session = pty_service.get_session_by_session_id(session_id)
    if pty_session:
        pty_service.terminate(pty_session.stream_id)
        pty_service.cleanup_session(pty_session.stream_id)

    if not manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.post("/{session_id}/cancel", response_model=SessionCancelResponse)
async def cancel_session(session_id: str) -> SessionCancelResponse:
    """Request cancellation of the currently running task in a session."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Terminate PTY session if active
    pty_service = get_pty_stream_service()
    pty_session = pty_service.get_session_by_session_id(session_id)
    if pty_session and pty_session.active:
        pty_service.terminate(pty_session.stream_id)
        manager.set_cancel_requested(session_id, True)
        return SessionCancelResponse(
            session_id=session_id,
            cancel_requested=True,
            message="PTY session terminated",
        )

    if not session.active:
        return SessionCancelResponse(
            session_id=session_id,
            cancel_requested=False,
            message="No active task to cancel",
        )

    manager.set_cancel_requested(session_id, True)
    return SessionCancelResponse(
        session_id=session_id,
        cancel_requested=True,
        message="Cancellation requested",
    )


@router.post("/{session_id}/tasks", response_model=TaskStatusResponse, status_code=201)
async def start_task(session_id: str, request: TaskCreate) -> TaskStatusResponse:
    """Start a new coding task in a session.

    This endpoint starts the task and returns immediately.
    Use GET /sessions/{id}/stream for SSE updates or /ws/{id} for WebSocket.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    executor = get_task_executor()

    try:
        task = executor.start_task(
            session_id=session_id,
            project_path=request.project_path,
            task_description=request.task_description,
            coding_account=request.coding_agent,
            coding_model=request.coding_model,
            coding_reasoning=request.coding_reasoning,
            terminal_rows=request.terminal_rows,
            terminal_cols=request.terminal_cols,
            screenshots=request.screenshots,
            override_prompt=request.override_prompt or request.override_exploration_prompt,
            verification_account=request.verification_agent,
            verification_model=request.verification_model,
            verification_reasoning=request.verification_reasoning,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return TaskStatusResponse(
        task_id=task.id,
        session_id=session_id,
        status=_task_state_to_status(task.state),
        progress=task.progress,
        result=task.result,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


@router.get("/{session_id}/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(session_id: str, task_id: str) -> TaskStatusResponse:
    """Get the status of a task."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    executor = get_task_executor()
    task = executor.get_task(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in session {session_id}")

    return TaskStatusResponse(
        task_id=task.id,
        session_id=session_id,
        status=_task_state_to_status(task.state),
        progress=task.progress,
        result=task.result,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


@router.get("/{session_id}/stream")
async def stream_session(
    session_id: str,
    since_seq: int = Query(default=0, description="Return events after this sequence"),
    include_terminal: bool = Query(default=True, description="Include raw PTY output"),
    include_events: bool = Query(default=True, description="Include structured events from EventLog"),
):
    """SSE endpoint for real-time session events.

    Uses EventMultiplexer to unify PTY and EventLog events into a single
    ordered stream with consistent sequence numbers.

    Event types:
    - terminal: Raw PTY output (base64 encoded)
    - event: Structured event from event log
    - ping: Keepalive every 15s
    - complete: Task completed
    - error: Error occurred
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    async def event_generator():
        """Generate SSE events using EventMultiplexer."""
        pty_service = get_pty_stream_service()
        executor = get_task_executor()

        # Find the task for this session to access its EventLog
        task = None
        for t in executor._tasks.values():
            if t.session_id == session_id:
                task = t
                break

        # Create multiplexer with task's EventLog
        event_log = task.event_log if task else None
        mux = EventMultiplexer(session_id, event_log)

        # Stream events through the multiplexer
        async for event in mux.stream_with_since(
            pty_service,
            since_seq=since_seq,
            include_terminal=include_terminal,
            include_events=include_events,
        ):
            yield format_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}/input")
async def send_input(session_id: str, request: SendInputRequest) -> dict:
    """Send input to the PTY session.

    The data should be base64 encoded bytes.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    pty_service = get_pty_stream_service()
    pty_session = pty_service.get_session_by_session_id(session_id)

    if not pty_session or not pty_session.active:
        raise HTTPException(status_code=400, detail="No active PTY session")

    try:
        data = base64.b64decode(request.data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")

    if not pty_service.send_input(pty_session.stream_id, data):
        raise HTTPException(status_code=500, detail="Failed to send input")

    return {"success": True}


@router.post("/{session_id}/resize")
async def resize_terminal(session_id: str, request: ResizeTerminalRequest) -> dict:
    """Resize the PTY terminal."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    pty_service = get_pty_stream_service()
    pty_session = pty_service.get_session_by_session_id(session_id)

    if not pty_session or not pty_session.active:
        raise HTTPException(status_code=400, detail="No active PTY session")

    if not pty_service.resize(pty_session.stream_id, request.rows, request.cols):
        raise HTTPException(status_code=500, detail="Failed to resize terminal")

    return {"success": True, "rows": request.rows, "cols": request.cols}


@router.post("/{session_id}/messages")
async def send_message(session_id: str, request: SendMessageRequest) -> dict:
    """Send a user message to the running session.

    The message is forwarded to the session's event loop, which writes it
    to the active PTY.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    executor = get_task_executor()
    task = None
    for t in executor._tasks.values():
        if t.session_id == session_id:
            task = t
            break

    if not task:
        raise HTTPException(status_code=400, detail="No active task in session")

    event_loop = getattr(task, "_session_event_loop", None)
    if event_loop is None:
        raise HTTPException(status_code=400, detail="No active event loop in session")

    event_loop.enqueue_message(request.content, request.source)
    return {"success": True, "session_id": session_id}


@router.get("/{session_id}/milestones")
async def get_milestones(
    session_id: str,
    since_seq: int = Query(default=0, description="Return milestones after this sequence"),
) -> dict:
    """Get milestones for a session (polling catch-up).

    Milestones also flow through the SSE endpoint via EventLog events,
    but this endpoint allows catch-up after reconnection.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    executor = get_task_executor()
    task = None
    for t in executor._tasks.values():
        if t.session_id == session_id:
            task = t
            break

    if not task:
        return {"milestones": [], "latest_seq": 0}

    event_loop = getattr(task, "_session_event_loop", None)
    if event_loop is None:
        return {"milestones": [], "latest_seq": 0}

    milestones = event_loop.get_milestones(since_seq)
    latest_seq = event_loop.get_latest_milestone_seq()
    return {"milestones": milestones, "latest_seq": latest_seq}


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: str,
    since_seq: int = Query(default=0, description="Return events after this sequence"),
    event_types: str | None = Query(
        default=None,
        description="Comma-separated list of event types to filter (e.g., 'session_started,tool_call_started')"
    ),
):
    """Get structured events from the session's EventLog.

    This endpoint retrieves persisted events from the EventLog, useful for:
    - Catching up on missed events after reconnecting
    - Building session history/timeline views
    - Debugging and auditing agent activity

    Event types include: session_started, model_selected, user_message,
    assistant_message, tool_call_started, tool_call_finished, terminal_output,
    verification_attempt, session_ended, etc.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Find the task for this session to access its EventLog
    executor = get_task_executor()
    task = None
    for t in executor._tasks.values():
        if t.session_id == session_id:
            task = t
            break

    if not task or not task.event_log:
        return {"events": [], "latest_seq": 0}

    # Parse event type filter
    type_filter = None
    if event_types:
        type_filter = [t.strip() for t in event_types.split(",") if t.strip()]

    events = task.event_log.get_events(since_seq=since_seq, event_types=type_filter)
    latest_seq = task.event_log.get_latest_seq()

    return {
        "events": events,
        "latest_seq": latest_seq,
        "session_id": session_id,
    }

"""Session management endpoints."""

import asyncio
import base64
import json
from datetime import datetime, timezone

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
    ResizeTerminalRequest,
)
from chad.server.services import Session, get_session_manager, get_task_executor, TaskState
from chad.server.services.pty_stream import get_pty_stream_service

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
        """Generate SSE events from PTY output and EventLog."""
        pty_service = get_pty_stream_service()
        executor = get_task_executor()
        seq = since_seq
        event_log_seq = since_seq  # Track EventLog sequence separately
        last_ping = datetime.now(timezone.utc)
        ping_interval = 15  # seconds

        while True:
            # Check for PTY session
            pty_session = pty_service.get_session_by_session_id(session_id)

            # Find the task for this session to access its EventLog
            task = None
            for t in executor._tasks.values():
                if t.session_id == session_id:
                    task = t
                    break

            if pty_session and include_terminal:
                # Stream PTY events with EventLog events interspersed
                try:
                    async for event in pty_service.subscribe(pty_session.stream_id):
                        seq += 1
                        if event.type == "output":
                            data = {
                                "data": event.data,
                                "seq": seq,
                                "has_ansi": event.has_ansi,
                            }
                            yield f"event: terminal\ndata: {json.dumps(data)}\nid: {seq}\n\n"

                            # Also emit any new EventLog events (non-terminal types)
                            if include_events and task and task.event_log:
                                new_events = task.event_log.get_events(since_seq=event_log_seq)
                                for log_event in new_events:
                                    # Skip terminal_output events - already streamed above
                                    if log_event.get("type") == "terminal_output":
                                        continue
                                    event_log_seq = log_event.get("seq", event_log_seq)
                                    seq += 1
                                    log_event["seq"] = seq
                                    yield f"event: event\ndata: {json.dumps(log_event)}\nid: {seq}\n\n"

                        elif event.type == "exit":
                            # Emit any remaining EventLog events before completion
                            if include_events and task and task.event_log:
                                new_events = task.event_log.get_events(since_seq=event_log_seq)
                                for log_event in new_events:
                                    if log_event.get("type") == "terminal_output":
                                        continue
                                    event_log_seq = log_event.get("seq", event_log_seq)
                                    seq += 1
                                    log_event["seq"] = seq
                                    yield f"event: event\ndata: {json.dumps(log_event)}\nid: {seq}\n\n"

                            seq += 1
                            data = {
                                "exit_code": event.exit_code,
                                "seq": seq,
                            }
                            yield f"event: complete\ndata: {json.dumps(data)}\nid: {seq}\n\n"
                            return
                        elif event.type == "error":
                            seq += 1
                            data = {
                                "error": event.error,
                                "seq": seq,
                            }
                            yield f"event: error\ndata: {json.dumps(data)}\nid: {seq}\n\n"
                            return
                except Exception as e:
                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                    return
            else:
                # No active PTY - poll for task events from executor
                found_task = False

                for task_id, t in list(executor._tasks.items()):
                    if t.session_id == session_id:
                        found_task = True
                        events = executor.get_events(task_id, timeout=0.01)
                        for event in events:
                            seq += 1
                            data = {"type": event.type, "seq": seq, **event.data}
                            yield f"event: event\ndata: {json.dumps(data)}\nid: {seq}\n\n"

                            if event.type in ("complete", "error"):
                                return

                        # Also check EventLog for structured events
                        if include_events and t.event_log:
                            new_events = t.event_log.get_events(since_seq=event_log_seq)
                            for log_event in new_events:
                                if log_event.get("type") == "terminal_output":
                                    continue
                                event_log_seq = log_event.get("seq", event_log_seq)
                                seq += 1
                                log_event["seq"] = seq
                                yield f"event: event\ndata: {json.dumps(log_event)}\nid: {seq}\n\n"

                # Send ping if needed
                now = datetime.now(timezone.utc)
                if (now - last_ping).total_seconds() >= ping_interval:
                    last_ping = now
                    ping_data = {"ts": now.isoformat()}
                    yield f"event: ping\ndata: {json.dumps(ping_data)}\n\n"

                # Brief sleep if no PTY session
                if not found_task:
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(0.1)

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

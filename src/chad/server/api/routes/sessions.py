"""Session management endpoints."""

from fastapi import APIRouter, HTTPException

from chad.server.api.schemas import (
    SessionCreate,
    SessionResponse,
    SessionListResponse,
    SessionCancelResponse,
    TaskCreate,
    TaskStatusResponse,
    TaskStatus,
)
from chad.server.services import Session, get_session_manager, get_task_executor, TaskState

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
    if not manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.post("/{session_id}/cancel", response_model=SessionCancelResponse)
async def cancel_session(session_id: str) -> SessionCancelResponse:
    """Request cancellation of the currently running task in a session."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

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
    Use the WebSocket endpoint to receive streaming updates.
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

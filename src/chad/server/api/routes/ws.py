"""WebSocket streaming endpoints."""

import asyncio
import base64
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from chad.server.services import get_session_manager, get_task_executor
from chad.server.services.pty_stream import get_pty_stream_service
from chad.server.services.event_mux import EventMultiplexer

router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for streaming updates."""

    def __init__(self):
        # Map session_id -> list of websockets
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        """Accept a new WebSocket connection for a session."""
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        """Remove a WebSocket connection."""
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def send_to_session(self, session_id: str, message: dict[str, Any]):
        """Send a message to all connections for a session."""
        if session_id not in self.active_connections:
            return
        dead_connections = []
        for websocket in self.active_connections[session_id]:
            try:
                await websocket.send_json(message)
            except Exception:
                dead_connections.append(websocket)
        # Clean up dead connections
        for websocket in dead_connections:
            self.disconnect(websocket, session_id)


# Global connection manager
manager = ConnectionManager()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    since_seq: int = 0,
    token: str | None = None,
):
    """WebSocket endpoint for streaming task updates.

    Bidirectional communication with PTY sessions.

    Query params:
    - since_seq: Resume from this sequence number (for reconnection)
    - token: Bearer token for authenticated connections

    Server -> Client message types:
    - terminal: Raw PTY output (base64 encoded)
    - event: Structured event
    - complete: Task/PTY exited
    - error: Error occurred
    - pong: Response to ping

    Client -> Server message types:
    - input: Send bytes to PTY (base64 encoded data field)
    - resize: Resize terminal (rows, cols fields)
    - cancel: Cancel/terminate PTY
    - ping: Heartbeat
    """
    # Check auth token if configured
    auth_token = getattr(websocket.app.state, "auth_token", None)
    if auth_token:
        from chad.server.auth import check_websocket_token
        if not check_websocket_token(websocket, auth_token):
            await websocket.close(code=4001, reason="Authentication required")
            return

    # Verify session exists
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)
    if session is None:
        await websocket.close(code=4004, reason=f"Session {session_id} not found")
        return

    await manager.connect(websocket, session_id)
    print(f"WebSocket client connected to session {session_id}")

    try:
        pty_service = get_pty_stream_service()
        executor = get_task_executor()

        async def stream_events():
            """Stream events to WebSocket using EventMultiplexer.

            Loops after task completion so follow-up tasks on the same
            session are streamed without requiring a WebSocket reconnect.
            """
            current_since_seq = since_seq

            while True:
                task = executor.get_latest_task_for_session(session_id)
                completed_task_id = task.id if task else None

                # Create multiplexer with task's EventLog
                event_log = task.event_log if task else None
                mux = EventMultiplexer(session_id, event_log)

                # Stream events for the current task
                async for event in mux.stream_with_since(
                    pty_service,
                    since_seq=current_since_seq,
                    include_terminal=True,
                    include_events=True,
                ):
                    message = {
                        "type": event.type,
                        "session_id": session_id,
                        "data": {**event.data, "seq": event.seq},
                    }
                    await manager.send_to_session(session_id, message)

                    if event.type in ("complete", "error"):
                        break

                # Task finished — wait for a new task to appear on this session
                # so follow-up messages stream correctly.
                while True:
                    await asyncio.sleep(0.3)
                    new_task = executor.get_latest_task_for_session(session_id)
                    if new_task and new_task.id != completed_task_id:
                        # New task started — stream from where we left off
                        current_since_seq = mux._seq
                        break

        # Start background task for streaming
        stream_task = asyncio.create_task(stream_events())

        try:
            # Handle incoming messages
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type")

                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "session_id": session_id})

                    elif msg_type == "input":
                        # Send input to PTY
                        pty_session = pty_service.get_session_by_session_id(session_id)
                        if pty_session and pty_session.active:
                            try:
                                input_data = base64.b64decode(msg.get("data", ""))
                                pty_service.send_input(pty_session.stream_id, input_data)
                            except Exception as e:
                                await websocket.send_json({
                                    "type": "error",
                                    "session_id": session_id,
                                    "data": {"error": f"Failed to send input: {e}"},
                                })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "session_id": session_id,
                                "data": {"error": "No active PTY session"},
                            })

                    elif msg_type == "resize":
                        # Resize PTY terminal
                        pty_session = pty_service.get_session_by_session_id(session_id)
                        if pty_session and pty_session.active:
                            rows = msg.get("rows", 24)
                            cols = msg.get("cols", 80)
                            pty_service.resize(pty_session.stream_id, rows, cols)
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "session_id": session_id,
                                "data": {"error": "No active PTY session"},
                            })

                    elif msg_type == "cancel":
                        cancelled_tasks = executor.cancel_tasks_for_session(session_id)
                        if cancelled_tasks > 0:
                            await websocket.send_json({
                                "type": "status",
                                "session_id": session_id,
                                "data": {"status": "Cancellation requested"},
                            })
                        else:
                            # Fallback: terminate any active PTY directly
                            pty_session = pty_service.get_session_by_session_id(session_id)
                            if pty_session:
                                pty_service.terminate(pty_session.stream_id)
                                await websocket.send_json({
                                    "type": "status",
                                    "session_id": session_id,
                                    "data": {"status": "PTY terminated"},
                                })

                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "data": {"error": "Invalid JSON"},
                    })

        finally:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)
        print(f"WebSocket client disconnected from session {session_id}")

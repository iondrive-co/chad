"""WebSocket streaming endpoints."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from chad.server.services import get_session_manager, get_task_executor

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
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for streaming task updates.

    Clients connect to receive real-time updates for a session's tasks.
    Message types:
    - stream: Raw AI output chunk
    - activity: Tool use or thinking activity
    - status: Status message
    - message_start: AI message started
    - message_complete: AI message completed
    - progress: Progress update
    - complete: Task completed
    - error: Error occurred
    """
    # Verify session exists
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)
    if session is None:
        await websocket.close(code=4004, reason=f"Session {session_id} not found")
        return

    await manager.connect(websocket, session_id)

    try:
        # Start a background task to poll for events
        executor = get_task_executor()

        async def poll_events():
            """Poll for task events and forward to WebSocket."""
            current_task_id = None

            while True:
                await asyncio.sleep(0.1)  # Poll every 100ms

                # Find active task for this session
                # Note: This is a simple approach; could track task_id explicitly
                for task_id, task in list(executor._tasks.items()):
                    if task.session_id == session_id:
                        current_task_id = task_id
                        break

                if current_task_id:
                    events = executor.get_events(current_task_id, timeout=0.01)
                    for event in events:
                        message = {
                            "type": event.type,
                            "session_id": session_id,
                            "data": event.data,
                        }
                        await manager.send_to_session(session_id, message)

        poll_task = asyncio.create_task(poll_events())

        try:
            # Handle incoming messages
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type")

                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "session_id": session_id})

                    elif msg_type == "cancel":
                        # Cancel the current task
                        for task_id, task in list(executor._tasks.items()):
                            if task.session_id == session_id:
                                executor.cancel_task(task_id)
                                await websocket.send_json({
                                    "type": "status",
                                    "session_id": session_id,
                                    "data": {"status": "Cancellation requested"},
                                })
                                break

                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "data": {"error": "Invalid JSON"},
                    })

        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)

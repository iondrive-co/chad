"""WebSocket streaming endpoints."""

import asyncio
import base64
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from chad.server.services import get_session_manager, get_task_executor
from chad.server.services.pty_stream import get_pty_stream_service

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

    Bidirectional communication with PTY sessions.

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
    # Verify session exists
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)
    if session is None:
        await websocket.close(code=4004, reason=f"Session {session_id} not found")
        return

    await manager.connect(websocket, session_id)

    try:
        pty_service = get_pty_stream_service()
        executor = get_task_executor()

        async def stream_pty_output():
            """Stream PTY output to WebSocket."""
            seq = 0
            while True:
                pty_session = pty_service.get_session_by_session_id(session_id)
                if pty_session:
                    try:
                        async for event in pty_service.subscribe(pty_session.stream_id):
                            seq += 1
                            if event.type == "output":
                                message = {
                                    "type": "terminal",
                                    "session_id": session_id,
                                    "data": {
                                        "data": event.data,
                                        "seq": seq,
                                        "has_ansi": event.has_ansi,
                                    },
                                }
                            elif event.type == "exit":
                                message = {
                                    "type": "complete",
                                    "session_id": session_id,
                                    "data": {
                                        "exit_code": event.exit_code,
                                        "seq": seq,
                                    },
                                }
                            elif event.type == "error":
                                message = {
                                    "type": "error",
                                    "session_id": session_id,
                                    "data": {
                                        "error": event.error,
                                        "seq": seq,
                                    },
                                }
                            else:
                                continue

                            await manager.send_to_session(session_id, message)

                            if event.type in ("exit", "error"):
                                return

                    except Exception:
                        pass

                # Also poll for task executor events (fallback for non-PTY tasks)
                for task_id, task in list(executor._tasks.items()):
                    if task.session_id == session_id:
                        events = executor.get_events(task_id, timeout=0.01)
                        for event in events:
                            seq += 1
                            message = {
                                "type": "event",
                                "session_id": session_id,
                                "data": {"seq": seq, "event_type": event.type, **event.data},
                            }
                            await manager.send_to_session(session_id, message)

                            if event.type in ("complete", "error"):
                                return

                await asyncio.sleep(0.1)

        # Start background task for streaming
        stream_task = asyncio.create_task(stream_pty_output())

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
                        # Cancel/terminate PTY
                        pty_session = pty_service.get_session_by_session_id(session_id)
                        if pty_session:
                            pty_service.terminate(pty_session.stream_id)
                            await websocket.send_json({
                                "type": "status",
                                "session_id": session_id,
                                "data": {"status": "PTY terminated"},
                            })
                        else:
                            # Try cancelling via task executor
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
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)

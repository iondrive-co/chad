"""WebSocket client for Chad server streaming."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

import httpx
import websockets
from websockets.sync.client import connect as sync_connect

from chad.ui.client.api_client import _auth_headers, _check_response


@dataclass
class StreamMessage:
    """A message from the WebSocket stream."""

    type: str  # stream, activity, status, message_start, message_complete, progress, complete, error
    session_id: str
    data: dict[str, Any]


def _ws_base_url(base_url: str) -> str:
    """Convert an http(s) base URL to ws(s)."""
    if base_url.startswith("http://"):
        return "ws://" + base_url[7:]
    if base_url.startswith("https://"):
        return "wss://" + base_url[8:]
    return base_url


def _http_base_url(ws_url: str) -> str:
    """Convert a ws(s) base URL back to http(s) for REST calls."""
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[5:]
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[6:]
    return ws_url


def _mint_ws_ticket(http_base_url: str, token: str, session_id: str) -> str:
    """Mint a fresh single-use WebSocket ticket.

    Tickets are single-use server-side, so every connect/reconnect must mint
    a new one — never cache or reuse a ticket.
    """
    resp = httpx.post(
        f"{http_base_url}/api/v1/ws-ticket/{session_id}",
        headers=_auth_headers(token),
        timeout=10.0,
    )
    _check_response(resp)
    return resp.json()["ticket"]


class WSClient:
    """Synchronous WebSocket client for task streaming."""

    def __init__(self, base_url: str = "ws://localhost:3184", token: str | None = None):
        """Initialize the WebSocket client.

        Args:
            base_url: Base WebSocket URL of the Chad server
            token: Bearer token for servers started with auth (chad --tunnel)
        """
        self.base_url = _ws_base_url(base_url).rstrip("/")
        self.token = token
        self._ws = None
        self._session_id = None

    def connect(self, session_id: str, since_seq: int = 0) -> None:
        """Connect to the WebSocket for a session.

        Mints a fresh single-use ticket on every call when a token is set.

        Args:
            session_id: The session ID to connect to
            since_seq: Resume streaming from this sequence number
        """
        url = f"{self.base_url}/api/v1/ws/{session_id}?since_seq={since_seq}"
        if self.token:
            ticket = _mint_ws_ticket(_http_base_url(self.base_url), self.token, session_id)
            url += f"&ticket={quote(ticket, safe='')}"
        self._ws = sync_connect(url)
        self._session_id = session_id

    def disconnect(self) -> None:
        """Disconnect from the WebSocket."""
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._session_id = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.disconnect()

    def send_ping(self) -> None:
        """Send a ping message."""
        if self._ws:
            self._ws.send(json.dumps({"type": "ping"}))

    def send_cancel(self) -> None:
        """Send a cancel request."""
        if self._ws:
            self._ws.send(json.dumps({"type": "cancel"}))

    def receive(self, timeout: float = 30.0) -> StreamMessage | None:
        """Receive a message from the WebSocket.

        Args:
            timeout: Timeout in seconds

        Returns:
            StreamMessage or None if timeout/disconnected
        """
        if not self._ws:
            return None

        try:
            # Set socket timeout
            self._ws.socket.settimeout(timeout)
            data = self._ws.recv()
            msg = json.loads(data)
            return StreamMessage(
                type=msg.get("type", "unknown"),
                session_id=msg.get("session_id", self._session_id),
                data=msg.get("data", {}),
            )
        except TimeoutError:
            return None
        except Exception:
            return None

    def iter_messages(
        self,
        timeout: float = 0.5,
        stop_on_complete: bool = True,
    ):
        """Iterate over messages from the WebSocket.

        Args:
            timeout: Timeout for each receive call
            stop_on_complete: Stop iteration when complete/error received

        Yields:
            StreamMessage objects
        """
        while True:
            msg = self.receive(timeout=timeout)
            if msg:
                yield msg
                if stop_on_complete and msg.type in ("complete", "error"):
                    break


class AsyncWSClient:
    """Async WebSocket client for task streaming."""

    def __init__(self, base_url: str = "ws://localhost:3184", token: str | None = None):
        """Initialize the WebSocket client.

        Args:
            base_url: Base WebSocket URL of the Chad server
            token: Bearer token for servers started with auth (chad --tunnel)
        """
        self.base_url = _ws_base_url(base_url).rstrip("/")
        self.token = token
        self._ws = None
        self._session_id = None

    async def connect(self, session_id: str, since_seq: int = 0) -> None:
        """Connect to the WebSocket for a session.

        Mints a fresh single-use ticket on every call when a token is set.
        """
        url = f"{self.base_url}/api/v1/ws/{session_id}?since_seq={since_seq}"
        if self.token:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_http_base_url(self.base_url)}/api/v1/ws-ticket/{session_id}",
                    headers=_auth_headers(self.token),
                )
            _check_response(resp)
            url += f"&ticket={quote(resp.json()['ticket'], safe='')}"
        self._ws = await websockets.connect(url)
        self._session_id = session_id

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            self._session_id = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    async def send_ping(self) -> None:
        """Send a ping message."""
        if self._ws:
            await self._ws.send(json.dumps({"type": "ping"}))

    async def send_cancel(self) -> None:
        """Send a cancel request."""
        if self._ws:
            await self._ws.send(json.dumps({"type": "cancel"}))

    async def receive(self, timeout: float = 30.0) -> StreamMessage | None:
        """Receive a message from the WebSocket."""
        if not self._ws:
            return None

        try:
            data = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            msg = json.loads(data)
            return StreamMessage(
                type=msg.get("type", "unknown"),
                session_id=msg.get("session_id", self._session_id),
                data=msg.get("data", {}),
            )
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    async def iter_messages(
        self,
        timeout: float = 0.5,
        stop_on_complete: bool = True,
    ):
        """Async iterate over messages from the WebSocket."""
        while True:
            msg = await self.receive(timeout=timeout)
            if msg:
                yield msg
                if stop_on_complete and msg.type in ("complete", "error"):
                    break


class StreamingTaskClient:
    """High-level client for running tasks with streaming output.

    Combines APIClient and WSClient for a unified task execution interface.
    """

    def __init__(
        self,
        api_client,
        on_stream: Callable[[str], None] | None = None,
        on_activity: Callable[[str, str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_message_start: Callable[[str], None] | None = None,
        on_message_complete: Callable[[str, str], None] | None = None,
        on_complete: Callable[[bool, str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        token: str | None = None,
    ):
        """Initialize the streaming task client.

        Args:
            api_client: APIClient instance for REST calls
            on_stream: Callback for stream chunks
            on_activity: Callback for activity updates (type, detail)
            on_status: Callback for status messages
            on_message_start: Callback for message start (speaker)
            on_message_complete: Callback for message complete (speaker, content)
            on_complete: Callback for task completion (success, message)
            on_error: Callback for errors
            token: Bearer token; defaults to the api_client's token
        """
        self.api_client = api_client
        self.token = token or getattr(api_client, "token", None)
        self.on_stream = on_stream
        self.on_activity = on_activity
        self.on_status = on_status
        self.on_message_start = on_message_start
        self.on_message_complete = on_message_complete
        self.on_complete = on_complete
        self.on_error = on_error

    def run_task(
        self,
        session_id: str,
        project_path: str,
        task_description: str,
        coding_agent: str,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
    ) -> bool:
        """Run a task with streaming output.

        Args:
            session_id: Session to run task in
            project_path: Project directory path
            task_description: Task description
            coding_agent: Account to use for coding
            coding_model: Optional model override
            coding_reasoning: Optional reasoning level

        Returns:
            True if task completed successfully
        """
        # Start the task via REST
        task_status = self.api_client.start_task(
            session_id=session_id,
            project_path=project_path,
            task_description=task_description,
            coding_agent=coding_agent,
            coding_model=coding_model,
            coding_reasoning=coding_reasoning,
        )

        # Connect to WebSocket for streaming
        base_url = self.api_client.base_url
        ws_client = WSClient(base_url, token=self.token)

        try:
            ws_client.connect(session_id)

            # Process messages
            for msg in ws_client.iter_messages(timeout=1.0, stop_on_complete=True):
                self._handle_message(msg)

            # Check final status
            final_status = self.api_client.get_task_status(
                session_id, task_status.task_id
            )
            return final_status.status == "completed"

        finally:
            ws_client.disconnect()

    def _handle_message(self, msg: StreamMessage) -> None:
        """Handle a WebSocket message."""
        data = msg.data

        if msg.type == "stream" and self.on_stream:
            self.on_stream(data.get("chunk", ""))

        elif msg.type == "activity" and self.on_activity:
            self.on_activity(
                data.get("activity_type", ""),
                data.get("detail", ""),
            )

        elif msg.type == "status" and self.on_status:
            self.on_status(data.get("status", ""))

        elif msg.type == "message_start" and self.on_message_start:
            self.on_message_start(data.get("speaker", ""))

        elif msg.type == "message_complete" and self.on_message_complete:
            self.on_message_complete(
                data.get("speaker", ""),
                data.get("content", ""),
            )

        elif msg.type == "complete" and self.on_complete:
            self.on_complete(
                data.get("success", False),
                data.get("message", ""),
            )

        elif msg.type == "error" and self.on_error:
            self.on_error(data.get("error", "Unknown error"))

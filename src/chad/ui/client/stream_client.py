"""Unified streaming client for SSE and WebSocket connections."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator

import httpx


@dataclass
class StreamEvent:
    """An event from the stream."""

    event_type: str  # terminal, event, ping, complete, error
    data: dict[str, Any]
    seq: int | None = None


class StreamClient:
    """Client for streaming API endpoints (SSE and input)."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize the stream client.

        Args:
            base_url: Base URL of the Chad server
        """
        self.base_url = base_url.rstrip("/")
        self._async_client: httpx.AsyncClient | None = None

    async def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create async client."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=None)
        return self._async_client

    async def close(self):
        """Close the client."""
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None

    def _url(self, path: str) -> str:
        """Build full URL for API path."""
        return f"{self.base_url}/api/v1{path}"

    async def stream_events(
        self,
        session_id: str,
        since_seq: int = 0,
        include_terminal: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Stream events from a session via SSE.

        Args:
            session_id: Session to stream from
            since_seq: Resume from this sequence number
            include_terminal: Include raw PTY output

        Yields:
            StreamEvent objects
        """
        client = await self._get_async_client()
        url = self._url(f"/sessions/{session_id}/stream")
        params = {
            "since_seq": since_seq,
            "include_terminal": str(include_terminal).lower(),
        }

        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()

            buffer = ""
            current_event = ""
            current_data = ""

            async for chunk in response.aiter_text():
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        current_data = line[5:].strip()
                    elif line.startswith("id:"):
                        # Event ID
                        pass
                    elif line == "" and current_event and current_data:
                        # End of event
                        try:
                            data = json.loads(current_data)
                            seq = data.get("seq")
                            yield StreamEvent(
                                event_type=current_event,
                                data=data,
                                seq=seq,
                            )
                        except json.JSONDecodeError:
                            pass

                        current_event = ""
                        current_data = ""

    async def send_input(self, session_id: str, data: bytes) -> bool:
        """Send input to the PTY session.

        Args:
            session_id: Session ID
            data: Raw bytes to send

        Returns:
            True if sent successfully
        """
        client = await self._get_async_client()
        url = self._url(f"/sessions/{session_id}/input")
        encoded = base64.b64encode(data).decode("ascii")

        try:
            response = await client.post(url, json={"data": encoded})
            response.raise_for_status()
            return True
        except Exception:
            return False

    async def resize_terminal(
        self,
        session_id: str,
        rows: int,
        cols: int,
    ) -> bool:
        """Resize the PTY terminal.

        Args:
            session_id: Session ID
            rows: Number of rows
            cols: Number of columns

        Returns:
            True if resized successfully
        """
        client = await self._get_async_client()
        url = self._url(f"/sessions/{session_id}/resize")

        try:
            response = await client.post(url, json={"rows": rows, "cols": cols})
            response.raise_for_status()
            return True
        except Exception:
            return False


class SyncStreamClient:
    """Synchronous wrapper around StreamClient for non-async code."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize the sync stream client.

        Args:
            base_url: Base URL of the Chad server
        """
        self.base_url = base_url.rstrip("/")
        self._sync_client = httpx.Client(timeout=None)

    def close(self):
        """Close the client."""
        self._sync_client.close()

    def _url(self, path: str) -> str:
        """Build full URL for API path."""
        return f"{self.base_url}/api/v1{path}"

    def stream_events(
        self,
        session_id: str,
        since_seq: int = 0,
        include_terminal: bool = True,
    ) -> Iterator[StreamEvent]:
        """Stream events from a session via SSE (blocking).

        Args:
            session_id: Session to stream from
            since_seq: Resume from this sequence number
            include_terminal: Include raw PTY output

        Yields:
            StreamEvent objects
        """
        url = self._url(f"/sessions/{session_id}/stream")
        params = {
            "since_seq": since_seq,
            "include_terminal": str(include_terminal).lower(),
        }

        with self._sync_client.stream("GET", url, params=params) as response:
            response.raise_for_status()

            buffer = ""
            current_event = ""
            current_data = ""

            for chunk in response.iter_text():
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        current_data = line[5:].strip()
                    elif line.startswith("id:"):
                        pass
                    elif line == "" and current_event and current_data:
                        try:
                            data = json.loads(current_data)
                            seq = data.get("seq")
                            yield StreamEvent(
                                event_type=current_event,
                                data=data,
                                seq=seq,
                            )
                        except json.JSONDecodeError:
                            pass

                        current_event = ""
                        current_data = ""

    def send_input(self, session_id: str, data: bytes) -> bool:
        """Send input to the PTY session (blocking).

        Args:
            session_id: Session ID
            data: Raw bytes to send

        Returns:
            True if sent successfully
        """
        url = self._url(f"/sessions/{session_id}/input")
        encoded = base64.b64encode(data).decode("ascii")

        try:
            response = self._sync_client.post(url, json={"data": encoded})
            response.raise_for_status()
            return True
        except Exception:
            return False

    def resize_terminal(
        self,
        session_id: str,
        rows: int,
        cols: int,
    ) -> bool:
        """Resize the PTY terminal (blocking).

        Args:
            session_id: Session ID
            rows: Number of rows
            cols: Number of columns

        Returns:
            True if resized successfully
        """
        url = self._url(f"/sessions/{session_id}/resize")

        try:
            response = self._sync_client.post(url, json={"rows": rows, "cols": cols})
            response.raise_for_status()
            return True
        except Exception:
            return False


def decode_terminal_data(data: str | bytes, *, is_text: bool = False) -> bytes:
    """Decode terminal output data.

    Args:
        data: Terminal payload from an event
        is_text: True when payload is already plain text (not base64)

    Returns:
        Terminal bytes suitable for writing to a TTY
    """
    if is_text:
        if isinstance(data, bytes):
            return data
        return str(data or "").encode("utf-8", errors="replace")

    if not data:
        return b""

    return base64.b64decode(data)

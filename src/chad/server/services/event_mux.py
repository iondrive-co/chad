"""Event multiplexer for unified streaming.

Combines PTY events and EventLog events into a single ordered stream,
eliminating the dual-path complexity in the SSE endpoint.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from chad.util.event_log import EventLog
    from chad.server.services.pty_stream import PTYStreamService


@dataclass
class MuxEvent:
    """A unified event from the multiplexer."""

    type: str  # "terminal", "event", "complete", "error", "ping"
    data: dict[str, Any]
    seq: int


class EventMultiplexer:
    """Unifies PTY events and EventLog events into a single ordered stream.

    This eliminates the dual streaming paths in the SSE endpoint by:
    1. Subscribing to PTY events as primary source
    2. Draining EventLog events after each PTY event
    3. Maintaining a single sequence counter for all events
    """

    def __init__(
        self,
        session_id: str,
        event_log: "EventLog | None" = None,
        ping_interval: float = 15.0,
    ):
        """Initialize the multiplexer.

        Args:
            session_id: The session to stream events for
            event_log: Optional EventLog for structured events
            ping_interval: Seconds between keepalive pings
        """
        self.session_id = session_id
        self.event_log = event_log
        self.ping_interval = ping_interval
        self._seq = 0
        self._event_log_seq = 0
        self._last_ping = datetime.now(timezone.utc)

    def _next_seq(self) -> int:
        """Get next sequence number."""
        self._seq += 1
        return self._seq

    def _sync_seq_with_log(self) -> int:
        """Align internal seq with the EventLog counter."""
        if not self.event_log:
            return self._seq

        latest = self.event_log.get_latest_seq()
        if latest > self._seq:
            self._seq = latest
        return self._seq

    def _drain_event_log(self, skip_terminal: bool = True) -> list[MuxEvent]:
        """Get all new EventLog events since last check.

        Args:
            skip_terminal: If True, skip terminal_output events (already in PTY stream)

        Returns:
            List of MuxEvent objects for new events
        """
        if not self.event_log:
            return []

        events = []
        new_log_events = self.event_log.get_events(since_seq=self._event_log_seq)

        for log_event in new_log_events:
            log_seq = log_event.get("seq", 0)
            self._event_log_seq = max(self._event_log_seq, log_seq)

            if log_event.get("type") == "terminal_output":
                if skip_terminal:
                    # Still keep log sequence in sync to avoid reprocessing
                    self._sync_seq_with_log()
                    continue

                # Use log sequence for terminal events to keep SSE ids aligned
                self._seq = max(self._seq, log_seq)
                events.append(
                    MuxEvent(
                        type="terminal",
                        data={
                            "data": log_event.get("data", ""),
                            "text": True,  # Indicates plain text, not base64
                            "ts": log_event.get("ts"),
                        },
                        seq=log_seq or self._next_seq(),
                    )
                )
                continue

            self._seq = max(self._seq, log_seq)
            events.append(
                MuxEvent(
                    type="event",
                    data=log_event,
                    seq=log_seq or self._next_seq(),
                )
            )

        return events

    def _should_ping(self) -> bool:
        """Check if a ping should be sent."""
        now = datetime.now(timezone.utc)
        if (now - self._last_ping).total_seconds() >= self.ping_interval:
            self._last_ping = now
            return True
        return False

    def _create_ping(self) -> MuxEvent:
        """Create a ping event."""
        return MuxEvent(
            type="ping",
            data={"ts": datetime.now(timezone.utc).isoformat()},
            seq=self._next_seq(),
        )

    async def stream_events(
        self,
        pty_service: "PTYStreamService",
        include_terminal: bool = True,
        include_events: bool = True,
    ) -> AsyncIterator[MuxEvent]:
        """Stream events from PTY and EventLog in unified order.

        Args:
            pty_service: PTY streaming service
            include_terminal: Include raw PTY output events
            include_events: Include structured EventLog events

        Yields:
            MuxEvent objects in sequence order
        """
        pty_session = None

        # If terminal output is requested, keep polling for the PTY session while
        # still streaming EventLog and ping events. This avoids the previous
        # race where we fell back permanently after a fixed wait.
        if include_terminal:
            while True:
                pty_session = pty_service.get_session_by_session_id(self.session_id)
                if pty_session:
                    break

                # Stream any available EventLog events while waiting
                if include_events or include_terminal:
                    events = self._drain_event_log(skip_terminal=not include_terminal)
                    for event in events:
                        yield event
                        if event.data.get("type") in ("session_ended",):
                            yield MuxEvent(
                                type="complete",
                                data={"exit_code": None},
                                seq=self._next_seq(),
                            )
                            return

                if self._should_ping():
                    yield self._create_ping()

                await asyncio.sleep(0.1)

        if pty_session and include_terminal:
            # Primary path: Stream PTY events with interspersed EventLog events
            # Use async iteration with ping support to keep SSE connection alive
            try:
                subscriber = pty_service.subscribe(pty_session.stream_id)
                pty_iter = subscriber.__aiter__()

                while True:
                    # Check if we should send a ping (keepalive for long waits)
                    if self._should_ping():
                        yield self._create_ping()

                    try:
                        # Get next PTY event - the subscribe generator already has
                        # internal polling with short sleeps, so we don't need wait_for.
                        # Using wait_for here would cancel the generator on timeout,
                        # which closes it and causes StopAsyncIteration on next call.
                        pty_event = await pty_iter.__anext__()
                    except StopAsyncIteration:
                        # PTY stream ended - but the task may have continuation phases
                        # that run in sequence. Wait for session_ended event before
                        # emitting complete to avoid premature verification.
                        self._sync_seq_with_log()
                        exit_code = pty_session.exit_code if pty_session else None

                        if include_events and self.event_log:
                            for event in self._drain_event_log(skip_terminal=True):
                                yield event
                                if event.data.get("type") == "session_ended":
                                    yield MuxEvent(
                                        type="complete",
                                        data={"exit_code": exit_code},
                                        seq=self._next_seq(),
                                    )
                                    return

                            # No session_ended yet - fall through to EventLog polling
                            # to wait for continuation phases to complete
                            break

                        # No event_log to poll - emit complete immediately
                        yield MuxEvent(
                            type="complete",
                            data={"exit_code": exit_code},
                            seq=self._next_seq(),
                        )
                        return

                    if pty_event.type == "output":
                        # Align seq with EventLog which has already logged the chunk
                        if self.event_log:
                            self._sync_seq_with_log()

                        # Yield the terminal output
                        yield MuxEvent(
                            type="terminal",
                            data={
                                "data": pty_event.data,
                                "has_ansi": pty_event.has_ansi,
                            },
                            seq=self._seq or self._next_seq(),
                        )

                        # Drain any pending EventLog events (skip terminal_output)
                        if include_events:
                            for event in self._drain_event_log(skip_terminal=True):
                                yield event

                    elif pty_event.type == "exit":
                        self._sync_seq_with_log()
                        # Drain remaining EventLog events - wait for session_ended
                        # to handle continuation phases properly
                        if include_events and self.event_log:
                            for event in self._drain_event_log(skip_terminal=True):
                                yield event
                                if event.data.get("type") == "session_ended":
                                    yield MuxEvent(
                                        type="complete",
                                        data={"exit_code": pty_event.exit_code},
                                        seq=self._next_seq(),
                                    )
                                    return

                            # No session_ended yet - fall through to EventLog polling
                            # to wait for continuation phases to complete
                            break

                        # No event_log to poll - emit complete immediately
                        yield MuxEvent(
                            type="complete",
                            data={"exit_code": pty_event.exit_code},
                            seq=self._next_seq(),
                        )
                        return

                    elif pty_event.type == "error":
                        self._sync_seq_with_log()
                        yield MuxEvent(
                            type="error",
                            data={"error": pty_event.error},
                            seq=self._next_seq(),
                        )
                        return

            except Exception as e:
                yield MuxEvent(
                    type="error",
                    data={"error": str(e)},
                    seq=self._next_seq(),
                )
                return

            # PTY ended but no session_ended event yet - continue polling EventLog
            # This handles continuation phases where multiple PTY sessions run sequentially
            exit_code = pty_session.exit_code if pty_session else None
            old_stream_id = pty_session.stream_id if pty_session else None

            # Safety check: only poll if we have an event_log to poll
            if not self.event_log:
                yield MuxEvent(
                    type="complete",
                    data={"exit_code": exit_code},
                    seq=self._next_seq(),
                )
                return

            # Safety timeout: stop polling after 45 minutes to prevent infinite hangs
            poll_deadline = datetime.now(timezone.utc).timestamp() + 45 * 60

            while True:
                if datetime.now(timezone.utc).timestamp() > poll_deadline:
                    yield MuxEvent(
                        type="complete",
                        data={"exit_code": exit_code, "timeout": True},
                        seq=self._next_seq(),
                    )
                    return

                # Check if a new PTY session has started (continuation phase)
                new_pty_session = pty_service.get_session_by_session_id(self.session_id)
                if new_pty_session and new_pty_session.stream_id != old_stream_id:
                    # New PTY session started - switch to streaming from it
                    pty_session = new_pty_session
                    old_stream_id = pty_session.stream_id
                    try:
                        subscriber = pty_service.subscribe(pty_session.stream_id)
                        pty_iter = subscriber.__aiter__()

                        while True:
                            if self._should_ping():
                                yield self._create_ping()

                            try:
                                pty_event = await pty_iter.__anext__()
                            except StopAsyncIteration:
                                # This PTY ended - check for session_ended or more continuation
                                self._sync_seq_with_log()
                                exit_code = pty_session.exit_code if pty_session else None

                                if include_events and self.event_log:
                                    for event in self._drain_event_log(skip_terminal=True):
                                        yield event
                                        if event.data.get("type") == "session_ended":
                                            yield MuxEvent(
                                                type="complete",
                                                data={"exit_code": exit_code},
                                                seq=self._next_seq(),
                                            )
                                            return
                                # Break inner loop to check for another continuation
                                break

                            if pty_event.type == "output":
                                if self.event_log:
                                    self._sync_seq_with_log()
                                yield MuxEvent(
                                    type="terminal",
                                    data={
                                        "data": pty_event.data,
                                        "has_ansi": pty_event.has_ansi,
                                    },
                                    seq=self._seq or self._next_seq(),
                                )
                                if include_events:
                                    for event in self._drain_event_log(skip_terminal=True):
                                        yield event
                            elif pty_event.type == "exit":
                                self._sync_seq_with_log()
                                exit_code = pty_event.exit_code
                                if include_events and self.event_log:
                                    for event in self._drain_event_log(skip_terminal=True):
                                        yield event
                                        if event.data.get("type") == "session_ended":
                                            yield MuxEvent(
                                                type="complete",
                                                data={"exit_code": exit_code},
                                                seq=self._next_seq(),
                                            )
                                            return
                                # Break inner loop to check for another continuation
                                break
                            elif pty_event.type == "error":
                                self._sync_seq_with_log()
                                yield MuxEvent(
                                    type="error",
                                    data={"error": pty_event.error},
                                    seq=self._next_seq(),
                                )
                                return

                    except Exception as e:
                        yield MuxEvent(
                            type="error",
                            data={"error": str(e)},
                            seq=self._next_seq(),
                        )
                        return

                if include_events:
                    events = self._drain_event_log(skip_terminal=True)
                    for event in events:
                        yield event
                        if event.data.get("type") == "session_ended":
                            yield MuxEvent(
                                type="complete",
                                data={"exit_code": exit_code},
                                seq=self._next_seq(),
                            )
                            return

                if self._should_ping():
                    yield self._create_ping()

                await asyncio.sleep(0.1)

        else:
            # Fallback path: Poll EventLog only (no active PTY)
            poll_deadline = datetime.now(timezone.utc).timestamp() + 45 * 60

            while True:
                if datetime.now(timezone.utc).timestamp() > poll_deadline:
                    yield MuxEvent(
                        type="complete",
                        data={"exit_code": None, "timeout": True},
                        seq=self._next_seq(),
                    )
                    return
                if include_events or include_terminal:
                    events = self._drain_event_log(skip_terminal=not include_terminal)
                    for event in events:
                        yield event
                        # Check for completion events
                        if event.data.get("type") == "session_ended":
                            yield MuxEvent(
                                type="complete",
                                data={"exit_code": None},
                                seq=self._next_seq(),
                            )
                            return

                # Send ping if needed
                if self._should_ping():
                    yield self._create_ping()

                await asyncio.sleep(0.1)

    async def stream_with_since(
        self,
        pty_service: "PTYStreamService",
        since_seq: int = 0,
        include_terminal: bool = True,
        include_events: bool = True,
    ) -> AsyncIterator[MuxEvent]:
        """Stream events, optionally resuming from a sequence number.

        This first catches up on any missed EventLog events, then
        streams live events.

        Args:
            pty_service: PTY streaming service
            since_seq: Only return events after this sequence
            include_terminal: Include raw PTY output events
            include_events: Include structured EventLog events

        Yields:
            MuxEvent objects after since_seq
        """
        # Catch up on missed EventLog events (structured + terminal when requested)
        if self.event_log and (include_events or include_terminal):
            catchup_events = self.event_log.get_events(since_seq=since_seq)
            for log_event in catchup_events:
                log_seq = log_event.get("seq", 0)
                if log_seq <= since_seq:
                    continue

                self._event_log_seq = max(self._event_log_seq, log_seq)
                self._seq = max(self._seq, log_seq)

                if log_event.get("type") == "terminal_output":
                    if include_terminal:
                        # EventLog terminal_output is plain text (not base64)
                        yield MuxEvent(
                            type="terminal",
                            data={
                                "data": log_event.get("data", ""),
                                "text": True,  # Indicates plain text, not base64
                                "ts": log_event.get("ts"),
                            },
                            seq=log_seq,
                        )
                    continue

                if include_events:
                    yield MuxEvent(
                        type="event",
                        data=log_event,
                        seq=log_seq,
                    )
                    # If session already ended during catchup, emit complete and stop
                    if log_event.get("type") == "session_ended":
                        yield MuxEvent(
                            type="complete",
                            data={"exit_code": None},
                            seq=self._next_seq(),
                        )
                        return

        # Stream live events
        async for event in self.stream_events(
            pty_service,
            include_terminal=include_terminal,
            include_events=include_events,
        ):
            yield event


def format_sse_event(event: MuxEvent) -> str:
    """Format a MuxEvent as an SSE event string.

    Args:
        event: The event to format

    Returns:
        SSE-formatted string ready to yield
    """
    import json

    data = {**event.data, "seq": event.seq}
    return f"event: {event.type}\ndata: {json.dumps(data)}\nid: {event.seq}\n\n"

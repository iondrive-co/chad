"""Convert event log messages to provider-specific formats for handoff.

This module extracts conversation history from the event log and formats it
appropriately for different AI providers, preserving reasoning/thinking
content and tool call context where supported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .event_log import EventLog


@dataclass
class ConversationTurn:
    """A single turn in a conversation (user or assistant message)."""

    role: Literal["user", "assistant"]
    blocks: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str | None = None

    @classmethod
    def from_user_message(cls, content: str, timestamp: str | None = None) -> "ConversationTurn":
        """Create a user turn from a simple text message."""
        return cls(
            role="user",
            blocks=[{"kind": "text", "content": content}],
            timestamp=timestamp,
        )

    @classmethod
    def from_assistant_blocks(
        cls, blocks: list[dict[str, Any]], timestamp: str | None = None
    ) -> "ConversationTurn":
        """Create an assistant turn from message blocks."""
        return cls(role="assistant", blocks=blocks, timestamp=timestamp)


def extract_conversation_from_events(
    event_log: EventLog,
    since_seq: int = 0,
    max_turns: int | None = None,
) -> list[ConversationTurn]:
    """Extract conversation turns from event log.

    Scans for user_message and assistant_message events and converts
    them into ConversationTurn objects that preserve the full message
    structure including thinking blocks and tool calls.

    Args:
        event_log: The EventLog instance to scan
        since_seq: Only consider events after this sequence number
        max_turns: Maximum number of turns to return (None = all)

    Returns:
        List of ConversationTurn objects in chronological order
    """
    events = event_log.get_events(
        since_seq=since_seq,
        event_types=["user_message", "assistant_message"],
    )

    turns: list[ConversationTurn] = []

    for event in events:
        event_type = event.get("type")
        timestamp = event.get("ts")

        if event_type == "user_message":
            content = event.get("content", "")
            if content:
                turns.append(ConversationTurn.from_user_message(content, timestamp))

        elif event_type == "assistant_message":
            blocks = event.get("blocks", [])
            if blocks:
                turns.append(ConversationTurn.from_assistant_blocks(blocks, timestamp))

    if max_turns is not None and len(turns) > max_turns:
        turns = turns[-max_turns:]

    return turns


def format_for_provider(
    turns: list[ConversationTurn],
    provider_type: str,
    new_message: str | None = None,
) -> str:
    """Format conversation turns for a specific provider.

    Different providers handle conversation context differently:
    - Claude (anthropic): Omit thinking blocks (it will regenerate), show tool calls compactly
    - Codex (openai): Include reasoning as [Reasoning] blocks, compact tool calls
    - Generic (gemini, qwen, mistral): Use XML-tagged format for structure

    Args:
        turns: List of conversation turns to format
        provider_type: The target provider type (anthropic, openai, gemini, qwen, mistral)
        new_message: Optional new message to append after conversation

    Returns:
        Formatted conversation string
    """
    if provider_type == "anthropic":
        return _format_for_claude(turns, new_message)
    elif provider_type == "openai":
        return _format_for_codex(turns, new_message)
    else:
        return _format_for_generic(turns, new_message)


def _format_for_claude(turns: list[ConversationTurn], new_message: str | None) -> str:
    """Format conversation for Claude (omits thinking blocks).

    Claude regenerates its own reasoning, so we don't need to include
    previous thinking blocks. We show tool calls compactly.
    """
    lines: list[str] = []

    for turn in turns:
        if turn.role == "user":
            text = _extract_text_content(turn.blocks)
            if text:
                lines.append(f"[User]: {text}")
                lines.append("")

        elif turn.role == "assistant":
            # Skip thinking blocks for Claude
            assistant_parts: list[str] = []

            for block in turn.blocks:
                kind = block.get("kind", "")

                if kind == "text":
                    content = block.get("content", "")
                    if content:
                        assistant_parts.append(content)

                elif kind == "tool_call":
                    tool = block.get("tool", "unknown")
                    args = block.get("args", {})
                    tool_summary = _format_tool_call_compact(tool, args)
                    if tool_summary:
                        assistant_parts.append(f"[Tool: {tool}] {tool_summary}")

                elif kind == "tool_result":
                    content = block.get("content", "")
                    if content:
                        # Truncate long results
                        truncated = content[:500] + "..." if len(content) > 500 else content
                        assistant_parts.append(f"[Result]: {truncated}")

            if assistant_parts:
                lines.append("[Assistant]:")
                lines.extend(assistant_parts)
                lines.append("")

    result = "\n".join(lines).strip()

    if new_message:
        result += f"\n\n[User]: {new_message}"

    return result


def _format_for_codex(turns: list[ConversationTurn], new_message: str | None) -> str:
    """Format conversation for Codex (includes reasoning blocks).

    Codex supports extended thinking, so we include reasoning/thinking
    blocks with a [Reasoning] prefix.
    """
    lines: list[str] = []

    for turn in turns:
        if turn.role == "user":
            text = _extract_text_content(turn.blocks)
            if text:
                lines.append(f"[User]: {text}")
                lines.append("")

        elif turn.role == "assistant":
            assistant_parts: list[str] = []

            for block in turn.blocks:
                kind = block.get("kind", "")

                if kind == "thinking":
                    content = block.get("content", "")
                    if content:
                        # Truncate very long thinking
                        truncated = content[:1000] + "..." if len(content) > 1000 else content
                        assistant_parts.append(f"[Reasoning]: {truncated}")

                elif kind == "text":
                    content = block.get("content", "")
                    if content:
                        assistant_parts.append(content)

                elif kind == "tool_call":
                    tool = block.get("tool", "unknown")
                    args = block.get("args", {})
                    tool_summary = _format_tool_call_compact(tool, args)
                    if tool_summary:
                        assistant_parts.append(f"[Tool: {tool}] {tool_summary}")

                elif kind == "tool_result":
                    content = block.get("content", "")
                    if content:
                        truncated = content[:500] + "..." if len(content) > 500 else content
                        assistant_parts.append(f"[Result]: {truncated}")

            if assistant_parts:
                lines.append("[Assistant]:")
                lines.extend(assistant_parts)
                lines.append("")

    result = "\n".join(lines).strip()

    if new_message:
        result += f"\n\n[User]: {new_message}"

    return result


def _format_for_generic(turns: list[ConversationTurn], new_message: str | None) -> str:
    """Format conversation for generic providers (XML-tagged format).

    Uses XML-like tags for structure that most providers can understand.
    """
    lines: list[str] = []

    for turn in turns:
        if turn.role == "user":
            text = _extract_text_content(turn.blocks)
            if text:
                lines.append(f'<turn role="user">{text}</turn>')
                lines.append("")

        elif turn.role == "assistant":
            lines.append('<turn role="assistant">')

            for block in turn.blocks:
                kind = block.get("kind", "")

                if kind == "thinking":
                    content = block.get("content", "")
                    if content:
                        truncated = content[:1000] + "..." if len(content) > 1000 else content
                        lines.append(f"<thinking>{truncated}</thinking>")

                elif kind == "text":
                    content = block.get("content", "")
                    if content:
                        lines.append(f"<response>{content}</response>")

                elif kind == "tool_call":
                    tool = block.get("tool", "unknown")
                    args = block.get("args", {})
                    tool_summary = _format_tool_call_compact(tool, args)
                    if tool_summary:
                        lines.append(f'<tool name="{tool}">{tool_summary}</tool>')

                elif kind == "tool_result":
                    content = block.get("content", "")
                    if content:
                        truncated = content[:500] + "..." if len(content) > 500 else content
                        lines.append(f"<result>{truncated}</result>")

            lines.append("</turn>")
            lines.append("")

    result = "\n".join(lines).strip()

    if new_message:
        result += f'\n\n<turn role="user">{new_message}</turn>'

    return result


def _extract_text_content(blocks: list[dict[str, Any]]) -> str:
    """Extract plain text content from message blocks."""
    texts = []
    for block in blocks:
        if block.get("kind") == "text":
            content = block.get("content", "")
            if content:
                texts.append(content)
    return "\n".join(texts)


def _format_tool_call_compact(tool: str, args: dict[str, Any]) -> str:
    """Format a tool call compactly for display.

    Args:
        tool: Tool name
        args: Tool arguments dict

    Returns:
        Compact string representation of the tool call
    """
    if tool == "Read":
        return args.get("file_path", "")
    elif tool == "Write":
        return args.get("file_path", "")
    elif tool == "Edit":
        return args.get("file_path", "")
    elif tool == "Bash":
        cmd = args.get("command", "")
        return cmd[:80] + "..." if len(cmd) > 80 else cmd
    elif tool == "Glob":
        return args.get("pattern", "")
    elif tool == "Grep":
        return args.get("pattern", "")
    elif tool == "Task":
        return args.get("description", "")
    elif tool == "WebSearch":
        return args.get("query", "")
    elif tool == "WebFetch":
        return args.get("url", "")
    else:
        # Generic: just show first string arg
        for v in args.values():
            if isinstance(v, str) and v:
                return v[:50] + "..." if len(v) > 50 else v
        return ""

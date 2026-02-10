"""Inter-provider handoff utilities for preserving context across provider switches.

This module provides functions to:
- Extract progress information from the event log
- Build markdown summaries for handoff context
- Log checkpoint events for resumption
- Build resume prompts from event log state
- Detect credit/quota exhaustion for automatic provider switching
"""

from __future__ import annotations

import re

from .event_log import ContextCondensedEvent, EventLog
from .message_converter import extract_conversation_from_events, format_for_provider


# Patterns indicating credit/quota exhaustion across different providers.
# These patterns are intentionally specific to avoid false matches on normal errors.
# Each pattern should match actual API error messages, not general text.
QUOTA_EXHAUSTION_PATTERNS = [
    # OpenAI/Codex specific error codes and messages
    r"\binsufficientquota\b",
    r"\binsufficient_quota\b",
    r"\brate_limit_exceeded\b",
    r"\bratelimitexceeded\b",
    r"\bbilling_hard_limit_reached\b",
    r"you\s+exceeded\s+your\s+current\s+quota",
    r"you\s+have\s+exceeded\s+your\s+(rate|usage)\s+limit",

    # Anthropic/Claude specific
    r"\bcredit_balance\b.*\binsufficient\b",
    r"api\s+is\s+overloaded",
    r"rate\s+limit\s+exceeded",  # With spaces, more specific than rate_limit_exceeded

    # Gemini specific (case-sensitive match for this one)
    r"\bRESOURCE_EXHAUSTED\b",
    r"quota\s+exceeded\s+for\s+(project|quota)",

    # Generic API error patterns (these are common across providers)
    r"\bquota\s+exceeded\b",
    r"\bquota\s+has\s+been\s+exceeded\b",
    r"\binsufficient\s+credits?\b",
    r"\binsufficient\s+quota\b",
    r"\binsufficient\s+funds\b",
    r"\bout\s+of\s+credits?\b",
    r"\bcredits?\s+exhausted\b",
    r"\busage\s+limit\s+exceeded\b",
    r"\bbilling\s+limit\s+exceeded\b",
    r"\bbilling\s+limit\s+reached\b",
    r"\bpayment\s+required\b",
    r"\baccount\s+(has\s+been\s+)?(suspended|disabled)\b",
    r"\btoo\s+many\s+requests\b",
    r"\bresource\s+exhausted\b",
    r"429\s+too\s+many\s+requests",
    r"error\s+429\b",
]

# Compiled regex for efficiency
_QUOTA_PATTERN = re.compile(
    "|".join(f"({p})" for p in QUOTA_EXHAUSTION_PATTERNS),
    re.IGNORECASE,
)


def is_quota_exhaustion_error(error_message: str) -> bool:
    """Check if an error message indicates quota/credit exhaustion.

    This function checks error messages from AI providers to determine
    if the error is due to quota exhaustion, rate limiting, or billing
    issues that would warrant automatic switching to another provider.

    Args:
        error_message: The error message text to analyze

    Returns:
        True if the error appears to be a quota/credit exhaustion issue
    """
    if not error_message:
        return False

    return bool(_QUOTA_PATTERN.search(error_message))


def get_quota_error_reason(error_message: str) -> str | None:
    """Extract the specific quota error type from an error message.

    Args:
        error_message: The error message text to analyze

    Returns:
        A brief description of the quota error type, or None if not a quota error
    """
    if not error_message:
        return None

    error_lower = error_message.lower()

    if "rate limit" in error_lower or "too many requests" in error_lower:
        return "rate_limit"
    elif "insufficient" in error_lower and ("credit" in error_lower or "quota" in error_lower):
        return "insufficient_credits"
    elif "quota" in error_lower and "exceeded" in error_lower:
        return "quota_exceeded"
    elif "billing" in error_lower:
        return "billing_issue"
    elif "resource exhausted" in error_lower:
        return "resource_exhausted"
    elif "payment required" in error_lower:
        return "payment_required"
    elif "suspended" in error_lower or "disabled" in error_lower:
        return "account_suspended"

    # Check generic pattern match
    if _QUOTA_PATTERN.search(error_message):
        return "quota_issue"

    return None


def extract_progress_from_events(event_log: EventLog, since_seq: int = 0) -> dict:
    """Parse event log to extract structured progress information.

    Scans tool_call_started events to identify:
    - Files that were changed (edit operations)
    - Files that were created (write operations)
    - Key commands run (pytest, npm, make, etc.)

    Args:
        event_log: The EventLog instance to scan
        since_seq: Only consider events after this sequence number

    Returns:
        Dictionary with keys:
        - files_changed: List of modified file paths
        - files_created: List of created file paths
        - key_commands: List of significant commands (last 10)
    """
    events = event_log.get_events(since_seq)
    files_changed: set[str] = set()
    files_created: set[str] = set()
    key_commands: list[str] = []

    for event in events:
        if event.get("type") == "tool_call_started":
            tool = event.get("tool", "")
            path = event.get("path")
            command = event.get("command")

            if tool == "write" and path:
                files_created.add(path)
            elif tool == "edit" and path:
                files_changed.add(path)
            elif tool == "bash" and command:
                # Extract meaningful commands (tests, builds, etc.)
                cmd_lower = command.lower()
                keywords = ["pytest", "npm", "make", "cargo", "go ", "yarn", "pnpm", "gradle", "mvn"]
                if any(kw in cmd_lower for kw in keywords):
                    key_commands.append(command[:100])

    return {
        "files_changed": sorted(files_changed),
        "files_created": sorted(files_created),
        "key_commands": key_commands[-10:],  # Keep last 10
    }


def build_handoff_summary(
    original_task: str,
    event_log: EventLog,
    target_provider: str = "generic",
    since_seq: int = 0,
    remaining_work: str = "",
) -> str:
    """Build a markdown summary for handoff context.

    Creates a structured summary that includes:
    - The original task description
    - Full conversation history (formatted for target provider)
    - Files created and modified
    - Key commands that were run
    - Any remaining work to be done

    Args:
        original_task: The original task description
        event_log: The EventLog instance to extract progress from
        target_provider: The provider type to format conversation for
            (anthropic, openai, gemini, qwen, mistral)
        since_seq: Only consider events after this sequence number
        remaining_work: Optional description of work still to be done

    Returns:
        Markdown-formatted summary string
    """
    progress = extract_progress_from_events(event_log, since_seq)

    parts = ["<previous_session>"]
    parts.append(f"## Original Task\n{original_task}\n")

    # Extract and format conversation history
    turns = extract_conversation_from_events(event_log, since_seq)
    if turns:
        conversation_text = format_for_provider(turns, target_provider)
        if conversation_text:
            parts.append("## Conversation History")
            parts.append(conversation_text)
            parts.append("")

    if progress["files_changed"] or progress["files_created"]:
        parts.append("## Files Modified")
        for f in progress["files_created"]:
            parts.append(f"- Created: `{f}`")
        for f in progress["files_changed"]:
            parts.append(f"- Modified: `{f}`")
        parts.append("")

    if progress["key_commands"]:
        parts.append("## Commands Run")
        for cmd in progress["key_commands"]:
            parts.append(f"- `{cmd}`")
        parts.append("")

    if remaining_work:
        parts.append(f"## Remaining Work\n{remaining_work}\n")

    parts.append("</previous_session>")

    return "\n".join(parts)


def log_handoff_checkpoint(
    event_log: EventLog,
    original_task: str,
    provider_session_id: str | None = None,
    remaining_work: str = "",
    target_provider: str = "generic",
) -> int:
    """Log a ContextCondensedEvent with handoff data.

    Creates a checkpoint in the event log that can be used to
    resume the session on a different provider. The checkpoint
    includes structured progress data and a markdown summary.

    Args:
        event_log: The EventLog instance to log to
        original_task: The original task description
        provider_session_id: Native session ID from the old provider
            (thread_id for Codex, session_id for Gemini/Qwen)
        remaining_work: Optional description of work still to do
        target_provider: The provider type to format conversation for

    Returns:
        The sequence number of the logged event
    """
    progress = extract_progress_from_events(event_log)
    summary = build_handoff_summary(
        original_task, event_log, target_provider=target_provider, remaining_work=remaining_work
    )

    event = ContextCondensedEvent(
        replaces_seq_range=(0, event_log.get_latest_seq()),
        summary_text=summary,
        policy="provider_handoff",
        original_task=original_task,
        files_changed=progress["files_changed"],
        files_created=progress["files_created"],
        key_commands=progress["key_commands"],
        remaining_work=remaining_work,
        provider_session_id=provider_session_id,
    )
    event_log.log(event)
    return event.seq


def build_resume_prompt(
    event_log: EventLog,
    new_message: str | None = None,
    target_provider: str = "generic",
) -> str:
    """Build a prompt for resuming a session from the event log.

    Looks for the latest ContextCondensedEvent with policy="provider_handoff"
    and uses its summary. If none exists, builds a fresh summary from
    the event log formatted for the target provider.

    Args:
        event_log: The EventLog instance to read from
        new_message: Optional new instructions to append
        target_provider: The provider type to format conversation for

    Returns:
        A prompt string containing context and optionally new instructions
    """
    # Always build fresh summary for the target provider to ensure proper formatting
    # Find original task from session_started
    started = event_log.get_events(event_types=["session_started"])
    task = ""
    if started:
        task = started[0].get("task_description", "")
    if not task:
        task = "Continue previous work"

    context = build_handoff_summary(task, event_log, target_provider=target_provider)

    if new_message:
        return f"{context}\n\nContinue with: {new_message}"
    return context


def get_last_checkpoint_provider_session_id(event_log: EventLog) -> str | None:
    """Get the provider session ID from the last handoff checkpoint.

    This can be used to attempt native resume on providers that support it
    (Codex thread_id, Gemini/Qwen session_id).

    Args:
        event_log: The EventLog instance to read from

    Returns:
        The provider session ID if found, None otherwise
    """
    events = event_log.get_events(event_types=["context_condensed"])

    for event in reversed(events):
        if event.get("policy") == "provider_handoff":
            return event.get("provider_session_id")

    return None

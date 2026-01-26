"""Configurable prompts for Chad's coding and verification agents.

Edit these prompts to customize agent behavior.
"""

from dataclasses import dataclass
from pathlib import Path


# =============================================================================
# CODING AGENT SYSTEM PROMPT
# =============================================================================
# The coding agent receives this prompt with:
# - {project_docs} replaced with references to on-disk docs (AGENTS.md, ARCHITECTURE.md, etc.)
# - {verification_instructions} replaced with project-specific verification commands
# - {task} replaced with the user's task description

CODING_AGENT_PROMPT = """\
{project_docs}

{verification_instructions}

You need to complete the following task:
---
# Task

{task}
---
Use the following sequence to complete the task:
1. Explore the code to understand the task.
2. Once you understand what needs to be done, you MUST output a progress update so the user can see what you found:
```json
{{"type": "progress", "summary": "Adding retry logic to handle API rate limits", "location": "src/api/client.py:45 - request() method"}}
```
3. Write test(s) that should fail until the fix/feature is implemented.
4. Make the changes, adjusting tests as needed. If no changes are required, skip to step 7.
5. Run verification commands (lint and tests) as described above.
6. Fix ALL failures and retest if required.
7. End your response with a JSON summary block like this:
```json
{{
  "change_summary": "One sentence describing what was changed",
  "hypothesis": "Brief root cause explanation (include if investigating a bug)"
}}
```
Only include the hypothesis field when investigating a bug.
"""


# =============================================================================
# VERIFICATION AGENT PROMPTS (Two-Phase)
# =============================================================================
# The verification agent reviews the coding agent's work in two phases:
# 1. Exploration - Free-form analysis of the changes
# 2. Conclusion - Structured JSON output
#
# IMPORTANT: The verification agent should NOT make any changes to files.

# Phase 1: Exploration - Agent can freely explore and analyze
VERIFICATION_EXPLORATION_PROMPT = """\
You are a code review agent verifying that another agent completed a coding task correctly.

IMPORTANT RULE: DO NOT modify or create any files in this codebase. Your only job is to verify the work.

## Task that was assigned:
---
{task}
---

## Coding agent's output:
---
{coding_output}
---

Please verify the work by:
1. Using Read, Glob, and Grep tools to check that what was actually modified on disk matches the coding agent's output
2. Checking that the coding agent's changes address everything the user asked for
3. Reviewing the changes for correctness and completeness

If the coding agent already ran tests and they passed, you do NOT need to re-run them. Trust the coding agent's test
output unless you have specific concerns about the implementation.

Explore the codebase and provide your analysis. After you're done exploring, I'll ask you for your final verdict.
"""

# Phase 2: Conclusion - Request structured JSON output
VERIFICATION_CONCLUSION_PROMPT = """\
Based on your analysis, provide your final verdict.

You MUST respond with ONLY valid JSON and nothing else:
```json
{{
  "passed": true,
  "summary": "Brief explanation of what was checked and why it looks correct"
}}
```
Or if issues were found:

```json
{{
  "passed": false,
  "summary": "Brief summary of what needs to be fixed",
  "issues": [
    "First issue that needs to be addressed",
    "Second issue that needs to be addressed"
  ]
}}
```
Output ONLY the JSON block, no other text.
"""

# Legacy single-phase prompt (kept for backwards compatibility)
VERIFICATION_AGENT_PROMPT = """\
You are a code review agent and follow this IMPORTANT RULE - DO NOT modify or create any files in this codebase, instead
your only job is to verify that another agent completed the following coding task correctly:
----
{task}
----
Here is the coding agents output for that task:
---
{coding_output}
---

Please verify the work by:
1. Checking that what was actually modified on disk matches the coding agents output
2. Checking that the coding agents output addresses everything the user asked for
3. Reviewing the changes for correctness and completeness

If the coding agent already ran tests and they passed, you do NOT need to re-run them. Trust the coding agent's test
output unless you have specific concerns about the implementation.

You MUST respond with only valid JSON and nothing else, for example:
```json
{{
  "passed": true,
  "summary": "Brief explanation of what was checked and why it looks correct"
}}
```
Or if issues were found:

```json
{{
  "passed": false,
  "summary": "Brief summary of what needs to be fixed",
  "issues": [
    "First issue that needs to be addressed",
    "Second issue that needs to be addressed"
  ]
}}
```
Output ONLY the JSON block, no other text.
"""


def build_coding_prompt(
    task: str,
    project_docs: str | None = None,
    project_path: str | Path | None = None,
) -> str:
    """Build the complete prompt for the coding agent.

    Args:
        task: The user's task description
        project_docs: Optional project documentation references (paths to read)
        project_path: Optional project path for detecting verification commands

    Returns:
        Complete prompt for the coding agent including the task
    """
    docs_section = ""
    if project_docs:
        docs_section = f"# Project Documentation\n\n{project_docs}\n\n"

    # Get verification instructions
    verification_section = ""
    if project_path:
        from chad.util.project_setup import build_verification_instructions
        verification_section = build_verification_instructions(Path(project_path))

    return CODING_AGENT_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task=task,
    )


def get_verification_prompt(coding_output: str, task: str = "", change_summary: str | None = None) -> str:
    """Build the prompt for the verification agent (legacy single-phase).

    Args:
        coding_output: The output from the coding agent
        task: The original task description
        change_summary: Optional extracted change summary to prepend

    Returns:
        Complete prompt for the verification agent
    """
    coding_block = coding_output
    if change_summary:
        coding_block = f"Summary from coding agent: {change_summary}\n\nFull response:\n{coding_output}"

    return VERIFICATION_AGENT_PROMPT.format(coding_output=coding_block, task=task or "(no task provided)")


def get_verification_exploration_prompt(
    coding_output: str,
    task: str = "",
    change_summary: str | None = None,
) -> str:
    """Build the exploration prompt for two-phase verification.

    This is Phase 1 where the verification agent can freely explore the codebase.

    Args:
        coding_output: The output from the coding agent
        task: The original task description
        change_summary: Optional extracted change summary to prepend

    Returns:
        Exploration prompt for the verification agent
    """
    coding_block = coding_output
    if change_summary:
        coding_block = f"Summary from coding agent: {change_summary}\n\nFull response:\n{coding_output}"

    return VERIFICATION_EXPLORATION_PROMPT.format(
        coding_output=coding_block,
        task=task or "(no task provided)",
    )


def get_verification_conclusion_prompt() -> str:
    """Get the conclusion prompt for two-phase verification.

    This is Phase 2 where the verification agent must output structured JSON.

    Returns:
        Conclusion prompt requesting JSON output
    """
    return VERIFICATION_CONCLUSION_PROMPT


class VerificationParseError(Exception):
    """Raised when verification response cannot be parsed."""

    pass


@dataclass
class CodingSummary:
    """Structured summary extracted from coding agent response."""

    change_summary: str
    hypothesis: str | None = None
    before_screenshot: str | None = None
    before_description: str | None = None
    after_screenshot: str | None = None
    after_description: str | None = None


@dataclass
class ProgressUpdate:
    """Intermediate progress update from coding agent."""

    summary: str
    location: str
    before_screenshot: str | None = None
    before_description: str | None = None


# Error patterns that indicate a provider failure rather than a parse issue
_PROVIDER_ERROR_PATTERNS = [
    (r"Error:\s*.*execution stalled", "Verification agent stalled (no output)"),
    (r"Error:\s*.*execution timed out", "Verification agent timed out"),
    (r"Failed to run.*command not found", "Verification agent CLI not installed"),
    (r"No response from", "Verification agent returned no response"),
    (r"Failed to run.*:", "Verification agent execution error"),
]


def parse_verification_response(response: str) -> tuple[bool, str, list[str]]:
    """Parse the JSON response from the verification agent.

    Args:
        response: Raw response from the verification agent

    Returns:
        Tuple of (passed: bool, summary: str, issues: list[str])

    Raises:
        VerificationParseError: If response is not valid JSON with required fields
    """
    import json
    import re

    # Check for known provider error patterns before parsing
    # These indicate execution failures that should fail verification gracefully
    for pattern, error_msg in _PROVIDER_ERROR_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            return False, error_msg, [response.strip()[:500]]

    # Strip thinking/reasoning prefixes that some models add before JSON
    # e.g., "*Thinking: **Ensuring valid JSON output***\n\n{..."
    cleaned = re.sub(r"^\s*\*+[Tt]hinking:.*?\*+\s*", "", response, flags=re.DOTALL)

    # Extract JSON from the response (may be wrapped in ```json ... ```)
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find JSON object by matching balanced braces
        # Find the first { and extract a valid JSON object from there
        brace_start = cleaned.find("{")
        if brace_start != -1:
            depth = 0
            in_string = False
            escape_next = False
            json_end = -1
            for i, char in enumerate(cleaned[brace_start:], brace_start):
                if escape_next:
                    escape_next = False
                    continue
                if char == "\\" and in_string:
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break
            if json_end != -1:
                json_str = cleaned[brace_start:json_end]
            else:
                raise VerificationParseError(f"No valid JSON found in response: {response[:200]}")
        else:
            raise VerificationParseError(f"No JSON found in response: {response[:200]}")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise VerificationParseError(f"Invalid JSON: {e}")

    if "passed" not in data:
        raise VerificationParseError("Missing required field 'passed' in JSON response")

    if not isinstance(data["passed"], bool):
        raise VerificationParseError(f"Field 'passed' must be boolean, got {type(data['passed']).__name__}")

    passed = data["passed"]
    summary = data.get("summary", "")
    issues = data.get("issues", [])

    return passed, summary, issues


def extract_coding_summary(response: str) -> CodingSummary | None:
    """Extract the structured summary from a coding agent response.

    Args:
        response: Raw response from the coding agent

    Returns:
        CodingSummary with change_summary and optional hypothesis/screenshot paths, or None
    """
    import json
    import re

    # Look for JSON block with change_summary
    json_match = re.search(r'```json\s*(\{[^`]*"change_summary"[^`]*\})\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if "change_summary" in data:
                return CodingSummary(
                    change_summary=data["change_summary"],
                    hypothesis=data.get("hypothesis"),
                    before_screenshot=data.get("before_screenshot"),
                    before_description=data.get("before_description"),
                    after_screenshot=data.get("after_screenshot"),
                    after_description=data.get("after_description"),
                )
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with change_summary (fallback - only gets change_summary)
    json_match = re.search(r'\{\s*"change_summary"\s*:\s*"([^"]+)"\s*\}', response)
    if json_match:
        return CodingSummary(change_summary=json_match.group(1))

    return None


# Placeholder patterns that indicate the agent copied the example verbatim
_PLACEHOLDER_PATTERNS = [
    "one line describing",
    "brief description of",
    "src/file.py:123",
    "/path/to/before.png",
]


def _is_placeholder_text(summary: str) -> bool:
    """Check if summary looks like placeholder text from the prompt example."""
    if not summary:
        return True
    lower = summary.lower()
    return any(pattern in lower for pattern in _PLACEHOLDER_PATTERNS)


def extract_progress_update(response: str) -> ProgressUpdate | None:
    """Extract a progress update from coding agent streaming output.

    Args:
        response: Raw response chunk from the coding agent

    Returns:
        ProgressUpdate if found, None otherwise. Returns None if the summary
        appears to be placeholder text copied from the prompt example.
    """
    import json
    import re

    # Look for JSON block with type: "progress"
    json_match = re.search(r'```json\s*(\{[^`]*"type"\s*:\s*"progress"[^`]*\})\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if data.get("type") == "progress":
                summary = data.get("summary", "")
                # Filter out placeholder text
                if _is_placeholder_text(summary):
                    return None
                return ProgressUpdate(
                    summary=summary,
                    location=data.get("location", ""),
                    before_screenshot=data.get("before_screenshot"),
                    before_description=data.get("before_description"),
                )
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with type: progress (fallback)
    raw_match = re.search(r'\{\s*"type"\s*:\s*"progress"[^}]+\}', response)
    if raw_match:
        try:
            data = json.loads(raw_match.group(0))
            if data.get("type") == "progress":
                summary = data.get("summary", "")
                # Filter out placeholder text
                if _is_placeholder_text(summary):
                    return None
                return ProgressUpdate(
                    summary=summary,
                    location=data.get("location", ""),
                    before_screenshot=data.get("before_screenshot"),
                    before_description=data.get("before_description"),
                )
        except json.JSONDecodeError:
            pass

    return None


def check_verification_mentioned(response: str) -> bool:
    """Check if the coding agent mentioned running verification.

    Args:
        response: Raw response from the coding agent

    Returns:
        True if verification was mentioned, False otherwise
    """
    import re

    verification_patterns = [
        r"flake8",
        r"pytest",
        r"verification.*passed",
        r"all tests pass",
        r"linting.*pass",
        r"\d+ passed",
    ]

    response_lower = response.lower()
    for pattern in verification_patterns:
        if re.search(pattern, response_lower):
            return True

    return False

"""Configurable prompts for Chad's coding and verification agents.

Edit these prompts to customize agent behavior.
"""

from dataclasses import dataclass


# =============================================================================
# CODING AGENT SYSTEM PROMPT
# =============================================================================
# The coding agent receives this prompt with:
# - {project_docs} replaced with content from AGENTS.md/CLAUDE.md if present
# - {task} replaced with the user's task description

CODING_AGENT_PROMPT = """\
{project_docs}

You need to complete the following task:
---
# Task

{task}
---
Use the following sequence to complete the task:
1. Explore the code to understand the task.
2. Once you understand what needs to be done, you MUST output a progress update so the user can see what you found:
```json
{{"type": "progress", "summary": "One line describing the issue/feature", "location": "src/file.py:123 - where changes will be made", "before_screenshot": "/path/to/before.png (optional, include if you took one)"}}
```
For UI tasks: take a "before" screenshot first and include the path. For non-UI tasks: omit before_screenshot.
3. Write test(s) that should fail until the fix/feature is implemented.
4. Make the changes, adjusting tests as needed. If no changes are required, skip to step 9.
5. Once you have completed your changes for the task, take an after screenshot (if that is supported) to confirm
that the user's request is fixed/done.
6. You MUST run verification before completing your task, for example (keep it lean and skip heavy visuals by default):
- Run linting: ./.venv/bin/python -m flake8 src/chad
- Run core tests (visuals excluded by marker): ./.venv/bin/python -m pytest tests/ -v --tb=short -n auto \\
                                               -m \"not visual\"
- Run only the visual tests mapped to the UI you touched (see src/chad/verification/visual_test_map.py):
    VTESTS=$(./.venv/bin/python - <<'PY'
import subprocess
from chad.verification.visual_test_map import tests_for_paths
changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
print(" or ".join(tests_for_paths(changed)))
PY
)
    if [ -n "$VTESTS" ]; then ./.venv/bin/python -m pytest tests/test_ui_integration.py \\
                                                       tests/test_ui_playwright_runner.py -v --tb=short \\
                                                       -m \"visual\" -k "$VTESTS"; fi
  If you add or change UI components, update visual_test_map.py so future runs pick the right visual tests.
7. Fix ALL failures and retest if required.
8. End your response with a JSON summary block like this:
```json
{{
  "change_summary": "One sentence describing what was changed",
  "hypothesis": "Brief root cause explanation (include if investigating a bug)",
  "before_screenshot": "/path/to/before.png (include if you took a before screenshot)",
  "after_screenshot": "/path/to/after.png (include if you took an after screenshot)"
}}
```
Only include optional fields (hypothesis, before_screenshot, after_screenshot) when applicable.
"""


# =============================================================================
# VERIFICATION AGENT PROMPT
# =============================================================================
# The verification agent reviews the coding agent's work and outputs JSON.
#
# IMPORTANT: The verification agent should NOT make any changes to files.

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


def build_coding_prompt(task: str, project_docs: str | None = None) -> str:
    """Build the complete prompt for the coding agent.

    Args:
        task: The user's task description
        project_docs: Optional project documentation (from AGENTS.md, CLAUDE.md, etc.)

    Returns:
        Complete prompt for the coding agent including the task
    """
    docs_section = ""
    if project_docs:
        docs_section = f"# Project Instructions\n\n{project_docs}\n\n"

    return CODING_AGENT_PROMPT.format(project_docs=docs_section, task=task)


def get_verification_prompt(coding_output: str, task: str = "", change_summary: str | None = None) -> str:
    """Build the prompt for the verification agent.

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


class VerificationParseError(Exception):
    """Raised when verification response cannot be parsed."""

    pass


@dataclass
class CodingSummary:
    """Structured summary extracted from coding agent response."""

    change_summary: str
    hypothesis: str | None = None
    before_screenshot: str | None = None
    after_screenshot: str | None = None


@dataclass
class ProgressUpdate:
    """Intermediate progress update from coding agent."""

    summary: str
    location: str
    before_screenshot: str | None = None


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
                    after_screenshot=data.get("after_screenshot"),
                )
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with change_summary (fallback - only gets change_summary)
    json_match = re.search(r'\{\s*"change_summary"\s*:\s*"([^"]+)"\s*\}', response)
    if json_match:
        return CodingSummary(change_summary=json_match.group(1))

    return None


def extract_progress_update(response: str) -> ProgressUpdate | None:
    """Extract a progress update from coding agent streaming output.

    Args:
        response: Raw response chunk from the coding agent

    Returns:
        ProgressUpdate if found, None otherwise
    """
    import json
    import re

    # Look for JSON block with type: "progress"
    json_match = re.search(r'```json\s*(\{[^`]*"type"\s*:\s*"progress"[^`]*\})\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if data.get("type") == "progress":
                return ProgressUpdate(
                    summary=data.get("summary", ""),
                    location=data.get("location", ""),
                    before_screenshot=data.get("before_screenshot"),
                )
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with type: progress (fallback)
    raw_match = re.search(r'\{\s*"type"\s*:\s*"progress"[^}]+\}', response)
    if raw_match:
        try:
            data = json.loads(raw_match.group(0))
            if data.get("type") == "progress":
                return ProgressUpdate(
                    summary=data.get("summary", ""),
                    location=data.get("location", ""),
                    before_screenshot=data.get("before_screenshot"),
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

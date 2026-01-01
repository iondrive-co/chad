"""Configurable prompts for Chad's coding and verification agents.

Edit these prompts to customize agent behavior.
"""

# =============================================================================
# CODING AGENT SYSTEM PROMPT
# =============================================================================
# The coding agent receives this prompt with:
# - {project_docs} replaced with content from AGENTS.md/CLAUDE.md if present
# - {task} replaced with the user's task description

CODING_AGENT_PROMPT = """\
{project_docs}

Firstly, write a test which should fail until the following task has been successfully completed. For any UI-affecting
work, see if the project has a means to take a "before" screenshot, if so do that and review the screenshot to confirm
you understand the issue/current state.
---
# Task

{task}
---
Once you have completed your the above, take an after screenshot if that is supported to confirm that it is fixed/done.
Run any tests and lint available in the project and fix all issues even if you didn't cause them.

When you are done, end your response with a JSON summary block like this:
```json
{{"change_summary": "One sentence describing what was changed"}}
```
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
1. Checking that what was actually modified on disk (use Read/Glob tools) matches the coding agents output
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


def get_verification_prompt(coding_output: str, task: str = "") -> str:
    """Build the prompt for the verification agent.

    Args:
        coding_output: The output from the coding agent
        task: The original task description

    Returns:
        Complete prompt for the verification agent
    """
    return VERIFICATION_AGENT_PROMPT.format(coding_output=coding_output, task=task or "(no task provided)")


class VerificationParseError(Exception):
    """Raised when verification response cannot be parsed."""

    pass


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

    # Extract JSON from the response (may be wrapped in ```json ... ```)
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON object
        json_match = re.search(r'\{[^{}]*"passed"[^{}]*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
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


def extract_coding_summary(response: str) -> str | None:
    """Extract the change_summary from a coding agent response.

    Args:
        response: Raw response from the coding agent

    Returns:
        The change_summary string if found, None otherwise
    """
    import json
    import re

    # Look for JSON block with change_summary
    json_match = re.search(r'```json\s*(\{[^`]*"change_summary"[^`]*\})\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if "change_summary" in data:
                return data["change_summary"]
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with change_summary
    json_match = re.search(r'\{\s*"change_summary"\s*:\s*"([^"]+)"\s*\}', response)
    if json_match:
        return json_match.group(1)

    return None

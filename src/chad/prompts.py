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
"""


# =============================================================================
# VERIFICATION AGENT PROMPT
# =============================================================================
# The verification agent reviews the coding agent's work and outputs JSON.
#
# IMPORTANT: The verification agent should NOT make any changes to files.

VERIFICATION_AGENT_PROMPT = """\
You are a code review agent. Your job is to verify that a coding task was completed correctly.

IMPORTANT RULES:
1. DO NOT modify any files - you are only here to review and verify
2. DO NOT create new files or make any changes to the codebase
3. Your only job is to check the work and report your findings

The coding agent was given a task and has reported completion. Here is their output:

---
{coding_output}
---

Please verify the work by:
1. Checking that the files mentioned were actually modified on disk
2. Reviewing the changes for correctness and completeness
3. Checking if tests pass (run `mcp__chad-ui-playwright__verify` if available)
4. Looking for any obvious bugs, security issues, or problems

You MUST respond with valid JSON in exactly this format:

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

    return CODING_AGENT_PROMPT.format(
        project_docs=docs_section,
        task=task
    )


def get_verification_prompt(coding_output: str) -> str:
    """Build the prompt for the verification agent.

    Args:
        coding_output: The output from the coding agent

    Returns:
        Complete prompt for the verification agent
    """
    return VERIFICATION_AGENT_PROMPT.format(coding_output=coding_output)


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
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
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

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

# =============================================================================
# PHASE 1: EXPLORATION PROMPT
# =============================================================================
# The exploration phase does initial codebase exploration and outputs a progress
# update. This phase ends after outputting the progress JSON.

EXPLORATION_PROMPT = """\
{project_docs}

{verification_instructions}

You need to complete the following task:
---
# Task

{task}
---

## Phase 1: Exploration

CRITICAL: This phase MUST complete within 60 seconds. Read only 2-3 key files to understand the area - do not spend excessive time exploring.
If the project instructions include a Class Map, use it first to choose your initial files before running broad searches.

When you have completed your quick exploration, IMMEDIATELY output a progress update using this JSON format:
```json
{{"type": "progress", "summary": "Adding retry logic to handle API rate limits", "location": "src/api/client.py:45", "next_step": "Writing tests to verify the retry behavior"}}
```

This marks the end of the exploration phase. The implementation phase will continue from here.
"""

# =============================================================================
# PHASE 2: IMPLEMENTATION PROMPT
# =============================================================================
# The implementation phase receives the exploration output and continues with
# writing tests, making changes, and running verification.

IMPLEMENTATION_PROMPT = """\
{project_docs}

{verification_instructions}

You need to complete the following task:
---
# Task

{task}
---

## Previous Exploration

The exploration phase found:
{exploration_output}

## Phase 2: Implementation

Continue from the exploration above. Complete the following steps:

1. Write test(s) that should fail until the fix/feature is implemented (you can explore more code if needed)
2. Make the changes, adjusting tests and exploring more code as needed. If no changes are required, skip to step 5.
3. Once you believe your changes will complete the task, run verification commands (lint and tests) as described above.
4. Fix ALL failures and retest if required.
5. End your response with a JSON summary block like this:
```json
{{
  "change_summary": "One sentence describing what was changed",
  "files_changed": ["src/auth.py", "tests/test_auth.py"],
  "completion_status": "success",
  "hypothesis": "Brief root cause explanation (include if investigating a bug)"
}}
```
Required fields:
- change_summary: One sentence describing what was done
- files_changed: Array of file paths that were modified, or "info_only" if this was just an information request with no file changes
- completion_status: One of "success", "partial" (hit context/token limit), "blocked" (needs user input), or "error"
Optional fields:
- hypothesis: Include only when investigating a bug
"""

# =============================================================================
# COMBINED CODING PROMPT (for providers that support continuous execution)
# =============================================================================
# This combines both phases for providers that can execute without interruption.
# Used as fallback or for testing.

CODING_AGENT_PROMPT = """\
## URGENT: PROGRESS UPDATE REQUIRED

STOP! Before doing anything else, you MUST:
1. Spend AT MOST 30 seconds reading 1-2 key files
2. IMMEDIATELY output a progress update in this exact JSON format:
```json
{{"type": "progress", "summary": "Brief description of what you found", "location": "file:line", "next_step": "What you will do next"}}
```

This progress update tells the user you are working. Output it NOW, within 30 seconds of starting.

---

{project_docs}

{verification_instructions}

# Task

{task}

---

## After your progress update, continue with:

3. Write test(s) that should fail until the fix/feature is implemented
4. Make the changes, adjusting tests as needed. If no changes are required, skip to step 6.
5. Run verification commands (lint and tests) and fix ALL failures.
6. End your response with a JSON summary:
```json
{{
  "change_summary": "One sentence describing what was changed",
  "files_changed": ["src/auth.py", "tests/test_auth.py"],
  "completion_status": "success"
}}
```
Fields: change_summary (required), files_changed (required, or "info_only"), completion_status (success/partial/blocked/error), hypothesis (optional, for bugs)
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
2. Checking that the coding agent's changes on disk address everything the user asked for
3. Checking that the coding agent's changes on disk do not include unnecessary changes
4. Reviewing the changes for correctness and completeness

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


@dataclass
class PromptPreviews:
    """Pre-filled prompt templates with project docs but task placeholders."""

    exploration: str
    implementation: str
    verification: str


def build_prompt_previews(project_path: str | Path | None) -> PromptPreviews:
    """Build prompt previews with project docs filled in but {task} as placeholder.

    Call this when the project path changes to show users what the prompts
    will look like before a task is started.

    Args:
        project_path: Path to the project directory

    Returns:
        PromptPreviews with all three prompts partially filled
    """
    from chad.util.project_setup import build_doc_reference_text

    project_docs = None
    if project_path:
        project_docs = build_doc_reference_text(Path(project_path))

    docs_section, verification_section = _build_docs_and_verification(project_docs, project_path)

    exploration = EXPLORATION_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task="{task}",
    )
    implementation = IMPLEMENTATION_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task="{task}",
        exploration_output="{exploration_output}",
    )
    verification = VERIFICATION_EXPLORATION_PROMPT.format(
        task="{task}",
        coding_output="{coding_output}",
    )

    return PromptPreviews(
        exploration=exploration,
        implementation=implementation,
        verification=verification,
    )


def _build_task_with_screenshots(task: str, screenshots: list[str] | None) -> str:
    """Build task section with screenshots if provided."""
    if not screenshots:
        return task
    screenshot_section = "\n\nThe user has attached the following screenshots for reference. " \
        "Use the Read tool to view them:\n"
    for screenshot_path in screenshots:
        screenshot_section += f"- {screenshot_path}\n"
    return task + screenshot_section


def _build_docs_and_verification(
    project_docs: str | None,
    project_path: str | Path | None,
) -> tuple[str, str]:
    """Build the docs and verification sections for prompts."""
    docs_section = ""
    if project_docs:
        docs_section = f"# Project Documentation\n\n{project_docs}\n\n"

    verification_section = ""
    if project_path:
        from chad.util.project_setup import build_verification_instructions
        verification_section = build_verification_instructions(Path(project_path))

    return docs_section, verification_section


def build_exploration_prompt(
    task: str,
    project_docs: str | None = None,
    project_path: str | Path | None = None,
    screenshots: list[str] | None = None,
) -> str:
    """Build the exploration phase prompt for the coding agent.

    This is Phase 1 of the 3-phase execution. The agent explores the codebase
    and outputs a progress JSON when done.

    Args:
        task: The user's task description
        project_docs: Optional project documentation references (paths to read)
        project_path: Optional project path for detecting verification commands
        screenshots: Optional list of screenshot file paths to include

    Returns:
        Exploration prompt for Phase 1
    """
    docs_section, verification_section = _build_docs_and_verification(project_docs, project_path)
    task_with_screenshots = _build_task_with_screenshots(task, screenshots)

    return EXPLORATION_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task=task_with_screenshots,
    )


def build_implementation_prompt(
    task: str,
    exploration_output: str,
    project_docs: str | None = None,
    project_path: str | Path | None = None,
) -> str:
    """Build the implementation phase prompt for the coding agent.

    This is Phase 2 of the 3-phase execution. The agent receives the exploration
    output and continues with writing tests, making changes, and verification.

    Args:
        task: The user's task description
        exploration_output: Output from the exploration phase
        project_docs: Optional project documentation references (paths to read)
        project_path: Optional project path for detecting verification commands

    Returns:
        Implementation prompt for Phase 2
    """
    docs_section, verification_section = _build_docs_and_verification(project_docs, project_path)

    return IMPLEMENTATION_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task=task,
        exploration_output=exploration_output,
    )


def build_coding_prompt(
    task: str,
    project_docs: str | None = None,
    project_path: str | Path | None = None,
    screenshots: list[str] | None = None,
) -> str:
    """Build the complete prompt for the coding agent (combined phases).

    This is the legacy single-prompt approach for providers that support
    continuous execution without interruption. For 3-phase execution,
    use build_exploration_prompt() and build_implementation_prompt().

    Args:
        task: The user's task description
        project_docs: Optional project documentation references (paths to read)
        project_path: Optional project path for detecting verification commands
        screenshots: Optional list of screenshot file paths to include

    Returns:
        Complete prompt for the coding agent including the task
    """
    docs_section, verification_section = _build_docs_and_verification(project_docs, project_path)
    task_with_screenshots = _build_task_with_screenshots(task, screenshots)

    return CODING_AGENT_PROMPT.format(
        project_docs=docs_section,
        verification_instructions=verification_section,
        task=task_with_screenshots,
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
    # files_changed is a list of file paths, or "info_only" string for information requests
    files_changed: list[str] | str | None = None
    # completion_status: "success", "partial", "blocked", "error", or None if not specified
    completion_status: str | None = None
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
    next_step: str | None = None
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

    def _extract_balanced_json_objects(text: str) -> list[str]:
        objects: list[str] = []
        depth = 0
        in_string = False
        escape_next = False
        start_idx: int | None = None

        for idx, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue

            if in_string:
                if char == "\\":
                    escape_next = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == "{":
                if depth == 0:
                    start_idx = idx
                depth += 1
                continue

            if char == "}":
                if depth == 0:
                    continue
                depth -= 1
                if depth == 0 and start_idx is not None:
                    objects.append(text[start_idx: idx + 1])
                    start_idx = None

        return objects

    # Collect candidate JSON objects from fenced and raw content.
    candidates: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE):
        candidate = match.group(1).strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    for candidate in _extract_balanced_json_objects(cleaned):
        normalized = candidate.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    if not candidates:
        raise VerificationParseError(f"No JSON found in response: {response[:200]}")

    # If multiple JSON objects exist, prefer one that includes `passed`.
    prioritized = sorted(candidates, key=lambda text: '"passed"' not in text)
    data = None
    parse_error = None
    missing_passed_seen = False
    for candidate in prioritized:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            parse_error = e
            continue

        if not isinstance(parsed, dict):
            continue

        if "passed" in parsed:
            data = parsed
            break

        missing_passed_seen = True

    if data is None:
        if missing_passed_seen:
            raise VerificationParseError("Missing required field 'passed' in JSON response")
        if parse_error is not None:
            raise VerificationParseError(f"Invalid JSON: {parse_error}")
        raise VerificationParseError(f"No valid JSON found in response: {response[:200]}")

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
                # Parse files_changed - can be array or "info_only" string
                files_changed = data.get("files_changed")
                if isinstance(files_changed, list):
                    files_changed = [str(f) for f in files_changed]
                elif isinstance(files_changed, str):
                    pass  # Keep as string (e.g., "info_only")
                else:
                    files_changed = None
                return CodingSummary(
                    change_summary=data["change_summary"],
                    files_changed=files_changed,
                    completion_status=data.get("completion_status"),
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
    # The exact example from the prompt - filter if agent copies it verbatim
    "adding retry logic to handle api rate limits",
    "src/api/client.py",
    "writing tests to verify the retry behavior",
]


def _is_placeholder_text(text: str) -> bool:
    """Check if text looks like placeholder from the prompt example."""
    if not text:
        return True
    lower = text.lower()
    return any(pattern in lower for pattern in _PLACEHOLDER_PATTERNS)


def extract_progress_update(response: str) -> ProgressUpdate | None:
    """Extract a progress update from coding agent streaming output.

    Supports two formats:
    1. JSON (preferred):
       {"type": "progress", "summary": "...", "location": "...", "next_step": "..."}
    2. Markdown (legacy fallback):
       ```
       **Progress:** summary text
       **Location:** file:line
       **Next:** next step
       ```

    Args:
        response: Raw response chunk from the coding agent

    Returns:
        ProgressUpdate if found, None otherwise. Returns None if the summary
        or next_step appears to be placeholder text copied from the prompt example.
    """
    import json
    import re

    def _parse_progress_json(json_text: str) -> ProgressUpdate | None:
        """Parse progress JSON, retrying with newline normalization."""
        candidates = [json_text, json_text.replace("\r", " ").replace("\n", " ")]
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "progress":
                return None
            summary = data.get("summary", "")
            next_step = data.get("next_step")
            # Filter if summary or next_step looks like placeholder text
            if _is_placeholder_text(summary):
                return None
            if next_step and _is_placeholder_text(next_step):
                return None
            return ProgressUpdate(
                summary=summary,
                location=data.get("location", ""),
                next_step=next_step,
                before_screenshot=data.get("before_screenshot"),
                before_description=data.get("before_description"),
            )
        return None

    # Try JSON format first (preferred)
    # Look for JSON block with type: "progress"
    json_match = re.search(r'```json\s*(\{[^`]*"type"\s*:\s*"progress"[^`]*\})\s*```', response, re.DOTALL)
    if json_match:
        parsed = _parse_progress_json(json_match.group(1))
        if parsed:
            return parsed

    # Try to find raw JSON with type: progress
    raw_match = re.search(r'\{\s*"type"\s*:\s*"progress"[^}]+\}', response, re.DOTALL)
    if raw_match:
        parsed = _parse_progress_json(raw_match.group(0))
        if parsed:
            return parsed

    # Fall back to markdown format for backwards compatibility
    # Match code block with **Progress:**, **Location:**, **Next:** lines
    md_block = re.search(r'```\s*\n(.*?\*\*Progress:\*\*.*?)```', response, re.DOTALL | re.IGNORECASE)
    if md_block:
        block_content = md_block.group(1)
    else:
        # Also try without code block - just the **Progress:** pattern
        block_content = response

    # Extract markdown fields
    progress_match = re.search(r'\*\*Progress:\*\*\s*(.+?)(?:\n|$)', block_content, re.IGNORECASE)
    location_match = re.search(r'\*\*Location:\*\*\s*(.+?)(?:\n|$)', block_content, re.IGNORECASE)
    next_match = re.search(r'\*\*Next:\*\*\s*(.+?)(?:\n|$)', block_content, re.IGNORECASE)

    if progress_match:
        summary = progress_match.group(1).strip()
        location = location_match.group(1).strip() if location_match else ""
        next_step = next_match.group(1).strip() if next_match else None

        # Filter placeholder text
        if _is_placeholder_text(summary):
            return None
        if next_step and _is_placeholder_text(next_step):
            return None

        return ProgressUpdate(summary=summary, location=location, next_step=next_step)

    # Last-resort manual extraction (handles malformed JSON with embedded newlines)
    manual_match = re.search(
        r'"type"\\s*:\\s*"progress"(?P<body>[^}]*)}',
        response,
        re.DOTALL,
    )
    if manual_match:
        body = manual_match.group("body")
        summary_match = re.search(r'"summary"\\s*:\\s*"(?P<summary>.*?)"', body, re.DOTALL)
        location_match_manual = re.search(r'"location"\\s*:\\s*"(?P<loc>[^"]+)"', body)
        next_step_match = re.search(r'"next_step"\\s*:\\s*"(?P<ns>[^"]+)"', body)
        if summary_match:
            summary = " ".join(summary_match.group("summary").splitlines()).strip()
            next_step = next_step_match.group("ns") if next_step_match else None
            if not _is_placeholder_text(summary):
                if next_step and _is_placeholder_text(next_step):
                    return None
                location = location_match_manual.group("loc") if location_match_manual else ""
                return ProgressUpdate(summary=summary, location=location, next_step=next_step)

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


# =============================================================================
# CODING CONTINUATION PROMPT
# =============================================================================
# Used when agent exits without completing (only progress update, no completion JSON)

CONTINUATION_PROMPT = """\
Your previous response ended with a progress update but you did not complete the task. \
Continue from where you left off:

1. Write test(s) that should fail until the fix/feature is implemented
2. Make the changes, adjusting tests as needed
3. Run verification commands (lint and tests)
4. Fix ALL failures and retest if required
5. End with a JSON summary block:
```json
{{
  "change_summary": "One sentence describing what was changed",
  "files_changed": ["src/file.py", "tests/test_file.py"],
  "completion_status": "success"
}}
```

Do NOT output another progress update - continue directly with implementation.
"""


def get_continuation_prompt(previous_output: str) -> str:
    """Build a continuation prompt when agent exits early (progress but no completion).

    Args:
        previous_output: The output from the previous run (included for context)

    Returns:
        Continuation prompt to re-invoke the agent
    """
    return CONTINUATION_PROMPT


# =============================================================================
# CODING SUMMARY VALIDATION AND RE-INVOKE
# =============================================================================

SUMMARY_COMPLETION_PROMPT = """\
Your previous response is missing required fields in the JSON summary block. Please output ONLY a complete JSON \
summary block with all required fields:

```json
{{
  "change_summary": "One sentence describing what was done",
  "files_changed": ["list", "of", "files"] or "info_only" if no files were changed,
  "completion_status": "success" or "partial" or "blocked" or "error"
}}
```

Required fields:
- change_summary: What you accomplished
- files_changed: Array of modified file paths, OR the string "info_only" if this was just an information request
- completion_status: One of "success", "partial" (hit token/context limit), "blocked" (needs user input), or "error"

Output ONLY the JSON block, nothing else.
"""


def get_summary_completion_prompt(summary: CodingSummary | None) -> str | None:
    """Check if a coding summary is missing required fields and return a completion prompt.

    This function validates that the mandatory fields (files_changed, completion_status)
    are present in the coding summary. If they're missing, it returns a prompt to
    re-invoke the coding agent to complete the summary.

    Args:
        summary: The extracted coding summary, or None if extraction failed

    Returns:
        A prompt string to re-invoke the agent if fields are missing, or None if complete
    """
    if summary is None:
        # No summary at all - need to ask for one
        return SUMMARY_COMPLETION_PROMPT

    # Check for missing mandatory fields
    missing_files_changed = summary.files_changed is None
    missing_completion_status = summary.completion_status is None

    if missing_files_changed or missing_completion_status:
        return SUMMARY_COMPLETION_PROMPT

    return None

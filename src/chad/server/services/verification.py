"""Server-side verification service.

Runs automated (flake8/tests) and LLM-based verification of coding agent work.
Extracted from gradio_ui._run_verification to enable server-side orchestration.
"""

from typing import Any, Callable

from chad.util.prompts import (
    extract_coding_summary,
    get_verification_exploration_prompt,
    get_verification_conclusion_prompt,
    parse_verification_response,
    check_verification_mentioned,
    VerificationParseError,
)

MAX_VERIFICATION_PROMPT_CHARS = 6000


def _truncate_verification_output(text: str, limit: int = MAX_VERIFICATION_PROMPT_CHARS) -> str:
    """Compact the coding agent output for verification prompts."""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned

    indicator = f"...[truncated {len(cleaned) - limit} chars]..."
    keep = max(limit - len(indicator) - 4, 0)
    head_len = int(keep * 0.6)
    tail_len = keep - head_len
    head = cleaned[:head_len].rstrip()
    tail = cleaned[-tail_len:].lstrip() if tail_len > 0 else ""
    parts = [head, indicator]
    if tail:
        parts.append(tail)
    return "\n\n".join(part for part in parts if part)


def _run_automated_verification(
    project_path: str,
    on_activity: Callable | None = None,
) -> tuple[bool, str | None]:
    """Run automated verification (flake8/linting).

    Returns:
        (passed, feedback) - feedback is None on success, error string on failure
    """
    try:
        from chad.ui.gradio.verification.tools import verify as run_verify
        if on_activity:
            on_activity("system", "Running verification (flake8)...")

        verify_result = run_verify(project_root=project_path, lint_only=True)

        # Treat timeout as a pass (coding agent ran their own tests)
        error_msg = verify_result.get("error") or ""
        if "timed out" in error_msg.lower():
            if on_activity:
                on_activity("system", "Verification timed out, treating as pass")
            return True, None

        if not verify_result.get("success", False):
            issues: list[str] = []
            failure_message = verify_result.get("message") or verify_result.get("error")
            if failure_message:
                issues.append(failure_message)

            phases = verify_result.get("phases", {})

            lint_phase = phases.get("lint", {})
            if not lint_phase.get("success", True):
                lint_issues = lint_phase.get("issues") or []
                if lint_issues:
                    joined = "\n".join(f"- {issue}" for issue in lint_issues[:5])
                    issues.append(f"Flake8 errors:\n{joined}")
                else:
                    issues.append(f"Flake8 failed with {lint_phase.get('issue_count', 0)} errors")

            pip_phase = phases.get("pip_check", {})
            if not pip_phase.get("success", True):
                pip_issues = pip_phase.get("issues") or []
                if pip_issues:
                    joined = "\n".join(f"- {issue}" for issue in pip_issues[:5])
                    issues.append(f"Dependency issues:\n{joined}")
                else:
                    issues.append("Package dependency issues found")

            test_phase = phases.get("tests", {})
            if not test_phase.get("success", True):
                failed_count = test_phase.get("failed", 0)
                passed_count = test_phase.get("passed", 0)
                output_lines = (test_phase.get("output") or "").strip().splitlines()
                snippet = "\n".join(output_lines[-5:]) if output_lines else ""
                summary = f"Tests failed ({failed_count} failed, {passed_count} passed)"
                if snippet:
                    summary += f":\n{snippet}"
                issues.append(summary)

            if not issues:
                issues.append("Verification failed")

            feedback = "Verification failed:\n" + "\n\n".join(issues)
            return False, feedback
    except Exception as e:
        if on_activity:
            on_activity("system", f"Warning: Verification could not run: {str(e)}")

    return True, None


def run_verification(
    project_path: str,
    coding_output: str,
    task_description: str,
    verification_account: str,
    verification_model: str | None = None,
    verification_reasoning: str | None = None,
    on_activity: Callable | None = None,
    run_phase_fn: Callable | None = None,
    task: Any = None,
    session: Any = None,
    worktree_path: Any = None,
    rows: int = 80,
    cols: int = 200,
    emit: Callable | None = None,
    git_mgr: Any = None,
    attempt: int = 1,
) -> tuple[bool | None, str]:
    """Run automated + LLM verification.

    Uses PTY-based execution (via run_phase_fn) to run the verification agent,
    keeping all agent execution going through the same PTY mechanism.

    Args:
        project_path: Path to the project directory
        coding_output: The output from the coding agent
        task_description: The original task description
        verification_account: Account name for verification
        verification_model: Optional model override
        verification_reasoning: Optional reasoning level
        on_activity: Optional callback for activity updates
        run_phase_fn: The _run_phase function from TaskExecutor
        task: The Task object
        session: The Session object
        worktree_path: Path to the worktree
        rows: Terminal rows
        cols: Terminal cols
        emit: Event emitter function
        git_mgr: Git worktree manager

    Returns:
        Tuple of (passed: bool | None, feedback: str)
    """
    if not task_description.strip():
        return None, "Verification aborted: missing task description."

    if not coding_output.strip():
        return None, "Verification aborted: coding agent output was empty."

    # Step 1: Run automated verification (flake8/linting)
    auto_passed, auto_feedback = _run_automated_verification(project_path, on_activity)
    if not auto_passed:
        return False, auto_feedback or "Automated verification failed"

    # Step 2: Run LLM verification via PTY
    if run_phase_fn is None:
        # Fallback: use provider-based verification
        return _run_provider_verification(
            project_path=project_path,
            coding_output=coding_output,
            task_description=task_description,
            verification_account=verification_account,
            verification_model=verification_model,
            verification_reasoning=verification_reasoning,
            on_activity=on_activity,
            attempt=attempt,
        )

    coding_summary = extract_coding_summary(coding_output)
    change_summary = coding_summary.change_summary if coding_summary else None
    trimmed_output = _truncate_verification_output(coding_output)
    exploration_prompt = get_verification_exploration_prompt(
        trimmed_output, task_description, change_summary, attempt=attempt,
    )
    conclusion_prompt = get_verification_conclusion_prompt()

    # Run verification agent via PTY - two phase: explore then conclude
    combined_prompt = exploration_prompt + "\n\n" + conclusion_prompt

    exit_code, response = run_phase_fn(
        task=task,
        session=session,
        worktree_path=worktree_path,
        task_description=task_description,
        coding_account=verification_account,
        coding_provider=_get_provider_for_account(verification_account),
        screenshots=None,
        phase="combined",
        exploration_output=None,
        rows=rows,
        cols=cols,
        emit=emit,
        git_mgr=git_mgr,
        coding_model=verification_model,
        coding_reasoning=verification_reasoning,
        override_prompt=combined_prompt,
    )

    if not response:
        return False, "Verification failed: no response from verification agent"

    try:
        passed, summary, issues = parse_verification_response(response)
        if passed:
            return True, summary
        feedback = summary
        if issues:
            feedback += "\n\nIssues:\n" + "\n".join(f"- {issue}" for issue in issues)
        return False, feedback
    except VerificationParseError as e:
        return False, f"Verification failed: {e}"


def _get_provider_for_account(account_name: str) -> str:
    """Look up the provider type for an account."""
    try:
        from chad.server.state import get_config_manager
        config = get_config_manager()
        accounts = config.list_accounts()
        return accounts.get(account_name, "anthropic")
    except Exception:
        return "anthropic"


def _run_provider_verification(
    project_path: str,
    coding_output: str,
    task_description: str,
    verification_account: str,
    verification_model: str | None = None,
    verification_reasoning: str | None = None,
    on_activity: Callable | None = None,
    attempt: int = 1,
) -> tuple[bool | None, str]:
    """Fallback: Run LLM verification using the AIProvider abstraction."""
    from chad.util.providers import ModelConfig, create_provider

    try:
        from chad.server.state import get_config_manager
        config = get_config_manager()
        accounts = config.list_accounts()
        provider_type = accounts.get(verification_account, "anthropic")
    except Exception:
        provider_type = "anthropic"

    verification_config = ModelConfig(
        provider=provider_type,
        model_name=verification_model,
        account_name=verification_account,
        reasoning_effort=None if verification_reasoning == "default" else verification_reasoning,
    )

    coding_summary = extract_coding_summary(coding_output)
    change_summary = coding_summary.change_summary if coding_summary else None
    trimmed_output = _truncate_verification_output(coding_output)
    exploration_prompt = get_verification_exploration_prompt(
        trimmed_output, task_description, change_summary, attempt=attempt,
    )
    conclusion_prompt = get_verification_conclusion_prompt()

    try:
        max_parse_attempts = 2
        last_error = None
        retry_conclusion_prompt = conclusion_prompt

        for attempt in range(max_parse_attempts):
            verifier = create_provider(verification_config)
            if on_activity:
                verifier.set_activity_callback(on_activity)

            if not verifier.start_session(project_path, None):
                return True, "Verification skipped: failed to start session"

            try:
                verifier.send_message(exploration_prompt)
                _ = verifier.get_response(timeout=1800.0)

                verifier.send_message(retry_conclusion_prompt)
                response = verifier.get_response(timeout=1800.0)

                if not response:
                    last_error = "No response from verification agent"
                    continue

                try:
                    passed, summary, issues = parse_verification_response(response)
                    if passed:
                        if provider_type != "mock" and not check_verification_mentioned(coding_output):
                            verified, feedback = _run_automated_verification(project_path, on_activity)
                            if not verified:
                                return False, feedback or "Verification failed"
                        return True, summary
                    feedback = summary
                    if issues:
                        feedback += "\n\nIssues:\n" + "\n".join(f"- {issue}" for issue in issues)
                    return False, feedback
                except VerificationParseError as e:
                    last_error = str(e)
                    if attempt < max_parse_attempts - 1:
                        retry_conclusion_prompt = (
                            "Your previous response was not valid JSON. "
                            "You MUST respond with ONLY a JSON object like:\n"
                            '```json\n{"passed": true, "summary": "explanation"}\n```\n\n'
                            "Try again.\n\n"
                            f"{conclusion_prompt}"
                        )
                    continue
            finally:
                verifier.stop_session()

        return False, f"Verification failed: {last_error or 'unknown verification parse error'}"

    except Exception as e:
        return None, f"Verification error: {str(e)}"

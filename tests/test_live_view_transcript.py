"""Visual regression for the live-view transcript panel (the right-hand panel).

The panel must render an append-only, Claude-Code-style agent transcript:
  - individual tool calls shown like `● Read(path)` / `● Bash(cmd)`
  - the agent's prose
  - WITHOUT the completion/progress JSON the prompt asks the agent to emit
  - WITHOUT the parser's collapsed `• 3 files read` summary lines (we render the
    real per-tool calls instead)
  - bottom-anchored and scrollable, like a real embedded terminal

This drives the real React render path by writing a synthetic session event log
to disk and opening that session in the UI, so no provider/API tokens are used.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chad.util.verification.ui_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

pytestmark = pytest.mark.visual

# Distinctive strings we assert on.
PROSE_MARKER = "Investigating the failure in validate()"
LEAKED_JSON_MARKER = "change_summary"
COLLAPSED_SUMMARY_MARKER = "3 files read"


def _write_synthetic_log(session_id: str, log_dir: Path) -> Path:
    """Write a realistic agent transcript event log for ``session_id``."""
    os.environ["CHAD_LOG_DIR"] = str(log_dir)
    from chad.util.event_log import (
        EventLog,
        SessionEndedEvent,
        SessionStartedEvent,
        TerminalOutputEvent,
        ToolCallStartedEvent,
    )

    log = EventLog(session_id)
    log.log(
        SessionStartedEvent(
            task_description="Investigate and fix the validate() ordering bug",
            project_path="/tmp/proj",
            coding_provider="anthropic",
            coding_account="claude",
            coding_model="claude-sonnet-4-20250514",
        )
    )
    log.log(ToolCallStartedEvent(tool_call_id="t1", tool="Read", path="src/bug.py"))
    # An EXPLORATION_RESULT progress line (emitted by every provider). It is
    # rendered as a Discovery bubble in the chat panel, so the live view must
    # drop the whole line to avoid showing the same text twice.
    log.log(TerminalOutputEvent(data="EXPLORATION_RESULT: tracing the validate() ordering\n"))
    log.log(TerminalOutputEvent(data=f"{PROSE_MARKER}: the null check runs after validation.\n"))
    log.log(ToolCallStartedEvent(tool_call_id="t2", tool="Grep", args={"pattern": "validate"}))
    log.log(ToolCallStartedEvent(tool_call_id="t3", tool="Edit", path="src/bug.py"))
    log.log(ToolCallStartedEvent(tool_call_id="t4", tool="Bash", command="pytest tests/ -q"))
    # Events logged by older servers keep gemini-style snake_case names; the
    # client must still render them canonically instead of dumping JSON args.
    log.log(ToolCallStartedEvent(
        tool_call_id="t5", tool="read_file", args={"absolute_path": "src/legacy.py"},
    ))
    # A tool call the model leaked as TEXT (llama.cpp parser miss) — machine
    # markup that must never render as prose.
    log.log(
        TerminalOutputEvent(
            data=(
                "<function=read_file>\n<parameter=absolute_path>\nsrc/bug.py\n"
                "</parameter>\n</function>\n</tool_call>\n"
            )
        )
    )
    # Prose that mixes in (a) the parser's collapsed tool summary and (b) the
    # completion JSON block — both must be filtered out of the rendered panel.
    log.log(
        TerminalOutputEvent(
            data=(
                "All tests pass after reordering the null check.\n"
                f"• {COLLAPSED_SUMMARY_MARKER}, 1 edit, 1 command\n"
                "Here is my completion:\n"
                "```json\n"
                '{"' + LEAKED_JSON_MARKER + '": "Reordered the null check before validate()", '
                '"completion_status": "success"}\n'
                "```\n"
            )
        )
    )
    log.log(SessionEndedEvent(success=True, reason="completed"))
    return log.log_path


def _run() -> tuple[str, dict]:
    """Open a synthetic session in the UI and return its transcript text + scroll metrics."""
    log_dir = Path(os.environ["CHAD_LOG_DIR"])
    env = create_temp_env(screenshot_mode=False)
    env.env_vars["CHAD_LOG_DIR"] = str(log_dir)
    instance = start_chad(env)
    out_dir = Path("/tmp/chad")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open_playwright_page(instance.port, headless=True) as page:
            # Create + open a fresh session via the New button.
            page.wait_for_selector(".new-session-btn", timeout=15000)
            page.click(".new-session-btn")
            page.wait_for_selector(".session-tab-name", timeout=10000)
            session_id = page.inner_text(".session-tab-name").strip()
            assert session_id, "expected an opened session tab"

            # Give that session a synthetic transcript. ChatView only reloads its
            # historical log on a fresh mount (it is hidden, not unmounted, on tab
            # switches; key is the session id). So create a second session, then
            # switch back — that remounts ChatView for our session with the log present.
            _write_synthetic_log(session_id, log_dir)
            page.click(".new-session-btn")
            page.wait_for_timeout(500)
            page.locator(".session-tab", has_text=session_id).click()
            page.wait_for_selector(".terminal-output", timeout=10000)
            page.wait_for_function(
                "() => { const el = document.querySelector('.terminal-output');"
                " return el && el.innerText.includes('Read('); }",
                timeout=10000,
            )

            text = page.inner_text(".terminal-output")
            metrics = page.eval_on_selector(
                ".terminal-output",
                "el => ({ sh: el.scrollHeight, ch: el.clientHeight, st: el.scrollTop })",
            )
            page.screenshot(path=str(out_dir / "live_view_transcript.png"))
            return text, metrics
    finally:
        stop_chad(instance)
        env.cleanup()


def test_live_view_renders_harness_transcript(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    os.environ["CHAD_LOG_DIR"] = str(log_dir)
    try:
        text, metrics = _run()
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    # Tool calls render like the Claude Code TUI.
    assert "●" in text, f"missing tool-call glyph; got:\n{text}"
    assert "Read(src/bug.py)" in text, f"missing Read tool line; got:\n{text}"
    assert "Bash(pytest tests/ -q)" in text, f"missing Bash tool line; got:\n{text}"
    assert "Grep(validate)" in text, f"missing Grep tool line; got:\n{text}"

    # Agent prose is shown.
    assert PROSE_MARKER in text, f"missing agent prose; got:\n{text}"

    # The completion JSON and the collapsed parser summary are filtered out.
    assert LEAKED_JSON_MARKER not in text, f"leaked completion JSON; got:\n{text}"
    assert COLLAPSED_SUMMARY_MARKER not in text, f"leaked collapsed summary; got:\n{text}"
    assert "```" not in text, f"leaked code fence; got:\n{text}"

    # EXPLORATION_RESULT lines are dropped entirely — they already appear as
    # Discovery bubbles in the chat panel, so keeping them here duplicates them.
    assert "EXPLORATION_RESULT:" not in text, f"leaked progress marker; got:\n{text}"
    assert "tracing the validate() ordering" not in text, f"duplicated exploration line; got:\n{text}"

    # Snake_case tool events from older logs render canonically.
    assert "Read(src/legacy.py)" in text, f"snake_case tool not aliased; got:\n{text}"
    assert "read_file({" not in text, f"raw tool JSON rendered; got:\n{text}"

    # Tool-call markup the model leaked as text is stripped.
    assert "<function=" not in text, f"leaked function markup; got:\n{text}"
    assert "tool_call" not in text, f"leaked tool_call tag; got:\n{text}"
    assert "<parameter=" not in text, f"leaked parameter tag; got:\n{text}"

    # Behaves like a terminal: scrollable and bottom-anchored on load.
    assert metrics["sh"] >= metrics["ch"], f"transcript not laid out: {metrics}"
    distance_from_bottom = metrics["sh"] - metrics["st"] - metrics["ch"]
    assert distance_from_bottom <= 32, f"transcript not anchored to bottom: {metrics}"

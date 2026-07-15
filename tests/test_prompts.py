"""Tests for the shared agent prompts.

These guard the task-scaling behaviour observed to fail in the field:
- information requests (e.g. "summarise this project") must not trigger
  test-writing, package installs, or verification runs
- the final JSON summary must carry the actual answer for information
  requests, so the UI's "Coding Complete" bubble shows the deliverable
- verification results must be reported honestly (no fabricated all-clears)
"""

from chad.util.prompts import (
    CODING_AGENT_PROMPT,
    SUMMARY_COMPLETION_PROMPT,
    build_prompt,
)


class TestCodingPromptTaskScaling:
    """The coding prompt must scale effort to the task type."""

    def test_info_requests_skip_tests_and_verification(self):
        """Questions/summaries must not require tests or verification runs."""
        prompt = build_prompt("Summarise this project in one sentence")
        assert "information request" in prompt, (
            "prompt must branch on whether the task requires changing files"
        )
        lowered = prompt.lower()
        assert "do not write tests" in lowered
        assert "do not install" in lowered
        assert "do not run verification" in lowered

    def test_change_summary_must_contain_the_answer(self):
        """For info requests the JSON summary must BE the answer, not describe it."""
        assert "the answer itself" in CODING_AGENT_PROMPT, (
            "change_summary must carry the actual answer for information requests"
        )

    def test_verification_reporting_must_be_honest(self):
        """The agent must never claim verification success it did not observe."""
        lowered = CODING_AGENT_PROMPT.lower()
        assert "never claim" in lowered, (
            "prompt must forbid fabricated verification all-clears"
        )
        assert '"partial"' in CODING_AGENT_PROMPT

    def test_summary_completion_prompt_requires_answer_for_info_requests(self):
        """The re-ask prompt keeps the answer-in-summary requirement."""
        assert "the answer itself" in SUMMARY_COMPLETION_PROMPT

    def test_prompt_still_requires_tests_for_code_changes(self):
        """Code-changing tasks keep the test-first requirement."""
        prompt = build_prompt("Fix the login bug")
        assert "should fail until" in prompt
        assert "```json" in prompt

    def test_exploration_results_are_findings_not_narration(self):
        """EXPLORATION_RESULT is for what was learned, never step narration.

        Regression: the old 'at least every 20 seconds, heartbeat if no finding'
        protocol made models prefix every 'Let me read X' with the marker,
        flooding the chat panel with Discovery bubbles.
        """
        assert "every 20 seconds" not in CODING_AGENT_PROMPT
        assert "heartbeat" not in CODING_AGENT_PROMPT.lower()
        lowered = CODING_AGENT_PROMPT.lower()
        assert "what you learned" in lowered
        assert "not what you are about to do" in lowered

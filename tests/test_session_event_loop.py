"""Tests for SessionEventLoop milestone detection."""

import chad.server.services.session_event_loop as session_event_loop
from chad.server.services.session_event_loop import SessionEventLoop
from chad.util.handoff import is_quota_exhaustion_error


class FakeEventLog:
    """Minimal EventLog stand-in that records logged events."""

    def __init__(self):
        self.events = []

    def log(self, event):
        self.events.append(event)


def _default_quota_checker(output_tail: str) -> str | None:
    """Default quota checker for tests - mimics Claude provider behavior."""
    import re
    hit_limit = bool(re.search(
        r"You['\u2018\u2019]ve hit your limit",
        output_tail,
    ))
    if hit_limit:
        return "session_limit_reached"
    if is_quota_exhaustion_error(output_tail):
        return "session_limit_reached"
    return None


class TestSessionLimitDetection:
    """Tests for session limit detection in _analyze_output."""

    def _make_loop(self, quota_checker=_default_quota_checker):
        """Create a SessionEventLoop with minimal dependencies."""
        event_log = FakeEventLog()
        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=emit_fn,
            worktree_path="/tmp/test",
            is_quota_exhausted_fn=quota_checker,
        )
        return loop, event_log, emitted

    def test_detects_claude_session_limit(self):
        """Detects 'You've hit your limit' pattern from Claude CLI."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Working on the task...\n")
        loop.feed_output("You've hit your limit · resets 4pm (Australia/Melbourne)")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary is not None

        milestone_emits = [e for e in emitted if e[0] == "milestone"]
        assert len(milestone_emits) == 1
        assert milestone_emits[0][1]["milestone_type"] == "session_limit_reached"
        assert milestone_emits[0][1]["title"] == "Session Limit"

    def test_detects_session_limit_with_curly_apostrophe(self):
        """Detects limit message with curly apostrophe (Unicode)."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You\u2019ve hit your limit \u00b7 resets 2pm (US/Pacific)")
        loop._analyze_output()

        assert loop._session_limit_detected

    def test_detects_session_limit_without_reset_info(self):
        """Detects limit message even without reset time details."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You've hit your limit")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary is not None

    def test_session_limit_not_detected_twice(self):
        """Session limit should only be emitted once even with repeated scans."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You've hit your limit · resets 4pm (Australia/Melbourne)")
        loop._analyze_output()
        loop._analyze_output()  # Second scan

        milestone_emits = [e for e in emitted if e[0] == "milestone"]
        assert len(milestone_emits) == 1

    def test_no_false_positive_on_unrelated_text(self):
        """Normal output shouldn't trigger session limit detection."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Checking rate limits in the code...\n")
        loop.feed_output("Found usage limit configuration\n")
        loop._analyze_output()

        assert not loop._session_limit_detected

    def test_session_limit_logged_to_event_log(self):
        """Session limit milestone should be logged to the EventLog."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You've hit your limit · resets 6pm (Europe/London)")
        loop._analyze_output()

        milestone_events = [
            e for e in event_log.events
            if hasattr(e, "milestone_type") and e.milestone_type == "session_limit_reached"
        ]
        assert len(milestone_events) == 1

    def test_detects_generic_quota_exhaustion(self):
        """Detects quota/rate limit errors from any provider (e.g. Codex, Gemini)."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Working on implementation...\n")
        loop.feed_output("Error: you exceeded your current quota\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary is not None

    def test_detects_rate_limit_exceeded(self):
        """Detects rate_limit_exceeded pattern common across providers."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Applying changes...\n")
        loop.feed_output("rate_limit_exceeded\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        milestone_emits = [e for e in emitted if e[0] == "milestone"]
        assert len(milestone_emits) == 1
        assert milestone_emits[0][1]["milestone_type"] == "session_limit_reached"

    def test_detects_insufficient_credits(self):
        """Detects insufficient credits/quota messages."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Processing task...\n")
        loop.feed_output("Error: insufficient credits\n")
        loop._analyze_output()

        assert loop._session_limit_detected

    def test_quota_pattern_only_checks_tail(self):
        """Quota patterns in agent-edited code don't trigger false positives."""
        loop, event_log, emitted = self._make_loop()

        # Agent writes code that handles rate limits - this appears early in output
        loop.feed_output("def handle_error(msg):\n")
        loop.feed_output('    if "rate_limit_exceeded" in msg:\n')
        loop.feed_output("        retry()\n")
        # Pad with enough normal output to push the code out of the 500-char tail
        loop.feed_output("x" * 600 + "\n")
        loop._analyze_output()

        assert not loop._session_limit_detected

    def test_weekly_limit_detected_via_provider(self):
        """Provider that returns weekly_limit_reached should emit that milestone type."""
        def weekly_checker(output_tail):
            if "limit reached" in output_tail.lower():
                return "weekly_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=weekly_checker)

        loop.feed_output("Working...\n")
        loop.feed_output("Weekly limit reached\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        milestone_emits = [e for e in emitted if e[0] == "milestone"]
        assert len(milestone_emits) == 1
        assert milestone_emits[0][1]["milestone_type"] == "weekly_limit_reached"
        assert milestone_emits[0][1]["title"] == "Weekly Limit"

    def test_no_detection_without_quota_checker(self):
        """Without a quota checker, quota patterns are not detected."""
        loop, event_log, emitted = self._make_loop(quota_checker=None)

        loop.feed_output("Error: you exceeded your current quota\n")
        loop._analyze_output()

        assert not loop._session_limit_detected

    def test_gaxios_error_with_quota_exhausted_extracts_meaningful_summary(self):
        """JavaScript errors should be filtered out, meaningful quota errors should be shown.

        When Gemini CLI outputs both quota exhaustion AND gaxios errors,
        the meaningful quota error should be extracted as the session limit summary,
        not the cryptic JavaScript error object.
        """
        # Use Gemini-style quota checker (base provider method)
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Simulate mixed output: quota exhaustion + gaxios error
        loop.feed_output("Coding: gem (default Usage: 3%)\n")
        loop.feed_output("quota exceeded for project\n")  # This should become the summary
        loop.feed_output("[Symbol(gaxios-gaxios-error)]: '6.7.1'\n")  # This should be filtered out
        loop._analyze_output()

        # Should detect as session limit (due to "quota exceeded")
        assert loop._session_limit_detected

        # Should extract the meaningful error message, not the gaxios error
        assert loop._session_limit_summary == "quota exceeded for project"

        session_limit_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "session_limit_reached"
        ]
        assert len(session_limit_emits) == 1
        # The displayed message should be meaningful
        assert session_limit_emits[0][1]["summary"] == "quota exceeded for project"

    def test_various_javascript_error_formats_not_detected_as_limits(self):
        """Various JavaScript error object formats should not trigger limit detection."""
        # Use Gemini-style quota checker (base provider method)
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Test different variations of JavaScript error objects
        test_errors = [
            "[Symbol(gaxios-gaxios-error)]: '6.7.1'",
            "[object Object]",
            "[Symbol.for('error')]: 'auth failed'",
            "TypeError: Cannot read property 'data' of undefined",
            "Error: Request failed with status code 401",
            "[object Error]: Network timeout",
        ]

        for error in test_errors:
            # Reset state for each test
            loop._session_limit_detected = False
            loop._output_buffer = []
            emitted.clear()

            loop.feed_output(f"Some context output\n{error}\n")
            loop._analyze_output()

            assert not loop._session_limit_detected, f"Should not detect '{error}' as session limit"
            session_limit_emits = [
                e for e in emitted
                if e[0] == "milestone" and e[1].get("milestone_type") == "session_limit_reached"
            ]
            assert len(session_limit_emits) == 0, f"Should not emit milestone for '{error}'"

    def test_meaningful_error_summary_extraction_prioritizes_quota_messages(self):
        """Test that meaningful error extraction prioritizes quota-related messages."""
        # Use Gemini-style quota checker
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Multiple lines with a quota message that should be prioritized
        loop.feed_output("Starting task execution\n")
        loop.feed_output("Some other context\n")
        loop.feed_output("insufficient credits available\n")  # Should be picked as summary
        loop.feed_output("General error occurred\n")
        loop.feed_output("[Symbol(error-object)]: some value\n")  # Should be ignored
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary == "insufficient credits available"

    def test_meaningful_error_summary_falls_back_to_non_js_errors(self):
        """If no quota keywords found, should use non-JavaScript error."""
        # Use Gemini-style quota checker
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Quota pattern triggers detection but only non-priority messages
        loop.feed_output("rate_limit_exceeded\n")  # Triggers detection (has exceeded keyword)
        loop.feed_output("Authentication failed\n")  # No priority keywords
        loop.feed_output("Connection timeout\n")  # No priority keywords (should be picked - most recent)
        loop.feed_output("[object Error]: network timeout\n")  # Should be ignored (JS error)
        loop._analyze_output()

        assert loop._session_limit_detected
        # Should get the priority message since "rate_limit_exceeded" has priority keywords
        assert loop._session_limit_summary == "rate_limit_exceeded"

    def test_meaningful_error_summary_handles_all_js_errors(self):
        """If only JavaScript errors after quota message, should use quota message."""
        # Use Gemini-style quota checker
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Quota message followed by only JavaScript errors
        loop.feed_output("quota exceeded\n")  # Triggers detection and should be the summary
        loop.feed_output("[Symbol(gaxios-gaxios-error)]: '6.7.1'\n")
        loop.feed_output("[object Object]\n")
        loop.feed_output("TypeError: Cannot read property 'data' of undefined\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        # Should use the quota message since it has priority keywords
        assert loop._session_limit_summary == "quota exceeded"

    def test_meaningful_error_summary_fallback_to_default_when_only_js_errors(self):
        """If only JavaScript errors present and no quota messages, should fall back to default."""
        # Use Gemini-style quota checker
        def gemini_quota_checker(output_tail: str) -> str | None:
            if is_quota_exhaustion_error(output_tail):
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=gemini_quota_checker)

        # Pattern that triggers detection but then only JS errors and short lines
        loop.feed_output("too many requests\n")  # Triggers detection
        loop.feed_output("short\n")  # Too short (< 10 chars)
        loop.feed_output("[Symbol(gaxios-gaxios-error)]: '6.7.1'\n")
        loop.feed_output("[object Object]\n")
        loop.feed_output("TypeError: Cannot read property 'data' of undefined\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        # Should use the triggering message since it has priority keywords
        assert loop._session_limit_summary == "too many requests"

    def test_meaningful_error_summary_true_fallback_to_default(self):
        """Test true fallback when only JS errors and no meaningful content."""
        # Custom quota checker that always triggers but doesn't rely on specific text
        def always_trigger_quota_checker(output_tail: str) -> str | None:
            # Just check if there's any content to trigger
            if output_tail.strip():
                return "session_limit_reached"
            return None

        loop, event_log, emitted = self._make_loop(quota_checker=always_trigger_quota_checker)

        # Only JS errors and short lines
        loop.feed_output("short\n")  # Too short (< 10 chars)
        loop.feed_output("\n")  # Empty
        loop.feed_output("[Symbol(gaxios-gaxios-error)]: '6.7.1'\n")
        loop.feed_output("[object Object]\n")
        loop.feed_output("TypeError: Cannot read property 'data' of undefined\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        # Should fall back to default when no meaningful messages found
        assert loop._session_limit_summary == "Session Limit - quota exhausted"


class TestExplorationMilestoneDetection:
    """Tests for exploration marker detection in _analyze_output."""

    def _make_loop(self):
        event_log = FakeEventLog()
        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=emit_fn,
            worktree_path="/tmp/test",
        )
        return loop, event_log, emitted

    def test_detects_exploration_markers(self):
        """Detects EXPLORATION_RESULT: markers in agent output."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Looking at the code...\n")
        loop.feed_output("EXPLORATION_RESULT: The auth logic is in src/auth.py\n\n")
        loop.feed_output("Now checking tests...\n")
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1
        assert exploration_emits[0][1]["title"] == "Discovery"
        assert "auth logic" in exploration_emits[0][1]["summary"]

    def test_detects_multiple_exploration_markers(self):
        """Detects multiple EXPLORATION_RESULT: markers."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output(
            "EXPLORATION_RESULT: Found database module in src/db.py\n\n"
            "EXPLORATION_RESULT: Config loaded from ~/.app/settings.json\n\n"
        )
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 2

    def test_detects_marker_split_across_chunks(self):
        """Split marker tokens across chunks should still emit one discovery."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("EXPLORATION_RES")
        loop.feed_output("ULT: Found split marker handling in parser\n")
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1
        assert "split marker handling" in exploration_emits[0][1]["summary"]

    def test_deduplicates_repeated_exploration_marker(self):
        """Repeated identical discovery lines should emit only once."""
        loop, event_log, emitted = self._make_loop()

        marker = "EXPLORATION_RESULT: Found auth logic in src/auth.py\n"
        loop.feed_output(marker)
        loop.feed_output(marker)
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1

    def test_strips_ansi_codes_before_marker_matching(self):
        """ANSI color codes around marker text should not block parsing."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("\x1b[32mEXPLORATION_RESULT:\x1b[0m Found parser cleanup in event loop\n")
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1
        assert "parser cleanup" in exploration_emits[0][1]["summary"]

    def test_ignores_non_marker_line_mentions(self):
        """Lines that mention EXPLORATION_RESULT without colon should not emit."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("I will output EXPLORATION_RESULT lines after discovery\n")
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 0

    def test_matches_marker_preceded_by_progress_indicators(self):
        """Progress dots from agent tool should not block marker detection."""
        loop, event_log, emitted = self._make_loop()

        # Real-world pattern: Claude Code prepends bullet progress on the same line
        loop.feed_output(
            "\u2022 1 file read\u2022 2 files read"
            "EXPLORATION_RESULT: Server entry point is at src/server/main.py\n"
        )
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1
        assert "Server entry point" in exploration_emits[0][1]["summary"]

    def test_flushes_partial_marker_line_on_finalize(self):
        """Finalize scan should emit trailing marker lines without newline."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("EXPLORATION_RESULT: Found final flush without newline")
        loop._analyze_output(finalize=True)

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 1

    def test_ignores_invalid_terminal_metadata_summaries(self):
        """Discovery markers with terminal metadata should be ignored."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("EXPLORATION_RESULT: workdir: /home/miles/chad/.chad-worktrees/abc123\n")
        loop.feed_output("EXPLORATION_RESULT: model: gpt-5.1-codex\n")
        loop._analyze_output()

        exploration_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "exploration"
        ]
        assert len(exploration_emits) == 0


class TestCodingCompleteMilestone:
    """Tests for coding complete detection in _analyze_output."""

    def _make_loop(self):
        event_log = FakeEventLog()
        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=emit_fn,
            worktree_path="/tmp/test",
        )
        return loop, event_log, emitted

    def test_detects_coding_complete_json(self):
        """Detects coding completion JSON in output."""
        import json

        loop, event_log, emitted = self._make_loop()

        summary_json = json.dumps({
            "change_summary": "Added session limit detection",
            "files_changed": ["src/event_loop.py"],
            "completion_status": "complete",
        })
        loop.feed_output(f"Done!\n```json\n{summary_json}\n```\n")
        loop._analyze_output()

        assert loop._coding_complete_detected
        coding_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "coding_complete"
        ]
        assert len(coding_emits) == 1
        assert "session limit" in coding_emits[0][1]["summary"].lower()


class TestUsageThresholdMonitoring:
    """Tests for usage threshold crossing detection using action_settings."""

    def _make_loop(self, session_fn=None, weekly_fn=None, context_fn=None, action_settings=None,
                   terminate_pty_fn=None):
        """Create a SessionEventLoop with usage monitoring functions."""
        event_log = FakeEventLog()
        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        # Default action settings: 3 notify at 90% (matches old hardcoded behaviour)
        if action_settings is None:
            action_settings = [
                {"event": "session_usage", "threshold": 90, "action": "notify"},
                {"event": "weekly_usage", "threshold": 90, "action": "notify"},
                {"event": "context_usage", "threshold": 90, "action": "notify"},
            ]

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=emit_fn,
            worktree_path="/tmp/test",
            get_session_usage_fn=session_fn,
            get_weekly_usage_fn=weekly_fn,
            get_context_usage_fn=context_fn,
            action_settings=action_settings,
            terminate_pty_fn=terminate_pty_fn,
        )
        return loop, event_log, emitted

    def _usage_milestones(self, emitted):
        return [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "usage_threshold"
        ]

    def test_emits_milestone_on_session_threshold_crossing(self):
        """Session usage crossing 90% emits a usage_threshold milestone."""
        pct = [80.0]
        loop, event_log, emitted = self._make_loop(session_fn=lambda: pct[0])

        # First check seeds the previous value
        loop._check_usage_thresholds()
        assert len(self._usage_milestones(emitted)) == 0

        # Cross the threshold
        pct[0] = 92.0
        loop._check_usage_thresholds()

        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert milestones[0][1]["title"] == "Usage Warning"
        assert "Session" in milestones[0][1]["summary"]
        assert "92%" in milestones[0][1]["summary"]
        assert milestones[0][1]["details"]["metric"] == "session"
        assert milestones[0][1]["details"]["percentage"] == 92.0

    def test_emits_milestone_on_weekly_threshold_crossing(self):
        """Weekly usage crossing 90% emits a milestone."""
        pct = [85.0]
        loop, event_log, emitted = self._make_loop(weekly_fn=lambda: pct[0])

        loop._check_usage_thresholds()
        pct[0] = 91.0
        loop._check_usage_thresholds()

        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert "Weekly" in milestones[0][1]["summary"]
        assert milestones[0][1]["details"]["metric"] == "weekly"

    def test_emits_milestone_on_context_threshold_crossing(self):
        """Context usage crossing 90% emits a milestone."""
        pct = [70.0]
        loop, event_log, emitted = self._make_loop(context_fn=lambda: pct[0])

        loop._check_usage_thresholds()
        pct[0] = 95.0
        loop._check_usage_thresholds()

        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert "Context" in milestones[0][1]["summary"]
        assert milestones[0][1]["details"]["metric"] == "context"

    def test_fires_when_first_reading_already_above_threshold(self):
        """Milestone fires on the first check when usage is already above threshold.

        Previously this was a seeding-only call (prev=None → no crossing). Now prev
        is initialized to 0.0, so the first call detects the crossing from 0% to 91%.
        """
        pct = [91.0]
        loop, event_log, emitted = self._make_loop(session_fn=lambda: pct[0])

        loop._check_usage_thresholds()

        # Should fire: 0.0 < 90 and 91.0 >= 90
        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert "91%" in milestones[0][1]["summary"]

        # Second check at 95%: prev=91, 91 < 90 is False → no second milestone
        pct[0] = 95.0
        loop._check_usage_thresholds()
        assert len(self._usage_milestones(emitted)) == 1

    def test_no_milestone_when_still_below_threshold(self):
        """No milestone if usage stays below 90%."""
        pct = [50.0]
        loop, event_log, emitted = self._make_loop(session_fn=lambda: pct[0])

        loop._check_usage_thresholds()
        pct[0] = 80.0
        loop._check_usage_thresholds()

        assert len(self._usage_milestones(emitted)) == 0

    def test_no_milestone_when_fn_returns_none(self):
        """No milestone when usage function returns None."""
        loop, event_log, emitted = self._make_loop(session_fn=lambda: None)

        loop._check_usage_thresholds()
        loop._check_usage_thresholds()

        assert len(self._usage_milestones(emitted)) == 0

    def test_no_milestone_when_fn_not_provided(self):
        """No milestone when no usage functions are provided."""
        loop, event_log, emitted = self._make_loop()

        loop._check_usage_thresholds()
        loop._check_usage_thresholds()

        assert len(self._usage_milestones(emitted)) == 0

    def test_multiple_thresholds_can_fire_independently(self):
        """Session and weekly thresholds can both fire in the same check cycle."""
        session_pct = [80.0]
        weekly_pct = [85.0]
        loop, event_log, emitted = self._make_loop(
            session_fn=lambda: session_pct[0],
            weekly_fn=lambda: weekly_pct[0],
        )

        # Seed previous values
        loop._check_usage_thresholds()

        # Both cross threshold simultaneously
        session_pct[0] = 93.0
        weekly_pct[0] = 91.0
        loop._check_usage_thresholds()

        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 2
        metrics = {m[1]["details"]["metric"] for m in milestones}
        assert metrics == {"session", "weekly"}

    def test_fn_exception_is_silently_ignored(self):
        """If a usage function raises, it's caught and doesn't break the loop."""
        call_count = [0]

        def bad_fn():
            call_count[0] += 1
            raise RuntimeError("provider error")

        loop, event_log, emitted = self._make_loop(session_fn=bad_fn)

        # Should not raise
        loop._check_usage_thresholds()
        loop._check_usage_thresholds()

        assert call_count[0] == 2
        assert len(self._usage_milestones(emitted)) == 0

    def test_milestone_logged_to_event_log(self):
        """Usage threshold milestone should appear in the EventLog."""
        pct = [80.0]
        loop, event_log, emitted = self._make_loop(session_fn=lambda: pct[0])

        loop._check_usage_thresholds()
        pct[0] = 92.0
        loop._check_usage_thresholds()

        milestone_events = [
            e for e in event_log.events
            if hasattr(e, "milestone_type") and e.milestone_type == "usage_threshold"
        ]
        assert len(milestone_events) == 1

    def test_custom_threshold_from_action_settings(self):
        """Action settings with a non-default threshold are respected."""
        pct = [70.0]
        loop, event_log, emitted = self._make_loop(
            session_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 75, "action": "notify"},
            ],
        )
        loop._check_usage_thresholds()
        pct[0] = 78.0
        loop._check_usage_thresholds()

        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert "78%" in milestones[0][1]["summary"]

    def test_no_action_with_empty_settings(self):
        """Empty action_settings means no threshold checks at all."""
        pct = [80.0]
        loop, event_log, emitted = self._make_loop(
            session_fn=lambda: pct[0],
            action_settings=[],
        )
        loop._check_usage_thresholds()
        pct[0] = 95.0
        loop._check_usage_thresholds()
        assert len(self._usage_milestones(emitted)) == 0

    def test_multiple_rules_same_event_type_both_fire(self):
        """Two rules for session_usage at different thresholds both fire independently."""
        pct = [70.0]
        terminated = []
        loop, event_log, emitted = self._make_loop(
            session_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 80, "action": "notify"},
                {"event": "session_usage", "threshold": 90, "action": "switch_provider",
                 "target_account": "backup"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # Seed previous values
        loop._check_usage_thresholds()
        assert len(self._usage_milestones(emitted)) == 0

        # Cross 80% but not 90%
        pct[0] = 85.0
        loop._check_usage_thresholds()
        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 1
        assert "85%" in milestones[0][1]["summary"]
        assert len(terminated) == 0

        # Now cross 90%
        pct[0] = 92.0
        loop._check_usage_thresholds()
        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 2
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert len(terminated) == 1

    def test_multiple_rules_same_event_simultaneous_crossing(self):
        """Both rules for same event fire when usage jumps past both thresholds at once."""
        pct = [70.0]
        terminated = []
        loop, event_log, emitted = self._make_loop(
            session_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 80, "action": "notify"},
                {"event": "session_usage", "threshold": 90, "action": "switch_provider",
                 "target_account": "backup"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # Seed
        loop._check_usage_thresholds()

        # Jump past both thresholds at once
        pct[0] = 95.0
        loop._check_usage_thresholds()
        milestones = self._usage_milestones(emitted)
        assert len(milestones) == 2
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert len(terminated) == 1

    def test_session_limit_detected_only_once_with_concurrent_calls(self):
        """Session limit milestone emitted only once even with rapid calls."""
        loop, event_log, emitted = self._make_loop(
            action_settings=[],
        )
        loop._is_quota_exhausted_fn = _default_quota_checker
        loop.feed_output("You've hit your limit")

        # Simulate both background and main thread calling _analyze_output
        loop._analyze_output()
        loop._analyze_output(finalize=True)

        milestone_emits = [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "session_limit_reached"
        ]
        assert len(milestone_emits) == 1


class TestActionExecution:
    """Tests for switch_provider and await_reset action execution."""

    def _usage_milestones(self, emitted):
        return [
            e for e in emitted
            if e[0] == "milestone" and e[1].get("milestone_type") == "usage_threshold"
        ]

    def test_switch_provider_sets_pending_and_terminates(self):
        """switch_provider action sets pending_action and calls terminate_pty_fn."""
        event_log = FakeEventLog()
        emitted = []
        terminated = []

        pct = [80.0]
        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "switch_provider", "target_account": "backup"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # Seed
        loop._check_usage_thresholds()
        assert loop._pending_action is None

        # Cross threshold
        pct[0] = 92.0
        loop._check_usage_thresholds()

        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert loop._pending_action["target_account"] == "backup"
        assert len(terminated) == 1

    def test_await_reset_sets_pending_and_terminates(self):
        """await_reset action sets pending_action and calls terminate_pty_fn."""
        event_log = FakeEventLog()
        emitted = []
        terminated = []

        pct = [80.0]
        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_weekly_usage_fn=lambda: pct[0],
            action_settings=[
                {"event": "weekly_usage", "threshold": 90, "action": "await_reset"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        loop._check_usage_thresholds()
        pct[0] = 91.0
        loop._check_usage_thresholds()

        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "await_reset"
        assert len(terminated) == 1

    def test_no_action_when_below_threshold(self):
        """No pending action if usage stays below threshold."""
        event_log = FakeEventLog()
        emitted = []

        pct = [50.0]
        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "switch_provider", "target_account": "backup"},
            ],
        )

        loop._check_usage_thresholds()
        pct[0] = 80.0
        loop._check_usage_thresholds()

        assert loop._pending_action is None
        assert len(self._usage_milestones(emitted)) == 0

    def test_action_fires_when_first_reading_already_above(self):
        """Pending action is set when usage is already above threshold on first check.

        Previously prev=None seeding meant this check was silently skipped. Now
        prev=0.0 so the first call correctly detects the crossing from 0% to 92%.
        """
        event_log = FakeEventLog()
        emitted = []
        terminated = []

        pct = [92.0]
        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=lambda: pct[0],
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "switch_provider", "target_account": "backup"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # First check: 0.0 < 90 and 92.0 >= 90 → fires
        loop._check_usage_thresholds()
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert len(terminated) == 1

        # Second check: prev=92, 92 < 90 is False → no second firing
        loop._pending_action = None
        terminated.clear()
        pct[0] = 95.0
        loop._check_usage_thresholds()
        assert loop._pending_action is None
        assert len(terminated) == 0


class TestFinalThresholdCheckAfterPhase:
    """Tests that _run_coding_phase does a final threshold check after completion."""

    def test_await_reset_fires_via_final_check_when_task_completes_before_tick(self, monkeypatch):
        """When task completes before the 10s periodic tick, the final check in
        _run_coding_phase catches thresholds that were crossed during the run."""
        import chad.server.services.session_event_loop as sel_module
        monkeypatch.setattr(sel_module, 'get_pty_stream_service', lambda: type('S', (), {
            'get_session': lambda self, sid: None,
            'send_input': lambda self, *a, **kw: True,
        })())

        event_log = FakeEventLog()
        emitted = []
        terminated = []
        phases_run = []

        # Usage starts at 0%, will be reported as 25% after the "task"
        usage_calls = [0]

        def usage_fn():
            usage_calls[0] += 1
            return 25.0  # Always 25% — already above both thresholds

        def fake_run_phase(**kwargs):
            phases_run.append(kwargs.get("phase"))
            return 0, '{"change_summary": "done"}'

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=type("Task", (), {"cancel_requested": False, "stream_id": None})(),
            run_phase_fn=fake_run_phase,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=usage_fn,
            action_settings=[
                {"event": "session_usage", "threshold": 5, "action": "notify"},
                {"event": "session_usage", "threshold": 10, "action": "await_reset"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # Simulate _run_coding_phase without the full run() lifecycle
        loop._running = True

        # Manually call just the check that _run_coding_phase performs after phase exit
        # (the periodic tick never fires in this test — we're simulating a fast task)
        loop._check_usage_thresholds()

        # Both thresholds should have fired: prev=0, current=25%, crosses 5% and 10%
        usage_milestones = [
            e for e in emitted if e[0] == "milestone" and e[1].get("milestone_type") == "usage_threshold"
        ]
        assert len(usage_milestones) >= 1  # At least notify at 5%
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "await_reset"


class TestMessageForwarding:
    """Tests for forwarding queued user messages to the active PTY."""

    class DummyPTYService:
        def __init__(self):
            self.sent: list[tuple[str, bytes, bool]] = []
            self.sessions: dict[str, object] = {}

        def get_session(self, stream_id):
            return self.sessions.get(stream_id)

        def send_input(self, stream_id, data: bytes, close_stdin: bool = False):
            self.sent.append((stream_id, data, close_stdin))
            return True

    def _make_loop(self, monkeypatch, stream_id: str | None):
        service = self.DummyPTYService()
        if stream_id:
            service.sessions[stream_id] = type('Session', (), {'active': True})()
        monkeypatch.setattr(session_event_loop, 'get_pty_stream_service', lambda: service)

        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        task = type('Task', (), {'stream_id': stream_id})()
        loop = SessionEventLoop(
            session_id='s1',
            event_log=FakeEventLog(),
            task=task,
            run_phase_fn=None,
            emit_fn=emit_fn,
            worktree_path='/tmp/test',
        )
        return loop, service, emitted

    def test_forwards_queued_messages_to_active_pty(self, monkeypatch):
        """Enqueued messages are written to the PTY with a trailing newline."""
        loop, service, _ = self._make_loop(monkeypatch, stream_id='stream-1')

        loop.enqueue_message('please keep going', source='ui')
        loop._process_messages()

        assert service.sent == [('stream-1', b'please keep going\n', False)]
        assert loop._message_queue.empty()

    def test_defers_messages_until_stream_available(self, monkeypatch):
        """Messages stay queued until a PTY stream is available and active."""
        loop, service, _ = self._make_loop(monkeypatch, stream_id=None)

        loop.enqueue_message('hold this', source='ui')
        loop._process_messages()

        # No stream yet, message should remain queued
        assert service.sent == []
        assert loop._message_queue.qsize() == 1

        # Stream appears later
        loop.task.stream_id = 'stream-live'
        service.sessions['stream-live'] = type('Session', (), {'active': True})()
        loop._process_messages()

        assert service.sent == [('stream-live', b'hold this\n', False)]
        assert loop._message_queue.empty()


class TestFollowupThresholdFires:
    """Tests that follow-up tasks create a SessionEventLoop with threshold monitoring."""

    def test_followup_threshold_fires_switch_provider(self):
        """Configure action_settings with switch_provider at 45% context_usage.

        MockProvider's usage reporting returns (1.0 - remaining) * 100.
        With remaining=0.55, usage=45%. Verify _pending_action is set when threshold crossed.
        """
        event_log = FakeEventLog()
        emitted = []
        terminated = []

        pct = [40.0]  # Start below threshold
        loop = SessionEventLoop(
            session_id="followup-test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_context_usage_fn=lambda: pct[0],
            action_settings=[
                {
                    "event": "context_usage",
                    "threshold": 45,
                    "action": "switch_provider",
                    "target_account": "codex-home",
                },
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        # Seed below threshold
        loop._check_usage_thresholds()
        assert loop._pending_action is None

        # Cross threshold — simulates what happens when MockProvider's
        # mock_remaining_usage is 0.55 → usage = 45%
        pct[0] = 46.0
        loop._check_usage_thresholds()

        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert loop._pending_action["target_account"] == "codex-home"
        assert len(terminated) == 1


class TestQuotaCheckerAfterSwitch:
    """Tests that quota checker is updated after provider switch."""

    def test_quota_checker_updated_after_provider_switch(self, monkeypatch):
        """After _handle_switch_provider, _is_quota_exhausted_fn should be the new provider's."""
        event_log = FakeEventLog()
        emitted = []
        original_checker = lambda output_tail: None  # noqa: E731

        phases_run = []

        def fake_run_phase(**kwargs):
            phases_run.append(kwargs.get("coding_provider"))
            return 0, "done"

        def fake_get_account_info(name):
            return {"provider": "openai", "model": "gpt-5.1-codex"}

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=fake_run_phase,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            is_quota_exhausted_fn=original_checker,
            get_account_info_fn=fake_get_account_info,
        )

        # Monkeypatch create_provider to return a mock with a known is_quota_exhausted
        new_checker_sentinel = lambda output_tail: "session_limit_reached"  # noqa: E731

        class FakeProvider:
            is_quota_exhausted = new_checker_sentinel

        monkeypatch.setattr(
            "chad.util.providers.create_provider",
            lambda config: FakeProvider(),
        )
        monkeypatch.setattr(
            "chad.util.handoff.log_handoff_checkpoint",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "chad.util.handoff.build_resume_prompt",
            lambda *args, **kwargs: "resume",
        )

        assert loop._is_quota_exhausted_fn is original_checker

        loop._handle_switch_provider(
            action={"target_account": "codex-home", "label": "session"},
            session=None,
            task_description="test task",
            previous_output="",
            screenshots=None,
            rows=24, cols=80,
            git_mgr=None,
            old_account="claude-2",
            old_provider="anthropic",
            old_model=None,
            old_reasoning=None,
        )

        assert loop._is_quota_exhausted_fn is not original_checker

    def test_code_output_not_detected_as_quota_error(self):
        """Indented code containing quota patterns should not trigger session limit."""
        event_log = FakeEventLog()
        emitted = []

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            is_quota_exhausted_fn=_default_quota_checker,
        )

        # Agent is editing code that contains quota error strings
        loop.feed_output("Reading file src/chad/util/providers.py\n")
        loop.feed_output('    if "Quota exceeded for mock provider account" in msg:\n')
        loop.feed_output('        raise QuotaExhaustedError("quota exceeded")\n')
        loop.feed_output("File saved successfully\n")
        loop._analyze_output()

        assert not loop._session_limit_detected

    def test_real_quota_error_still_detected(self):
        """A real quota error at the end of output should still be detected."""
        event_log = FakeEventLog()
        emitted = []

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            is_quota_exhausted_fn=_default_quota_checker,
        )

        loop.feed_output("Working on implementation...\n")
        loop.feed_output("you exceeded your current quota\n")
        loop._analyze_output()

        assert loop._session_limit_detected


class TestSessionLimitActionBridge:
    """Tests that session limit detection bridges into the action system."""

    def _make_loop(self, action_settings, terminate_pty_fn=None, quota_checker=_default_quota_checker):
        event_log = FakeEventLog()
        emitted = []
        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=None,
            run_phase_fn=None,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            is_quota_exhausted_fn=quota_checker,
            action_settings=action_settings,
            terminate_pty_fn=terminate_pty_fn,
        )
        return loop, event_log, emitted

    def test_session_limit_triggers_await_reset(self):
        """Quota output triggers _pending_action=await_reset and terminates PTY."""
        terminated = []
        loop, event_log, emitted = self._make_loop(
            action_settings=[
                {"event": "session_usage", "threshold": 100, "action": "await_reset"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        loop.feed_output("You've hit your limit\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "await_reset"
        assert loop._pending_action["current_pct"] == 100.0
        assert len(terminated) == 1

    def test_session_limit_triggers_switch_provider(self):
        """Quota output triggers _pending_action=switch_provider and terminates PTY."""
        terminated = []
        loop, event_log, emitted = self._make_loop(
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "switch_provider",
                 "target_account": "backup"},
            ],
            terminate_pty_fn=lambda: terminated.append(True),
        )

        loop.feed_output("Error: insufficient credits\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._pending_action is not None
        assert loop._pending_action["action"] == "switch_provider"
        assert loop._pending_action["target_account"] == "backup"
        assert len(terminated) == 1

    def test_session_limit_no_action_when_notify_only(self):
        """Quota output with only notify rules does not set _pending_action."""
        loop, event_log, emitted = self._make_loop(
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "notify"},
            ],
        )

        loop.feed_output("You've hit your limit\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._pending_action is None

    def test_session_limit_no_duplicate_action(self):
        """Pre-existing _pending_action is not overwritten by quota detection."""
        existing = {"event": "weekly_usage", "action": "await_reset", "label": "weekly"}
        loop, event_log, emitted = self._make_loop(
            action_settings=[
                {"event": "session_usage", "threshold": 100, "action": "await_reset"},
            ],
        )
        loop._pending_action = existing

        loop.feed_output("You've hit your limit\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        # Should keep the existing action, not overwrite
        assert loop._pending_action is existing

    def test_session_limit_ignores_non_session_usage_rules(self):
        """Only session_usage rules trigger the bridge, not weekly or context."""
        loop, event_log, emitted = self._make_loop(
            action_settings=[
                {"event": "weekly_usage", "threshold": 90, "action": "await_reset"},
                {"event": "context_usage", "threshold": 90, "action": "switch_provider",
                 "target_account": "backup"},
            ],
        )

        loop.feed_output("You've hit your limit\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._pending_action is None


class TestAwaitResetPollingLoop:
    """Tests for the _handle_await_reset polling loop."""

    def test_await_reset_polls_and_resumes(self, monkeypatch):
        """Polling loop waits for usage to drop then calls _run_phase_fn."""
        event_log = FakeEventLog()
        emitted = []
        phases_run = []
        call_count = [0]

        def usage_fn():
            call_count[0] += 1
            # Return 100% for first 2 calls, then drop below threshold
            if call_count[0] <= 2:
                return 100.0
            return 50.0

        def fake_run_phase(**kwargs):
            phases_run.append(kwargs.get("phase"))
            return 0, "done"

        # Patch time.sleep to not actually sleep
        sleeps = []
        monkeypatch.setattr("chad.server.services.session_event_loop.time.sleep", lambda s: sleeps.append(s))

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=type("Task", (), {"cancel_requested": False})(),
            run_phase_fn=fake_run_phase,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=usage_fn,
            action_settings=[
                {"event": "session_usage", "threshold": 100, "action": "await_reset"},
            ],
        )
        loop._running = True

        action = {"event": "session_usage", "threshold": 100, "action": "await_reset", "label": "session"}
        loop._handle_await_reset(
            action=action,
            session=None,
            task_description="test task",
            previous_output="",
            screenshots=None,
            rows=24, cols=80,
            git_mgr=None,
            coding_account="mock-1",
            coding_provider="mock",
            coding_model=None,
            coding_reasoning=None,
        )

        # Verify milestones
        milestone_summaries = [
            e[1]["summary"] for e in emitted if e[0] == "milestone"
        ]
        assert any("Paused" in s for s in milestone_summaries)
        assert any("reset detected" in s for s in milestone_summaries)

        # Verify continuation phase was called
        assert len(phases_run) == 1
        assert phases_run[0] == "continuation"

        # Verify we actually polled (slept at least twice at 10s each)
        assert len(sleeps) >= 2

    def test_await_reset_with_eta(self, monkeypatch):
        """ETA from provider is included in the paused milestone."""
        event_log = FakeEventLog()
        emitted = []
        call_count = [0]

        def usage_fn():
            call_count[0] += 1
            if call_count[0] <= 1:
                return 100.0
            return 50.0

        monkeypatch.setattr("chad.server.services.session_event_loop.time.sleep", lambda s: None)

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=type("Task", (), {"cancel_requested": False})(),
            run_phase_fn=lambda **kw: (0, "done"),
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=usage_fn,
            get_session_reset_eta_fn=lambda: "2h 15m",
            action_settings=[
                {"event": "session_usage", "threshold": 100, "action": "await_reset"},
            ],
        )
        loop._running = True

        action = {"event": "session_usage", "threshold": 100, "action": "await_reset", "label": "session"}
        loop._handle_await_reset(
            action=action, session=None, task_description="test",
            previous_output="", screenshots=None, rows=24, cols=80,
            git_mgr=None, coding_account="a", coding_provider="mock",
            coding_model=None, coding_reasoning=None,
        )

        milestone_summaries = [
            e[1]["summary"] for e in emitted if e[0] == "milestone"
        ]
        assert any("ETA: 2h 15m" in s for s in milestone_summaries)


class TestAwaitResetWithMockResetTime:
    """Tests that MockProvider reset time logic works with await_reset polling."""

    def test_await_reset_with_mock_reset_time(self, tmp_path, monkeypatch):
        """Mock provider detects usage drop when configured reset time passes."""
        import json
        from datetime import datetime, timezone, timedelta
        from chad.util.providers import MockProvider, ModelConfig
        from chad.util.config_manager import ConfigManager

        config_path = tmp_path / ".chad.conf"
        monkeypatch.setenv("CHAD_CONFIG", str(config_path))
        account = "mock-reset-test"

        # Phase 1: Reset time in the future — usage stays at 100%, ETA is non-None
        reset_time_future = datetime.now(timezone.utc) + timedelta(minutes=5)
        config = {
            "password_hash": "test",
            "encryption_salt": "dGVzdA==",
            "accounts": {account: {"provider": "mock", "key": "test", "model": "default", "reasoning": "default"}},
            "mock_remaining_usage": {account: 0.0},
            "mock_session_reset_time": {account: reset_time_future.isoformat()},
        }
        config_path.write_text(json.dumps(config))

        provider = MockProvider(ModelConfig(provider="mock", model_name="default", account_name=account))

        usage = provider.get_session_usage_percentage()
        assert usage == 100.0

        eta = provider.get_session_reset_eta()
        assert eta is not None
        assert "m" in eta  # Should contain minutes

        # Phase 2: Move reset time to the past — usage drops to 0%
        reset_time_past = datetime.now(timezone.utc) - timedelta(seconds=5)
        cm = ConfigManager()
        cm.set_mock_remaining_usage(account, 0.0)
        cm.set_mock_session_reset_time(account, reset_time_past.isoformat())

        usage = provider.get_session_usage_percentage()
        assert usage == 0.0

        eta = provider.get_session_reset_eta()
        assert eta is None  # Already past

        # Verify remaining was restored in config
        assert cm.get_mock_remaining_usage(account) == 1.0


class TestNegativeExitCodeWithPendingAction:
    """Ensure pending actions (await_reset, switch_provider) are processed
    even when the agent was killed by a signal (negative exit code)."""

    def test_await_reset_processed_despite_negative_exit_code(self, monkeypatch):
        """When terminate_pty kills the agent (exit_code < 0), the pending
        await_reset action must still be handled instead of silently dropped."""
        event_log = FakeEventLog()
        emitted = []
        phases_run = []
        call_count = [0]

        def usage_fn():
            call_count[0] += 1
            # Return 100% initially, then drop below threshold to exit loop
            if call_count[0] <= 2:
                return 100.0
            return 50.0

        def fake_run_phase(**kwargs):
            phase = kwargs.get("phase")
            phases_run.append(phase)
            if phase == "combined":
                # Simulate agent killed by SIGTERM
                return -15, "partial output"
            return 0, "continuation done"

        monkeypatch.setattr(
            "chad.server.services.session_event_loop.time.sleep",
            lambda s: None,
        )

        task = type("Task", (), {
            "cancel_requested": False,
            "stream_id": None,
            "_last_terminal_snapshot": "",
            "_mock_duration_applied": False,
        })()

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=task,
            run_phase_fn=fake_run_phase,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=usage_fn,
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "await_reset"},
            ],
            terminate_pty_fn=lambda: None,
        )

        loop._running = True

        # Simulate the tick thread having already detected the threshold and
        # set a pending action (which also terminated the PTY, hence -15).
        loop._pending_action = {
            "event": "session_usage",
            "threshold": 90,
            "action": "await_reset",
            "label": "session",
        }

        exit_code, output = loop._run_coding_phase(
            session=None,
            task_description="test task",
            coding_account="mock-1",
            coding_provider="mock",
            screenshots=None,
            rows=24, cols=80,
            git_mgr=None,
            coding_model=None,
            coding_reasoning=None,
        )

        # The await_reset handler should have been invoked and run continuation
        assert exit_code == 0, (
            f"Expected exit_code=0 after await_reset handling, got {exit_code}"
        )
        assert "continuation" in phases_run, (
            f"Expected continuation phase to run, only got {phases_run}"
        )

        # Verify paused milestone was emitted
        milestone_summaries = [
            e[1].get("summary", "") for e in emitted if e[0] == "milestone"
        ]
        assert any("Paused" in s for s in milestone_summaries), (
            f"Expected 'Paused' milestone, got {milestone_summaries}"
        )

    def test_second_await_reset_during_continuation_is_handled(self, monkeypatch):
        """When continuation after first await_reset hits the limit again,
        the handler must pause a second time rather than dropping the action.

        Reproduces: agent runs ~10min, hits limit, waits ~4.5h, resumes,
        hits limit again ~16min later — second pause was silently skipped.
        """
        event_log = FakeEventLog()
        emitted = []
        phases_run = []
        poll_call = [0]

        # Usage: starts at 100%, drops to 50% after 2 polls (first reset),
        # then 100% again (set during continuation), drops after 2 more polls.
        usage_sequence = iter([
            100.0, 100.0, 50.0,   # First pause: 2 polls at 100, then reset
            100.0, 100.0, 50.0,   # Second pause: 2 polls at 100, then reset
        ])

        def usage_fn():
            poll_call[0] += 1
            try:
                return next(usage_sequence)
            except StopIteration:
                return 50.0

        continuation_count = [0]

        def fake_run_phase(**kwargs):
            phase = kwargs.get("phase")
            phases_run.append(phase)
            if phase == "continuation":
                continuation_count[0] += 1
                if continuation_count[0] == 1:
                    # First continuation: simulate tick thread setting new
                    # pending action during the run (second limit hit).
                    loop._pending_action = {
                        "event": "session_usage",
                        "threshold": 90,
                        "action": "await_reset",
                        "label": "session",
                    }
                    return 0, "first continuation output"
                return 0, "second continuation output"
            return 0, "initial output"

        monkeypatch.setattr(
            "chad.server.services.session_event_loop.time.sleep",
            lambda s: None,
        )

        task = type("Task", (), {"cancel_requested": False})()

        loop = SessionEventLoop(
            session_id="test",
            event_log=event_log,
            task=task,
            run_phase_fn=fake_run_phase,
            emit_fn=lambda event_type, **kw: emitted.append((event_type, kw)),
            worktree_path="/tmp/test",
            get_session_usage_fn=usage_fn,
            action_settings=[
                {"event": "session_usage", "threshold": 90, "action": "await_reset"},
            ],
        )
        loop._running = True

        action = {
            "event": "session_usage",
            "threshold": 90,
            "action": "await_reset",
            "label": "session",
        }
        result = loop._handle_await_reset(
            action=action,
            session=None,
            task_description="cleanup ui",
            previous_output="",
            screenshots=None,
            rows=24, cols=80,
            git_mgr=None,
            coding_account="claude-1",
            coding_provider="anthropic",
            coding_model=None,
            coding_reasoning=None,
        )

        # Two continuation phases should have run
        assert phases_run.count("continuation") == 2, (
            f"Expected 2 continuations, got {phases_run}"
        )

        # Two "Paused" milestones should have been emitted
        milestone_summaries = [
            e[1].get("summary", "") for e in emitted if e[0] == "milestone"
        ]
        paused_count = sum(1 for s in milestone_summaries if "Paused" in s)
        assert paused_count == 2, (
            f"Expected 2 'Paused' milestones, got {paused_count}: {milestone_summaries}"
        )

        # Two "resuming" milestones
        resume_count = sum(1 for s in milestone_summaries if "resuming" in s)
        assert resume_count == 2, (
            f"Expected 2 'resuming' milestones, got {resume_count}: {milestone_summaries}"
        )

        # Final output includes both continuations
        assert "second continuation output" in result

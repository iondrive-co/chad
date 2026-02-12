"""Tests for SessionEventLoop milestone detection."""

from chad.server.services.session_event_loop import SessionEventLoop


class FakeEventLog:
    """Minimal EventLog stand-in that records logged events."""

    def __init__(self):
        self.events = []

    def log(self, event):
        self.events.append(event)


class TestSessionLimitDetection:
    """Tests for session limit detection in _analyze_output."""

    def _make_loop(self):
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
        )
        return loop, event_log, emitted

    def test_detects_claude_session_limit(self):
        """Detects 'You've hit your limit' pattern from Claude CLI."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Working on the task...\n")
        loop.feed_output("You've hit your limit · resets 4pm (Australia/Melbourne)")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary == "Session limit reached - resets 4pm (Australia/Melbourne)"

        milestone_emits = [e for e in emitted if e[0] == "milestone"]
        assert len(milestone_emits) == 1
        assert milestone_emits[0][1]["milestone_type"] == "session_limit_reached"
        assert milestone_emits[0][1]["title"] == "Session Limit"
        assert "resets 4pm" in milestone_emits[0][1]["summary"]

    def test_detects_session_limit_with_curly_apostrophe(self):
        """Detects limit message with curly apostrophe (Unicode)."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You\u2019ve hit your limit \u00b7 resets 2pm (US/Pacific)")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert "resets 2pm (US/Pacific)" in loop._session_limit_summary

    def test_detects_session_limit_without_reset_info(self):
        """Detects limit message even without reset time details."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("You've hit your limit")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert loop._session_limit_summary == "Session limit reached"

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
        assert "resets 6pm" in milestone_events[0].summary

    def test_detects_generic_quota_exhaustion(self):
        """Detects quota/rate limit errors from any provider (e.g. Codex, Gemini)."""
        loop, event_log, emitted = self._make_loop()

        loop.feed_output("Working on implementation...\n")
        loop.feed_output("Error: you exceeded your current quota\n")
        loop._analyze_output()

        assert loop._session_limit_detected
        assert "quota" in loop._session_limit_summary.lower()

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

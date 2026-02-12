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


class TestUsageThresholdMonitoring:
    """Tests for usage threshold crossing detection."""

    def _make_loop(self, session_fn=None, weekly_fn=None, context_fn=None):
        """Create a SessionEventLoop with usage monitoring functions."""
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
            get_session_usage_fn=session_fn,
            get_weekly_usage_fn=weekly_fn,
            get_context_usage_fn=context_fn,
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

    def test_no_milestone_when_already_above_threshold(self):
        """No milestone if previous reading was already above 90%."""
        pct = [91.0]
        loop, event_log, emitted = self._make_loop(session_fn=lambda: pct[0])

        loop._check_usage_thresholds()
        pct[0] = 95.0
        loop._check_usage_thresholds()

        assert len(self._usage_milestones(emitted)) == 0

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

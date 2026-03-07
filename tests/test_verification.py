"""Tests for verification service milestone emission."""

from unittest.mock import MagicMock


class TestVerificationMilestones:
    """Tests for milestone emission during verification."""

    def test_emits_automated_verification_milestone(self, monkeypatch):
        """run_verification emits a milestone when running automated verification."""
        from chad.server.services import verification
        from chad.util.verification import tools as verify_tools

        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        # Mock the underlying verify function to return success
        monkeypatch.setattr(
            verify_tools,
            "verify",
            lambda **kwargs: {"success": True},
        )

        # Mock run_phase_fn to avoid actual PTY execution
        def fake_run_phase(**kwargs):
            return 0, '{"passed": true, "summary": "All checks passed"}'

        verification.run_verification(
            project_path="/tmp/test",
            coding_output="some output with changes",
            task_description="Test task",
            verification_account="test-account",
            emit=emit_fn,
            run_phase_fn=fake_run_phase,
            task=MagicMock(),
            session=MagicMock(),
            worktree_path="/tmp/test",
        )

        milestone_events = [e for e in emitted if e[0] == "milestone"]
        automated_milestones = [
            m for m in milestone_events
            if m[1].get("milestone_type") == "verification_automated"
        ]
        assert len(automated_milestones) >= 1
        assert "Automated" in automated_milestones[0][1].get("title", "")

    def test_emits_llm_verification_milestone(self, monkeypatch):
        """run_verification emits a milestone when running LLM verification."""
        from chad.server.services import verification
        from chad.util.verification import tools as verify_tools

        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        # Mock the underlying verify function to return success
        monkeypatch.setattr(
            verify_tools,
            "verify",
            lambda **kwargs: {"success": True},
        )

        # Mock run_phase_fn to avoid actual PTY execution
        def fake_run_phase(**kwargs):
            return 0, '{"passed": true, "summary": "All checks passed"}'

        verification.run_verification(
            project_path="/tmp/test",
            coding_output="some output with changes",
            task_description="Test task",
            verification_account="test-account",
            emit=emit_fn,
            run_phase_fn=fake_run_phase,
            task=MagicMock(),
            session=MagicMock(),
            worktree_path="/tmp/test",
        )

        milestone_events = [e for e in emitted if e[0] == "milestone"]
        llm_milestones = [
            m for m in milestone_events
            if m[1].get("milestone_type") == "verification_llm"
        ]
        assert len(llm_milestones) >= 1
        assert "LLM" in llm_milestones[0][1].get("title", "")

    def test_automated_verification_failure_emits_milestone(self, monkeypatch):
        """Automated verification failure emits a milestone with failure details."""
        from chad.server.services import verification
        from chad.util.verification import tools as verify_tools

        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        # Mock the underlying verify function to return failure
        monkeypatch.setattr(
            verify_tools,
            "verify",
            lambda **kwargs: {
                "success": False,
                "message": "Flake8 found 3 errors",
                "phases": {"lint": {"success": False, "issue_count": 3}},
            },
        )

        passed, feedback = verification.run_verification(
            project_path="/tmp/test",
            coding_output="some output with changes",
            task_description="Test task",
            verification_account="test-account",
            emit=emit_fn,
        )

        assert passed is False
        milestone_events = [e for e in emitted if e[0] == "milestone"]
        # Should have emitted a milestone for automated verification
        automated_milestones = [
            m for m in milestone_events
            if m[1].get("milestone_type") == "verification_automated"
        ]
        assert len(automated_milestones) >= 1

    def test_no_milestones_when_emit_not_provided(self, monkeypatch):
        """When emit callback is not provided, verification still works."""
        from chad.server.services import verification
        from chad.util.verification import tools as verify_tools

        # Mock the underlying verify function to return success
        monkeypatch.setattr(
            verify_tools,
            "verify",
            lambda **kwargs: {"success": True},
        )

        # Mock run_phase_fn
        def fake_run_phase(**kwargs):
            return 0, '{"passed": true, "summary": "All checks passed"}'

        # Should not raise even without emit callback
        passed, feedback = verification.run_verification(
            project_path="/tmp/test",
            coding_output="some output with changes",
            task_description="Test task",
            verification_account="test-account",
            emit=None,  # No emit callback
            run_phase_fn=fake_run_phase,
            task=MagicMock(),
            session=MagicMock(),
            worktree_path="/tmp/test",
        )

        assert passed is True

    def test_milestone_includes_attempt_number(self, monkeypatch):
        """Milestones include the verification attempt number."""
        from chad.server.services import verification
        from chad.util.verification import tools as verify_tools

        emitted = []

        def emit_fn(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        # Mock the underlying verify function to return success
        monkeypatch.setattr(
            verify_tools,
            "verify",
            lambda **kwargs: {"success": True},
        )

        def fake_run_phase(**kwargs):
            return 0, '{"passed": true, "summary": "All checks passed"}'

        verification.run_verification(
            project_path="/tmp/test",
            coding_output="some output with changes",
            task_description="Test task",
            verification_account="test-account",
            emit=emit_fn,
            run_phase_fn=fake_run_phase,
            task=MagicMock(),
            session=MagicMock(),
            worktree_path="/tmp/test",
            attempt=3,
        )

        milestone_events = [e for e in emitted if e[0] == "milestone"]
        # Check that at least one milestone includes attempt info
        has_attempt_info = any(
            m[1].get("details", {}).get("attempt") == 3
            for m in milestone_events
        )
        assert has_attempt_info


class TestMilestoneTitles:
    """Tests for milestone title definitions."""

    def test_verification_milestone_titles_defined(self):
        """Verification milestone types have titles defined in SessionEventLoop."""
        from chad.server.services.session_event_loop import SessionEventLoop

        titles = SessionEventLoop._MILESTONE_TITLES

        assert "verification_automated" in titles
        assert "verification_llm" in titles
        assert "Automated" in titles["verification_automated"]
        assert "LLM" in titles["verification_llm"]

"""End-to-end tests exercising the full task lifecycle with the mock provider.

These tests go through the real API, PTY, and event loop code paths without
consuming any real API tokens.  They catch integration issues that unit tests
with mocked providers cannot:

- Task start → coding → verification → revision → completion
- Follow-up tasks reusing a session
- Cancellation mid-task
- Event log completeness across the lifecycle
- Milestone emission ordering
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client with isolated config."""
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    initial_config = {
        "encryption_salt": "dGVzdHNhbHQ=",
        "password_hash": "",
        "accounts": {},
    }
    temp_config.write_text(json.dumps(initial_config))

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()

    app = create_app()
    with TestClient(app) as c:
        yield c

    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
        env={**subprocess.os.environ, "GIT_AUTHOR_NAME": "test",
             "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "test",
             "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_terminal(client, session_id, task_id, timeout=30.0, interval=0.15):
    """Wait for a task to reach a terminal state and return it."""
    terminal = {"completed", "failed", "cancelled"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/v1/sessions/{session_id}/tasks/{task_id}")
        if resp.status_code == 200:
            status = resp.json().get("status")
            if status in terminal:
                return status
        time.sleep(interval)
    pytest.fail(f"Task {task_id} did not finish within {timeout}s")


def _get_events(client, session_id, timeout=5.0, interval=0.1):
    """Fetch events, waiting for latest_seq to stabilise."""
    deadline = time.time() + timeout
    events = []
    while time.time() < deadline:
        resp = client.get(f"/api/v1/sessions/{session_id}/events")
        assert resp.status_code == 200
        payload = resp.json()
        events = payload.get("events", [])
        if events:
            max_seq = max(e.get("seq", 0) for e in events)
            if payload.get("latest_seq", 0) == max_seq:
                return events
        time.sleep(interval)
    return events


def _start_task(client, session_id, git_repo, task_desc,
                coding_agent="e2e-mock", verification_agent=None):
    """Start a task and return the task_id."""
    body = {
        "project_path": str(git_repo),
        "task_description": task_desc,
        "coding_agent": coding_agent,
    }
    if verification_agent:
        body["verification_agent"] = verification_agent
    resp = client.post(f"/api/v1/sessions/{session_id}/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["task_id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEndFullLoop:
    """Full lifecycle tests using the mock provider."""

    def _setup_session(self, client, git_repo):
        """Create session and account, return session_id."""
        sess = client.post("/api/v1/sessions", json={"name": "E2E"})
        session_id = sess.json()["id"]

        acct = client.post("/api/v1/accounts",
                           json={"name": "e2e-mock", "provider": "mock"})
        assert acct.status_code == 201
        return session_id

    def test_coding_only_completes(self, client, git_repo):
        """Task with coding only (no verification) runs to completion."""
        session_id = self._setup_session(client, git_repo)
        task_id = _start_task(client, session_id, git_repo, "Add a comment to BUGS.md")
        status = _wait_terminal(client, session_id, task_id)
        assert status == "completed"

        # Events should include session_started and session_ended
        events = _get_events(client, session_id)
        types = [e["type"] for e in events]
        assert "session_started" in types
        assert "session_ended" in types

    def test_coding_produces_terminal_output(self, client, git_repo):
        """Coding task produces terminal output events."""
        session_id = self._setup_session(client, git_repo)
        task_id = _start_task(client, session_id, git_repo, "Mock coding task")
        _wait_terminal(client, session_id, task_id)

        events = _get_events(client, session_id)
        terminal_events = [e for e in events if e["type"] == "terminal_output"]
        assert len(terminal_events) > 0, "Expected terminal output from mock agent"

    def test_followup_reuses_session(self, client, git_repo):
        """A follow-up task on the same session succeeds."""
        session_id = self._setup_session(client, git_repo)

        # First task
        task1_id = _start_task(client, session_id, git_repo, "Initial task")
        status1 = _wait_terminal(client, session_id, task1_id)
        assert status1 == "completed"

        # Follow-up task on same session
        task2_id = _start_task(client, session_id, git_repo, "Follow-up task")
        status2 = _wait_terminal(client, session_id, task2_id)
        assert status2 == "completed"

        # Both tasks should have events
        events = _get_events(client, session_id)
        session_started = [e for e in events if e["type"] == "session_started"]
        assert len(session_started) >= 2, "Expected session_started for both tasks"

    def test_cancel_stops_running_task(self, client, git_repo, monkeypatch):
        """Cancelling a session stops its running task."""
        session_id = self._setup_session(client, git_repo)

        # Use a slow mock task so we can cancel mid-run
        import chad.server.services.task_executor as te
        orig = te.build_agent_command

        def slow_mock(*args, **kwargs):
            cmd, env, inp = orig(*args, **kwargs)
            # Replace the mock command with a long sleep
            cmd = ["bash", "-c", "sleep 30"]
            return cmd, env, inp

        monkeypatch.setattr(te, "build_agent_command", slow_mock)

        task_id = _start_task(client, session_id, git_repo, "Long running task")

        # Wait briefly for it to start
        time.sleep(0.5)

        # Cancel
        cancel_resp = client.post(f"/api/v1/sessions/{session_id}/cancel")
        assert cancel_resp.status_code == 200

        status = _wait_terminal(client, session_id, task_id, timeout=10)
        assert status in ("cancelled", "failed")

    def test_task_status_progression(self, client, git_repo):
        """Task status progresses through expected states."""
        session_id = self._setup_session(client, git_repo)
        task_id = _start_task(client, session_id, git_repo, "Status check task")

        # Should be running or pending immediately
        resp = client.get(f"/api/v1/sessions/{session_id}/tasks/{task_id}")
        assert resp.status_code == 200
        early_status = resp.json()["status"]
        assert early_status in ("pending", "running")

        # Wait for completion
        final = _wait_terminal(client, session_id, task_id)
        assert final == "completed"

        # Verify final status via API
        resp = client.get(f"/api/v1/sessions/{session_id}/tasks/{task_id}")
        assert resp.json()["status"] == "completed"

    def test_duplicate_start_rejected(self, client, git_repo, monkeypatch):
        """Starting a second task while one is running returns 409."""
        session_id = self._setup_session(client, git_repo)

        # Start a slow task
        import chad.server.services.task_executor as te
        orig = te.build_agent_command

        def slow_mock(*args, **kwargs):
            cmd, env, inp = orig(*args, **kwargs)
            cmd = ["bash", "-c", "sleep 30"]
            return cmd, env, inp

        monkeypatch.setattr(te, "build_agent_command", slow_mock)

        task_id = _start_task(client, session_id, git_repo, "First task")
        time.sleep(0.3)

        # Try to start another
        resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Second task",
                "coding_agent": "e2e-mock",
            },
        )
        assert resp.status_code == 409

        # Clean up
        client.post(f"/api/v1/sessions/{session_id}/cancel")
        _wait_terminal(client, session_id, task_id, timeout=10)

    def test_event_log_has_complete_lifecycle(self, client, git_repo):
        """Event log captures the full lifecycle: start → output → end."""
        session_id = self._setup_session(client, git_repo)
        task_id = _start_task(client, session_id, git_repo, "Lifecycle test")
        _wait_terminal(client, session_id, task_id)

        events = _get_events(client, session_id)
        types = [e["type"] for e in events]

        # Must have bookend events
        assert types[0] == "session_started", f"First event should be session_started, got {types[0]}"
        assert types[-1] == "session_ended", f"Last event should be session_ended, got {types[-1]}"

        # Must have some content in between
        assert len(events) >= 3, f"Expected at least 3 events, got {len(events)}"

    def test_session_started_event_has_metadata(self, client, git_repo):
        """session_started event includes task description and provider info."""
        session_id = self._setup_session(client, git_repo)
        task_desc = "Metadata check task"
        task_id = _start_task(client, session_id, git_repo, task_desc)
        _wait_terminal(client, session_id, task_id)

        events = _get_events(client, session_id)
        started = [e for e in events if e["type"] == "session_started"][0]

        assert started.get("task_description") == task_desc
        assert started.get("coding_provider") == "mock"
        assert started.get("coding_account") == "e2e-mock"

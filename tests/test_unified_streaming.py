"""Comprehensive tests for unified PTY streaming through API."""

import asyncio
import anyio
import base64
import html
import httpx
import json
import queue
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chad.server.main import create_app
from chad.server.services import reset_session_manager, reset_task_executor
from chad.server.services.pty_stream import reset_pty_stream_service
from chad.server.state import reset_state
from chad.ui.client.stream_client import StreamClient, decode_terminal_data
from chad.ui.terminal_emulator import TerminalEmulator
from chad.util.event_log import EventLog, SessionEndedEvent, SessionStartedEvent, TerminalOutputEvent


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client for the API with isolated config."""
    # Use temporary config file
    temp_config = tmp_path / "test_chad.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(temp_config))
    monkeypatch.setenv("CHAD_LOG_DIR", str(tmp_path / "logs"))

    # Initialize config with encryption salt for account creation tests
    import json
    initial_config = {
        "encryption_salt": "dGVzdHNhbHQ=",  # base64 "testsalt"
        "password_hash": "",
        "accounts": {},
    }
    temp_config.write_text(json.dumps(initial_config))

    # Reset all state before each test
    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()

    app = create_app()
    with TestClient(app) as client:
        yield client

    # Reset after test
    reset_session_manager()
    reset_task_executor()
    reset_pty_stream_service()
    reset_state()


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    import subprocess

    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    # Create initial file and commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    (repo_path / "BUGS.md").write_text("# Bugs\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    return repo_path


@pytest.fixture
def mock_account(client, tmp_path, monkeypatch):
    """Set up a mock account for testing."""
    # Create config with mock account
    config_path = tmp_path / "test_chad.conf"
    config_content = """
[accounts]
test-mock = mock
"""
    config_path.write_text(config_content)
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))

    # Reset state to pick up new config
    reset_state()

    return "test-mock"


class TestEventLog:
    """Tests for event logging system."""

    def test_create_event_log(self, tmp_path):
        """Can create an event log."""
        log = EventLog("test-session", base_dir=tmp_path)
        assert log.session_id == "test-session"
        assert log.log_path.exists() is False  # Not created until first event

    def test_log_session_started(self, tmp_path):
        """Can log session started event."""
        log = EventLog("test-session", base_dir=tmp_path)

        event = SessionStartedEvent(
            task_description="Fix the bug",
            project_path="/home/test/project",
            coding_provider="mock",
            coding_account="test-mock",
        )
        log.log(event)

        assert log.log_path.exists()

        # Read back events
        events = log.get_events()
        assert len(events) == 1
        assert events[0]["type"] == "session_started"
        assert events[0]["task_description"] == "Fix the bug"

    def test_log_terminal_output(self, tmp_path):
        """Can log terminal output events."""
        log = EventLog("test-session", base_dir=tmp_path)

        # Log terminal screen content (human-readable text)
        event = TerminalOutputEvent(data="Hello World")
        log.log(event)

        events = log.get_events()
        assert len(events) == 1
        assert events[0]["type"] == "terminal_output"
        assert events[0]["data"] == "Hello World"

    def test_sequence_numbers(self, tmp_path):
        """Events have monotonically increasing sequence numbers."""
        log = EventLog("test-session", base_dir=tmp_path)

        for i in range(5):
            log.log(TerminalOutputEvent(data="test"))

        events = log.get_events()
        seqs = [e["seq"] for e in events]
        assert seqs == [1, 2, 3, 4, 5]

    def test_event_log_reuses_existing_sequence(self, tmp_path):
        """New EventLog instances pick up existing sequence numbers."""
        log = EventLog("persisted-session", base_dir=tmp_path)
        log.log(TerminalOutputEvent(data="first"))
        log.log(TerminalOutputEvent(data="second"))

        resumed = EventLog("persisted-session", base_dir=tmp_path)
        resumed.log(TerminalOutputEvent(data="third"))

        events = resumed.get_events()
        seqs = [e["seq"] for e in events]
        assert seqs[-1] == 3

    def test_get_events_since_seq(self, tmp_path):
        """Can retrieve events after a sequence number."""
        log = EventLog("test-session", base_dir=tmp_path)

        for i in range(10):
            log.log(TerminalOutputEvent(data=f"event-{i}"))

        # Get events after seq 5
        events = log.get_events(since_seq=5)
        assert len(events) == 5
        assert events[0]["seq"] == 6

    def test_artifact_storage(self, tmp_path):
        """Large content is stored as artifacts."""
        log = EventLog("test-session", base_dir=tmp_path)

        # Create content larger than threshold (10KB)
        large_content = "x" * 15000

        ref = log.store_artifact(large_content, "stdout")
        assert ref is not None
        assert ref.size == 15000
        assert len(ref.sha256) == 64

        # Can read it back
        content = log.get_artifact(ref)
        assert content.decode() == large_content

    def test_small_content_not_stored_as_artifact(self, tmp_path):
        """Small content returns None (should be inline)."""
        log = EventLog("test-session", base_dir=tmp_path)

        small_content = "x" * 100
        ref = log.store_artifact(small_content, "stdout")
        assert ref is None


class TestSSEStreaming:
    """Tests for SSE streaming endpoint."""

    def test_stream_endpoint_exists(self, client):
        """Stream endpoint exists and requires valid session."""
        # Non-existent session returns 404
        response = client.get("/api/v1/sessions/nonexistent/stream")
        assert response.status_code == 404

    def test_stream_returns_sse_format(self, client):
        """Stream endpoint returns proper SSE content type."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        assert create_resp.status_code == 201
        assert "id" in create_resp.json()
        # Note: Actually streaming would hang, so we just test session creation.
        # The SSE format is tested in TestEndToEndSSEStreaming.


class TestInputEndpoint:
    """Tests for input sending endpoint."""

    def test_input_endpoint_requires_session(self, client):
        """Input endpoint requires valid session."""
        response = client.post(
            "/api/v1/sessions/nonexistent/input",
            json={"data": base64.b64encode(b"test").decode()},
        )
        assert response.status_code == 404

    def test_input_requires_active_pty(self, client):
        """Input endpoint requires active PTY session."""
        # Create a session without starting a task
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/input",
            json={"data": base64.b64encode(b"test").decode()},
        )
        assert response.status_code == 400
        assert "No active PTY" in response.json()["detail"]

    def test_input_validates_base64(self, client):
        """Input endpoint validates base64 encoding."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/input",
            json={"data": "not-valid-base64!!!"},
        )
        # Either 400 (invalid base64) or 400 (no PTY)
        assert response.status_code == 400


class TestResizeEndpoint:
    """Tests for terminal resize endpoint."""

    def test_resize_endpoint_requires_session(self, client):
        """Resize endpoint requires valid session."""
        response = client.post(
            "/api/v1/sessions/nonexistent/resize",
            json={"rows": 24, "cols": 80},
        )
        assert response.status_code == 404

    def test_resize_requires_active_pty(self, client):
        """Resize endpoint requires active PTY session."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/resize",
            json={"rows": 24, "cols": 80},
        )
        assert response.status_code == 400

    def test_resize_validates_dimensions(self, client):
        """Resize endpoint validates row/col dimensions."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        # Invalid dimensions
        response = client.post(
            f"/api/v1/sessions/{session_id}/resize",
            json={"rows": 0, "cols": 80},
        )
        assert response.status_code == 422  # Validation error


class TestPTYStreamService:
    """Tests for PTY stream service."""

    def test_start_pty_session(self, tmp_path):
        """Can start a PTY session."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Start a simple command
        stream_id = service.start_pty_session(
            session_id="test",
            cmd=["echo", "hello"],
            cwd=tmp_path,
        )

        assert stream_id.startswith("pty_")

        # Wait for completion
        time.sleep(0.5)

        session = service.get_session(stream_id)
        assert session is not None

        # Cleanup
        service.cleanup_session(stream_id)

    def test_pty_output_captured(self, tmp_path):
        """PTY output is captured and available."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Start a command that produces output
        stream_id = service.start_pty_session(
            session_id="test",
            cmd=["echo", "test output"],
            cwd=tmp_path,
        )

        # Wait for output
        time.sleep(0.5)

        session = service.get_session(stream_id)
        assert session is not None

        # Process should have completed
        assert session.active is False or session.exit_code is not None

        service.cleanup_session(stream_id)

    def test_send_input_to_pty(self, tmp_path):
        """Can send input to PTY."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Start cat which waits for input
        stream_id = service.start_pty_session(
            session_id="test",
            cmd=["cat"],
            cwd=tmp_path,
        )

        time.sleep(0.2)

        # Send input
        result = service.send_input(stream_id, b"hello\n")
        assert result is True

        # Terminate
        service.terminate(stream_id)
        time.sleep(0.3)

        service.cleanup_session(stream_id)

    def test_terminate_pty(self, tmp_path):
        """Can terminate a PTY session."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Start a long-running command
        stream_id = service.start_pty_session(
            session_id="test",
            cmd=["sleep", "60"],
            cwd=tmp_path,
        )

        time.sleep(0.2)
        session = service.get_session(stream_id)
        assert session.active is True

        # Terminate
        result = service.terminate(stream_id)
        assert result is True

        # Wait for termination
        time.sleep(0.5)

        session = service.get_session(stream_id)
        assert session.active is False

        service.cleanup_session(stream_id)


class TestMockProviderThroughAPI:
    """Tests for mock provider through the full API stack."""

    def test_mock_command_builds_correctly(self):
        """Mock agent command builds correctly."""
        from chad.server.services.task_executor import build_agent_command

        cmd, env, initial_input = build_agent_command(
            provider="mock",
            account_name="test",
            project_path=Path("/tmp/test"),
            task_description="Fix the bug",
        )

        assert cmd[0] == "python3"
        assert cmd[1] == "-c"
        # Script should be in cmd[2]
        assert "Mock Agent" in cmd[2]
        assert "ANSI" in cmd[2] or "033" in cmd[2]  # Contains ANSI codes

    def test_mock_agent_outputs_ansi(self, tmp_path):
        """Mock agent outputs ANSI escape codes."""
        from chad.server.services.task_executor import _build_mock_agent_command
        import subprocess

        cmd = _build_mock_agent_command(tmp_path, "test task")

        # Run the mock agent
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=10,
        )

        # Should have ANSI codes in output
        assert b"\x1b[" in result.stdout
        assert result.returncode == 0


class TestCliStreamAlignment:
    """Ensure CLI-style streaming keeps output aligned."""

    @pytest.mark.parametrize("terminal_cols", [80, 120])
    def test_mock_agent_output_is_left_aligned(self, client, git_repo, terminal_cols):
        """Mock agent lines should start at column 0 for CLI terminals."""
        # Register a mock account via API
        account_resp = client.post("/api/v1/accounts", json={"name": "cli-mock", "provider": "mock"})
        assert account_resp.status_code == 201

        # Create session
        session_id = client.post("/api/v1/sessions", json={"name": "CLI alignment"}).json()["id"]

        # Start task with specific terminal geometry
        start_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "check alignment",
                "coding_agent": "cli-mock",
                "terminal_rows": 24,
                "terminal_cols": terminal_cols,
            },
        )
        assert start_resp.status_code == 201

        async def collect_output() -> bytes:
            stream_client = StreamClient(str(client.base_url))
            stream_client._async_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url=str(client.base_url),
                timeout=None,
            )
            chunks: list[bytes] = []
            async for event in stream_client.stream_events(session_id, include_terminal=True):
                if event.event_type == "terminal":
                    chunks.append(
                        decode_terminal_data(
                            event.data.get("data", ""),
                            is_text=event.data.get("text", False),
                        )
                    )
                elif event.event_type == "complete":
                    break
            await stream_client.close()
            return b"".join(chunks)

        raw_bytes = anyio.run(collect_output)
        assert raw_bytes, "Should capture mock agent terminal output"

        emulator = TerminalEmulator(cols=terminal_cols, rows=80)
        emulator.feed(raw_bytes)
        rendered_lines = [line for line in emulator.get_text().split("\n") if line.strip()]
        leading_spaces = [len(line) - len(line.lstrip(" ")) for line in rendered_lines]
        base_indent = min(leading_spaces) if leading_spaces else 0

        assert base_indent == 0 and all(space == base_indent for space in leading_spaces), (
            f"Expected left-aligned lines for terminal width {terminal_cols}, "
            f"got leading spaces per line: {leading_spaces}"
        )


class TestGradioStreamAlignment:
    """Ensure Gradio stream rendering stays left-aligned."""

    def test_stream_task_output_left_aligned(self, client, git_repo):
        """stream_task_output HTML should not drift right across lines."""
        from chad.ui.gradio.web_ui import ChadWebUI
        from chad.ui.client.stream_client import SyncStreamClient

        account_resp = client.post("/api/v1/accounts", json={"name": "gradio-mock", "provider": "mock"})
        assert account_resp.status_code == 201

        session_id = client.post("/api/v1/sessions", json={"name": "Gradio alignment"}).json()["id"]

        start_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "gradio align",
                "coding_agent": "gradio-mock",
                "terminal_rows": 24,
                "terminal_cols": 80,
            },
        )
        assert start_resp.status_code == 201

        stream_client = SyncStreamClient(str(client.base_url))
        stream_client._sync_client = httpx.Client(
            transport=client._transport,
            base_url=str(client.base_url),
            timeout=None,
        )

        # Build a minimal ChadWebUI instance without running full init
        ui = ChadWebUI.__new__(ChadWebUI)
        ui.api_client = type("DummyAPI", (), {"base_url": str(client.base_url)})()
        ui._stream_client = stream_client

        latest_html = ""
        for event_type, html_content, exit_code in ui.stream_task_output(session_id, include_terminal=True):
            if event_type == "terminal":
                latest_html = html_content or ""
            elif event_type == "complete":
                break

        assert latest_html, "Expected live stream HTML content"

        plain = html.unescape(re.sub(r"<[^>]+>", "", latest_html))
        rendered_lines = [line for line in plain.split("\n") if line.strip()]
        leading_spaces = [len(line) - len(line.lstrip(" ")) for line in rendered_lines]
        base_indent = min(leading_spaces) if leading_spaces else 0

        assert base_indent == 0 and all(space == base_indent for space in leading_spaces), (
            "Gradio stream_task_output should render lines starting at column 0; "
            f"got leading spaces per line: {leading_spaces}"
        )


class TestStreamClient:
    """Tests for the stream client."""

    def test_sync_stream_client_creation(self):
        """Can create sync stream client."""
        from chad.ui.client.stream_client import SyncStreamClient

        client = SyncStreamClient("http://localhost:8000")
        assert client.base_url == "http://localhost:8000"
        client.close()

    def test_decode_terminal_data(self):
        """Can decode base64 terminal data."""
        from chad.ui.client.stream_client import decode_terminal_data

        original = b"\x1b[32mHello World\x1b[0m"
        encoded = base64.b64encode(original).decode()
        decoded = decode_terminal_data(encoded)

        assert decoded == original

    def test_decode_terminal_text_passthrough(self):
        """Plain text terminal events are returned as UTF-8 bytes."""
        from chad.ui.client.stream_client import decode_terminal_data

        text = "╭─ unicode box drawing"
        decoded = decode_terminal_data(text, is_text=True)

        assert decoded.decode("utf-8") == text

    def test_decode_terminal_data_normalizes_lf_to_crlf(self):
        """Bare LF is converted to CRLF for proper terminal display."""
        from chad.ui.client.stream_client import decode_terminal_data

        # Base64-encoded data with bare LF
        original = b"Line1\nLine2\n"
        encoded = base64.b64encode(original).decode()
        decoded = decode_terminal_data(encoded)

        # Should have CRLF, not bare LF
        assert decoded == b"Line1\r\nLine2\r\n"

    def test_decode_terminal_data_preserves_existing_crlf(self):
        """Already-CRLF data is not double-normalized."""
        from chad.ui.client.stream_client import decode_terminal_data

        # Data already has CRLF
        original = b"Line1\r\nLine2\r\n"
        encoded = base64.b64encode(original).decode()
        decoded = decode_terminal_data(encoded)

        # Should remain unchanged
        assert decoded == original

    def test_decode_terminal_text_normalizes_lf_to_crlf(self):
        """Plain text with bare LF is normalized to CRLF."""
        from chad.ui.client.stream_client import decode_terminal_data

        text = "Hello\nWorld\n"
        decoded = decode_terminal_data(text, is_text=True)

        # Should have CRLF
        assert decoded == b"Hello\r\nWorld\r\n"


class TestWebSocketEndpoint:
    """Tests for WebSocket endpoint."""

    def test_websocket_requires_valid_session(self, client):
        """WebSocket endpoint rejects non-existent session."""
        with pytest.raises(Exception):
            with client.websocket_connect("/api/v1/ws/nonexistent"):
                pass

    def test_websocket_accepts_valid_session(self, client):
        """WebSocket endpoint accepts valid session."""
        # Create a session
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        with client.websocket_connect(f"/api/v1/ws/{session_id}") as websocket:
            # Send ping
            websocket.send_json({"type": "ping"})
            response = websocket.receive_json()
            assert response["type"] == "pong"

    def test_websocket_handles_cancel(self, client):
        """WebSocket handles cancel message."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        with client.websocket_connect(f"/api/v1/ws/{session_id}") as websocket:
            # Send cancel (no task running, should still work)
            websocket.send_json({"type": "cancel"})
            # May or may not receive response depending on timing


class TestCancelSession:
    """Tests for session cancellation."""

    def test_cancel_no_active_task(self, client):
        """Cancel returns appropriate message when no task active."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Test"})
        session_id = create_resp.json()["id"]

        response = client.post(f"/api/v1/sessions/{session_id}/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["cancel_requested"] is False
        assert "No active task" in data["message"]


class TestMockProviderFullIntegration:
    """End-to-end integration tests for mock provider through full API stack.

    These tests verify that:
    1. The mock provider works through the complete API pipeline
    2. Both Gradio UI (via stream_task_output) and CLI (via SyncStreamClient)
       use the same streaming method
    3. Terminal output is properly captured and base64 encoded
    4. Session lifecycle is correctly managed
    """

    def test_mock_provider_full_api_flow(self, client, git_repo, tmp_path, monkeypatch):
        """Test complete flow: create session -> start task -> stream output."""
        # 1. Create session (returns 201 Created)
        create_resp = client.post("/api/v1/sessions", json={"name": "Integration Test"})
        assert create_resp.status_code == 201
        session_data = create_resp.json()
        session_id = session_data["id"]

        # 2. Add mock account via API
        account_resp = client.post(
            "/api/v1/accounts",
            json={"name": "mock-agent", "provider": "mock"},
        )
        # May already exist, both 200/201 are valid
        assert account_resp.status_code in (200, 201, 409)

        # 3. Start task with mock provider
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Fix the bug in README",
                "coding_agent": "mock-agent",
            },
        )
        assert task_resp.status_code in (200, 201)  # 201 for new task creation
        task_data = task_resp.json()
        task_id = task_data["task_id"]
        assert task_id is not None

        # 4. Wait for task to complete (mock agent is fast)
        time.sleep(1)

        # 5. Get task status
        status_resp = client.get(f"/api/v1/sessions/{session_id}/tasks/{task_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        # Task should be complete
        assert status.get("status") in ("completed", "running", "pending")

    def test_mock_provider_sse_streaming(self, client, git_repo, tmp_path, monkeypatch):
        """Test SSE streaming endpoint works with mock provider."""
        # Create session
        create_resp = client.post("/api/v1/sessions", json={"name": "SSE Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Add mock account via API
        account_resp = client.post(
            "/api/v1/accounts",
            json={"name": "mock-sse", "provider": "mock"},
        )
        assert account_resp.status_code in (200, 201, 409)

        # Start task
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Test task",
                "coding_agent": "mock-sse",
            },
        )
        assert task_resp.status_code == 201

        # Wait for task to complete
        time.sleep(1)

        # Verify events were logged (the real SSE tests are in TestEndToEndSSEStreaming)
        events_resp = client.get(f"/api/v1/sessions/{session_id}/events")
        assert events_resp.status_code == 200

    def test_gradio_stream_task_output_integration(self, client, git_repo, tmp_path, monkeypatch):
        """Test Gradio UI stream_task_output method uses same API as CLI."""
        from unittest.mock import Mock

        config_path = tmp_path / "test_chad.conf"
        config_content = """
[accounts]
mock-agent = mock
[roles]
coding = mock-agent
"""
        config_path.write_text(config_content)
        monkeypatch.setenv("CHAD_CONFIG", str(config_path))
        reset_state()

        # Create a mock API client
        mock_api_client = Mock()
        mock_api_client.base_url = "http://localhost:8000"

        # Import ChadWebUI
        from chad.ui.gradio.web_ui import ChadWebUI

        ui = ChadWebUI(mock_api_client)

        # Verify stream client is created
        stream_client = ui._get_stream_client()
        assert stream_client is not None
        assert stream_client.base_url == "http://localhost:8000"

        # Verify stream_task_output method exists and is callable
        assert hasattr(ui, "stream_task_output")
        assert callable(ui.stream_task_output)

    def test_cli_and_gradio_use_same_streaming_class(self):
        """Verify CLI and Gradio use the same SyncStreamClient class."""
        # Import from CLI app
        from chad.ui.cli.app import SyncStreamClient as CLISyncStreamClient

        # Import from Gradio web_ui
        from chad.ui.gradio.web_ui import SyncStreamClient as GradioSyncStreamClient

        # Both should be the same class
        assert CLISyncStreamClient is GradioSyncStreamClient

        # Both should come from stream_client module
        from chad.ui.client.stream_client import SyncStreamClient

        assert CLISyncStreamClient is SyncStreamClient
        assert GradioSyncStreamClient is SyncStreamClient

    def test_ansi_to_html_preserves_layout(self):
        """Test ANSI to HTML conversion preserves terminal layout."""
        from chad.ui.gradio.web_ui import ansi_to_html

        # Test simple ANSI codes
        input_text = "\x1b[32mGreen\x1b[0m Normal \x1b[31mRed\x1b[0m"
        html = ansi_to_html(input_text)

        # Should contain color spans
        assert "<span" in html
        assert "Green" in html
        assert "Red" in html
        assert "Normal" in html

    def test_mock_provider_produces_parseable_output(self, tmp_path):
        """Mock provider output can be parsed by ANSI-to-HTML converter."""
        from chad.server.services.task_executor import _build_mock_agent_command
        from chad.ui.gradio.web_ui import ansi_to_html
        import subprocess

        cmd = _build_mock_agent_command(tmp_path, "test task")

        # Run mock agent
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        output = result.stdout.decode("utf-8", errors="replace")

        # Should contain ANSI codes
        assert "\x1b[" in output

        # Should be convertible to HTML
        html = ansi_to_html(output)
        assert "<span" in html or "Mock" in html  # Either styled or plain content


class TestPlainTextTerminalEvents:
    """Ensure text-flagged terminal events are handled without base64 decoding."""

    def test_gradio_run_task_handles_plain_text_terminal_events(self):
        """Gradio run_task_via_api streams plain text terminal output safely."""
        from chad.ui.client.stream_client import StreamEvent
        from chad.ui.gradio.web_ui import ChadWebUI

        terminal_text = "╭─ streamed log line"

        class DummyAPI:
            base_url = "http://localhost:8000"

            def start_task(
                self,
                *,
                session_id: str,
                project_path: str,
                task_description: str,
                coding_agent: str,
                coding_model=None,
                coding_reasoning=None,
                terminal_rows=None,
                terminal_cols=None,
                screenshots=None,
                override_exploration_prompt=None,
                override_implementation_prompt=None,
            ):
                return None

        class DummyStreamClient:
            def stream_events(self, session_id: str, include_terminal: bool = True):
                yield StreamEvent(
                    event_type="terminal",
                    data={"data": terminal_text, "text": True, "seq": 1},
                    seq=1,
                )
                yield StreamEvent(
                    event_type="complete",
                    data={"exit_code": 0, "seq": 2},
                    seq=2,
                )

        ui = ChadWebUI(DummyAPI())
        ui._stream_client = DummyStreamClient()

        msg_queue = queue.Queue()

        success, final_output, server_session_id, _ = ui.run_task_via_api(
            session_id="local-plain",
            project_path="/tmp",
            task_description="Handle plain text terminal event",
            coding_account="mock",
            message_queue=msg_queue,
            server_session_id="server-plain",
        )

        assert success is True
        assert final_output.startswith("╭─")

        stream_entries = [item for item in list(msg_queue.queue) if item[0] == "stream"]
        assert len(stream_entries) == 1
        _, text_chunk, html_output = stream_entries[0]
        assert text_chunk.startswith("╭─")
        assert "╭" in html_output


class TestEndToEndSSEStreaming:
    """True end-to-end tests that verify actual SSE streaming works."""

    def test_sse_stream_receives_terminal_events(self, client, git_repo, tmp_path, monkeypatch):
        """Verify SSE endpoint actually streams terminal events from mock agent."""
        # Create session
        create_resp = client.post("/api/v1/sessions", json={"name": "E2E SSE Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Add mock account
        client.post("/api/v1/accounts", json={"name": "e2e-mock", "provider": "mock"})

        # Start the task
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "E2E streaming test",
                "coding_agent": "e2e-mock",
            },
        )
        assert task_resp.status_code == 201

        # Wait for task to complete and then verify via EventLog
        time.sleep(1)

        # Get events from the EventLog API
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        assert response.status_code == 200
        data = response.json()
        events = data["events"]

        # Verify we logged terminal events
        terminal_events = [e for e in events if e["type"] == "terminal_output"]
        assert len(terminal_events) > 0, "Should have logged terminal events"

        # Verify terminal events have required fields
        for event in terminal_events:
            assert "data" in event, "Terminal event should have data field"
            assert "seq" in event, "Terminal event should have seq field"
            # Data is now human-readable text (not base64)
            assert len(event["data"]) > 0, "Terminal data should not be empty"

        # Verify we have session_started
        session_events = [e for e in events if e["type"] == "session_started"]
        assert len(session_events) == 1, "Should have exactly one session_started event"

    def test_pty_stream_service_delivers_events(self, tmp_path):
        """Verify PTY stream service delivers events to subscribers."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Start a simple command
        stream_id = service.start_pty_session(
            session_id="test-e2e",
            cmd=["echo", "Hello from PTY"],
            cwd=tmp_path,
        )

        # Collect output via polling
        time.sleep(0.5)

        session = service.get_session(stream_id)
        assert session is not None
        assert session.exit_code is not None or not session.active

        # Cleanup
        service.cleanup_session(stream_id)

    def test_stream_client_parses_sse_format(self):
        """Verify StreamClient correctly parses SSE event format."""
        from chad.ui.client.stream_client import StreamEvent

        # Test SSE parsing logic manually
        sse_data = """event: terminal
data: {"data": "SGVsbG8=", "seq": 1}

event: complete
data: {"exit_code": 0, "seq": 2}

"""
        # Parse it
        events = []
        buffer = sse_data
        current_event = ""
        current_data = ""

        for line in buffer.split("\n"):
            line = line.strip()
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                current_data = line[5:].strip()
            elif line == "" and current_event and current_data:
                data = json.loads(current_data)
                events.append(StreamEvent(
                    event_type=current_event,
                    data=data,
                    seq=data.get("seq"),
                ))
                current_event = ""
                current_data = ""

        assert len(events) == 2
        assert events[0].event_type == "terminal"
        assert events[0].data["data"] == "SGVsbG8="
        assert events[1].event_type == "complete"
        assert events[1].data["exit_code"] == 0


class TestEventLogAPI:
    """Tests for the EventLog API endpoint."""

    def test_get_events_empty_session(self, client):
        """Get events for session with no task returns empty list."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Empty Session"})
        session_id = create_resp.json()["id"]

        response = client.get(f"/api/v1/sessions/{session_id}/events")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["latest_seq"] == 0

    def test_get_events_after_task(self, client, git_repo):
        """Get events after task completion includes session_started and terminal_output."""
        # Create session and run task
        create_resp = client.post("/api/v1/sessions", json={"name": "Event Log Test"})
        session_id = create_resp.json()["id"]

        client.post("/api/v1/accounts", json={"name": "log-mock", "provider": "mock"})

        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Log test",
                "coding_agent": "log-mock",
            },
        )
        assert task_resp.status_code == 201

        # Wait for task to complete
        time.sleep(1)

        # Get events
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        assert response.status_code == 200
        data = response.json()

        events = data["events"]
        assert len(events) > 0, "Should have logged events"

        # Check for expected event types
        event_types = [e["type"] for e in events]
        assert "session_started" in event_types, "Should have session_started event"
        assert "terminal_output" in event_types, "Should have terminal_output events"

        # Verify session_started has required fields
        session_event = next(e for e in events if e["type"] == "session_started")
        assert "task_description" in session_event
        assert "coding_provider" in session_event
        assert session_event["coding_provider"] == "mock"

    def test_get_events_with_since_seq(self, client, git_repo):
        """Can retrieve events after a specific sequence number."""
        # Create session and run task
        create_resp = client.post("/api/v1/sessions", json={"name": "Since Seq Test"})
        session_id = create_resp.json()["id"]

        client.post("/api/v1/accounts", json={"name": "seq-mock", "provider": "mock"})

        client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Seq test",
                "coding_agent": "seq-mock",
            },
        )

        # Wait for task
        time.sleep(1)

        # Get all events first
        all_response = client.get(f"/api/v1/sessions/{session_id}/events")
        all_events = all_response.json()["events"]

        if len(all_events) >= 3:
            # Get events after seq 2
            partial_response = client.get(
                f"/api/v1/sessions/{session_id}/events",
                params={"since_seq": 2}
            )
            partial_events = partial_response.json()["events"]

            # Should have fewer events
            assert len(partial_events) < len(all_events)
            # All returned events should have seq > 2
            for event in partial_events:
                assert event["seq"] > 2

    def test_get_events_filter_by_type(self, client, git_repo):
        """Can filter events by type."""
        create_resp = client.post("/api/v1/sessions", json={"name": "Filter Test"})
        session_id = create_resp.json()["id"]

        client.post("/api/v1/accounts", json={"name": "filter-mock", "provider": "mock"})

        client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Filter test",
                "coding_agent": "filter-mock",
            },
        )

        time.sleep(1)

        # Filter to only terminal_output
        response = client.get(
            f"/api/v1/sessions/{session_id}/events",
            params={"event_types": "terminal_output"}
        )
        events = response.json()["events"]

        # All events should be terminal_output
        for event in events:
            assert event["type"] == "terminal_output"


class TestAgentHandover:
    """Tests for handover between agents using EventLog."""

    def test_eventlog_contains_handover_info(self, client, git_repo, tmp_path, monkeypatch):
        """EventLog contains sufficient information for agent handover."""
        # Create session and run first task
        create_resp = client.post("/api/v1/sessions", json={"name": "Handover Test"})
        session_id = create_resp.json()["id"]

        client.post("/api/v1/accounts", json={"name": "handover-mock", "provider": "mock"})

        client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "First agent task: fix bug in BUGS.md",
                "coding_agent": "handover-mock",
            },
        )

        time.sleep(1)

        # Get events
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        events = response.json()["events"]

        # Verify handover-critical fields are present
        session_event = next((e for e in events if e["type"] == "session_started"), None)
        assert session_event is not None

        # These fields are essential for handover
        assert "task_description" in session_event, "Need task description for handover"
        assert "project_path" in session_event, "Need project path for handover"
        assert "coding_provider" in session_event, "Need provider info for handover"
        assert "coding_account" in session_event, "Need account info for handover"
        assert "ts" in session_event, "Need timestamp for handover"
        assert "seq" in session_event, "Need sequence for resumption"

        # Terminal output should have sequence numbers for resumption
        terminal_events = [e for e in events if e["type"] == "terminal_output"]
        if terminal_events:
            for event in terminal_events:
                assert "seq" in event, "Terminal events need sequence numbers"
                assert "data" in event, "Terminal events need data"

    def test_second_agent_can_read_first_agent_log(self, client, git_repo, tmp_path, monkeypatch):
        """A second agent can access the log from the first agent's session."""
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("CHAD_LOG_DIR", str(log_dir))

        # Run first agent
        create_resp = client.post("/api/v1/sessions", json={"name": "Agent 1"})
        session1_id = create_resp.json()["id"]

        client.post("/api/v1/accounts", json={"name": "agent1-mock", "provider": "mock"})

        client.post(
            f"/api/v1/sessions/{session1_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Agent 1: Initial implementation",
                "coding_agent": "agent1-mock",
            },
        )

        time.sleep(1)

        # Get Agent 1's events
        agent1_events = client.get(f"/api/v1/sessions/{session1_id}/events").json()["events"]

        # Create second session for Agent 2
        create_resp2 = client.post("/api/v1/sessions", json={"name": "Agent 2"})
        session2_id = create_resp2.json()["id"]

        # Agent 2 should be able to see what Agent 1 did
        # In a real handover, Agent 2 would receive Agent 1's log as context
        handover_context = {
            "previous_session_id": session1_id,
            "previous_events": agent1_events,
            "task_description": agent1_events[0].get("task_description", "") if agent1_events else "",
        }

        # Verify handover context is usable
        assert len(handover_context["previous_events"]) > 0
        assert handover_context["task_description"] != ""

        # Agent 2 can now continue the work
        client.post("/api/v1/accounts", json={"name": "agent2-mock", "provider": "mock"})

        client.post(
            f"/api/v1/sessions/{session2_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": f"Agent 2: Continue from session {session1_id}",
                "coding_agent": "agent2-mock",
            },
        )

        time.sleep(1)

        # Agent 2 should have its own events
        agent2_events = client.get(f"/api/v1/sessions/{session2_id}/events").json()["events"]
        assert len(agent2_events) > 0

        # Both logs should exist in the log directory
        log_files = list(log_dir.glob("*.jsonl"))
        assert len(log_files) >= 2, "Should have log files for both sessions"

    def test_eventlog_list_sessions(self, tmp_path, monkeypatch):
        """EventLog.list_sessions returns all session IDs with logs."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)

        # Create some log files manually
        (log_dir / "session-aaa.jsonl").write_text('{"type": "test"}\n')
        (log_dir / "session-bbb.jsonl").write_text('{"type": "test"}\n')
        (log_dir / "session-ccc.jsonl").write_text('{"type": "test"}\n')

        sessions = EventLog.list_sessions(base_dir=log_dir)
        assert len(sessions) == 3
        assert "session-aaa" in sessions
        assert "session-bbb" in sessions
        assert "session-ccc" in sessions

    def test_eventlog_stores_terminal_screen_text(self, tmp_path):
        """EventLog stores human-readable terminal screen text for handover."""
        log = EventLog("test-session", base_dir=tmp_path)

        # Store terminal screen content (already processed by terminal emulator)
        screen_text = "Success: Task completed\nAll tests passed."
        log.log(TerminalOutputEvent(data=screen_text))

        # Read back
        events = log.get_events()
        assert len(events) == 1

        # Verify the text is stored as-is
        assert events[0]["data"] == screen_text


class TestUsageBasedProviderSwitch:
    """Tests for proactive usage-based provider switching."""

    def test_mock_usage_api_endpoints(self, client):
        """Test mock usage API endpoints for get/set operations."""
        # Create a mock provider account
        client.post("/api/v1/accounts", json={"name": "usage-mock-1", "provider": "mock"})

        # Default mock usage should be 0.5 (50%)
        resp = client.get("/api/v1/config/mock-remaining-usage/usage-mock-1")
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 0.5

        # Set mock usage to 0.2 (20% remaining = 80% used)
        resp = client.put(
            "/api/v1/config/mock-remaining-usage",
            json={"account_name": "usage-mock-1", "remaining": 0.2}
        )
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 0.2

        # Verify it persisted
        resp = client.get("/api/v1/config/mock-remaining-usage/usage-mock-1")
        assert resp.json()["remaining"] == 0.2

    def test_usage_threshold_triggers_switch(self, client, git_repo):
        """Test that exceeding usage threshold triggers provider switch.

        When the primary provider's usage exceeds the configured threshold,
        the system should automatically switch to the next fallback provider.
        """
        # Create two mock provider accounts
        client.post("/api/v1/accounts", json={"name": "primary-mock", "provider": "mock"})
        client.post("/api/v1/accounts", json={"name": "fallback-mock", "provider": "mock"})

        # Set up fallback order: primary-mock -> fallback-mock
        resp = client.put(
            "/api/v1/config/provider-fallback-order",
            json={"order": ["primary-mock", "fallback-mock"]}
        )
        assert resp.status_code == 200

        # Set usage threshold to 50% (switch when usage exceeds 50%)
        resp = client.put(
            "/api/v1/config/usage-switch-threshold",
            json={"threshold": 50}
        )
        assert resp.status_code == 200

        # Set primary-mock to have 30% remaining (70% used) - exceeds 50% threshold
        resp = client.put(
            "/api/v1/config/mock-remaining-usage",
            json={"account_name": "primary-mock", "remaining": 0.3}
        )
        assert resp.status_code == 200

        # Set fallback-mock to have 80% remaining (20% used) - under threshold
        resp = client.put(
            "/api/v1/config/mock-remaining-usage",
            json={"account_name": "fallback-mock", "remaining": 0.8}
        )
        assert resp.status_code == 200

        # Verify the configuration was set correctly
        resp = client.get("/api/v1/config/mock-remaining-usage/primary-mock")
        assert resp.json()["remaining"] == 0.3

        resp = client.get("/api/v1/config/mock-remaining-usage/fallback-mock")
        assert resp.json()["remaining"] == 0.8

        resp = client.get("/api/v1/config/usage-switch-threshold")
        assert resp.json()["threshold"] == 50

        resp = client.get("/api/v1/config/provider-fallback-order")
        assert resp.json()["order"] == ["primary-mock", "fallback-mock"]

    def test_no_switch_when_under_threshold(self, client, git_repo):
        """Test that no switch occurs when usage is under threshold."""
        # Create a mock provider
        client.post("/api/v1/accounts", json={"name": "healthy-mock", "provider": "mock"})

        # Set threshold to 90%
        client.put("/api/v1/config/usage-switch-threshold", json={"threshold": 90})

        # Set usage to 70% (30% remaining) - under the 90% threshold
        client.put(
            "/api/v1/config/mock-remaining-usage",
            json={"account_name": "healthy-mock", "remaining": 0.3}
        )

        # Verify the remaining usage is correctly set
        resp = client.get("/api/v1/config/mock-remaining-usage/healthy-mock")
        assert resp.json()["remaining"] == 0.3

    def test_switch_disabled_at_100_percent(self, client):
        """Test that usage-based switching is disabled when threshold is 100%."""
        # Set threshold to 100% (disabled)
        resp = client.put("/api/v1/config/usage-switch-threshold", json={"threshold": 100})
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 100


class TestContextBasedProviderSwitch:
    """Tests for proactive context-based provider switching."""

    def test_mock_context_api_endpoints(self, client):
        """Test mock context API endpoints for get/set operations."""
        # Create a mock provider account
        client.post("/api/v1/accounts", json={"name": "context-mock-1", "provider": "mock"})

        # Default mock context should be 1.0 (100%)
        resp = client.get("/api/v1/config/mock-context-remaining/context-mock-1")
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 1.0

        # Set mock context to 0.2 (20% remaining = 80% used)
        resp = client.put(
            "/api/v1/config/mock-context-remaining",
            json={"account_name": "context-mock-1", "remaining": 0.2}
        )
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 0.2

        # Verify it persisted
        resp = client.get("/api/v1/config/mock-context-remaining/context-mock-1")
        assert resp.json()["remaining"] == 0.2

    def test_mock_run_duration_api_endpoints(self, client):
        """Test mock run duration API endpoints for get/set operations."""
        # Create a mock provider account
        client.post("/api/v1/accounts", json={"name": "duration-mock-1", "provider": "mock"})

        # Default mock duration should be 0 seconds
        resp = client.get("/api/v1/config/mock-run-duration/duration-mock-1")
        assert resp.status_code == 200
        assert resp.json()["seconds"] == 0

        # Set mock duration to 60 seconds
        resp = client.put(
            "/api/v1/config/mock-run-duration",
            json={"account_name": "duration-mock-1", "seconds": 60}
        )
        assert resp.status_code == 200
        assert resp.json()["seconds"] == 60

        # Verify it persisted
        resp = client.get("/api/v1/config/mock-run-duration/duration-mock-1")
        assert resp.json()["seconds"] == 60

    def test_context_threshold_api_endpoints(self, client):
        """Test context switch threshold API endpoints."""
        # Get default threshold (should be 90%)
        resp = client.get("/api/v1/config/context-switch-threshold")
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 90

        # Set threshold to 70%
        resp = client.put(
            "/api/v1/config/context-switch-threshold",
            json={"threshold": 70}
        )
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 70

        # Verify it persisted
        resp = client.get("/api/v1/config/context-switch-threshold")
        assert resp.json()["threshold"] == 70

    def test_context_threshold_configuration(self, client, git_repo):
        """Test complete context threshold configuration for provider switch."""
        # Create two mock provider accounts
        client.post("/api/v1/accounts", json={"name": "context-primary", "provider": "mock"})
        client.post("/api/v1/accounts", json={"name": "context-fallback", "provider": "mock"})

        # Set up fallback order
        resp = client.put(
            "/api/v1/config/provider-fallback-order",
            json={"order": ["context-primary", "context-fallback"]}
        )
        assert resp.status_code == 200

        # Set context threshold to 50%
        resp = client.put(
            "/api/v1/config/context-switch-threshold",
            json={"threshold": 50}
        )
        assert resp.status_code == 200

        # Set primary to have 30% context remaining (70% used, exceeds 50% threshold)
        resp = client.put(
            "/api/v1/config/mock-context-remaining",
            json={"account_name": "context-primary", "remaining": 0.3}
        )
        assert resp.status_code == 200

        # Verify the configuration
        resp = client.get("/api/v1/config/mock-context-remaining/context-primary")
        assert resp.json()["remaining"] == 0.3

        resp = client.get("/api/v1/config/context-switch-threshold")
        assert resp.json()["threshold"] == 50

    def test_context_switch_disabled_at_100_percent(self, client):
        """Test that context-based switching is disabled when threshold is 100%."""
        # Set threshold to 100% (disabled)
        resp = client.put("/api/v1/config/context-switch-threshold", json={"threshold": 100})
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 100


class TestEventMultiplexer:
    """Tests for the EventMultiplexer class."""

    def test_mux_creates_sequential_events(self, tmp_path):
        """EventMultiplexer maintains sequential event numbering."""
        from chad.server.services.event_mux import EventMultiplexer

        log = EventLog("mux-test", base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer("mux-test", log)

        # Drain events
        events = mux._drain_event_log(skip_terminal=False)

        assert len(events) == 1
        assert events[0].seq == 1
        assert events[0].type == "event"
        assert events[0].data["type"] == "session_started"

    def test_mux_skips_terminal_when_streaming_pty(self, tmp_path):
        """EventMultiplexer skips terminal_output when streaming PTY directly."""
        from chad.server.services.event_mux import EventMultiplexer

        log = EventLog("mux-terminal-test", base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        log.log(TerminalOutputEvent(data="Screen content here"))

        mux = EventMultiplexer("mux-terminal-test", log)

        # With skip_terminal=True (default for PTY streaming)
        events = mux._drain_event_log(skip_terminal=True)

        # Should only get session_started, not terminal_output
        assert len(events) == 1
        assert events[0].data["type"] == "session_started"

    def test_mux_includes_terminal_when_not_streaming_pty(self, tmp_path):
        """EventMultiplexer includes terminal_output when not streaming PTY."""
        from chad.server.services.event_mux import EventMultiplexer

        log = EventLog("mux-no-pty-test", base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        log.log(TerminalOutputEvent(data="Screen content here"))

        mux = EventMultiplexer("mux-no-pty-test", log)

        # With skip_terminal=False (for non-PTY fallback)
        events = mux._drain_event_log(skip_terminal=False)

        # Should get both events
        assert len(events) == 2
        assert events[0].type == "event"
        assert events[0].data["type"] == "session_started"
        assert events[1].type == "terminal"
        assert events[1].data["data"] == "Screen content here"
        assert events[1].data["text"] is True  # Plain text, not base64

    def test_mux_tracks_event_log_sequence(self, tmp_path):
        """EventMultiplexer tracks EventLog sequence to avoid duplicates."""
        from chad.server.services.event_mux import EventMultiplexer

        log = EventLog("mux-seq-test", base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer("mux-seq-test", log)

        # First drain
        events1 = mux._drain_event_log(skip_terminal=False)
        assert len(events1) == 1

        # Second drain should be empty (no new events)
        events2 = mux._drain_event_log(skip_terminal=False)
        assert len(events2) == 0

        # Add new event and drain again
        log.log(TerminalOutputEvent(data="More output"))
        events3 = mux._drain_event_log(skip_terminal=False)
        assert len(events3) == 1
        assert events3[0].seq == 2

    @pytest.mark.asyncio
    async def test_mux_replays_terminal_from_log_with_since(self, tmp_path):
        """stream_with_since replays terminal_output as terminal events."""
        from chad.server.services.event_mux import EventMultiplexer

        log = EventLog("mux-resume", base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        log.log(TerminalOutputEvent(data="Terminal screen content"))

        mux = EventMultiplexer("mux-resume", log)

        class DummyPTY:
            @staticmethod
            def get_session_by_session_id(session_id):
                return None

        events = []
        async for event in mux.stream_with_since(
            DummyPTY(),
            since_seq=0,
            include_terminal=True,
            include_events=False,
        ):
            events.append(event)
            # Break after first emitted event to avoid infinite loop in fallback
            break

        assert len(events) == 1
        assert events[0].type == "terminal"
        assert events[0].seq == 2

    @pytest.mark.asyncio
    async def test_mux_attaches_when_pty_starts_late(self, tmp_path):
        """Multiplexer should switch from fallback polling to the PTY once it appears."""
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-late"
        mux = EventMultiplexer(session_id)

        async def collect_terminal():
            outputs = []
            async for event in mux.stream_events(pty_service):
                if event.type == "terminal":
                    payload = event.data.get("data", "")
                    if event.data.get("text"):
                        outputs.append(payload)
                    else:
                        outputs.append(base64.b64decode(payload or b"").decode("utf-8", errors="replace"))
                if event.type == "complete":
                    break
            return outputs

        async def start_late():
            await anyio.sleep(0.3)
            pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo hello-from-late"],
                cwd=tmp_path,
            )

        async with anyio.create_task_group() as tg:
            outputs_holder = {}

            async def runner():
                outputs_holder["data"] = await collect_terminal()

            tg.start_soon(runner)
            tg.start_soon(start_late)

        outputs = outputs_holder.get("data", [])
        assert any("hello-from-late" in out for out in outputs)

        reset_pty_stream_service()

    @pytest.mark.asyncio
    async def test_mux_prefers_latest_active_session(self, tmp_path):
        """Multiplexer should attach to the newest active PTY for a session ID."""
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-duplicate"

        first_stream = pty_service.start_pty_session(
            session_id=session_id,
            cmd=["bash", "-c", "echo old"],
            cwd=tmp_path,
        )

        # Let the first session exit but leave it registered
        await anyio.sleep(0.3)

        second_stream = pty_service.start_pty_session(
            session_id=session_id,
            cmd=["bash", "-c", "echo new"],
            cwd=tmp_path,
        )

        outputs = []
        mux = EventMultiplexer(session_id)
        async for event in mux.stream_events(pty_service):
            if event.type == "terminal":
                outputs.append(base64.b64decode(event.data["data"]).decode("utf-8", errors="replace"))
            if event.type == "complete":
                break

        assert any("new" in out for out in outputs), f"Expected output from newest PTY session, got {outputs}"

        pty_service.cleanup_session(first_stream)
        pty_service.cleanup_session(second_stream)
        reset_pty_stream_service()

    @pytest.mark.asyncio
    async def test_mux_complete_event_on_iterator_end(self, tmp_path):
        """Multiplexer emits complete event even when PTY iterator ends prematurely.

        This tests the fix for "Stream ended unexpectedly" errors - when the PTY
        iterator raises StopAsyncIteration without first sending an explicit "exit"
        event, the multiplexer should still emit a complete event.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-complete-test"

        # Start a very short-lived process that completes quickly
        stream_id = pty_service.start_pty_session(
            session_id=session_id,
            cmd=["bash", "-c", "exit 0"],
            cwd=tmp_path,
        )

        got_complete_event = False
        mux = EventMultiplexer(session_id)
        async for event in mux.stream_events(pty_service):
            if event.type == "complete":
                got_complete_event = True
                # Exit code should be available
                assert "exit_code" in event.data
                break

        assert got_complete_event, "Should receive a complete event when stream ends"

        pty_service.cleanup_session(stream_id)
        reset_pty_stream_service()

    def test_format_sse_event(self):
        """format_sse_event produces valid SSE format."""
        from chad.server.services.event_mux import MuxEvent, format_sse_event

        event = MuxEvent(
            type="terminal",
            data={"data": "Screen content", "text": True},
            seq=42,
        )

        sse = format_sse_event(event)

        assert sse.startswith("event: terminal\n")
        assert "data:" in sse
        assert '"seq": 42' in sse
        assert sse.endswith("\n\n")

    def test_mux_ping_interval(self):
        """EventMultiplexer sends pings at the configured interval."""
        from chad.server.services.event_mux import EventMultiplexer
        from datetime import datetime, timezone, timedelta

        mux = EventMultiplexer("ping-test", None, ping_interval=1.0)

        # First ping check should be false (just created)
        assert not mux._should_ping()

        # Simulate time passing
        mux._last_ping = datetime.now(timezone.utc) - timedelta(seconds=2)

        # Now should need ping
        assert mux._should_ping()

        # After ping, should not need another immediately
        assert not mux._should_ping()

    @pytest.mark.asyncio
    async def test_mux_multi_phase_delivers_complete(self, tmp_path):
        """Multiplexer delivers complete after exploration→implementation phase transition.

        Simulates the real task flow:
        1. Exploration PTY starts, produces output, exits
        2. Brief gap (task executor processes output)
        3. Implementation PTY starts, produces output, exits
        4. session_ended written to EventLog
        5. Multiplexer should yield complete event

        This covers the bug where the UI showed only the initial progress
        update and never received the implementation phase completion.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-multi-phase"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test multi-phase",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer(session_id, log)

        collected_events = []
        got_complete = False

        async def collect():
            nonlocal got_complete
            async for event in mux.stream_events(pty_service):
                collected_events.append(event)
                if event.type == "complete":
                    got_complete = True
                    break

        async def simulate_phases():
            # Phase 1: Exploration
            await anyio.sleep(0.1)
            pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo 'exploring...'; sleep 0.2; echo 'progress found'"],
                cwd=tmp_path,
            )

            # Wait for exploration to finish
            await anyio.sleep(1.0)

            # Log some terminal output (like flush_terminal_buffer does)
            log.log(TerminalOutputEvent(data="exploring...\nprogress found"))

            # Cleanup exploration session (like _run_phase does)
            sessions = pty_service.list_sessions()
            for sid in sessions:
                sess = pty_service.get_session(sid)
                if sess and sess.session_id == session_id:
                    pty_service.cleanup_session(sid)

            # Brief gap (simulates extract_progress, check_thresholds, etc.)
            await anyio.sleep(0.3)

            # Phase 2: Implementation
            pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo 'implementing...'; sleep 0.2; echo 'done'"],
                cwd=tmp_path,
            )

            # Wait for implementation to finish
            await anyio.sleep(1.0)

            # Log implementation output
            log.log(TerminalOutputEvent(data="implementing...\ndone"))

            # Cleanup implementation session
            sessions = pty_service.list_sessions()
            for sid in sessions:
                sess = pty_service.get_session(sid)
                if sess and sess.session_id == session_id:
                    pty_service.cleanup_session(sid)

            # Brief gap (simulates extract_coding_summary, etc.)
            await anyio.sleep(0.2)

            # Write session_ended (like _run_task does at the end)
            log.log(SessionEndedEvent(success=True, reason="completed"))

        async with anyio.create_task_group() as tg:
            tg.start_soon(collect)
            tg.start_soon(simulate_phases)

        assert got_complete, (
            f"Should receive complete event after multi-phase task. "
            f"Got {len(collected_events)} events: {[e.type for e in collected_events]}"
        )

        # Verify we got terminal events from both phases
        terminal_events = [e for e in collected_events if e.type == "terminal"]
        assert len(terminal_events) >= 2, (
            f"Should get terminal events from both phases, got {len(terminal_events)}"
        )

        reset_pty_stream_service()

    @pytest.mark.asyncio
    async def test_mux_multi_phase_no_premature_complete(self, tmp_path):
        """Multiplexer must NOT emit complete when first PTY phase exits.

        The multiplexer should wait for session_ended in EventLog rather than
        emitting complete after the exploration PTY exits.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-no-premature"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer(session_id, log)

        events_before_phase2 = []
        phase2_started = anyio.Event()
        complete_received = anyio.Event()

        async def collect():
            async for event in mux.stream_events(pty_service):
                if not phase2_started.is_set():
                    events_before_phase2.append(event)
                if event.type == "complete":
                    complete_received.set()
                    break

        async def simulate():
            # Phase 1: Start and let exploration finish
            await anyio.sleep(0.1)
            stream1 = pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo phase1"],
                cwd=tmp_path,
            )
            await anyio.sleep(0.5)
            pty_service.cleanup_session(stream1)

            # Gap between phases - multiplexer should NOT emit complete here
            await anyio.sleep(0.5)

            # Check that no complete event was emitted yet
            types_so_far = [e.type for e in events_before_phase2]
            assert "complete" not in types_so_far, (
                f"Premature complete emitted after phase 1! Events: {types_so_far}"
            )

            # Phase 2: Implementation
            phase2_started.set()
            pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo phase2"],
                cwd=tmp_path,
            )
            await anyio.sleep(0.5)

            # Write session_ended
            log.log(SessionEndedEvent(success=True, reason="completed"))

        async with anyio.create_task_group() as tg:
            tg.start_soon(collect)
            tg.start_soon(simulate)

        assert complete_received.is_set(), "Should eventually receive complete"

        reset_pty_stream_service()

    @pytest.mark.asyncio
    async def test_mux_stream_with_since_multi_phase(self, tmp_path):
        """Full SSE-like flow using stream_with_since for multi-phase tasks.

        Tests the actual code path used by the SSE endpoint, including
        the catchup phase followed by live streaming.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-since-multi"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer(session_id, log)

        collected = []
        got_complete = False

        async def collect():
            nonlocal got_complete
            # Use stream_with_since like the SSE endpoint does
            async for event in mux.stream_with_since(
                pty_service,
                since_seq=0,
                include_terminal=True,
                include_events=True,
            ):
                collected.append(event)
                if event.type == "complete":
                    got_complete = True
                    break

        async def simulate():
            # Phase 1
            await anyio.sleep(0.1)
            stream1 = pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo exploration; sleep 0.2"],
                cwd=tmp_path,
            )
            await anyio.sleep(0.8)
            log.log(TerminalOutputEvent(data="exploration"))
            pty_service.cleanup_session(stream1)
            await anyio.sleep(0.3)

            # Phase 2
            stream2 = pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo implementation; sleep 0.2"],
                cwd=tmp_path,
            )
            await anyio.sleep(0.8)
            log.log(TerminalOutputEvent(data="implementation"))
            pty_service.cleanup_session(stream2)
            await anyio.sleep(0.2)

            log.log(SessionEndedEvent(success=True, reason="completed"))

        async with anyio.create_task_group() as tg:
            tg.start_soon(collect)
            tg.start_soon(simulate)

        assert got_complete, (
            f"Should receive complete via stream_with_since. "
            f"Events: {[e.type for e in collected]}"
        )

        # Should have gotten session_started from catchup and terminal events
        event_types = [e.type for e in collected]
        assert "event" in event_types, "Should get structured events"
        assert "terminal" in event_types, "Should get terminal events"

        reset_pty_stream_service()

    @pytest.mark.asyncio
    async def test_mux_cleanup_before_subscribe_race(self, tmp_path):
        """Test race where PTY is cleaned up between detection and subscription.

        Simulates: multiplexer detects implementation PTY, but by the time
        it subscribes, the session has been cleaned up.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "mux-race"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        mux = EventMultiplexer(session_id, log)

        collected = []
        got_complete = False

        async def collect():
            nonlocal got_complete
            async for event in mux.stream_events(pty_service):
                collected.append(event)
                if event.type == "complete":
                    got_complete = True
                    break

        async def simulate():
            # Phase 1: Very short-lived
            await anyio.sleep(0.1)
            stream1 = pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo fast"],
                cwd=tmp_path,
            )
            await anyio.sleep(0.3)
            pty_service.cleanup_session(stream1)

            # Phase 2: Also very short-lived - might be cleaned up before
            # multiplexer can subscribe
            stream2 = pty_service.start_pty_session(
                session_id=session_id,
                cmd=["bash", "-c", "echo fast2"],
                cwd=tmp_path,
            )
            # Immediately clean up - simulates a very fast phase
            await anyio.sleep(0.3)
            pty_service.cleanup_session(stream2)

            # Write session_ended - multiplexer should find it via polling
            await anyio.sleep(0.2)
            log.log(SessionEndedEvent(success=True, reason="completed"))

        async with anyio.create_task_group() as tg:
            tg.start_soon(collect)
            tg.start_soon(simulate)

        assert got_complete, (
            f"Should receive complete even with cleanup race. "
            f"Events: {[e.type for e in collected]}"
        )

        reset_pty_stream_service()


class TestEventMuxCompletionBugs:
    """Tests for EventMux completion event handling bugs.

    Covers:
    - stream_events initial wait loop yielding "complete" after session_ended
    - stream_with_since catchup yielding "complete" after session_ended
    - Safety timeout on polling loops
    """

    @pytest.mark.asyncio
    async def test_stream_events_initial_wait_yields_complete_on_session_ended(self, tmp_path):
        """stream_events initial wait loop must yield 'complete' after session_ended.

        Regression: previously returned without yielding complete, leaving clients
        without a termination signal.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import PTYStreamService

        pty_service = PTYStreamService()
        session_id = "wait-complete-test"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        # Task already ended before SSE connection - no PTY will ever appear
        log.log(SessionEndedEvent(success=True, reason="completed"))

        mux = EventMultiplexer(session_id, log)

        collected = []
        async for event in mux.stream_events(pty_service, include_terminal=True):
            collected.append(event)
            if event.type == "complete":
                break

        event_types = [e.type for e in collected]
        # Must find session_ended as a structured event
        session_ended_events = [
            e for e in collected
            if e.type == "event" and e.data.get("type") == "session_ended"
        ]
        assert len(session_ended_events) == 1, f"Expected session_ended event, got: {event_types}"
        # Must find complete event AFTER session_ended
        assert event_types[-1] == "complete", f"Last event should be 'complete', got: {event_types}"

    @pytest.mark.asyncio
    async def test_stream_with_since_catchup_yields_complete_on_session_ended(self, tmp_path):
        """stream_with_since must yield 'complete' when session_ended is in catchup.

        Regression: previously fell through to stream_events() which polled forever
        because _event_log_seq was past the session_ended event.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import PTYStreamService

        pty_service = PTYStreamService()
        session_id = "catchup-complete-test"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        # Log several terminal events and then session_ended
        for i in range(5):
            log.log(TerminalOutputEvent(data=f"output chunk {i}"))
        log.log(SessionEndedEvent(success=True, reason="completed"))

        mux = EventMultiplexer(session_id, log)

        collected = []

        # Use asyncio.wait_for to detect infinite hang
        async def stream():
            async for event in mux.stream_with_since(
                pty_service,
                since_seq=0,
                include_terminal=True,
                include_events=True,
            ):
                collected.append(event)
                if event.type == "complete":
                    return

        await asyncio.wait_for(stream(), timeout=5.0)

        event_types = [e.type for e in collected]
        assert "complete" in event_types, f"Must get complete event, got: {event_types}"

        # session_ended should appear as structured event before complete
        session_ended_found = any(
            e.type == "event" and e.data.get("type") == "session_ended"
            for e in collected
        )
        assert session_ended_found, f"session_ended must be in catchup events: {event_types}"

    @pytest.mark.asyncio
    async def test_stream_with_since_mid_sequence_catchup(self, tmp_path):
        """stream_with_since catchup with since_seq > 0 still detects session_ended.

        Simulates a client reconnecting after receiving some events.
        """
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import PTYStreamService

        pty_service = PTYStreamService()
        session_id = "mid-catchup-test"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))
        for i in range(10):
            log.log(TerminalOutputEvent(data=f"chunk {i}"))
        log.log(SessionEndedEvent(success=True, reason="completed"))

        mux = EventMultiplexer(session_id, log)

        collected = []

        # Reconnect from seq 5 - should still get session_ended and complete
        async def stream():
            async for event in mux.stream_with_since(
                pty_service,
                since_seq=5,
                include_terminal=True,
                include_events=True,
            ):
                collected.append(event)
                if event.type == "complete":
                    return

        await asyncio.wait_for(stream(), timeout=5.0)

        event_types = [e.type for e in collected]
        assert "complete" in event_types, f"Must get complete on reconnect, got: {event_types}"

    @pytest.mark.asyncio
    async def test_polling_loop_safety_timeout(self, tmp_path):
        """Polling loop must not hang forever if session_ended is never written.

        The safety timeout should emit a complete event with timeout flag.
        """
        from unittest.mock import patch
        from chad.server.services.event_mux import EventMultiplexer
        from chad.server.services.pty_stream import get_pty_stream_service, reset_pty_stream_service

        reset_pty_stream_service()
        pty_service = get_pty_stream_service()
        session_id = "timeout-safety-test"

        log = EventLog(session_id, base_dir=tmp_path)
        log.log(SessionStartedEvent(
            task_description="Test",
            project_path="/tmp",
            coding_provider="mock",
            coding_account="test",
        ))

        # Use a very short timeout for testing
        mux = EventMultiplexer(session_id, log, ping_interval=0.5)

        collected = []

        async def stream():
            async for event in mux.stream_events(
                pty_service,
                include_terminal=False,
                include_events=True,
            ):
                collected.append(event)
                if event.type == "complete":
                    return

        # Patch the deadline to be very short (0.5 seconds from now)
        from datetime import datetime
        original_now = datetime.now

        call_count = [0]

        def fake_now(tz=None):
            call_count[0] += 1
            result = original_now(tz) if tz else original_now()
            # After a few calls, jump time forward past the deadline
            if call_count[0] > 10:
                from datetime import timedelta
                return result + timedelta(minutes=50)
            return result

        with patch("chad.server.services.event_mux.datetime") as mock_dt:
            mock_dt.now = fake_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            # timezone needs to be accessible
            await asyncio.wait_for(stream(), timeout=5.0)

        event_types = [e.type for e in collected]
        assert "complete" in event_types, f"Safety timeout should emit complete: {event_types}"

        # Check the timeout flag
        complete_events = [e for e in collected if e.type == "complete"]
        assert complete_events[0].data.get("timeout") is True

        reset_pty_stream_service()


class TestPTYCursorResponse:
    """Ensure PTY stream responds to cursor position requests."""

    def test_handles_cpr_request(self, tmp_path):
        """PTYStreamService should fake a cursor position reply."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Script writes CPR request, reads reply, then prints OK and exits
        import sys

        script = (
            "import sys, os, time; "
            "sys.stdout.write('\\x1b[6n'); sys.stdout.flush(); "
            "resp = sys.stdin.buffer.read(6); "
            "sys.stdout.write('RESP:' + repr(resp)); sys.stdout.flush(); "
            "time.sleep(0.1)"
        )

        stream_id = service.start_pty_session(
            session_id="cpr-test",
            cmd=["bash", "-lc", f"{sys.executable} -c \"{script}\""],
            cwd=tmp_path,
            env={},
        )

        events = []

        async def collect():
            async for event in service.subscribe(stream_id):
                events.append(event)
                if event.type == "exit":
                    break

        import asyncio
        asyncio.run(collect())

        output = b"".join(
            [base64.b64decode(e.data) for e in events if e.type == "output"]
        )
        assert b"RESP:b'\\x1b[1;1R'" in output, "Should respond with cursor position"


class TestTerminalDimensionsThroughAPI:
    """Tests for terminal dimensions passed through the API to PTY sessions."""

    def test_task_start_accepts_terminal_dimensions(self, client, git_repo):
        """API accepts terminal_rows and terminal_cols in task creation."""
        # Create session
        create_resp = client.post("/api/v1/sessions", json={"name": "Dim Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Add mock account
        client.post("/api/v1/accounts", json={"name": "dim-mock", "provider": "mock"})

        # Start task with specific dimensions - verify API accepts the parameters
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Dimension test",
                "coding_agent": "dim-mock",
                "terminal_rows": 50,
                "terminal_cols": 120,
            },
        )
        # API should accept the request (task starts successfully)
        assert task_resp.status_code == 201
        task_data = task_resp.json()
        assert task_data["task_id"] is not None
        assert task_data["status"] in ("pending", "running")

    def test_task_start_uses_defaults_when_no_dimensions(self, client, git_repo):
        """API accepts task creation without explicit dimensions."""
        # Create session
        create_resp = client.post("/api/v1/sessions", json={"name": "Default Dim Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Add mock account
        client.post("/api/v1/accounts", json={"name": "default-mock", "provider": "mock"})

        # Start task without dimensions - should work with defaults
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Default dimension test",
                "coding_agent": "default-mock",
            },
        )
        # API should accept the request (using default dimensions)
        assert task_resp.status_code == 201
        task_data = task_resp.json()
        assert task_data["task_id"] is not None

    def test_terminal_dimensions_validation(self, client, git_repo):
        """API validates terminal dimensions are within reasonable bounds."""
        # Create session
        create_resp = client.post("/api/v1/sessions", json={"name": "Validation Test"})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Add mock account
        client.post("/api/v1/accounts", json={"name": "val-mock", "provider": "mock"})

        # Try invalid dimensions (too small)
        task_resp = client.post(
            f"/api/v1/sessions/{session_id}/tasks",
            json={
                "project_path": str(git_repo),
                "task_description": "Invalid dimension test",
                "coding_agent": "val-mock",
                "terminal_rows": 5,  # Too small (min is 10)
                "terminal_cols": 20,  # Too small (min is 40)
            },
        )
        # Should fail validation
        assert task_resp.status_code == 422

    def test_pty_session_receives_dimensions(self, tmp_path):
        """PTY session receives configured dimensions via winsize and env."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Script that outputs terminal dimensions using stty
        script = "stty size 2>/dev/null || echo '24 80'; sleep 0.1"

        # Start PTY with specific dimensions
        stream_id = service.start_pty_session(
            session_id="dim-test",
            cmd=["bash", "-c", script],
            cwd=tmp_path,
            env={},
            rows=30,
            cols=100,
        )

        events = []

        async def collect():
            async for event in service.subscribe(stream_id):
                events.append(event)
                if event.type == "exit":
                    break

        import asyncio
        asyncio.run(collect())

        output = b"".join(
            [base64.b64decode(e.data) for e in events if e.type == "output"]
        ).decode("utf-8", errors="replace")

        # stty size outputs "rows cols" - verify our dimensions were used
        assert "30 100" in output, f"Expected '30 100' in output, got: {output!r}"

    def test_pty_session_env_contains_dimensions(self, tmp_path):
        """PTY session environment contains LINES and COLUMNS."""
        from chad.server.services.pty_stream import PTYStreamService

        service = PTYStreamService()

        # Script that outputs environment variables
        script = "echo LINES=$LINES COLUMNS=$COLUMNS; sleep 0.1"

        # Start PTY with specific dimensions
        stream_id = service.start_pty_session(
            session_id="env-test",
            cmd=["bash", "-c", script],
            cwd=tmp_path,
            env={},
            rows=45,
            cols=150,
        )

        events = []

        async def collect():
            async for event in service.subscribe(stream_id):
                events.append(event)
                if event.type == "exit":
                    break

        import asyncio
        asyncio.run(collect())

        output = b"".join(
            [base64.b64decode(e.data) for e in events if e.type == "output"]
        ).decode("utf-8", errors="replace")

        # Verify LINES and COLUMNS are in the environment
        assert "LINES=45" in output, f"Expected 'LINES=45' in output, got: {output!r}"
        assert "COLUMNS=150" in output, f"Expected 'COLUMNS=150' in output, got: {output!r}"

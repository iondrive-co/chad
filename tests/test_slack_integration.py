"""Tests for Slack integration: SlackService and config getters/setters."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from chad.util.config_manager import ConfigManager


class TestSlackConfigManager:
    """Test Slack-related config getter/setter methods."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.config_path = tmp_path / "test_chad.conf"
        self.cm = ConfigManager(config_path=self.config_path)
        # Bootstrap with minimal config
        self.cm.save_config({"password_hash": "x", "encryption_salt": "dGVzdHNhbHQ="})

    def test_slack_enabled_default_false(self):
        assert self.cm.get_slack_enabled() is False

    def test_slack_enabled_roundtrip(self):
        self.cm.set_slack_enabled(True)
        assert self.cm.get_slack_enabled() is True
        self.cm.set_slack_enabled(False)
        assert self.cm.get_slack_enabled() is False

    def test_slack_bot_token_default_none(self):
        assert self.cm.get_slack_bot_token() is None

    def test_slack_bot_token_roundtrip(self):
        self.cm.set_slack_bot_token("xoxb-test-token-123")
        assert self.cm.get_slack_bot_token() == "xoxb-test-token-123"

    def test_slack_bot_token_clear(self):
        self.cm.set_slack_bot_token("xoxb-token")
        self.cm.set_slack_bot_token(None)
        assert self.cm.get_slack_bot_token() is None

    def test_slack_channel_default_none(self):
        assert self.cm.get_slack_channel() is None

    def test_slack_channel_roundtrip(self):
        self.cm.set_slack_channel("C0123456789")
        assert self.cm.get_slack_channel() == "C0123456789"

    def test_slack_channel_clear(self):
        self.cm.set_slack_channel("C0123456789")
        self.cm.set_slack_channel(None)
        assert self.cm.get_slack_channel() is None


class TestSlackService:
    """Test SlackService methods with mocked HTTP."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.config_path = tmp_path / "test_chad.conf"
        self.cm = ConfigManager(config_path=self.config_path)
        self.cm.save_config({
            "password_hash": "x",
            "encryption_salt": "dGVzdHNhbHQ=",
            "slack_enabled": True,
            "slack_bot_token": "xoxb-test-token",
            "slack_channel": "C12345",
        })

    @patch("chad.server.services.slack_service.get_config_manager")
    def test_post_milestone_success(self, mock_get_cm):
        mock_get_cm.return_value = self.cm
        from chad.server.services.slack_service import SlackService
        svc = SlackService()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        svc._http = MagicMock()
        svc._http.post.return_value = mock_resp

        result = svc.post_milestone("abc123", "coding_complete", "Coding Complete", "Task finished")
        assert result is True

        call_args = svc._http.post.call_args
        assert call_args[0][0] == "https://slack.com/api/chat.postMessage"
        assert call_args[1]["headers"]["Authorization"] == "Bearer xoxb-test-token"
        body = call_args[1]["json"]
        assert body["channel"] == "C12345"
        assert "Coding Complete" in body["text"]
        assert "abc123" in body["text"]

    @patch("chad.server.services.slack_service.get_config_manager")
    def test_post_milestone_disabled(self, mock_get_cm):
        self.cm.set_slack_enabled(False)
        mock_get_cm.return_value = self.cm
        from chad.server.services.slack_service import SlackService
        svc = SlackService()
        svc._http = MagicMock()

        result = svc.post_milestone("abc", "coding_complete", "Done", "Summary")
        assert result is False
        svc._http.post.assert_not_called()

    @patch("chad.server.services.slack_service.get_config_manager")
    def test_post_milestone_api_error(self, mock_get_cm):
        mock_get_cm.return_value = self.cm
        from chad.server.services.slack_service import SlackService
        svc = SlackService()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}
        svc._http = MagicMock()
        svc._http.post.return_value = mock_resp

        result = svc.post_milestone("abc", "coding_complete", "Done", "Summary")
        assert result is False

    @patch("chad.server.services.slack_service.get_config_manager")
    def test_post_milestone_no_token(self, mock_get_cm):
        self.cm.set_slack_bot_token(None)
        mock_get_cm.return_value = self.cm
        from chad.server.services.slack_service import SlackService
        svc = SlackService()
        svc._http = MagicMock()

        result = svc.post_milestone("abc", "coding_complete", "Done", "Summary")
        assert result is False


class TestSlackConfigEndpoints:
    """Test the /api/v1/config/slack GET and PUT endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from chad.server.main import create_app
        self.app = create_app()
        self.client = TestClient(self.app)

        self.config_path = tmp_path / "test_chad.conf"
        self.cm = ConfigManager(config_path=self.config_path)
        self.cm.save_config({"password_hash": "x", "encryption_salt": "dGVzdHNhbHQ="})

        self._patcher = patch("chad.server.state._config_manager", self.cm)
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_get_slack_defaults(self):
        resp = self.client.get("/api/v1/config/slack")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["channel"] is None
        assert data["has_token"] is False

    def test_put_slack_settings(self):
        resp = self.client.put("/api/v1/config/slack", json={
            "enabled": True,
            "channel": "C999",
            "bot_token": "xoxb-abc",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["channel"] == "C999"
        assert data["has_token"] is True

        # Verify persisted
        assert self.cm.get_slack_enabled() is True
        assert self.cm.get_slack_channel() == "C999"
        assert self.cm.get_slack_bot_token() == "xoxb-abc"

    def test_put_partial_update(self):
        self.cm.set_slack_enabled(True)
        self.cm.set_slack_channel("C111")
        resp = self.client.put("/api/v1/config/slack", json={"channel": "C222"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True  # unchanged
        assert data["channel"] == "C222"  # updated

    def test_slack_test_no_token(self):
        resp = self.client.post("/api/v1/slack/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "token" in data["error"].lower()

    def test_slack_test_no_channel(self):
        self.cm.set_slack_bot_token("xoxb-test")
        resp = self.client.post("/api/v1/slack/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "channel" in data["error"].lower()


class TestMilestoneSlackHook:
    """Test that _emit_milestone triggers Slack notification."""

    def test_emit_milestone_calls_slack(self):
        from chad.server.services.session_event_loop import SessionEventLoop

        # Create a minimal event loop
        loop = SessionEventLoop.__new__(SessionEventLoop)
        loop.session_id = "test-sess"
        loop.event_log = None
        loop._milestone_seq = 0
        loop._milestones = []
        loop._milestone_lock = __import__("threading").Lock()
        loop._emit_fn = MagicMock()
        loop._notify_slack = True

        mock_svc = MagicMock()
        with patch(
            "chad.server.services.slack_service._slack_service",
            mock_svc,
        ), patch(
            "chad.server.services.slack_service.get_slack_service",
            return_value=mock_svc,
        ):
            loop._emit_milestone("coding_complete", "Task done")

        mock_svc.post_milestone_async.assert_called_once_with(
            "test-sess", "coding_complete", "Coding Complete", "Task done",
        )

    def test_emit_milestone_skips_slack_by_default(self):
        """notify_slack defaults to False so tests don't leak real Slack calls."""
        from chad.server.services.session_event_loop import SessionEventLoop

        loop = SessionEventLoop.__new__(SessionEventLoop)
        loop.session_id = "test-sess"
        loop.event_log = None
        loop._milestone_seq = 0
        loop._milestones = []
        loop._milestone_lock = __import__("threading").Lock()
        loop._emit_fn = MagicMock()
        loop._notify_slack = False

        mock_svc = MagicMock()
        with patch(
            "chad.server.services.slack_service.get_slack_service",
            return_value=mock_svc,
        ):
            loop._emit_milestone("coding_complete", "Task done")

        mock_svc.post_milestone_async.assert_not_called()

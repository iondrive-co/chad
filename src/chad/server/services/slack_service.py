"""Slack integration service for milestone notifications and message forwarding."""

import hashlib
import hmac
import logging
import threading
import time

import httpx

from chad.server.state import get_config_manager

logger = logging.getLogger(__name__)

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackService:
    """Posts milestone notifications to Slack and forwards incoming messages to sessions."""

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=10)

    def _is_enabled(self) -> bool:
        cm = get_config_manager()
        return cm.get_slack_enabled() and bool(cm.get_slack_bot_token()) and bool(cm.get_slack_channel())

    def get_signing_secret(self) -> str | None:
        """Return the configured Slack signing secret, or None if unset."""
        cm = get_config_manager()
        return cm.get_slack_signing_secret()

    def post_milestone(
        self,
        session_id: str,
        milestone_type: str,
        title: str,
        summary: str,
    ) -> bool:
        """Post a milestone notification to the configured Slack channel.

        Returns True if the message was posted successfully.
        """
        if not self._is_enabled():
            return False

        cm = get_config_manager()
        token = cm.get_slack_bot_token()
        channel = cm.get_slack_channel()

        text = f"*{title}* \u2014 {summary}\n_Session {session_id} \u00b7 {milestone_type}_"

        try:
            resp = self._http.post(
                SLACK_POST_MESSAGE_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel, "text": text},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Slack API error: %s", data.get("error", "unknown"))
                return False
            return True
        except Exception:
            logger.warning("Failed to post milestone to Slack", exc_info=True)
            return False

    def post_milestone_async(
        self,
        session_id: str,
        milestone_type: str,
        title: str,
        summary: str,
    ) -> None:
        """Fire-and-forget milestone post in a background thread."""
        if not self._is_enabled():
            return
        t = threading.Thread(
            target=self.post_milestone,
            args=(session_id, milestone_type, title, summary),
            daemon=True,
        )
        t.start()

    def forward_message_to_session(self, text: str, session_id: str | None = None) -> bool:
        """Forward a Slack message to an active Chad session.

        If session_id is None, forwards to the most recently active session.
        Returns True if the message was delivered.
        """
        from chad.server.services.session_manager import get_session_manager
        from chad.server.services.task_executor import get_task_executor

        sm = get_session_manager()

        if session_id:
            session = sm.get_session(session_id)
            if not session or not session.active:
                return False
            target_sessions = [session]
        else:
            target_sessions = sm.get_active_sessions()
            if not target_sessions:
                return False
            target_sessions.sort(key=lambda s: s.last_activity, reverse=True)
            target_sessions = target_sessions[:1]

        executor = get_task_executor()
        session = target_sessions[0]

        # Find the active task's event loop for this session
        task = executor.get_running_task_for_session(session.id)
        event_loop = task._session_event_loop if task else None
        if event_loop is None:
            return False

        event_loop.enqueue_message(f"[Slack] {text}", source="slack")
        return True

    @staticmethod
    def verify_webhook_signature(
        signing_secret: str,
        timestamp: str,
        signature: str,
        body: bytes,
    ) -> bool:
        """Verify a Slack webhook request signature (v0).

        Args:
            signing_secret: The Slack app's signing secret
            timestamp: X-Slack-Request-Timestamp header
            signature: X-Slack-Signature header
            body: Raw request body bytes
        """
        try:
            if abs(time.time() - float(timestamp)) > 300:
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed = "v0=" + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)


# Global singleton
_slack_service: SlackService | None = None


def get_slack_service() -> SlackService:
    """Get the global SlackService instance."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackService()
    return _slack_service

"""Slack integration service for outgoing milestone notifications."""

import logging
import threading

import httpx

from chad.server.state import get_config_manager

logger = logging.getLogger(__name__)

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackService:
    """Posts milestone notifications to Slack."""

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=10)

    def _is_enabled(self) -> bool:
        cm = get_config_manager()
        return cm.get_slack_enabled() and bool(cm.get_slack_bot_token()) and bool(cm.get_slack_channel())

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


# Global singleton
_slack_service: SlackService | None = None


def get_slack_service() -> SlackService:
    """Get the global SlackService instance."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackService()
    return _slack_service


def reset_slack_service() -> None:
    """Reset the global SlackService singleton (for testing)."""
    global _slack_service
    _slack_service = None

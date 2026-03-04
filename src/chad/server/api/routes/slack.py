"""Slack test endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server.services import slack_service
from chad.server.state import get_config_manager

router = APIRouter()


class SlackTestResult(BaseModel):
    """Result of a Slack connection test."""

    ok: bool = Field(description="Whether the test message was posted")
    error: str | None = Field(default=None, description="Error message if failed")


@router.post("/slack/test", response_model=SlackTestResult)
async def test_slack_connection() -> SlackTestResult:
    """Send a test message to the configured Slack channel.

    Use this to verify your bot token and channel ID are correct.
    """
    cm = get_config_manager()
    if not cm.get_slack_bot_token():
        return SlackTestResult(ok=False, error="No bot token configured")
    if not cm.get_slack_channel():
        return SlackTestResult(ok=False, error="No channel ID configured")

    svc = slack_service.get_slack_service()
    ok = svc.post_milestone("test", "connection_test", "Connection Test", "Chad is connected to Slack")
    if not ok:
        import httpx
        token = cm.get_slack_bot_token()
        channel = cm.get_slack_channel()
        try:
            resp = httpx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel, "text": "Chad connection test - this channel is now linked."},
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                return SlackTestResult(ok=False, error=f"Slack API: {data.get('error', 'unknown')}")
            return SlackTestResult(ok=True)
        except Exception as exc:
            return SlackTestResult(ok=False, error=str(exc))

    return SlackTestResult(ok=True)

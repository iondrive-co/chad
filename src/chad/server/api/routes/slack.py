"""Slack webhook and test endpoints."""

import json

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from chad.server.services.slack_service import get_slack_service
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
    # Signing secret is optional; do not require for test

    svc = get_slack_service()
    # Temporarily force enabled for the test
    ok = svc.post_milestone("test", "connection_test", "Connection Test", "Chad is connected to Slack")
    if not ok:
        # Try posting directly to give a better error
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


@router.post("/slack/webhook")
async def slack_webhook(request: Request) -> Response:
    """Receive Slack event subscriptions.

    Handles:
    - URL verification challenges (Slack sends these when setting up the webhook)
    - Message events (forwarded to the active Chad session)
    """
    body = await request.body()

    # If a signing secret is configured, verify request signature
    cm = get_config_manager()
    signing_secret = cm.get_slack_signing_secret()
    if signing_secret:
        ts = request.headers.get("X-Slack-Request-Timestamp")
        sig = request.headers.get("X-Slack-Signature")
        from chad.server.services.slack_service import SlackService

        if not ts or not sig or not SlackService.verify_webhook_signature(signing_secret, ts, sig, body):
            return Response(status_code=401, content="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400, content="Invalid JSON")

    # URL verification challenge
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

    # Event callback
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})

        # Only handle user messages (not bot messages to avoid loops)
        if event.get("type") == "message" and not event.get("bot_id"):
            text = event.get("text", "")
            if text:
                slack_service = get_slack_service()
                slack_service.forward_message_to_session(text)

    return Response(status_code=200)

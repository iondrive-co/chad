"""Reading one account's usage, for everything that shows it.

The API's usage endpoint and the tray menu ask the same question of the same
providers, so they ask it here. Reading is blocking and can mean an HTTPS round
trip (Anthropic) or a scan of session snapshots (Codex) — never call it on an
event loop or a menu thread.
"""

from __future__ import annotations

from dataclasses import dataclass

from chad.util import provider_login
from chad.util.providers import ModelConfig, create_provider


@dataclass(frozen=True)
class UsageReading:
    """One account's usage, as far as its provider will say.

    A percentage of None means the reading was unavailable, not zero: a
    provider that reports no usage, an account that is logged out, or a call
    that failed. Reporting 0% instead reads as "plenty of room left".
    """

    account_name: str
    provider: str
    session_pct: float | None = None
    weekly_pct: float | None = None
    session_reset_eta: str | None = None
    weekly_reset_eta: str | None = None
    usage_as_of: str | None = None
    logged_out: bool = False


def read_account_usage(
    account_name: str, provider: str, model: str = "default"
) -> UsageReading:
    """Ask an account's provider what it has used. Blocking."""
    instance = create_provider(ModelConfig(
        provider=provider, model_name=model, account_name=account_name,
    ))
    return UsageReading(
        account_name=account_name,
        provider=provider,
        session_pct=instance.get_session_usage_percentage(),
        weekly_pct=instance.get_weekly_usage_percentage(),
        session_reset_eta=instance.get_session_reset_eta(),
        weekly_reset_eta=instance.get_weekly_reset_eta(),
        usage_as_of=instance.get_usage_as_of(),
        logged_out=not provider_login.is_logged_in(provider, account_name),
    )

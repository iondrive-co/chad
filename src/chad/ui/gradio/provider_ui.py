from __future__ import annotations

import base64
import json
import math
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr

from chad.util.utils import platform_path, safe_home
from chad.util.model_catalog import ModelCatalog
from chad.util.installer import AIToolInstaller

if TYPE_CHECKING:
    from chad.ui.client import APIClient


class ProviderUIManager:
    """Provider management and display helpers for the web UI."""

    _ALL_PROVIDERS = {"anthropic", "openai", "gemini", "qwen", "mistral", "opencode", "kimi", "mock"}
    SUPPORTED_PROVIDERS = {"anthropic", "openai", "gemini", "qwen", "mistral", "opencode", "kimi", "mock"}  # For backwards compat
    OPENAI_REASONING_LEVELS = ["default", "low", "medium", "high", "xhigh"]

    def get_supported_providers(self) -> set[str]:
        """Get the set of supported providers based on dev mode."""
        if self.dev_mode:
            return self._ALL_PROVIDERS
        return self._ALL_PROVIDERS - {"mock"}

    def get_provider_choices(self) -> list[str]:
        """Get ordered list of provider type choices for dropdowns."""
        # Fixed order for consistent UI
        order = ["anthropic", "openai", "gemini", "qwen", "mistral", "opencode", "kimi", "mock"]
        supported = self.get_supported_providers()
        return [p for p in order if p in supported]

    def __init__(
        self,
        api_client: "APIClient",
        model_catalog: ModelCatalog | None = None,
        installer: AIToolInstaller | None = None,
        dev_mode: bool = False,
    ):
        self.api_client = api_client
        self.model_catalog = model_catalog or ModelCatalog(api_client)
        self.installer = installer or AIToolInstaller()
        self.dev_mode = dev_mode

    def set_mock_remaining_usage(self, account_name: str, value: float) -> None:
        """Set mock remaining usage for testing handover (0.0-1.0)."""
        clamped = max(0.0, min(1.0, value))
        try:
            self.api_client.set_mock_remaining_usage(account_name, clamped)
        except Exception:
            pass  # Ignore errors setting mock usage

    def get_mock_remaining_usage(self, account_name: str) -> float:
        """Get mock remaining usage (0.0-1.0), defaults to 0.5."""
        try:
            return self.api_client.get_mock_remaining_usage(account_name)
        except Exception:
            return 0.5  # Default to 50%

    def set_mock_run_duration_seconds(self, account_name: str, seconds: int) -> None:
        """Set mock run duration in seconds for handover testing."""
        try:
            clamped = max(0, min(3600, int(seconds)))
            self.api_client.set_mock_run_duration_seconds(account_name, clamped)
        except Exception:
            pass  # Ignore errors setting mock run duration

    def get_mock_run_duration_seconds(self, account_name: str) -> int:
        """Get mock run duration in seconds, defaults to 0."""
        try:
            value = self.api_client.get_mock_run_duration_seconds(account_name)
            return max(0, min(3600, int(value)))
        except Exception:
            return 0

    def get_provider_card_items(self, accounts=None) -> list[tuple[str, str]]:
        """Return provider account items for card display."""
        if accounts is None:
            accounts = self.api_client.list_accounts()
        account_items = [(acc.name, acc.provider) for acc in accounts]

        # In screenshot mode, ensure deterministic ordering for tests
        if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
            # Use the same order as defined in MOCK_ACCOUNTS for consistency
            from .verification.screenshot_fixtures import MOCK_ACCOUNTS
            screenshot_order = []
            other_accounts = []

            # First add accounts in the same order as MOCK_ACCOUNTS
            for mock_name in MOCK_ACCOUNTS.keys():
                for name, provider in account_items:
                    if name == mock_name:
                        screenshot_order.append((name, provider))
                        break

            # Add any additional accounts not in MOCK_ACCOUNTS
            mock_names = set(MOCK_ACCOUNTS.keys())
            for name, provider in account_items:
                if name not in mock_names:
                    other_accounts.append((name, provider))

            account_items = screenshot_order + other_accounts

            # Apply the duplication logic for visual testing
            if len(account_items) >= 3:
                account_items = account_items[:3] + [account_items[1]] + account_items[3:]

        return account_items

    def format_provider_header(self, account_name: str, provider: str, idx: int) -> str:
        """Return the provider card header HTML."""
        header_class = "provider-card__header-text"
        if os.environ.get("CHAD_SCREENSHOT_MODE") == "1" and idx > 0:
            header_class = "provider-card__header-text-secondary"
        return f'<span class="{header_class}">{account_name} ({provider})</span>'

    def _get_account_role(self, account_name: str) -> str | None:
        """Return the role assigned to the account, if any."""
        try:
            account = self.api_client.get_account(account_name)
            return account.role
        except Exception:
            return None

    def get_provider_usage(self, account_name: str) -> str:
        """Get usage text for a single provider."""
        # Check for screenshot mode - return synthetic data
        if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
            from .verification.screenshot_fixtures import get_mock_usage

            return get_mock_usage(account_name)

        try:
            account = self.api_client.get_account(account_name)
            provider = account.provider
        except Exception:
            provider = None

        if not provider:
            return "Select a provider to see usage details."

        if provider == "openai":
            status_text = self._get_codex_usage(account_name)
        elif provider == "anthropic":
            status_text = self._get_claude_usage(account_name)
        elif provider == "gemini":
            status_text = self._get_gemini_usage()
        elif provider == "qwen":
            status_text = self._get_qwen_usage()
        elif provider == "mistral":
            status_text = self._get_mistral_usage()
        elif provider == "opencode":
            status_text = self._get_opencode_usage(account_name)
        elif provider == "kimi":
            status_text = self._get_kimi_usage(account_name)
        elif provider == "mock":
            status_text = ""  # Mock provider uses slider input instead
        else:
            status_text = "⚠️ **Unknown provider**"

        return status_text

    @staticmethod
    def _normalize_pct(value: float | int | None) -> float:
        """Normalize a utilization percentage into the 0-100 range."""
        try:
            pct = float(0.0 if value is None else value)
        except (TypeError, ValueError):
            return 0.0

        if math.isnan(pct) or math.isinf(pct):
            return 0.0

        return max(0.0, min(100.0, pct))

    def _progress_bar(self, utilization_pct: float | int | None, width: int = 20) -> str:
        """Create a text progress bar for usage displays."""
        pct = self._normalize_pct(utilization_pct)
        filled = int(pct / (100 / width))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _format_usd_from_cents(value: float | int | None) -> str:
        """Format a cents value as a USD string."""
        try:
            cents = float(0.0 if value is None else value)
        except (TypeError, ValueError):
            cents = 0.0

        if math.isnan(cents) or math.isinf(cents):
            cents = 0.0

        dollars = max(0.0, cents) / 100.0
        return f"${dollars:.2f}"

    def get_remaining_usage(self, account_name: str) -> float:
        """Get remaining usage as 0.0-1.0 (1.0 = full capacity remaining).

        Used to sort providers by availability - highest remaining usage first.
        """
        try:
            account = self.api_client.get_account(account_name)
            provider = account.provider
        except Exception:
            provider = None

        if not provider:
            return 0.0

        if provider == "anthropic":
            return self._get_claude_remaining_usage(account_name)
        if provider == "openai":
            return self._get_codex_remaining_usage(account_name)
        if provider == "gemini":
            return self._get_gemini_remaining_usage()
        if provider == "qwen":
            return self._get_qwen_remaining_usage()
        if provider == "mistral":
            return self._get_mistral_remaining_usage()
        if provider == "opencode":
            return self._get_opencode_remaining_usage(account_name)
        if provider == "kimi":
            return self._get_kimi_remaining_usage(account_name)
        if provider == "mock":
            return self.get_mock_remaining_usage(account_name)

        return 0.3  # Unknown provider, bias low

    def _get_claude_remaining_usage(self, account_name: str) -> float:
        """Get Claude remaining usage from API (0.0-1.0)."""
        import requests

        config_dir = self._get_claude_config_dir(account_name)
        creds_file = config_dir / ".credentials.json"
        if not creds_file.exists():
            return 0.0

        try:
            with open(creds_file, encoding="utf-8") as f:
                creds = json.load(f)

            oauth_data = creds.get("claudeAiOauth", {})
            access_token = oauth_data.get("accessToken", "")
            if not access_token:
                return 0.0

            response = requests.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "User-Agent": "claude-code/2.0.32",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )

            if response.status_code != 200:
                return 0.3  # API error, bias low

            usage_data = response.json()
            five_hour = usage_data.get("five_hour", {})
            util = self._normalize_pct(five_hour.get("utilization"))
            return max(0.0, min(1.0, 1.0 - (util / 100.0)))

        except Exception:
            return 0.3  # Error, bias low

    def _get_codex_remaining_usage(self, account_name: str) -> float:
        """Get Codex remaining usage from session files (0.0-1.0)."""
        self._sync_codex_home(account_name)
        codex_home = self._get_codex_home(account_name)
        auth_file = codex_home / ".codex" / "auth.json"
        if not auth_file.exists():
            return 0.0

        sessions_dir = codex_home / ".codex" / "sessions"
        if not sessions_dir.exists():
            return 0.8  # Logged in but no sessions, assume mostly available

        session_files: list[tuple[float, Path]] = []
        for root, _, files in os.walk(sessions_dir):
            for filename in files:
                if filename.endswith(".jsonl"):
                    path = platform_path(root) / filename
                    session_files.append((path.stat().st_mtime, path))

        if not session_files:
            return 0.8

        session_files.sort(reverse=True)
        latest_session = session_files[0][1]

        try:
            rate_limits = None
            with open(latest_session, encoding="utf-8") as f:
                for line in f:
                    if "rate_limits" in line:
                        data = json.loads(line.strip())
                        if data.get("type") == "event_msg":
                            payload = data.get("payload", {})
                            if payload.get("type") == "token_count":
                                rate_limits = payload.get("rate_limits")

            if rate_limits:
                primary = rate_limits.get("primary", {})
                if primary:
                    util = self._normalize_pct(primary.get("used_percent"))
                    return max(0.0, min(1.0, 1.0 - (util / 100.0)))

        except Exception:
            pass

        return 0.3  # Error, bias low

    def _get_gemini_remaining_usage(self) -> float:
        """Get Gemini remaining usage (0.0-1.0) by counting today's requests.

        Reads from the Gemini usage JSONL file written by the provider.
        Uses a conservative daily limit based on free-tier Gemini API limits.
        """
        from datetime import datetime, timezone

        from chad.util.providers import _read_gemini_usage

        oauth_file = Path.home() / ".gemini" / "oauth_creds.json"
        if not oauth_file.exists():
            return 0.0

        records = _read_gemini_usage()
        if not records:
            return 1.0  # Logged in, no usage yet

        today_requests = 0
        today = datetime.now(timezone.utc).date()

        for rec in records:
            ts = rec.get("timestamp", "")
            if ts:
                try:
                    rec_date = datetime.fromisoformat(ts).date()
                    if rec_date == today:
                        today_requests += 1
                except (ValueError, AttributeError):
                    pass

        # Free-tier flash models allow ~500 RPD; use conservative limit so
        # the percentage is meaningful even with a few requests.
        daily_limit = 100
        used_pct = today_requests / daily_limit
        return max(0.0, min(1.0, 1.0 - used_pct))

    def _get_mistral_remaining_usage(self) -> float:
        """Get Mistral remaining usage (0.0-1.0) by counting today's requests.

        Calculates remaining daily quota based on local session files.
        """
        from datetime import datetime, timezone

        vibe_config = Path.home() / ".vibe" / "config.toml"
        if not vibe_config.exists():
            return 0.0

        sessions_dir = Path.home() / ".vibe" / "logs" / "session"
        if not sessions_dir.exists():
            return 1.0  # Logged in, no usage yet

        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 1000

        for session_file in sessions_dir.glob("session_*.json"):
            try:
                mtime = datetime.fromtimestamp(session_file.stat().st_mtime, tz=timezone.utc)
                if mtime.date() != today:
                    continue

                with open(session_file, encoding="utf-8") as f:
                    data = json.load(f)

                metadata = data.get("metadata", {})
                stats = metadata.get("stats", {})
                prompt_count = stats.get("prompt_count", 1)
                today_requests += prompt_count
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        # Return remaining as 0.0-1.0 (1.0 = full capacity)
        used_pct = today_requests / daily_limit
        return max(0.0, min(1.0, 1.0 - used_pct))

    def _get_qwen_remaining_usage(self) -> float:
        """Get Qwen remaining usage (0.0-1.0) by counting today's requests.

        Calculates remaining daily quota based on local session files.
        """
        from datetime import datetime, timezone

        qwen_oauth = Path.home() / ".qwen" / "oauth_creds.json"
        if not qwen_oauth.exists():
            return 0.0

        projects_dir = Path.home() / ".qwen" / "projects"
        if not projects_dir.exists():
            return 1.0  # Logged in, no usage yet

        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000

        for session_file in projects_dir.glob("*/chats/*.jsonl"):
            try:
                with open(session_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("type") == "assistant":
                                timestamp = event.get("timestamp", "")
                                if timestamp:
                                    try:
                                        msg_date = datetime.fromisoformat(
                                            timestamp.replace("Z", "+00:00")
                                        ).date()
                                        if msg_date == today:
                                            today_requests += 1
                                    except (ValueError, AttributeError):
                                        pass
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        # Return remaining as 0.0-1.0 (1.0 = full capacity)
        used_pct = today_requests / daily_limit
        return max(0.0, min(1.0, 1.0 - used_pct))

    def provider_state(self, card_slots: int, pending_delete: str | None = None) -> tuple:
        """Build UI state for provider cards (per-account controls)."""
        account_items = self.get_provider_card_items()

        outputs: list = []
        for idx in range(card_slots):
            if idx < len(account_items):
                account_name, provider = account_items[idx]
                header = self.format_provider_header(account_name, provider, idx)
                usage = self.get_provider_usage(account_name)
                is_mock = provider == "mock"

                delete_btn_update = (
                    gr.update(value="✓", variant="stop")
                    if pending_delete == account_name
                    else gr.update(value="🗑︎", variant="secondary")
                )

                # Mock providers use slider, others use markdown
                if is_mock:
                    mock_value = int((1.0 - self.get_mock_remaining_usage(account_name)) * 100)
                    mock_duration = self.get_mock_run_duration_seconds(account_name)
                    usage_update = gr.update(visible=False)
                    slider_update = gr.update(visible=True, value=mock_value)
                    duration_slider_update = gr.update(visible=True, value=mock_duration)
                else:
                    usage_update = gr.update(visible=True, value=usage)
                    slider_update = gr.update(visible=False)
                    duration_slider_update = gr.update(visible=False)

                outputs.extend(
                    [
                        gr.update(visible=True),  # Show column
                        gr.update(visible=True),  # Show card group
                        header,
                        account_name,
                        usage_update,
                        slider_update,
                        duration_slider_update,
                        delete_btn_update,
                    ]
                )
            else:
                outputs.extend(
                    [
                        gr.update(visible=False),  # Hide column
                        gr.update(visible=False),  # Hide card group
                        "",
                        "",
                        gr.update(visible=False),  # usage_box hidden
                        gr.update(visible=False),  # slider hidden
                        gr.update(visible=False),  # duration slider hidden
                        gr.update(value="🗑︎", variant="secondary"),
                    ]
                )

        return tuple(outputs)

    def provider_action_response(self, feedback: str, card_slots: int, pending_delete: str | None = None):
        """Return standard provider panel updates with feedback text."""
        return (feedback, *self.provider_state(card_slots, pending_delete=pending_delete))

    def provider_state_with_confirm(self, pending_delete: str, card_slots: int) -> tuple:
        """Build provider state with one delete button showing 'Confirm?'."""
        return self.provider_state(card_slots, pending_delete=pending_delete)

    def _get_codex_home(self, account_name: str) -> Path:
        """Get the isolated HOME directory for a Codex account."""
        # Use temp home in screenshot mode
        temp_home = os.environ.get("CHAD_TEMP_HOME")
        base_home = safe_home() if temp_home else safe_home(ignore_temp_home=True)
        return platform_path(base_home / ".chad" / "codex-homes" / account_name)

    def _sync_codex_home(self, account_name: str) -> None:
        """Sync real-home Codex data into the isolated home.

        IMPORTANT: This only syncs files that DON'T already exist in the isolated home.
        Once an account has its own auth.json, it should never be overwritten by the
        real home's auth - that would cause multiple accounts to share credentials.
        """
        isolated_home = platform_path(self._get_codex_home(account_name) / ".codex")
        real_home = platform_path(safe_home(ignore_temp_home=True) / ".codex")
        if not real_home.exists():
            return

        isolated_home.mkdir(parents=True, exist_ok=True)

        def sync_file_if_missing(src: Path, dest: Path) -> None:
            """Only copy if destination doesn't exist - never overwrite existing auth."""
            try:
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            except OSError:
                return

        def sync_tree_if_missing(src_dir: Path, dest_dir: Path) -> None:
            """Sync tree but never overwrite existing files."""
            for root, _, files in os.walk(src_dir):
                for filename in files:
                    src_path = platform_path(root) / filename
                    rel_path = src_path.relative_to(src_dir)
                    dest_path = dest_dir / rel_path
                    sync_file_if_missing(src_path, dest_path)

        # Only sync auth.json if the isolated home doesn't have one yet
        # This prevents overwriting account-specific credentials
        sync_file_if_missing(real_home / "auth.json", isolated_home / "auth.json")
        sync_file_if_missing(real_home / "config.toml", isolated_home / "config.toml")
        if (real_home / "sessions").exists():
            sync_tree_if_missing(real_home / "sessions", isolated_home / "sessions")

    def _get_claude_config_dir(self, account_name: str) -> Path:
        """Get the isolated CLAUDE_CONFIG_DIR for a Claude account.

        Each Claude account gets its own config directory to support
        multiple Claude accounts with separate authentication.
        """
        base_home = safe_home()
        return platform_path(base_home / ".chad" / "claude-configs" / account_name)

    @staticmethod
    def _is_windows() -> bool:
        """Return True when running on Windows."""
        return os.name == "nt"

    def _get_codex_usage(self, account_name: str) -> str:
        """Get usage info from Codex by parsing JWT token and session files."""
        self._sync_codex_home(account_name)
        codex_home = self._get_codex_home(account_name)
        auth_file = codex_home / ".codex" / "auth.json"
        if not auth_file.exists():
            return "❌ **Not logged in**\n\nClick 'Login' to authenticate this account."

        try:
            with open(auth_file, encoding="utf-8") as f:
                auth_data = json.load(f)

            tokens = auth_data.get("tokens", {})
            access_token = tokens.get("access_token", "")

            if not access_token:
                return "❌ **Not logged in**\n\nClick 'Login' to authenticate this account."

            parts = access_token.split(".")
            if len(parts) != 3:
                return "⚠️ **Invalid token format**"

            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding

            decoded = base64.urlsafe_b64decode(payload)
            jwt_data = json.loads(decoded)

            auth_info = jwt_data.get("https://api.openai.com/auth", {})
            profile = jwt_data.get("https://api.openai.com/profile", {})

            plan_type = auth_info.get("chatgpt_plan_type", "unknown").upper()
            email = profile.get("email", "Unknown")
            exp_timestamp = jwt_data.get("exp", 0)
            exp_date = datetime.fromtimestamp(exp_timestamp).strftime("%Y-%m-%d %H:%M") if exp_timestamp else "Unknown"

            result = f"✅ **Logged in** ({plan_type} plan)\n\n"
            result += f"**Account:** {email}\n"
            result += f"**Token expires:** {exp_date}\n\n"

            usage_data = self._get_codex_session_usage(account_name)
            if usage_data:
                result += "**Current Usage**\n\n"
                result += usage_data
            else:
                result += "⚠️ **Usage data unavailable**\n\n"
                result += "OpenAI/Codex only provides usage information after the first model interaction. "
                result += "Start a coding session to see rate limit details.\n\n"
                result += "*Press refresh after using this provider to see current data*"

            return result

        except Exception as exc:  # pragma: no cover - defensive catch
            return f"⚠️ **Error reading auth data:** {str(exc)}"

    def _get_codex_session_usage(self, account_name: str) -> str | None:  # noqa: C901
        """Extract usage data from the most recent Codex session file."""
        self._sync_codex_home(account_name)
        codex_home = self._get_codex_home(account_name)
        sessions_dir = codex_home / ".codex" / "sessions"
        if not sessions_dir.exists():
            return None

        session_files: list[tuple[float, Path]] = []
        for root, _, files in os.walk(sessions_dir):
            for filename in files:
                if filename.endswith(".jsonl"):
                    path = platform_path(root) / filename
                    session_files.append((path.stat().st_mtime, path))

        if not session_files:
            return None

        session_files.sort(reverse=True)
        latest_session = session_files[0][1]

        rate_limits = None
        timestamp = None
        try:
            with open(latest_session, encoding="utf-8") as f:
                for line in f:
                    if "rate_limits" not in line:
                        continue
                    try:
                        data = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

                    # Try various locations where rate_limits might be
                    # 1. Old format: event_msg with token_count payload
                    if data.get("type") == "event_msg":
                        payload = data.get("payload", {})
                        if payload.get("type") == "token_count":
                            rate_limits = payload.get("rate_limits")
                            timestamp = data.get("timestamp")
                            continue

                    # 2. New format: rate_limits at top level
                    if "rate_limits" in data and isinstance(data.get("rate_limits"), dict):
                        rate_limits = data.get("rate_limits")
                        timestamp = data.get("timestamp")
                        continue

                    # 3. New format: rate_limits in item
                    item = data.get("item", {})
                    if isinstance(item, dict) and "rate_limits" in item:
                        rate_limits = item.get("rate_limits")
                        timestamp = data.get("timestamp")
                        continue

                    # 4. turn.ended event format
                    if data.get("type") in ("turn.ended", "thread.ended"):
                        if "rate_limits" in data:
                            rate_limits = data.get("rate_limits")
                            timestamp = data.get("timestamp")
        except OSError:
            return None

        if not rate_limits:
            return None

        result = ""

        primary = rate_limits.get("primary", {})
        if primary:
            util = self._normalize_pct(primary.get("used_percent"))
            reset_at = primary.get("resets_at", 0)
            bar = self._progress_bar(util)

            reset_str = datetime.fromtimestamp(reset_at).strftime("%I:%M%p") if reset_at else "N/A"
            result += "**5-hour session**\n"
            result += f"[{bar}] {util:.0f}% used\n"
            result += f"Resets at {reset_str}\n\n"

        secondary = rate_limits.get("secondary", {})
        if secondary:
            util = self._normalize_pct(secondary.get("used_percent"))
            reset_at = secondary.get("resets_at", 0)
            bar = self._progress_bar(util)

            reset_str = datetime.fromtimestamp(reset_at).strftime("%b %d") if reset_at else "N/A"
            result += "**Weekly limit**\n"
            result += f"[{bar}] {util:.0f}% used\n"
            result += f"Resets {reset_str}\n\n"

        credits = rate_limits.get("credits", {})
        if credits:
            has_credits = credits.get("has_credits", False)
            unlimited = credits.get("unlimited", False)
            balance = credits.get("balance")

            if unlimited:
                result += "**Credits:** Unlimited\n\n"
            elif has_credits and balance is not None:
                result += f"**Credits balance:** ${balance}\n\n"

        if timestamp:
            try:
                update_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                # Convert to local time for display
                local_dt = update_dt.astimezone()
                result += f"*Last updated: {local_dt.strftime('%Y-%m-%d %H:%M')}*\n"
            except ValueError:
                pass

        return result if result else None

    def _refresh_claude_token(self, account_name: str) -> bool:
        """Refresh Claude OAuth token using the refresh token.

        Returns True if refresh was successful and credentials were updated.
        """
        import requests

        config_dir = self._get_claude_config_dir(account_name)
        creds_file = config_dir / ".credentials.json"

        if not creds_file.exists():
            return False

        try:
            with open(creds_file, encoding="utf-8") as f:
                creds = json.load(f)

            oauth_data = creds.get("claudeAiOauth", {})
            refresh_token = oauth_data.get("refreshToken", "")

            if not refresh_token:
                return False

            # Use the v1 OAuth endpoint which works for token refresh
            response = requests.post(
                "https://console.anthropic.com/v1/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "claude-code/2.0.32",
                },
                timeout=15,
            )

            if response.status_code != 200:
                return False

            token_data = response.json()

            # Update credentials with new tokens
            oauth_data["accessToken"] = token_data.get("access_token", "")
            oauth_data["refreshToken"] = token_data.get("refresh_token", refresh_token)
            oauth_data["expiresAt"] = int((datetime.now().timestamp() + token_data.get("expires_in", 28800)) * 1000)
            if "scope" in token_data:
                oauth_data["scopes"] = token_data["scope"].split()

            creds["claudeAiOauth"] = oauth_data

            with open(creds_file, "w", encoding="utf-8") as f:
                json.dump(creds, f)

            return True

        except Exception:
            return False

    def _get_claude_usage(self, account_name: str) -> str:  # noqa: C901
        """Get usage info from Claude via API."""
        import requests

        config_dir = self._get_claude_config_dir(account_name)
        creds_file = config_dir / ".credentials.json"
        if not creds_file.exists():
            return "❌ **Not logged in**\n\n" "Click **Login** below to authenticate this account."

        try:
            with open(creds_file, encoding="utf-8") as f:
                creds = json.load(f)

            oauth_data = creds.get("claudeAiOauth", {})
            access_token = oauth_data.get("accessToken", "")
            subscription_type = (oauth_data.get("subscriptionType") or "unknown").upper()

            if not access_token:
                return "❌ **Not logged in**\n\n" "Click **Login** below to authenticate this account."

            response = requests.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "User-Agent": "claude-code/2.0.32",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )

            # Handle expired token - try to refresh
            if response.status_code == 401:
                if self._refresh_claude_token(account_name):
                    # Re-read credentials and retry
                    with open(creds_file, encoding="utf-8") as f:
                        creds = json.load(f)
                    oauth_data = creds.get("claudeAiOauth", {})
                    access_token = oauth_data.get("accessToken", "")

                    response = requests.get(
                        "https://api.anthropic.com/api/oauth/usage",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "anthropic-beta": "oauth-2025-04-20",
                            "User-Agent": "claude-code/2.0.32",
                            "Content-Type": "application/json",
                        },
                        timeout=10,
                    )

            if response.status_code == 403:
                # Token doesn't have user:profile scope - still logged in, just can't get usage
                return "✅ **Logged in**\n\n*Usage stats not available with this token.*"
            elif response.status_code != 200:
                return f"⚠️ **Error fetching usage:** HTTP {response.status_code}"

            usage_data = response.json()

            result = f"✅ **Logged in** ({subscription_type} plan)\n\n"
            result += "**Current Usage**\n\n"

            five_hour = usage_data.get("five_hour", {})
            if five_hour:
                util = self._normalize_pct(five_hour.get("utilization"))
                reset_at = five_hour.get("resets_at", "")
                bar = self._progress_bar(util)

                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                        # Convert to local time for display
                        local_reset = reset_dt.astimezone()
                        reset_str = local_reset.strftime("%I:%M%p")
                    except ValueError:
                        reset_str = reset_at
                else:
                    reset_str = "N/A"

                result += "**5-hour session**\n"
                result += f"[{bar}] {util:.0f}% used\n"
                result += f"Resets at {reset_str}\n\n"

            seven_day = usage_data.get("seven_day")
            if seven_day:
                util = self._normalize_pct(seven_day.get("utilization"))
                reset_at = seven_day.get("resets_at", "")
                bar = self._progress_bar(util)

                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                        # Convert to local time for display
                        local_reset = reset_dt.astimezone()
                        reset_str = local_reset.strftime("%b %d")
                    except ValueError:
                        reset_str = reset_at
                else:
                    reset_str = "N/A"

                result += "**Weekly limit**\n"
                result += f"[{bar}] {util:.0f}% used\n"
                result += f"Resets {reset_str}\n\n"

            extra = usage_data.get("extra_usage", {})
            if extra and extra.get("is_enabled"):
                used = extra.get("used_credits", 0)
                limit = extra.get("monthly_limit", 0)
                util = self._normalize_pct(extra.get("utilization"))
                bar = self._progress_bar(util)
                result += "**Extra credits**\n"
                result += (
                    f"[{bar}] {self._format_usd_from_cents(used)} / "
                    f"{self._format_usd_from_cents(limit)} ({util:.1f}%)\n\n"
                )

            return result

        except requests.exceptions.RequestException as exc:
            return f"⚠️ **Network error:** {str(exc)}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"⚠️ **Error:** {str(exc)}"

    def _get_gemini_usage(self) -> str:  # noqa: C901
        """Get usage info from Gemini by reading the usage JSONL file."""
        from collections import defaultdict
        from datetime import datetime, timezone

        from chad.util.providers import _read_gemini_usage

        gemini_dir = Path.home() / ".gemini"
        oauth_file = gemini_dir / "oauth_creds.json"

        if not oauth_file.exists():
            return "❌ **Not logged in**\n\nRun `gemini` in terminal to authenticate."

        records = _read_gemini_usage()
        if not records:
            return "✅ **Logged in**\n\n*No usage data yet*"

        model_usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        )
        today_requests = 0
        today = datetime.now(timezone.utc).date()
        # Conservative daily limit based on free-tier Gemini API limits
        daily_limit = 100

        for rec in records:
            model = rec.get("model", "unknown")
            model_usage[model]["requests"] += 1
            model_usage[model]["input_tokens"] += rec.get("input_tokens", 0)
            model_usage[model]["output_tokens"] += rec.get("output_tokens", 0)
            model_usage[model]["cached_tokens"] += rec.get("cached_tokens", 0)

            ts = rec.get("timestamp", "")
            if ts:
                try:
                    rec_date = datetime.fromisoformat(ts).date()
                    if rec_date == today:
                        today_requests += 1
                except (ValueError, AttributeError):
                    pass

        # Calculate usage percentage — ceil so any usage shows at least 1%
        util_pct = min(math.ceil((today_requests / daily_limit) * 100), 100)
        bar = self._progress_bar(util_pct)

        result = "✅ **Logged in**\n\n"

        # Daily usage progress bar
        result += "**Daily Usage**\n"
        result += f"[{bar}] {util_pct}% used\n"
        result += f"{today_requests:,} / ~{daily_limit:,} requests\n"
        result += "Resets at Midnight UTC\n\n"

        # Model usage breakdown
        result += "**Model Usage** (all time)\n\n"
        result += "| Model | Reqs | Input | Output |\n"
        result += "|-------|------|-------|--------|\n"

        total_input = 0
        total_output = 0
        total_cached = 0

        for model, usage in sorted(model_usage.items()):
            reqs = usage["requests"]
            input_tok = usage["input_tokens"]
            output_tok = usage["output_tokens"]
            cached_tok = usage["cached_tokens"]

            total_input += input_tok
            total_output += output_tok
            total_cached += cached_tok

            result += f"| {model} | {reqs:,} | {input_tok:,} | {output_tok:,} |\n"

        if total_cached > 0 and total_input > 0:
            cache_pct = (total_cached / total_input) * 100
            result += f"\n**Cache savings:** {total_cached:,} ({cache_pct:.1f}%) tokens served from cache\n"

        return result

    def _get_mistral_usage(self) -> str:
        """Get usage info from Mistral Vibe by parsing session files."""
        from datetime import datetime, timezone

        vibe_config = Path.home() / ".vibe" / "config.toml"
        if not vibe_config.exists():
            return "❌ **Not logged in**\n\nRun `vibe --setup` in terminal to authenticate."

        sessions_dir = Path.home() / ".vibe" / "logs" / "session"
        if not sessions_dir.exists():
            return "✅ **Logged in**\n\n*No session data yet*"

        session_files = list(sessions_dir.glob("session_*.json"))
        if not session_files:
            return "✅ **Logged in**\n\n*No session data yet*"

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0
        session_count = 0
        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 1000  # Conservative estimate for free tier

        for session_file in session_files:
            try:
                # Check file modification time to see if it's from today
                mtime = datetime.fromtimestamp(session_file.stat().st_mtime, tz=timezone.utc)

                with open(session_file, encoding="utf-8") as f:
                    data = json.load(f)

                metadata = data.get("metadata", {})
                stats = metadata.get("stats", {})

                total_prompt_tokens += stats.get("session_prompt_tokens", 0)
                total_completion_tokens += stats.get("session_completion_tokens", 0)
                total_cost += stats.get("session_cost", 0.0)
                session_count += 1

                # Count today's requests based on file modification time
                if mtime.date() == today:
                    # Use prompt_count if available, otherwise count as 1
                    prompt_count = stats.get("prompt_count", 1)
                    today_requests += prompt_count
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        if session_count == 0:
            return "✅ **Logged in**\n\n*No valid session data found*"

        # Calculate usage percentage
        util_pct = min((today_requests / daily_limit) * 100, 100.0)
        bar = self._progress_bar(util_pct)
        total_tokens = total_prompt_tokens + total_completion_tokens

        result = "✅ **Logged in**\n\n"

        # Daily usage progress bar
        result += "**Daily Usage**\n"
        result += f"[{bar}] {util_pct:.0f}% used\n"
        result += f"{today_requests:,} / {daily_limit:,} requests\n"
        result += "Resets at Midnight UTC\n\n"

        # Cumulative stats
        result += "**Cumulative Usage**\n\n"
        result += f"**Sessions:** {session_count:,}\n"
        result += f"**Input tokens:** {total_prompt_tokens:,}\n"
        result += f"**Output tokens:** {total_completion_tokens:,}\n"
        result += f"**Total tokens:** {total_tokens:,}\n"
        result += f"**Estimated cost:** ${total_cost:.4f}\n"

        return result

    def _get_qwen_usage(self) -> str:
        """Get usage info from Qwen Code by counting today's requests.

        Qwen Code uses QwenChat OAuth with 2000 free daily requests.
        We count today's API calls from local session files.
        """
        from datetime import datetime, timezone

        qwen_oauth = Path.home() / ".qwen" / "oauth_creds.json"

        if not qwen_oauth.exists():
            return "❌ **Not logged in**\n\nRun `qwen` in terminal to authenticate."

        # Count today's requests from session files
        projects_dir = Path.home() / ".qwen" / "projects"
        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000

        if projects_dir.exists():
            for session_file in projects_dir.glob("*/chats/*.jsonl"):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                                # Count assistant responses (each is one API call)
                                if event.get("type") == "assistant":
                                    timestamp = event.get("timestamp", "")
                                    if timestamp:
                                        try:
                                            msg_date = datetime.fromisoformat(
                                                timestamp.replace("Z", "+00:00")
                                            ).date()
                                            if msg_date == today:
                                                today_requests += 1
                                        except (ValueError, AttributeError):
                                            pass
                            except json.JSONDecodeError:
                                continue
                except OSError:
                    continue

        # Calculate usage percentage
        util_pct = min((today_requests / daily_limit) * 100, 100.0)
        bar = self._progress_bar(util_pct)

        # Reset time is midnight UTC
        reset_str = "Midnight UTC"

        result = "✅ **Logged in**\n\n"
        result += "**Qwen3-Coder** (QwenChat OAuth)\n\n"
        result += "**Daily Usage**\n"
        result += f"[{bar}] {util_pct:.0f}% used\n"
        result += f"{today_requests:,} / {daily_limit:,} requests\n"
        result += f"Resets at {reset_str}\n"

        return result

    def _get_opencode_usage(self, account_name: str) -> str:
        """Get usage info from OpenCode by counting today's requests.

        OpenCode supports multiple backends and stores session data
        in XDG_DATA_HOME/opencode/sessions/.
        """
        from datetime import datetime, timezone

        # OpenCode v1.1+ stores session data at ~/.local/share/opencode
        data_dir = Path.home() / ".local" / "share" / "opencode"

        sessions_dir = data_dir / "sessions"

        if not sessions_dir.exists():
            return "✅ **Ready**\n\nNo sessions yet."

        # Count today's requests from session files
        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000  # Default, varies by backend

        for session_file in sessions_dir.glob("*.jsonl"):
            try:
                with open(session_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("type") == "assistant":
                                timestamp = event.get("timestamp", "")
                                if timestamp:
                                    try:
                                        msg_date = datetime.fromisoformat(
                                            timestamp.replace("Z", "+00:00")
                                        ).date()
                                        if msg_date == today:
                                            today_requests += 1
                                    except (ValueError, AttributeError):
                                        pass
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        # Calculate usage percentage
        util_pct = min((today_requests / daily_limit) * 100, 100.0)
        bar = self._progress_bar(util_pct)

        result = "✅ **Ready**\n\n"
        result += "**OpenCode** (Multi-backend AI agent)\n\n"
        result += "**Daily Usage**\n"
        result += f"[{bar}] {util_pct:.0f}% used\n"
        result += f"{today_requests:,} / {daily_limit:,} requests\n"
        result += "Resets at Midnight UTC\n"

        return result

    def _get_opencode_remaining_usage(self, account_name: str) -> float:
        """Get OpenCode remaining usage (0.0-1.0) by counting today's requests."""
        from datetime import datetime, timezone

        # OpenCode v1.1+ stores session data at ~/.local/share/opencode
        data_dir = Path.home() / ".local" / "share" / "opencode"

        sessions_dir = data_dir / "sessions"
        if not sessions_dir.exists():
            return 1.0  # No sessions yet, full capacity

        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000

        for session_file in sessions_dir.glob("*.jsonl"):
            try:
                with open(session_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("type") == "assistant":
                                timestamp = event.get("timestamp", "")
                                if timestamp:
                                    try:
                                        msg_date = datetime.fromisoformat(
                                            timestamp.replace("Z", "+00:00")
                                        ).date()
                                        if msg_date == today:
                                            today_requests += 1
                                    except (ValueError, AttributeError):
                                        pass
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        used_pct = today_requests / daily_limit
        return max(0.0, min(1.0, 1.0 - used_pct))

    def _get_kimi_usage(self, account_name: str) -> str:
        """Get usage info from Kimi Code by counting today's requests.

        Kimi Code stores config and sessions in ~/.kimi/.
        """
        from datetime import datetime, timezone

        # Get isolated config directory for this account
        if account_name:
            kimi_dir = Path.home() / ".chad" / "kimi-homes" / account_name / ".kimi"
        else:
            kimi_dir = Path.home() / ".kimi"

        creds_file = kimi_dir / "credentials" / "kimi-code.json"

        if not creds_file.exists():
            return "❌ **Not logged in**\n\nAdd this provider again to start the login flow."

        # Count today's requests from session files
        sessions_dir = kimi_dir / "sessions"
        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000  # Kimi has generous limits

        if sessions_dir.exists():
            for session_file in sessions_dir.glob("*.jsonl"):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                                if event.get("type") == "assistant" or event.get("role") == "assistant":
                                    timestamp = event.get("timestamp", "") or event.get("created_at", "")
                                    if timestamp:
                                        try:
                                            msg_date = datetime.fromisoformat(
                                                timestamp.replace("Z", "+00:00")
                                            ).date()
                                            if msg_date == today:
                                                today_requests += 1
                                        except (ValueError, AttributeError):
                                            pass
                            except json.JSONDecodeError:
                                continue
                except OSError:
                    continue

        # Calculate usage percentage
        util_pct = min((today_requests / daily_limit) * 100, 100.0)
        bar = self._progress_bar(util_pct)

        result = "✅ **Configured**\n\n"
        result += "**Kimi Code** (Kimi K2.5)\n\n"
        result += "**Daily Usage**\n"
        result += f"[{bar}] {util_pct:.0f}% used\n"
        result += f"{today_requests:,} / {daily_limit:,} requests\n"
        result += "Resets at Midnight UTC\n"

        return result

    def _get_kimi_remaining_usage(self, account_name: str) -> float:
        """Get Kimi remaining usage (0.0-1.0) by counting today's requests."""
        from datetime import datetime, timezone

        # Get isolated config directory for this account
        if account_name:
            kimi_dir = Path.home() / ".chad" / "kimi-homes" / account_name / ".kimi"
        else:
            kimi_dir = Path.home() / ".kimi"

        creds_file = kimi_dir / "credentials" / "kimi-code.json"
        if not creds_file.exists():
            return 0.0  # Not configured

        sessions_dir = kimi_dir / "sessions"
        if not sessions_dir.exists():
            return 1.0  # Configured but no sessions yet

        today_requests = 0
        today = datetime.now(timezone.utc).date()
        daily_limit = 2000

        for session_file in sessions_dir.glob("*.jsonl"):
            try:
                with open(session_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("type") == "assistant" or event.get("role") == "assistant":
                                timestamp = event.get("timestamp", "") or event.get("created_at", "")
                                if timestamp:
                                    try:
                                        msg_date = datetime.fromisoformat(
                                            timestamp.replace("Z", "+00:00")
                                        ).date()
                                        if msg_date == today:
                                            today_requests += 1
                                    except (ValueError, AttributeError):
                                        pass
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        used_pct = today_requests / daily_limit
        return max(0.0, min(1.0, 1.0 - used_pct))

    def get_account_choices(self) -> list[str]:
        """Get list of account names for dropdowns."""
        accounts = self.api_client.list_accounts()
        return [acc.name for acc in accounts]

    def _check_provider_login(self, provider_type: str, account_name: str) -> tuple[bool, str]:  # noqa: C901
        """Check if a provider is logged in."""
        try:
            if provider_type == "openai":
                codex_home = self._get_codex_home(account_name)
                auth_file = codex_home / ".codex" / "auth.json"
                if auth_file.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            if provider_type == "anthropic":
                config_dir = self._get_claude_config_dir(account_name)
                creds_file = config_dir / ".credentials.json"
                if creds_file.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            if provider_type == "gemini":
                gemini_oauth = Path.home() / ".gemini" / "oauth_creds.json"
                if gemini_oauth.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            if provider_type == "qwen":
                qwen_oauth = Path.home() / ".qwen" / "oauth_creds.json"
                if qwen_oauth.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            if provider_type == "mistral":
                vibe_config = safe_home() / ".vibe" / "config.toml"
                if vibe_config.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            if provider_type == "opencode":
                # Check for OAuth credentials from `opencode auth login`
                auth_file = Path(safe_home()) / ".local" / "share" / "opencode" / "auth.json"
                if auth_file.exists():
                    try:
                        auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
                        if auth_data:
                            return True, "Logged in"
                    except (json.JSONDecodeError, OSError):
                        pass
                return False, "Not logged in"

            if provider_type == "kimi":
                # Check for credentials AND populated config (models/providers).
                # A partial login leaves creds but empty config, causing "LLM not set".
                isolated_creds = (
                    safe_home() / ".chad" / "kimi-homes" / account_name / ".kimi" / "credentials" / "kimi-code.json"
                )
                global_creds = safe_home() / ".kimi" / "credentials" / "kimi-code.json"
                if not (isolated_creds.exists() or global_creds.exists()):
                    return False, "Not logged in"
                config_file = safe_home() / ".chad" / "kimi-homes" / account_name / ".kimi" / "config.toml"
                if not (config_file.exists() and "[models." in config_file.read_text(encoding="utf-8")):
                    # Repair: write default config so the CLI has model/provider entries
                    self._write_kimi_default_config(config_file)
                return True, "Logged in"

            if provider_type == "mock":
                return True, "Mock provider (no login required)"

            return False, "Unknown provider type"

        except Exception as exc:
            return False, f"Error: {str(exc)}"

    def _setup_codex_account(self, account_name: str) -> Path:
        """Setup isolated home directory for a Codex account."""
        codex_home = self._get_codex_home(account_name)
        codex_dir = codex_home / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        return codex_home

    def _setup_claude_account(self, account_name: str) -> Path:
        """Setup isolated config directory for a Claude account."""
        config_dir = self._get_claude_config_dir(account_name)
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    @staticmethod
    def _write_kimi_default_config(config_file: Path) -> None:
        """Write default Kimi config with model/provider entries.

        Called when OAuth creds exist but config wasn't populated (partial login).
        """
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            'default_model = "kimi-code/kimi-k2.5"\n\n'
            '[models."kimi-code/kimi-k2.5"]\n'
            'provider = "managed:kimi-code"\n'
            'model = "kimi-k2.5"\n'
            'max_context_size = 131072\n\n'
            '[providers."managed:kimi-code"]\n'
            'type = "kimi"\n'
            'base_url = "https://api.kimi.com/coding/v1"\n'
            'api_key = ""\n\n'
            '[providers."managed:kimi-code".oauth]\n'
            'storage = "file"\n'
            'key = "kimi-code"\n',
            encoding="utf-8",
        )

    def _ensure_provider_cli(self, provider_type: str) -> tuple[bool, str]:
        """Ensure the provider's CLI is present; install if missing."""
        tool_map = {
            "openai": "codex",
            "anthropic": "claude",
            "gemini": "gemini",
            "qwen": "qwen",
            "mistral": "vibe",
            "opencode": "opencode",
            "kimi": "kimi",
        }
        tool_key = tool_map.get(provider_type)
        if not tool_key:
            return True, ""
        return self.installer.ensure_tool(tool_key)

    def add_provider(self, provider_name: str, provider_type: str, card_slots: int, api_key: str = ""):  # noqa: C901
        """Add a new provider and return refreshed provider panel state."""
        import subprocess  # not at module level to avoid circular imports

        name_field_value = provider_name
        add_btn_state = gr.update(interactive=bool(provider_name.strip()))
        accordion_state = gr.update(open=True)

        try:
            if provider_type not in self.get_supported_providers():
                base_response = self.provider_action_response(f"❌ Unsupported provider '{provider_type}'", card_slots)
                return (*base_response, name_field_value, add_btn_state, accordion_state)

            cli_ok, cli_detail = self._ensure_provider_cli(provider_type)
            if not cli_ok:
                base_response = self.provider_action_response(f"❌ {cli_detail}", card_slots)
                return (*base_response, name_field_value, add_btn_state, accordion_state)

            existing_accounts = self.api_client.list_accounts()
            existing_names = {acc.name for acc in existing_accounts}
            base_name = provider_type
            counter = 1
            account_name = provider_name if provider_name else base_name

            while account_name in existing_names:
                account_name = f"{base_name}-{counter}"
                counter += 1

            if provider_type == "openai":
                import time

                codex_home = Path(self._setup_codex_account(account_name))
                codex_cli = cli_detail or "codex"
                is_windows = self._is_windows()

                env = os.environ.copy()
                env["HOME"] = str(codex_home)
                if is_windows:
                    env["USERPROFILE"] = str(codex_home)

                isolated_auth_file = codex_home / ".codex" / "auth.json"
                real_home = Path.home()
                real_auth_file = real_home / ".codex" / "auth.json"

                # Track the mtime of real auth file before login to detect changes
                real_auth_mtime_before = None
                if is_windows and real_auth_file.exists():
                    try:
                        real_auth_mtime_before = real_auth_file.stat().st_mtime
                    except OSError:
                        pass

                login_success = False

                try:
                    if is_windows:
                        # On Windows, open Codex in a new console with custom HOME
                        CREATE_NEW_CONSOLE = 0x00000010
                        process = subprocess.Popen(
                            [codex_cli, "login"],
                            env=env,
                            creationflags=CREATE_NEW_CONSOLE,
                        )

                        # Poll for auth file - check both isolated and real home
                        # (in case Codex doesn't respect our custom HOME)
                        start_time = time.time()
                        timeout_secs = 120
                        while time.time() - start_time < timeout_secs:
                            # First check isolated location (preferred)
                            if isolated_auth_file.exists():
                                try:
                                    with open(isolated_auth_file, encoding="utf-8") as f:
                                        auth_data = json.load(f)
                                    tokens = auth_data.get("tokens", {})
                                    if tokens.get("access_token"):
                                        login_success = True
                                        break
                                except (json.JSONDecodeError, KeyError, OSError):
                                    pass

                            # Fallback: check if real home auth file was updated
                            if real_auth_file.exists():
                                try:
                                    current_mtime = real_auth_file.stat().st_mtime
                                    # Only use if file is new or was modified after we started
                                    if real_auth_mtime_before is None or current_mtime > real_auth_mtime_before:
                                        with open(real_auth_file, encoding="utf-8") as f:
                                            auth_data = json.load(f)
                                        tokens = auth_data.get("tokens", {})
                                        if tokens.get("access_token"):
                                            # Copy to isolated directory
                                            isolated_auth_file.parent.mkdir(parents=True, exist_ok=True)
                                            shutil.copy2(real_auth_file, isolated_auth_file)
                                            login_success = True
                                            break
                                except (json.JSONDecodeError, KeyError, OSError):
                                    pass

                            time.sleep(2)

                        # Clean up process if still running
                        try:
                            process.terminate()
                        except Exception:
                            pass
                    else:
                        # On Unix, use subprocess.run directly
                        login_result = subprocess.run(
                            [codex_cli, "login"],
                            env=env,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=120,
                        )
                        login_success = login_result.returncode == 0

                except FileNotFoundError:
                    # CLI not found - provide helpful message
                    codex_home_path = self._get_codex_home(account_name)
                    if codex_home_path.exists():
                        shutil.rmtree(codex_home_path, ignore_errors=True)
                    result = (
                        "❌ Codex CLI not found.\n\n"
                        "Please install Codex first:\n"
                        "```\nnpm install -g @openai/codex\n```"
                    )
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

                except Exception:
                    pass  # Fall through to login_success check

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    codex_home_path = self._get_codex_home(account_name)
                    if codex_home_path.exists():
                        shutil.rmtree(codex_home_path, ignore_errors=True)

                    if is_windows:
                        result = (
                            f"❌ Login timed out for '{account_name}'.\n\n"
                            "A Codex CLI window should have opened. If you didn't complete the login in time, "
                            "please try again and complete the browser authentication within 2 minutes."
                        )
                    else:
                        result = f"❌ Login failed for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            elif provider_type == "anthropic":
                # Create isolated config directory for this Claude account
                config_dir = self._setup_claude_account(account_name)
                claude_cli = cli_detail or "claude"

                # Check if already logged in (user may have pre-authenticated)
                login_success, login_msg = self._check_provider_login(provider_type, account_name)

                if not login_success:
                    # Use browser OAuth flow to get all scopes (user:inference, user:profile)
                    # Token auto-refreshes via _refresh_claude_token when expired
                    import time

                    creds_file = config_dir / ".credentials.json"
                    is_windows = self._is_windows()

                    try:
                        if is_windows:
                            # On Windows, open Claude in a new console window for user interaction
                            # and poll for credentials in the background

                            # Set up environment
                            env = os.environ.copy()
                            env["CLAUDE_CONFIG_DIR"] = str(config_dir)

                            # Start Claude in a new console window (CREATE_NEW_CONSOLE = 0x10)
                            CREATE_NEW_CONSOLE = 0x00000010
                            process = subprocess.Popen(
                                [claude_cli],
                                env=env,
                                creationflags=CREATE_NEW_CONSOLE,
                            )

                            # Poll for credentials file to appear (OAuth callback)
                            start_time = time.time()
                            timeout_secs = 120
                            while time.time() - start_time < timeout_secs:
                                if creds_file.exists():
                                    try:
                                        with open(creds_file, encoding="utf-8") as f:
                                            creds_data = json.load(f)
                                        oauth = creds_data.get("claudeAiOauth", {})
                                        if oauth.get("accessToken"):
                                            login_success = True
                                            break
                                    except (json.JSONDecodeError, KeyError, OSError):
                                        pass
                                time.sleep(2)

                            # Clean up process if still running
                            try:
                                process.terminate()
                            except Exception:
                                pass

                        else:
                            # On Unix, use pexpect for automated login
                            import pexpect

                            env = os.environ.copy()
                            env["CLAUDE_CONFIG_DIR"] = str(config_dir)
                            env["TERM"] = "xterm-256color"

                            child = pexpect.spawn(claude_cli, timeout=120, encoding="utf-8", env=env)

                            try:
                                # Step 1: Theme selection
                                child.expect("Choose the text style", timeout=20)
                                time.sleep(1)
                                child.send("\r")

                                # Step 2: Login method selection
                                child.expect("Select login method", timeout=15)
                                time.sleep(1)
                                child.send("\r")

                                # Step 3: Wait for browser to open
                                child.expect(["Opening browser", "browser"], timeout=15)

                                # Poll for credentials file to appear (OAuth callback)
                                start_time = time.time()
                                timeout_secs = 120
                                while time.time() - start_time < timeout_secs:
                                    if creds_file.exists():
                                        try:
                                            with open(creds_file, encoding="utf-8") as f:
                                                creds_data = json.load(f)
                                            oauth = creds_data.get("claudeAiOauth", {})
                                            if oauth.get("accessToken"):
                                                login_success = True
                                                break
                                        except (json.JSONDecodeError, KeyError, OSError):
                                            pass
                                    time.sleep(2)

                            except pexpect.TIMEOUT:
                                pass  # Will fall through to login_success check
                            except pexpect.EOF:
                                pass  # Process ended unexpectedly
                            finally:
                                try:
                                    child.close()
                                except Exception:
                                    pass

                    except FileNotFoundError:
                        # CLI not found - provide helpful message
                        if config_dir.exists():
                            shutil.rmtree(config_dir, ignore_errors=True)
                        result = (
                            "❌ Claude CLI not found.\n\n"
                            "Please install Claude Code first:\n"
                            "```\nnpm install -g @anthropic-ai/claude-code\n```"
                        )
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)

                    except Exception:
                        pass  # Any error, fall through to login_success check

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    # Login failed/timed out - clean up
                    if config_dir.exists():
                        shutil.rmtree(config_dir, ignore_errors=True)

                    if is_windows:
                        result = (
                            f"❌ Login timed out for '{account_name}'.\n\n"
                            "A Claude CLI window should have opened. If you didn't complete the login in time, "
                            "please try again and complete the browser authentication within 2 minutes."
                        )
                    else:
                        result = f"❌ Login timed out for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            elif provider_type == "gemini":
                # Gemini uses browser OAuth
                import time

                gemini_cli = cli_detail or "gemini"
                is_windows = self._is_windows()
                gemini_oauth = Path.home() / ".gemini" / "oauth_creds.json"

                # Check if already logged in
                login_success, _ = self._check_provider_login(provider_type, account_name)

                if not login_success:
                    # Pre-create settings.json so the CLI skips the interactive
                    # auth-type selection dialog and goes straight to browser
                    # OAuth. Without this, the Ink TUI renders a dialog that
                    # needs an Enter press to proceed, which is fragile to
                    # detect through ANSI-heavy output.
                    import json as _json

                    gemini_dir = Path.home() / ".gemini"
                    gemini_settings = gemini_dir / "settings.json"
                    if not gemini_settings.exists():
                        gemini_dir.mkdir(parents=True, exist_ok=True)
                        gemini_settings.write_text(
                            _json.dumps(
                                {"security": {"auth": {"selectedType": "oauth-personal"}}}
                            )
                        )

                    try:
                        if is_windows:
                            CREATE_NEW_CONSOLE = 0x00000010
                            process = subprocess.Popen(
                                [gemini_cli],
                                creationflags=CREATE_NEW_CONSOLE,
                            )

                            # Poll for credentials file
                            start_time = time.time()
                            timeout_secs = 120
                            while time.time() - start_time < timeout_secs:
                                if gemini_oauth.exists():
                                    login_success = True
                                    break
                                time.sleep(2)

                            try:
                                process.terminate()
                            except Exception:
                                pass
                        else:
                            # On Unix, use pexpect to provide a PTY for the CLI
                            import pexpect

                            env = os.environ.copy()
                            env["TERM"] = "xterm-256color"

                            # With settings.json pre-created, the CLI goes
                            # straight to browser OAuth without an interactive
                            # dialog. Poll for the oauth_creds.json file while
                            # draining PTY output to prevent buffer blocking.
                            child = pexpect.spawn(
                                gemini_cli, timeout=120, encoding="utf-8", env=env, dimensions=(50, 120)
                            )

                            try:
                                start_time = time.time()
                                timeout_secs = 120
                                while time.time() - start_time < timeout_secs:
                                    if gemini_oauth.exists():
                                        login_success = True
                                        # Let the CLI finish sending its HTTP
                                        # response to the browser before we
                                        # kill it.
                                        time.sleep(2)
                                        break
                                    if not child.isalive():
                                        break
                                    try:
                                        child.read_nonblocking(size=10000, timeout=0.1)
                                    except (pexpect.TIMEOUT, pexpect.EOF):
                                        pass
                                    time.sleep(0.5)
                            except (pexpect.TIMEOUT, pexpect.EOF):
                                pass
                            finally:
                                try:
                                    child.close()
                                except Exception:
                                    pass

                    except FileNotFoundError:
                        result = (
                            "❌ Gemini CLI not found.\n\n"
                            "Please install Gemini CLI first:\n"
                            "```\nnpm install -g @google/gemini-cli\n```"
                        )
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)
                    except Exception:
                        pass

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    if is_windows:
                        result = (
                            f"❌ Login timed out for '{account_name}'.\n\n"
                            "A Gemini CLI window should have opened. Please try again."
                        )
                    else:
                        result = f"❌ Login timed out for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            elif provider_type == "qwen":
                # Qwen uses QwenChat OAuth (fork of Gemini CLI)
                import time

                qwen_cli = cli_detail or "qwen"
                is_windows = self._is_windows()
                qwen_oauth = Path.home() / ".qwen" / "oauth_creds.json"

                # Check if already logged in
                login_success, _ = self._check_provider_login(provider_type, account_name)

                if not login_success:
                    try:
                        if is_windows:
                            CREATE_NEW_CONSOLE = 0x00000010
                            process = subprocess.Popen(
                                [qwen_cli],
                                creationflags=CREATE_NEW_CONSOLE,
                            )

                            # Poll for oauth file
                            start_time = time.time()
                            timeout_secs = 120
                            while time.time() - start_time < timeout_secs:
                                if qwen_oauth.exists():
                                    login_success = True
                                    break
                                time.sleep(2)

                            try:
                                process.terminate()
                            except Exception:
                                pass
                        else:
                            # On Unix, use pexpect for qwen OAuth flow
                            # Qwen uses device authorization - let CLI handle browser opening
                            import pexpect

                            env = os.environ.copy()
                            env["TERM"] = "xterm-256color"

                            child = pexpect.spawn(
                                qwen_cli, timeout=180, encoding="utf-8", env=env, dimensions=(50, 120)
                            )

                            try:
                                # Qwen CLI shows auth prompts on first run
                                # Wait briefly for render, then send Enter to proceed
                                time.sleep(0.5)
                                child.send("\r")
                                time.sleep(0.5)
                                child.send("\r")

                                # Poll for oauth file while process runs
                                # CLI handles browser opening and device polling internally
                                # Drain output to prevent buffer blocking (device auth produces
                                # continuous output like QR codes and countdown timers)
                                start_time = time.time()
                                timeout_secs = 120
                                while time.time() - start_time < timeout_secs:
                                    if qwen_oauth.exists():
                                        login_success = True
                                        break
                                    if not child.isalive():
                                        break
                                    # Drain any pending output to prevent blocking
                                    try:
                                        child.read_nonblocking(size=10000, timeout=0.1)
                                    except (pexpect.TIMEOUT, pexpect.EOF):
                                        pass
                                    time.sleep(0.5)

                            except (pexpect.TIMEOUT, pexpect.EOF):
                                pass
                            finally:
                                try:
                                    child.close()
                                except Exception:
                                    pass

                    except FileNotFoundError:
                        result = (
                            "❌ Qwen Code CLI not found.\n\n"
                            "Please install Qwen Code first:\n"
                            "```\nnpm install -g @qwen-code/qwen-code\n```"
                        )
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)
                    except Exception:
                        pass

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    if is_windows:
                        result = (
                            f"❌ Login timed out for '{account_name}'.\n\n"
                            "A Qwen Code CLI window should have opened. Please try again."
                        )
                    else:
                        result = f"❌ Login timed out for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            elif provider_type == "kimi":
                # Kimi login via `kimi login --json` which emits structured JSON events.
                # The CLI itself opens the browser — we must NOT open it again.
                import time

                login_success = False
                kimi_home = Path(safe_home()) / ".chad" / "kimi-homes" / account_name
                kimi_home.mkdir(parents=True, exist_ok=True)
                creds_file = kimi_home / ".kimi" / "credentials" / "kimi-code.json"
                config_file = kimi_home / ".kimi" / "config.toml"

                if creds_file.exists():
                    # Creds already exist — ensure config is populated and skip login.
                    if not (config_file.exists() and "[models." in config_file.read_text(encoding="utf-8")):
                        self._write_kimi_default_config(config_file)
                    login_success = True
                else:
                    resolved = self.installer.resolve_tool_path("kimi")
                    kimi_cli = str(resolved) if resolved else shutil.which("kimi")
                    if not kimi_cli:
                        result = (
                            "❌ Kimi CLI not found.\n\n"
                            "Please install Kimi Code first:\n"
                            "```\nnpm install -g @anthropic-ai/kimi-code\n```"
                        )
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)

                    env = os.environ.copy()
                    env["HOME"] = str(kimi_home)
                    env["TERM"] = "xterm-256color"

                    try:
                        proc = subprocess.Popen(
                            [kimi_cli, "login", "--json"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            env=env,
                        )
                        start_time = time.time()
                        timeout_secs = 120

                        while time.time() - start_time < timeout_secs:
                            line = proc.stdout.readline()
                            if not line:
                                if proc.poll() is not None:
                                    break
                                time.sleep(0.5)
                                continue
                            try:
                                event = json.loads(line.decode("utf-8", errors="replace").strip())
                                evt_type = event.get("type", "")
                                # Don't open browser — the CLI already does it.
                                if evt_type == "success":
                                    login_success = True
                                    break
                                elif evt_type == "error":
                                    break
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue

                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    except FileNotFoundError:
                        result = "❌ Kimi CLI not found."
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)

                    # The CLI saves creds before listing models. Even if model
                    # listing fails (emitting "error"), creds are on disk.
                    # Check for creds and write config ourselves if needed.
                    if not login_success and creds_file.exists():
                        if not (config_file.exists() and "[models." in config_file.read_text(encoding="utf-8")):
                            self._write_kimi_default_config(config_file)
                        login_success = True

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    result = f"❌ Kimi login failed for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            elif provider_type == "opencode":
                # OpenCode stores credentials at ~/.local/share/opencode/auth.json.
                # Check if already logged in; if not, require an API key via the UI.
                auth_file = Path(safe_home()) / ".local" / "share" / "opencode" / "auth.json"

                login_success, _ = self._check_provider_login(provider_type, account_name)

                if not login_success:
                    if not api_key or not api_key.strip():
                        import webbrowser
                        webbrowser.open("https://opencode.ai/auth")
                        result = (
                            "❌ OpenCode requires an API key.\n\n"
                            "A browser tab has been opened to **opencode.ai/auth** — "
                            "create a key there, then paste it in the API Key field above and click Add Provider again."
                        )
                        base_response = self.provider_action_response(result, card_slots)
                        return (*base_response, name_field_value, add_btn_state, accordion_state)

                    # Write auth.json in the format the OpenCode CLI expects
                    auth_file.parent.mkdir(parents=True, exist_ok=True)
                    auth_data = {"opencode": {"type": "api", "key": api_key.strip()}}
                    auth_file.write_text(json.dumps(auth_data), encoding="utf-8")
                    login_success = True

                if login_success:
                    self.api_client.create_account(account_name, provider_type)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    result = f"❌ OpenCode login failed for '{account_name}'."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            else:
                # Generic flow for mistral and other providers
                login_success, login_msg = self._check_provider_login(provider_type, account_name)
                auth_info = {
                    "mistral": ("vibe --setup", "Set up your Mistral API key"),
                }

                # Gate account creation for providers that require up-front CLI auth.
                if provider_type in auth_info and not login_success:
                    auth_cmd, auth_desc = auth_info[provider_type]
                    result = (
                        f"❌ {provider_type.capitalize()} is not logged in yet.\n\n"
                        f"Run `{auth_cmd}` in terminal ({auth_desc}), then add this provider again."
                    )
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

                self.api_client.create_account(account_name, provider_type)
                result = f"✓ Provider '{account_name}' ({provider_type}) added."

                if login_success:
                    result += f" ✅ {login_msg}"
                else:
                    result += f" ⚠️ {login_msg}"
                    auth_cmd, auth_desc = auth_info.get(provider_type, ("unknown", ""))
                    if auth_cmd != "unknown":
                        result += f" — manual login: run `{auth_cmd}` ({auth_desc})"

                name_field_value = ""
                add_btn_state = gr.update(interactive=False)
                accordion_state = gr.update(open=False)

            base_response = self.provider_action_response(result, card_slots)
            return (*base_response, name_field_value, add_btn_state, accordion_state)
        except subprocess.TimeoutExpired:
            codex_home_path = self._get_codex_home(account_name)
            if codex_home_path.exists():
                shutil.rmtree(codex_home_path, ignore_errors=True)
            base_response = self.provider_action_response(
                f"❌ Login timed out for '{account_name}'. Please try again.", card_slots
            )
            return (*base_response, name_field_value, add_btn_state, accordion_state)
        except Exception as exc:
            base_response = self.provider_action_response(f"❌ Error adding provider: {str(exc)}", card_slots)
            return (*base_response, name_field_value, add_btn_state, accordion_state)

    def _unassign_account_roles(self, account_name: str) -> None:
        """Remove all role assignments for an account."""
        try:
            account = self.api_client.get_account(account_name)
            if account.role:
                self.api_client.set_account_role(account_name, "")
        except Exception:
            pass

    def _format_usage_metrics(self, account_name: str) -> str:
        """Format usage remaining as a compact string."""
        try:
            usage_remaining = self.get_remaining_usage(account_name)
            # Use floor for remaining so any usage reduces from 100%
            usage_pct = int(usage_remaining * 100)
            return f"[Usage: {usage_pct}%]"
        except Exception:
            return ""

    def get_role_config_status(
        self,
        task_state: str | None = None,
        worktree_path: str | None = None,
        switched_from: str | None = None,
        active_account: str | None = None,
        project_path: str | None = None,
        verification_account: str | None = None,
        accounts=None,
    ) -> tuple[bool, str]:
        """Check if roles are properly configured for running tasks.

        Args:
            task_state: Optional task state (running, verifying, completed, failed).
                       When provided, shows dynamic status instead of static "Ready".
            worktree_path: Optional worktree path to display during active tasks.
            switched_from: If set, indicates this is a recent provider switch and
                          shows the previous provider name.
            active_account: If set, use this account as the active provider instead
                           of looking up the CODING role assignment.
            project_path: Optional project path to display (shown when no worktree exists).
            verification_account: If set, use this account when showing verifying status.
            accounts: Pre-fetched accounts list to avoid API call.

        Returns:
            Tuple of (is_ready, status_text)
        """
        if accounts is None:
            accounts = self.api_client.list_accounts()
        if not accounts:
            return False, "⚠️ Add a provider to start tasks."

        # Find coding account - prefer active_account if provided
        coding_account = None
        if active_account:
            for acc in accounts:
                if acc.name == active_account:
                    coding_account = acc
                    break
        if not coding_account:
            for acc in accounts:
                if acc.role == "CODING":
                    coding_account = acc
                    break

        if not coding_account:
            return False, "⚠️ Please select a Coding Agent in the Run Task tab."

        # Build switch indicator if there was a recent handoff
        switch_indicator = ""
        if switched_from:
            switch_indicator = f" *(switched from {switched_from})*"

        # Build dynamic status based on task state
        if task_state:
            state_icon = {
                "running": "⚡",
                "verifying": "🔍",
                "completed": "✓",
                "failed": "❌",
            }.get(task_state, "⏳")
            state_label = task_state.capitalize()

            # Determine which account to show metrics for
            if task_state == "verifying" and verification_account:
                metrics_account = verification_account
                agent_info = f"{verification_account}"
            else:
                metrics_account = coding_account.name
                agent_info = f"{coding_account.name} ({coding_account.provider})"

            usage_metrics = self._format_usage_metrics(metrics_account)

            # Show worktree path during active tasks, agent name when idle
            if worktree_path and task_state in ("running", "verifying"):
                if usage_metrics:
                    return True, f"{state_icon} {state_label} — **Worktree:** `{worktree_path}` {usage_metrics}{switch_indicator}"
                return True, f"{state_icon} {state_label} — **Worktree:** `{worktree_path}`{switch_indicator}"
            else:
                if usage_metrics:
                    return True, f"{state_icon} {state_label} — **Agent:** {agent_info} {usage_metrics}{switch_indicator}"
                return True, f"{state_icon} {state_label} — **Agent:** {agent_info}{switch_indicator}"

        # Static "Ready" status when no task is active
        coding_model_str = coding_account.model if coding_account.model else ""
        coding_info = f"{coding_account.name} ({coding_account.provider}"
        if coding_model_str:
            coding_info += f", {coding_model_str}"
        coding_info += ")"

        # Add usage metrics for ready status
        usage_metrics = self._format_usage_metrics(coding_account.name)

        # Show worktree path in Ready status if available, otherwise show project path
        from pathlib import Path
        if worktree_path:
            worktree_name = Path(worktree_path).name
            if usage_metrics:
                return True, f"✓ Ready — **Coding:** {coding_info} {usage_metrics} · **Worktree:** `{worktree_name}`{switch_indicator}"
            return True, f"✓ Ready — **Coding:** {coding_info} · **Worktree:** `{worktree_name}`{switch_indicator}"
        elif project_path:
            project_name = Path(project_path).name
            if usage_metrics:
                return True, f"✓ Ready — **Coding:** {coding_info} {usage_metrics} · **Project:** `{project_name}`{switch_indicator}"
            return True, f"✓ Ready — **Coding:** {coding_info} · **Project:** `{project_name}`{switch_indicator}"

        if usage_metrics:
            return True, f"✓ Ready — **Coding:** {coding_info} {usage_metrics}{switch_indicator}"
        return True, f"✓ Ready — **Coding:** {coding_info}{switch_indicator}"

    def assign_role(self, account_name: str, role: str, card_slots: int):
        """Assign a role to a provider and refresh the provider panel."""
        try:
            if not account_name:
                return self.provider_action_response("❌ Please select an account to assign a role", card_slots)
            if not role or not str(role).strip():
                return self.provider_action_response("❌ Please select a role", card_slots)

            accounts = self.api_client.list_accounts()
            account_names = {acc.name for acc in accounts}
            if account_name not in account_names:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            if role == "(none)":
                self._unassign_account_roles(account_name)
                return self.provider_action_response(f"✓ Removed role assignments from {account_name}", card_slots)

            if role.upper() != "CODING":
                return self.provider_action_response("❌ Only the CODING role is supported", card_slots)

            self._unassign_account_roles(account_name)
            self.api_client.set_account_role(account_name, "CODING")
            return self.provider_action_response(f"✓ Assigned CODING role to {account_name}", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error assigning role: {str(exc)}", card_slots)

    def set_model(self, account_name: str, model: str, card_slots: int):
        """Set the model for a provider account and refresh the provider panel."""
        try:
            if not account_name:
                return self.provider_action_response("❌ Please select an account", card_slots)

            if not model:
                return self.provider_action_response("❌ Please select a model", card_slots)

            accounts = self.api_client.list_accounts()
            account_names = {acc.name for acc in accounts}
            if account_name not in account_names:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            self.api_client.set_account_model(account_name, model)
            return self.provider_action_response(f"✓ Set model to `{model}` for {account_name}", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error setting model: {str(exc)}", card_slots)

    def set_reasoning(self, account_name: str, reasoning: str, card_slots: int):
        """Set reasoning effort for a provider account and refresh the provider panel."""
        try:
            if not account_name:
                return self.provider_action_response("❌ Please select an account", card_slots)

            if not reasoning:
                return self.provider_action_response("❌ Please select a reasoning level", card_slots)

            accounts = self.api_client.list_accounts()
            account_names = {acc.name for acc in accounts}
            if account_name not in account_names:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            self.api_client.set_account_reasoning(account_name, reasoning)
            return self.provider_action_response(f"✓ Set reasoning to `{reasoning}` for {account_name}", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error setting reasoning: {str(exc)}", card_slots)

    def get_models_for_account(
        self, account_name: str, model_catalog_override: ModelCatalog | None = None
    ) -> list[str]:
        """Get available models for an account based on its provider."""
        if not account_name:
            return ["default"]

        try:
            account = self.api_client.get_account(account_name)
            provider = account.provider
        except Exception:
            provider = ""

        catalog = model_catalog_override or self.model_catalog
        return catalog.get_models(provider, account_name)

    def get_reasoning_choices(self, provider: str, account_name: str | None = None) -> list[str]:
        """Return reasoning dropdown options for the provider."""
        if provider == "openai":
            stored = "default"
            if account_name:
                try:
                    account = self.api_client.get_account(account_name)
                    stored = account.reasoning or "default"
                except Exception:
                    stored = "default"
            stored = stored if isinstance(stored, str) else "default"
            choices = set(self.OPENAI_REASONING_LEVELS)
            if stored:
                choices.add(stored)
            ordered = [level for level in self.OPENAI_REASONING_LEVELS if level in choices]
            for choice in sorted(choices):
                if choice not in ordered:
                    ordered.append(choice)
            return ordered
        return ["default"]

    def delete_provider(self, account_name: str, confirmed: bool, card_slots: int):
        """Delete a provider after confirmation and refresh the provider panel."""
        try:
            if not account_name:
                return self.provider_action_response("❌ No provider selected", card_slots)

            if not confirmed:
                return self.provider_action_response("Deletion cancelled.", card_slots)

            try:
                account = self.api_client.get_account(account_name)
                provider = account.provider
            except Exception:
                provider = None

            if provider == "openai":
                codex_home = self._get_codex_home(account_name)
                if codex_home.exists():
                    shutil.rmtree(codex_home, ignore_errors=True)
            elif provider == "anthropic":
                claude_config = self._get_claude_config_dir(account_name)
                if claude_config.exists():
                    shutil.rmtree(claude_config, ignore_errors=True)

            self.api_client.delete_account(account_name)
            return self.provider_action_response(f"✓ Provider '{account_name}' deleted", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error deleting provider: {str(exc)}", card_slots)

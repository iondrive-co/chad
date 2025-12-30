from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path

import gradio as gr

from .model_catalog import ModelCatalog
from .installer import AIToolInstaller
from .mcp_config import ensure_global_mcp_config


class ProviderUIManager:
    """Provider management and display helpers for the web UI."""

    SUPPORTED_PROVIDERS = {"anthropic", "openai", "gemini", "mistral"}
    OPENAI_REASONING_LEVELS = ["default", "low", "medium", "high", "xhigh"]

    def __init__(
        self,
        security_mgr,
        main_password: str,
        model_catalog: ModelCatalog | None = None,
        installer: AIToolInstaller | None = None,
    ):
        self.security_mgr = security_mgr
        self.main_password = main_password
        self.model_catalog = model_catalog or ModelCatalog(security_mgr)
        self.installer = installer or AIToolInstaller()

    def list_providers(self) -> str:
        """Summarize all configured providers with model settings."""
        accounts = self.security_mgr.list_accounts()

        if not accounts:
            return "No providers configured yet. Add a provider with the ➕ below."

        rows = []
        for account_name, provider in accounts.items():
            model = self.security_mgr.get_account_model(account_name)
            model_str = f" | preferred model: `{model}`" if model != "default" else ""
            reasoning = self.security_mgr.get_account_reasoning(account_name)
            reasoning_str = f" | reasoning: `{reasoning}`" if reasoning != "default" else ""
            rows.append(f"- **{account_name}** ({provider}){model_str}{reasoning_str}")

        return "\n".join(rows)

    def _get_account_role(self, account_name: str) -> str | None:
        """Return the role assigned to the account, if any."""
        role_assignments = self.security_mgr.list_role_assignments()
        roles = [role for role, acct in role_assignments.items() if acct == account_name and role == "CODING"]
        return roles[0] if roles else None

    def get_provider_usage(self, account_name: str) -> str:
        """Get usage text for a single provider."""
        # Check for screenshot mode - return synthetic data
        if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
            from .screenshot_fixtures import get_mock_usage
            return get_mock_usage(account_name)

        accounts = self.security_mgr.list_accounts()
        provider = accounts.get(account_name)

        if not provider:
            return "Select a provider to see usage details."

        if provider == "openai":
            status_text = self._get_codex_usage(account_name)
        elif provider == "anthropic":
            status_text = self._get_claude_usage(account_name)
        elif provider == "gemini":
            status_text = self._get_gemini_usage()
        elif provider == "mistral":
            status_text = self._get_mistral_usage()
        else:
            status_text = "⚠️ **Unknown provider**"

        return status_text

    def _progress_bar(self, utilization_pct: float, width: int = 20) -> str:
        """Create a text progress bar for usage displays."""
        filled = int(max(0.0, min(100.0, utilization_pct)) / (100 / width))
        return "█" * filled + "░" * (width - filled)

    def get_remaining_usage(self, account_name: str) -> float:
        """Get remaining usage as 0.0-1.0 (1.0 = full capacity remaining).

        Used to sort providers by availability - highest remaining usage first.
        """
        accounts = self.security_mgr.list_accounts()
        provider = accounts.get(account_name)

        if not provider:
            return 0.0

        if provider == "anthropic":
            return self._get_claude_remaining_usage()
        if provider == "openai":
            return self._get_codex_remaining_usage(account_name)
        if provider == "gemini":
            return self._get_gemini_remaining_usage()
        if provider == "mistral":
            return self._get_mistral_remaining_usage()

        return 0.3  # Unknown provider, bias low

    def _get_claude_remaining_usage(self) -> float:
        """Get Claude remaining usage from API (0.0-1.0)."""
        import requests

        creds_file = Path.home() / ".claude" / ".credentials.json"
        if not creds_file.exists():
            return 0.0

        try:
            with open(creds_file) as f:
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
            util = five_hour.get("utilization", 0)
            return max(0.0, min(1.0, 1.0 - (util / 100.0)))

        except Exception:
            return 0.3  # Error, bias low

    def _get_codex_remaining_usage(self, account_name: str) -> float:
        """Get Codex remaining usage from session files (0.0-1.0)."""
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
                    path = Path(root) / filename
                    session_files.append((path.stat().st_mtime, path))

        if not session_files:
            return 0.8

        session_files.sort(reverse=True)
        latest_session = session_files[0][1]

        try:
            rate_limits = None
            with open(latest_session) as f:
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
                    util = primary.get("used_percent", 0)
                    return max(0.0, min(1.0, 1.0 - (util / 100.0)))

        except Exception:
            pass

        return 0.3  # Error, bias low

    def _get_gemini_remaining_usage(self) -> float:
        """Estimate Gemini remaining usage (0.0-1.0).

        No programmatic API available for quota, so we estimate based on
        whether logged in. Biased low since we can't verify actual quota.
        """
        oauth_file = Path.home() / ".gemini" / "oauth_creds.json"
        if not oauth_file.exists():
            return 0.0

        return 0.3  # Logged in but no quota API, bias low

    def _get_mistral_remaining_usage(self) -> float:
        """Estimate Mistral remaining usage (0.0-1.0).

        No programmatic API available for quota, so we estimate based on
        whether logged in. Biased low since we can't verify actual quota.
        """
        vibe_config = Path.home() / ".vibe" / "config.toml"
        if not vibe_config.exists():
            return 0.0

        return 0.3  # Logged in but no quota API, bias low

    def provider_state(self, card_slots: int, pending_delete: str | None = None) -> tuple:
        """Build UI state for provider cards (summary + per-account controls)."""
        accounts = self.security_mgr.list_accounts()
        # Keep insertion order - don't reorder on each refresh
        account_items = list(accounts.items())
        list_md = self.list_providers()

        outputs: list = [list_md]
        for idx in range(card_slots):
            if idx < len(account_items):
                account_name, provider = account_items[idx]
                header = f'<span class="provider-card__header-text">{account_name} ({provider})</span>'
                usage = self.get_provider_usage(account_name)

                delete_btn_update = (
                    gr.update(value="✓", variant="stop")
                    if pending_delete == account_name
                    else gr.update(value="🗑︎", variant="secondary")
                )

                outputs.extend(
                    [
                        gr.update(visible=True),  # Show card
                        header,
                        account_name,
                        usage,
                        delete_btn_update,
                    ]
                )
            else:
                outputs.extend(
                    [
                        gr.update(visible=False),  # Hide card
                        "",
                        "",
                        "",
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
        if temp_home:
            return Path(temp_home) / ".chad" / "codex-homes" / account_name
        return Path.home() / ".chad" / "codex-homes" / account_name

    def _get_claude_config_dir(self, account_name: str) -> Path:
        """Get the isolated CLAUDE_CONFIG_DIR for a Claude account.

        Each Claude account gets its own config directory to support
        multiple Claude accounts with separate authentication.
        """
        # Use temp home in screenshot mode
        temp_home = os.environ.get("CHAD_TEMP_HOME")
        if temp_home:
            return Path(temp_home) / ".chad" / "claude-configs" / account_name
        return Path.home() / ".chad" / "claude-configs" / account_name

    def _get_codex_usage(self, account_name: str) -> str:
        """Get usage info from Codex by parsing JWT token and session files."""
        codex_home = self._get_codex_home(account_name)
        auth_file = codex_home / ".codex" / "auth.json"
        if not auth_file.exists():
            return "❌ **Not logged in**\n\nClick 'Login' to authenticate this account."

        try:
            with open(auth_file) as f:
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
        codex_home = self._get_codex_home(account_name)
        sessions_dir = codex_home / ".codex" / "sessions"
        if not sessions_dir.exists():
            return None

        session_files: list[tuple[float, Path]] = []
        for root, _, files in os.walk(sessions_dir):
            for filename in files:
                if filename.endswith(".jsonl"):
                    path = Path(root) / filename
                    session_files.append((path.stat().st_mtime, path))

        if not session_files:
            return None

        session_files.sort(reverse=True)
        latest_session = session_files[0][1]

        rate_limits = None
        timestamp = None
        try:
            with open(latest_session) as f:
                for line in f:
                    if "rate_limits" in line:
                        data = json.loads(line.strip())
                        if data.get("type") == "event_msg":
                            payload = data.get("payload", {})
                            if payload.get("type") == "token_count":
                                rate_limits = payload.get("rate_limits")
                                timestamp = data.get("timestamp")
        except (json.JSONDecodeError, OSError):
            return None

        if not rate_limits:
            return None

        result = ""

        primary = rate_limits.get("primary", {})
        if primary:
            util = primary.get("used_percent", 0)
            reset_at = primary.get("resets_at", 0)
            bar = self._progress_bar(util)

            reset_str = datetime.fromtimestamp(reset_at).strftime("%I:%M%p") if reset_at else "N/A"
            result += "**5-hour session**\n"
            result += f"[{bar}] {util:.0f}% used\n"
            result += f"Resets at {reset_str}\n\n"

        secondary = rate_limits.get("secondary", {})
        if secondary:
            util = secondary.get("used_percent", 0)
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
                result += f"*Last updated: {update_dt.strftime('%Y-%m-%d %H:%M UTC')}*\n"
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
            with open(creds_file) as f:
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
            oauth_data["expiresAt"] = int(
                (datetime.now().timestamp() + token_data.get("expires_in", 28800)) * 1000
            )
            if "scope" in token_data:
                oauth_data["scopes"] = token_data["scope"].split()

            creds["claudeAiOauth"] = oauth_data

            with open(creds_file, "w") as f:
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
            return (
                "❌ **Not logged in**\n\n"
                "Click **Login** below to authenticate this account."
            )

        try:
            with open(creds_file) as f:
                creds = json.load(f)

            oauth_data = creds.get("claudeAiOauth", {})
            access_token = oauth_data.get("accessToken", "")
            subscription_type = (oauth_data.get("subscriptionType") or "unknown").upper()

            if not access_token:
                return (
                    "❌ **Not logged in**\n\n"
                    "Click **Login** below to authenticate this account."
                )

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
                    with open(creds_file) as f:
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
                util = five_hour.get("utilization", 0)
                reset_at = five_hour.get("resets_at", "")
                bar = self._progress_bar(util)

                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                        reset_str = reset_dt.strftime("%I:%M%p")
                    except ValueError:
                        reset_str = reset_at
                else:
                    reset_str = "N/A"

                result += "**5-hour session**\n"
                result += f"[{bar}] {util:.0f}% used\n"
                result += f"Resets at {reset_str}\n\n"

            seven_day = usage_data.get("seven_day")
            if seven_day:
                util = seven_day.get("utilization", 0)
                reset_at = seven_day.get("resets_at", "")
                bar = self._progress_bar(util)

                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                        reset_str = reset_dt.strftime("%b %d")
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
                util = extra.get("utilization", 0)
                bar = self._progress_bar(util)
                result += "**Extra credits**\n"
                result += f"[{bar}] ${used:.0f} / ${limit:.0f} ({util:.1f}%)\n\n"

            return result

        except requests.exceptions.RequestException as exc:
            return f"⚠️ **Network error:** {str(exc)}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"⚠️ **Error:** {str(exc)}"

    def _get_gemini_usage(self) -> str:  # noqa: C901
        """Get usage info from Gemini by parsing session files."""
        from collections import defaultdict

        gemini_dir = Path.home() / ".gemini"
        oauth_file = gemini_dir / "oauth_creds.json"

        if not oauth_file.exists():
            return "❌ **Not logged in**\n\nRun `gemini` in terminal to authenticate."

        tmp_dir = gemini_dir / "tmp"
        if not tmp_dir.exists():
            return (
                "✅ **Logged in**\n\n"
                "⚠️ **Usage data unavailable**\n\n"
                "Google Gemini only provides usage information after the first model interaction. "
                "Start a coding session to see token usage details.\n\n"
                "*Press refresh after using this provider to see current data*"
            )

        session_files = list(tmp_dir.glob("*/chats/session-*.json"))
        if not session_files:
            return (
                "✅ **Logged in**\n\n"
                "⚠️ **Usage data unavailable**\n\n"
                "Google Gemini only provides usage information after the first model interaction. "
                "Start a coding session to see token usage details.\n\n"
                "*Press refresh after using this provider to see current data*"
            )

        model_usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        )

        for session_file in session_files:
            try:
                with open(session_file) as f:
                    session_data = json.load(f)

                messages = session_data.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "gemini":
                        tokens = msg.get("tokens", {})
                        model = msg.get("model", "unknown")

                        model_usage[model]["requests"] += 1
                        model_usage[model]["input_tokens"] += tokens.get("input", 0)
                        model_usage[model]["output_tokens"] += tokens.get("output", 0)
                        model_usage[model]["cached_tokens"] += tokens.get("cached", 0)
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        if not model_usage:
            return "✅ **Logged in**\n\n*No usage data yet*"

        result = "✅ **Logged in**\n\n"
        result += "**Model Usage**\n\n"
        result += "| Model | Reqs | Input | Output |\n"
        result += "|-------|------|-------|--------|\n"

        total_input = 0
        total_output = 0
        total_cached = 0
        total_requests = 0

        for model, usage in sorted(model_usage.items()):
            reqs = usage["requests"]
            input_tok = usage["input_tokens"]
            output_tok = usage["output_tokens"]
            cached_tok = usage["cached_tokens"]

            total_requests += reqs
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

        for session_file in session_files:
            try:
                with open(session_file) as f:
                    data = json.load(f)

                metadata = data.get("metadata", {})
                stats = metadata.get("stats", {})

                total_prompt_tokens += stats.get("session_prompt_tokens", 0)
                total_completion_tokens += stats.get("session_completion_tokens", 0)
                total_cost += stats.get("session_cost", 0.0)
                session_count += 1
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        if session_count == 0:
            return "✅ **Logged in**\n\n*No valid session data found*"

        total_tokens = total_prompt_tokens + total_completion_tokens

        result = "✅ **Logged in**\n\n"
        result += "**Cumulative Usage**\n\n"
        result += f"**Sessions:** {session_count:,}\n"
        result += f"**Input tokens:** {total_prompt_tokens:,}\n"
        result += f"**Output tokens:** {total_completion_tokens:,}\n"
        result += f"**Total tokens:** {total_tokens:,}\n"
        result += f"**Estimated cost:** ${total_cost:.4f}\n"

        return result

    def get_account_choices(self) -> list[str]:
        """Get list of account names for dropdowns."""
        return list(self.security_mgr.list_accounts().keys())

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

            if provider_type == "mistral":
                vibe_config = Path.home() / ".vibe" / "config.toml"
                if vibe_config.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            return False, "Unknown provider type"

        except Exception as exc:
            return False, f"Error: {str(exc)}"

    def _setup_codex_account(self, account_name: str) -> str:
        """Setup isolated home directory for a Codex account."""
        codex_home = self._get_codex_home(account_name)
        codex_dir = codex_home / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        ensure_global_mcp_config(home=codex_home)
        return str(codex_home)

    def _setup_claude_account(self, account_name: str) -> str:
        """Setup isolated config directory for a Claude account."""
        config_dir = self._get_claude_config_dir(account_name)
        config_dir.mkdir(parents=True, exist_ok=True)
        return str(config_dir)

    def _ensure_provider_cli(self, provider_type: str) -> tuple[bool, str]:
        """Ensure the provider's CLI is present; install if missing."""
        tool_map = {
            "openai": "codex",
            "anthropic": "claude",
            "gemini": "gemini",
            "mistral": "vibe",
        }
        tool_key = tool_map.get(provider_type)
        if not tool_key:
            return True, ""
        return self.installer.ensure_tool(tool_key)

    def add_provider(self, provider_name: str, provider_type: str, card_slots: int):  # noqa: C901
        """Add a new provider and return refreshed provider panel state."""
        import subprocess

        name_field_value = provider_name
        add_btn_state = gr.update(interactive=bool(provider_name.strip()))
        accordion_state = gr.update(open=True)

        try:
            if provider_type not in self.SUPPORTED_PROVIDERS:
                base_response = self.provider_action_response(f"❌ Unsupported provider '{provider_type}'", card_slots)
                return (*base_response, name_field_value, add_btn_state, accordion_state)

            cli_ok, cli_detail = self._ensure_provider_cli(provider_type)
            if not cli_ok:
                base_response = self.provider_action_response(f"❌ {cli_detail}", card_slots)
                return (*base_response, name_field_value, add_btn_state, accordion_state)

            existing_accounts = self.security_mgr.list_accounts()
            base_name = provider_type
            counter = 1
            account_name = provider_name if provider_name else base_name

            while account_name in existing_accounts:
                account_name = f"{base_name}-{counter}"
                counter += 1

            if provider_type == "openai":
                import os
                codex_home = self._setup_codex_account(account_name)
                codex_cli = cli_detail or "codex"

                env = os.environ.copy()
                env["HOME"] = codex_home

                login_result = subprocess.run(
                    [codex_cli, "login"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if login_result.returncode == 0:
                    self.security_mgr.store_account(account_name, provider_type, "", self.main_password)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    import shutil

                    codex_home_path = self._get_codex_home(account_name)
                    if codex_home_path.exists():
                        shutil.rmtree(codex_home_path, ignore_errors=True)

                    error = login_result.stderr.strip() if login_result.stderr else "Login was cancelled or failed"
                    result = f"❌ Login failed for '{account_name}': {error}"
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
                    import os

                    creds_file = Path(config_dir) / ".credentials.json"

                    try:
                        import pexpect

                        # Set up environment for Claude
                        env = os.environ.copy()
                        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
                        env["TERM"] = "xterm-256color"

                        child = pexpect.spawn(
                            claude_cli,
                            timeout=120,
                            encoding='utf-8',
                            env=env
                        )

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
                                        with open(creds_file) as f:
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

                    except ImportError:
                        # pexpect not available, fall back to script wrapper
                        login_process = subprocess.Popen(
                            ["script", "-q", "-c",
                             f'CLAUDE_CONFIG_DIR="{config_dir}" "{claude_cli}"',
                             "/dev/null"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
                            start_new_session=True,
                        )

                        start_time = time.time()
                        timeout_secs = 120
                        while time.time() - start_time < timeout_secs:
                            if creds_file.exists():
                                try:
                                    with open(creds_file) as f:
                                        creds_data = json.load(f)
                                    oauth = creds_data.get("claudeAiOauth", {})
                                    if oauth.get("accessToken"):
                                        login_success = True
                                        break
                                except (json.JSONDecodeError, KeyError, OSError):
                                    pass
                            time.sleep(2)

                        try:
                            login_process.terminate()
                            login_process.wait(timeout=5)
                        except Exception:
                            try:
                                login_process.kill()
                            except Exception:
                                pass

                    except Exception:
                        pass  # Any error, fall through to login_success check

                if login_success:
                    self.security_mgr.store_account(account_name, provider_type, "", self.main_password)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    # Login failed/timed out - clean up
                    import shutil
                    config_path = Path(config_dir)
                    if config_path.exists():
                        shutil.rmtree(config_path, ignore_errors=True)

                    result = f"❌ Login timed out for '{account_name}'. Please try again."
                    base_response = self.provider_action_response(result, card_slots)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            else:
                self.security_mgr.store_account(account_name, provider_type, "", self.main_password)
                result = f"✓ Provider '{account_name}' ({provider_type}) added."

                login_success, login_msg = self._check_provider_login(provider_type, account_name)

                if login_success:
                    result += f" ✅ {login_msg}"
                else:
                    result += f" ⚠️ {login_msg}"
                    auth_info = {
                        "gemini": ("gemini", "Opens browser to authenticate with your Google account"),
                        "mistral": ("vibe --setup", "Set up your Mistral API key"),
                    }
                    auth_cmd, auth_desc = auth_info.get(provider_type, ("unknown", ""))
                    result += f" — manual login: run `{auth_cmd}` ({auth_desc})"

                name_field_value = ""
                add_btn_state = gr.update(interactive=False)
                accordion_state = gr.update(open=False)

            base_response = self.provider_action_response(result, card_slots)
            return (*base_response, name_field_value, add_btn_state, accordion_state)
        except subprocess.TimeoutExpired:
            import shutil

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
        role_assignments = self.security_mgr.list_role_assignments()
        for role, acct in list(role_assignments.items()):
            if acct == account_name and role == "CODING":
                self.security_mgr.clear_role(role)

    def get_role_config_status(self) -> tuple[bool, str]:
        """Check if roles are properly configured for running tasks."""
        role_assignments = self.security_mgr.list_role_assignments()
        coding_account = role_assignments.get("CODING")

        accounts = self.security_mgr.list_accounts()
        if not accounts:
            return False, "⚠️ Add a provider to start tasks."

        if not coding_account or coding_account not in accounts:
            return False, "⚠️ Please select a Coding Agent in the Run Task tab."

        coding_provider = accounts.get(coding_account, "unknown")
        coding_model = self.security_mgr.get_account_model(coding_account)
        coding_model_str = coding_model if coding_model != "default" else ""

        coding_info = f"{coding_account} ({coding_provider}"
        if coding_model_str:
            coding_info += f", {coding_model_str}"
        coding_info += ")"

        return True, f"✓ Ready — **Coding:** {coding_info}"

    def format_role_status(self) -> str:
        """Return role status text."""
        _, status = self.get_role_config_status()
        return status

    def assign_role(self, account_name: str, role: str, card_slots: int):
        """Assign a role to a provider and refresh the provider panel."""
        try:
            if not account_name:
                return self.provider_action_response("❌ Please select an account to assign a role", card_slots)
            if not role or not str(role).strip():
                return self.provider_action_response("❌ Please select a role", card_slots)

            accounts = self.security_mgr.list_accounts()
            if account_name not in accounts:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            if role == "(none)":
                self._unassign_account_roles(account_name)
                return self.provider_action_response(f"✓ Removed role assignments from {account_name}", card_slots)

            if role.upper() != "CODING":
                return self.provider_action_response("❌ Only the CODING role is supported", card_slots)

            self._unassign_account_roles(account_name)
            self.security_mgr.assign_role(account_name, "CODING")
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

            accounts = self.security_mgr.list_accounts()
            if account_name not in accounts:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            self.security_mgr.set_account_model(account_name, model)
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

            accounts = self.security_mgr.list_accounts()
            if account_name not in accounts:
                return self.provider_action_response(f"❌ Provider '{account_name}' not found", card_slots)

            self.security_mgr.set_account_reasoning(account_name, reasoning)
            return self.provider_action_response(f"✓ Set reasoning to `{reasoning}` for {account_name}", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error setting reasoning: {str(exc)}", card_slots)

    def get_models_for_account(
        self, account_name: str, model_catalog_override: ModelCatalog | None = None
    ) -> list[str]:
        """Get available models for an account based on its provider."""
        if not account_name:
            return ["default"]

        accounts = self.security_mgr.list_accounts()
        provider = accounts.get(account_name, "")
        catalog = model_catalog_override or self.model_catalog
        return catalog.get_models(provider, account_name)

    def get_reasoning_choices(self, provider: str, account_name: str | None = None) -> list[str]:
        """Return reasoning dropdown options for the provider."""
        if provider == "openai":
            stored = "default"
            if account_name:
                getter = getattr(self.security_mgr, "get_account_reasoning", None)
                if getter:
                    try:
                        stored = getter(account_name) or "default"
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
        import shutil

        try:
            if not account_name:
                return self.provider_action_response("❌ No provider selected", card_slots)

            if not confirmed:
                return self.provider_action_response("Deletion cancelled.", card_slots)

            accounts = self.security_mgr.list_accounts()
            provider = accounts.get(account_name)

            if provider == "openai":
                codex_home = self._get_codex_home(account_name)
                if codex_home.exists():
                    shutil.rmtree(codex_home, ignore_errors=True)
            elif provider == "anthropic":
                claude_config = self._get_claude_config_dir(account_name)
                if claude_config.exists():
                    shutil.rmtree(claude_config, ignore_errors=True)

            self.security_mgr.delete_account(account_name)
            return self.provider_action_response(f"✓ Provider '{account_name}' deleted", card_slots)
        except Exception as exc:
            return self.provider_action_response(f"❌ Error deleting provider: {str(exc)}", card_slots)

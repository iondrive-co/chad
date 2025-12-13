"""Gradio web interface for Chad."""

import re
import gradio as gr
from pathlib import Path
import threading
import queue
from typing import Iterator

from .security import SecurityManager
from .session_manager import (
    SessionManager, get_coding_timeout, get_management_timeout,
    TaskPhase, INVESTIGATION_PROMPT, IMPLEMENTATION_PROMPT, VERIFICATION_PROMPT, SAFETY_CONSTRAINTS,
    CODING_INVESTIGATION_CONTEXT, CODING_IMPLEMENTATION_CONTEXT
)
from .providers import ModelConfig, parse_codex_output, extract_final_codex_response


# Custom styling for the provider management area to improve contrast between
# the summary header and each provider card.
PROVIDER_PANEL_CSS = """
.provider-section-title {
  color: #e2e8f0;
  letter-spacing: 0.01em;
}

.provider-summary {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 12px 14px;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
  color: #0f172a;
}

.provider-card {
  background: linear-gradient(135deg, #0c1424 0%, #0a1a32 100%);
  border: 1px solid #1f2b46;
  border-radius: 16px;
  padding: 14px 16px;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.28);
  gap: 8px;
}

.provider-card:nth-of-type(even) {
  background: linear-gradient(135deg, #0b1b32 0%, #0c1324 100%);
  border-color: #243552;
}

.provider-card .provider-card__header-row,
.provider-card__header-row {
  display: flex;
  align-items: stretch;
  background: #000 !important;
  border-radius: 12px;
  padding: 8px 10px;
  gap: 8px;
}

.provider-card .provider-card__header-row .provider-card__header,
.provider-card .provider-card__header {
  background: #000 !important;
  color: #fff !important;
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  flex: 1;
  border-radius: 10px;
}

.provider-card .provider-card__header-row .provider-card__header-text,
.provider-card__header-row .provider-card__header-text {
  background: #000;
  color: #fff;
  padding: 6px 10px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  letter-spacing: 0.02em;
}

.provider-card .provider-card__header-row .provider-card__header .prose,
.provider-card .provider-card__header-row .provider-card__header .prose *,
.provider-card .provider-card__header .prose,
.provider-card .provider-card__header .prose * {
  color: #fff !important;
  background: #000 !important;
  margin: 0;
  padding: 0;
}

.provider-card .provider-card__header-row .provider-card__header > *,
.provider-card .provider-card__header > * {
  background: #000 !important;
  color: #fff !important;
}

.provider-card .provider-card__header-row .provider-card__header :is(h1, h2, h3, h4, h5, h6, p, span),
.provider-card .provider-card__header :is(h1, h2, h3, h4, h5, h6, p, span) {
  margin: 0;
  padding: 0;
  background: transparent !important;
  color: inherit !important;
}

.provider-card .provider-controls {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid #243047;
  border-radius: 12px;
  padding: 10px 12px;
}

.provider-usage-title {
  margin-top: 10px !important;
  color: #475569;
  border-top: 1px solid #e2e8f0;
  padding-top: 8px;
  letter-spacing: 0.01em;
}

.provider-usage {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px 12px;
  color: #0f172a;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06);
}

.provider-delete {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  min-width: 32px;
  max-width: 32px;
  flex-shrink: 0;
  padding: 4px;
  border-radius: 8px;
  background: #0f172a !important;
  border: 1px solid #f97373 !important;
  color: #fecdd3 !important;
  font-size: 14px;
  line-height: 1;
  box-shadow: none;
}

#status-box {
  padding: 8px 12px;
  font-size: 14px;
}

#live-output-box {
  max-height: 220px;
  overflow-y: auto;
}

#live-stream-box {
  margin-top: 8px;
}

#live-stream-box .live-output-header {
  background: #2a2a3e;
  color: #a8d4ff;
  padding: 6px 12px;
  border-radius: 8px 8px 0 0;
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.05em;
  margin: 0;
}

#live-stream-box .live-output-content {
  background: #3a3a4e !important;
  color: #e2e8f0 !important;
  border: 1px solid #555 !important;
  border-top: none !important;
  border-radius: 0 0 8px 8px !important;
  padding: 12px !important;
  margin: 0 !important;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: monospace;
  font-size: 13px;
}

#live-stream-box .live-output-content * {
  background: transparent !important;
  color: #e2e8f0 !important;
}

/* Agent communication chatbot - preserve scroll position */
.chatbot-container, [data-testid="chatbot"] {
  scroll-behavior: auto !important;
}
"""


def summarize_content(content: str, max_length: int = 200) -> str:
    """Create a brief summary of content for collapsed view."""
    # Remove markdown formatting for cleaner summary
    clean = content.replace('*', '').replace('`', '').replace('#', '')
    # Get first paragraph or first max_length chars
    first_para = clean.split('\n\n')[0].strip()
    if len(first_para) <= max_length:
        return first_para
    return first_para[:max_length].rsplit(' ', 1)[0] + '...'


def make_chat_message(speaker: str, content: str, collapsible: bool = True) -> dict:
    """Create a Gradio 6.x compatible chat message.

    Args:
        speaker: The speaker name (e.g., "MANAGEMENT AI", "CODING AI")
        content: The message content
        collapsible: Whether to make long messages collapsible with a summary
    """
    # Map speakers to roles
    # MANAGEMENT AI messages are 'user' (outgoing instructions)
    # CODING AI messages are 'assistant' (incoming responses)
    role = "user" if "MANAGEMENT" in speaker else "assistant"

    # For long content, make it collapsible with a summary
    if collapsible and len(content) > 300:
        summary = summarize_content(content)
        formatted = f"**{speaker}**\n\n{summary}\n\n<details><summary>Show full output</summary>\n\n{content}\n\n</details>"  # noqa: E501
    else:
        formatted = f"**{speaker}**\n\n{content}"

    return {"role": role, "content": formatted}


class ChadWebUI:
    """Web interface for Chad using Gradio."""

    def __init__(self, security_mgr: SecurityManager, main_password: str):
        self.security_mgr = security_mgr
        self.main_password = main_password
        self.session_manager = None
        self.active_sessions = {}
        self.cancel_requested = False
        # Number of provider cards to render; expanded during UI creation to allow new providers
        self.provider_card_count = 10

    # Available models per provider
    PROVIDER_MODELS = {
        'anthropic': ['claude-sonnet-4-20250514', 'claude-opus-4-20250514', 'default'],
        'openai': ['o3', 'o4-mini', 'codex-mini', 'default'],
        'gemini': ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'default'],
        'mistral': ['default']
    }

    def list_providers(self) -> str:
        """Summarize all configured providers with role and model."""
        accounts = self.security_mgr.list_accounts()
        role_assignments = self.security_mgr.list_role_assignments()

        if not accounts:
            return "No providers configured yet. Add a provider with the ➕ below."

        rows = []
        for account_name, provider in accounts.items():
            roles = [role for role, acct in role_assignments.items() if acct == account_name]
            role_str = f" — roles: {', '.join(roles)}" if roles else ""
            model = self.security_mgr.get_account_model(account_name)
            model_str = f" | preferred model: `{model}`" if model != 'default' else ""
            rows.append(f"- **{account_name}** ({provider}){role_str}{model_str}")

        return "\n".join(rows)

    def _get_account_role(self, account_name: str) -> str | None:
        """Return the role assigned to the account, if any."""
        role_assignments = self.security_mgr.list_role_assignments()
        roles = [role for role, acct in role_assignments.items() if acct == account_name]
        if len(roles) == 0:
            return None
        if 'CODING' in roles and 'MANAGEMENT' in roles:
            return "BOTH"
        return roles[0]

    def get_provider_usage(self, account_name: str) -> str:
        """Get usage text for a single provider."""
        accounts = self.security_mgr.list_accounts()
        provider = accounts.get(account_name)

        if not provider:
            return "Select a provider to see usage details."

        if provider == 'openai':
            status_text = self._get_codex_usage(account_name)
        elif provider == 'anthropic':
            status_text = self._get_claude_usage()
        elif provider == 'gemini':
            status_text = self._get_gemini_usage()
        elif provider == 'mistral':
            status_text = self._get_mistral_usage()
        else:
            status_text = "⚠️ **Unknown provider**"

        return status_text

    def _provider_state(self) -> tuple:
        """Build UI state for provider cards (summary + per-account controls)."""
        accounts = self.security_mgr.list_accounts()
        account_items = list(accounts.items())
        list_md = self.list_providers()

        outputs: list = [list_md]
        card_slots = self.provider_card_count

        for idx in range(card_slots):
            if idx < len(account_items):
                account_name, provider = account_items[idx]
                header = f'<span class="provider-card__header-text">{account_name} ({provider})</span>'
                current_role = self._get_account_role(account_name)
                role_value = current_role if current_role else "(none)"
                model_choices = self.get_models_for_account(account_name)
                stored_model = self.security_mgr.get_account_model(account_name)
                model_value = stored_model if stored_model in model_choices else model_choices[0]
                usage = self.get_provider_usage(account_name)

                outputs.extend([
                    gr.update(visible=True),
                    header,
                    account_name,
                    gr.update(value=role_value),
                    gr.update(choices=model_choices, value=model_value),
                    usage
                ])
            else:
                outputs.extend([
                    gr.update(visible=False),
                    "",
                    "",
                    gr.update(value="(none)"),
                    gr.update(choices=['default'], value='default'),
                    ""
                ])

        return tuple(outputs)

    def _provider_action_response(self, feedback: str):
        """Return standard provider panel updates with feedback text."""
        return (feedback, *self._provider_state())

    def _get_codex_home(self, account_name: str) -> Path:
        """Get the isolated HOME directory for a Codex account."""
        from pathlib import Path
        return Path.home() / ".chad" / "codex-homes" / account_name

    def _get_codex_usage(self, account_name: str) -> str:
        """Get usage info from Codex by parsing JWT token and session files."""
        import json
        import base64
        from datetime import datetime

        codex_home = self._get_codex_home(account_name)
        auth_file = codex_home / ".codex" / "auth.json"
        if not auth_file.exists():
            return "❌ **Not logged in**\n\nClick 'Login' to authenticate this account."

        try:
            with open(auth_file) as f:
                auth_data = json.load(f)

            tokens = auth_data.get('tokens', {})
            access_token = tokens.get('access_token', '')

            if not access_token:
                return "❌ **Not logged in**\n\nClick 'Login' to authenticate this account."

            # Decode JWT payload (middle part)
            parts = access_token.split('.')
            if len(parts) != 3:
                return "⚠️ **Invalid token format**"

            # Add padding for base64 decode
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding

            decoded = base64.urlsafe_b64decode(payload)
            jwt_data = json.loads(decoded)

            # Extract info
            auth_info = jwt_data.get('https://api.openai.com/auth', {})
            profile = jwt_data.get('https://api.openai.com/profile', {})

            plan_type = auth_info.get('chatgpt_plan_type', 'unknown').upper()
            email = profile.get('email', 'Unknown')
            exp_timestamp = jwt_data.get('exp', 0)

            # Format expiration
            exp_date = datetime.fromtimestamp(exp_timestamp).strftime('%Y-%m-%d %H:%M') if exp_timestamp else 'Unknown'

            result = f"✅ **Logged in** ({plan_type} plan)\n\n"
            result += f"**Account:** {email}\n"
            result += f"**Token expires:** {exp_date}\n\n"

            # Try to get usage data from Codex session files
            usage_data = self._get_codex_session_usage(account_name)
            if usage_data:
                result += "**Current Usage**\n\n"
                result += usage_data
            else:
                result += "*Usage data will appear after running Codex*"

            return result

        except Exception as e:
            return f"⚠️ **Error reading auth data:** {str(e)}"

    def _get_codex_session_usage(self, account_name: str) -> str | None:  # noqa: C901
        """Extract usage data from the most recent Codex session file."""
        import json
        import os
        from pathlib import Path
        from datetime import datetime

        codex_home = self._get_codex_home(account_name)
        sessions_dir = codex_home / ".codex" / "sessions"
        if not sessions_dir.exists():
            return None

        # Find the most recent session file
        session_files = []
        for root, _, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith('.jsonl'):
                    path = Path(root) / f
                    session_files.append((path.stat().st_mtime, path))

        if not session_files:
            return None

        # Sort by modification time, most recent first
        session_files.sort(reverse=True)
        latest_session = session_files[0][1]

        # Read the file and find the last rate_limits entry
        rate_limits = None
        timestamp = None
        try:
            with open(latest_session) as f:
                for line in f:
                    if 'rate_limits' in line:
                        data = json.loads(line.strip())
                        if data.get('type') == 'event_msg':
                            payload = data.get('payload', {})
                            if payload.get('type') == 'token_count':
                                rate_limits = payload.get('rate_limits')
                                timestamp = data.get('timestamp')
        except (json.JSONDecodeError, OSError):
            return None

        if not rate_limits:
            return None

        result = ""

        # 5-hour limit (primary)
        primary = rate_limits.get('primary', {})
        if primary:
            util = primary.get('used_percent', 0)
            reset_at = primary.get('resets_at', 0)

            # Create progress bar
            filled = int(util / 5)  # 20 chars total
            bar = '█' * filled + '░' * (20 - filled)

            # Format reset time
            if reset_at:
                reset_dt = datetime.fromtimestamp(reset_at)
                reset_str = reset_dt.strftime('%I:%M%p')
            else:
                reset_str = 'N/A'

            result += "**5-hour session**\n"
            result += f"[{bar}] {util:.0f}% used\n"
            result += f"Resets at {reset_str}\n\n"

        # Weekly limit (secondary)
        secondary = rate_limits.get('secondary', {})
        if secondary:
            util = secondary.get('used_percent', 0)
            reset_at = secondary.get('resets_at', 0)

            filled = int(util / 5)
            bar = '█' * filled + '░' * (20 - filled)

            if reset_at:
                reset_dt = datetime.fromtimestamp(reset_at)
                reset_str = reset_dt.strftime('%b %d')
            else:
                reset_str = 'N/A'

            result += "**Weekly limit**\n"
            result += f"[{bar}] {util:.0f}% used\n"
            result += f"Resets {reset_str}\n\n"

        # Credits (if available)
        credits = rate_limits.get('credits', {})
        if credits:
            has_credits = credits.get('has_credits', False)
            unlimited = credits.get('unlimited', False)
            balance = credits.get('balance')

            if unlimited:
                result += "**Credits:** Unlimited\n\n"
            elif has_credits and balance is not None:
                result += f"**Credits balance:** ${balance}\n\n"

        # Show when data was last updated
        if timestamp:
            try:
                update_dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                result += f"*Last updated: {update_dt.strftime('%Y-%m-%d %H:%M UTC')}*\n"
            except ValueError:
                pass

        return result if result else None

    def _get_claude_usage(self) -> str:  # noqa: C901
        """Get usage info from Claude via API."""
        import json
        import requests
        from pathlib import Path
        from datetime import datetime

        creds_file = Path.home() / ".claude" / ".credentials.json"
        if not creds_file.exists():
            return "❌ **Not logged in**\n\nRun `claude` in terminal to authenticate."

        try:
            with open(creds_file) as f:
                creds = json.load(f)

            oauth_data = creds.get('claudeAiOauth', {})
            access_token = oauth_data.get('accessToken', '')
            subscription_type = oauth_data.get('subscriptionType', 'unknown').upper()

            if not access_token:
                return "❌ **Not logged in**\n\nRun `claude` in terminal to authenticate."

            # Call the usage API
            response = requests.get(
                'https://api.anthropic.com/api/oauth/usage',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'anthropic-beta': 'oauth-2025-04-20',
                    'User-Agent': 'claude-code/2.0.32',
                    'Content-Type': 'application/json',
                },
                timeout=10
            )

            if response.status_code != 200:
                return f"⚠️ **Error fetching usage:** HTTP {response.status_code}"

            usage_data = response.json()

            # Build the display
            result = f"✅ **Logged in** ({subscription_type} plan)\n\n"
            result += "**Current Usage**\n\n"

            # 5-hour limit
            five_hour = usage_data.get('five_hour', {})
            if five_hour:
                util = five_hour.get('utilization', 0)
                reset_at = five_hour.get('resets_at', '')

                # Create progress bar
                filled = int(util / 5)  # 20 chars total
                bar = '█' * filled + '░' * (20 - filled)

                # Format reset time
                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace('Z', '+00:00'))
                        reset_str = reset_dt.strftime('%I:%M%p')
                    except ValueError:
                        reset_str = reset_at
                else:
                    reset_str = 'N/A'

                result += "**5-hour session**\n"
                result += f"[{bar}] {util:.0f}% used\n"
                result += f"Resets at {reset_str}\n\n"

            # 7-day limit (if present)
            seven_day = usage_data.get('seven_day')
            if seven_day:
                util = seven_day.get('utilization', 0)
                reset_at = seven_day.get('resets_at', '')

                filled = int(util / 5)
                bar = '█' * filled + '░' * (20 - filled)

                if reset_at:
                    try:
                        reset_dt = datetime.fromisoformat(reset_at.replace('Z', '+00:00'))
                        reset_str = reset_dt.strftime('%b %d')
                    except ValueError:
                        reset_str = reset_at
                else:
                    reset_str = 'N/A'

                result += "**Weekly limit**\n"
                result += f"[{bar}] {util:.0f}% used\n"
                result += f"Resets {reset_str}\n\n"

            # Extra usage (if enabled)
            extra = usage_data.get('extra_usage', {})
            if extra and extra.get('is_enabled'):
                used = extra.get('used_credits', 0)
                limit = extra.get('monthly_limit', 0)
                util = extra.get('utilization', 0)

                filled = int(util / 5)
                bar = '█' * filled + '░' * (20 - filled)

                result += "**Extra credits**\n"
                result += f"[{bar}] ${used:.0f} / ${limit:.0f} ({util:.1f}%)\n\n"

            return result

        except requests.exceptions.RequestException as e:
            return f"⚠️ **Network error:** {str(e)}"
        except Exception as e:
            return f"⚠️ **Error:** {str(e)}"

    def _get_gemini_usage(self) -> str:  # noqa: C901
        """Get usage info from Gemini by parsing session files."""
        import json
        from pathlib import Path
        from collections import defaultdict

        gemini_dir = Path.home() / ".gemini"
        oauth_file = gemini_dir / "oauth_creds.json"

        if not oauth_file.exists():
            return "❌ **Not logged in**\n\nRun `gemini` in terminal to authenticate."

        # Find all session files
        tmp_dir = gemini_dir / "tmp"
        if not tmp_dir.exists():
            return "✅ **Logged in**\n\n*No session data yet*"

        session_files = list(tmp_dir.glob("*/chats/session-*.json"))
        if not session_files:
            return "✅ **Logged in**\n\n*No session data yet*"

        # Aggregate token usage by model
        model_usage = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0})

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

        # Build display
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

        # Summary with cache savings
        if total_cached > 0 and total_input > 0:
            cache_pct = (total_cached / total_input) * 100
            result += f"\n**Cache savings:** {total_cached:,} ({cache_pct:.1f}%) tokens served from cache\n"

        return result

    def _get_mistral_usage(self) -> str:
        """Get usage info from Mistral Vibe by parsing session files."""
        import json
        from pathlib import Path

        vibe_config = Path.home() / ".vibe" / "config.toml"
        if not vibe_config.exists():
            return "❌ **Not logged in**\n\nRun `vibe --setup` in terminal to authenticate."

        # Find session files in default location
        sessions_dir = Path.home() / ".vibe" / "logs" / "session"
        if not sessions_dir.exists():
            return "✅ **Logged in**\n\n*No session data yet*"

        session_files = list(sessions_dir.glob("session_*.json"))
        if not session_files:
            return "✅ **Logged in**\n\n*No session data yet*"

        # Aggregate stats from all session files
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
        from pathlib import Path

        try:
            if provider_type == 'openai':
                codex_home = self._get_codex_home(account_name)
                auth_file = codex_home / ".codex" / "auth.json"
                if auth_file.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            elif provider_type == 'anthropic':
                creds_file = Path.home() / ".claude" / ".credentials.json"
                if creds_file.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            elif provider_type == 'gemini':
                # Check for Gemini CLI's own oauth credentials, not gcloud
                gemini_oauth = Path.home() / ".gemini" / "oauth_creds.json"
                if gemini_oauth.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            elif provider_type == 'mistral':
                # Check for Vibe config file
                vibe_config = Path.home() / ".vibe" / "config.toml"
                if vibe_config.exists():
                    return True, "Logged in"
                return False, "Not logged in"

            return False, "Unknown provider type"

        except Exception as e:
            return False, f"Error: {str(e)}"

    def _setup_codex_account(self, account_name: str) -> str:
        """Setup isolated home directory for a Codex account."""
        codex_home = self._get_codex_home(account_name)
        codex_dir = codex_home / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        return str(codex_home)

    def login_codex_account(self, account_name: str) -> str:
        """Initiate login for a Codex account. Returns instructions for the user."""
        import subprocess
        import os

        if not account_name:
            return "❌ Please select an account to login"

        accounts = self.security_mgr.list_accounts()
        if account_name not in accounts:
            return f"❌ Account '{account_name}' not found"

        if accounts[account_name] != 'openai':
            return f"❌ Account '{account_name}' is not an OpenAI account"

        # Setup isolated home
        codex_home = self._setup_codex_account(account_name)

        # Create environment with isolated HOME
        env = os.environ.copy()
        env['HOME'] = codex_home

        # First logout any existing session
        subprocess.run(['codex', 'logout'], env=env, capture_output=True, timeout=10)

        # Now run login - this will open a browser
        result = subprocess.run(
            ['codex', 'login'],
            env=env,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            return f"✅ **Login successful for '{account_name}'!**\n\nRefresh the Usage Statistics to see account details."  # noqa: E501
        else:
            error = result.stderr.strip() if result.stderr else "Unknown error"
            return f"⚠️ **Login may have failed**\n\n{error}\n\nTry refreshing Usage Statistics to check status."

    def add_provider(self, provider_name: str, provider_type: str):  # noqa: C901
        """Add a new provider and return refreshed provider panel state."""
        import subprocess
        import os

        # Default form updates: keep current entry and leave accordion open
        name_field_value = provider_name
        add_btn_state = gr.update(interactive=bool(provider_name.strip()))
        accordion_state = gr.update(open=True)

        try:
            if provider_type not in self.PROVIDER_MODELS:
                base_response = self._provider_action_response(f"❌ Unsupported provider '{provider_type}'")
                return (*base_response, name_field_value, add_btn_state, accordion_state)

            existing_accounts = self.security_mgr.list_accounts()
            base_name = provider_type
            counter = 1
            account_name = provider_name if provider_name else base_name

            while account_name in existing_accounts:
                account_name = f"{base_name}-{counter}"
                counter += 1

            if provider_type == 'openai':
                # For OpenAI, setup isolated home and run login immediately
                codex_home = self._setup_codex_account(account_name)

                # Create environment with isolated HOME
                env = os.environ.copy()
                env['HOME'] = codex_home

                # Run login - this will open a browser
                login_result = subprocess.run(
                    ['codex', 'login'],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if login_result.returncode == 0:
                    # Login succeeded, now save the account
                    self.security_mgr.store_account(account_name, provider_type, "", self.main_password)
                    result = f"✅ Provider '{account_name}' added and logged in!"
                    name_field_value = ""
                    add_btn_state = gr.update(interactive=False)
                    accordion_state = gr.update(open=False)
                else:
                    # Login failed, clean up
                    import shutil
                    codex_home_path = self._get_codex_home(account_name)
                    if codex_home_path.exists():
                        shutil.rmtree(codex_home_path, ignore_errors=True)

                    error = login_result.stderr.strip() if login_result.stderr else "Login was cancelled or failed"
                    result = f"❌ Login failed for '{account_name}': {error}"
                    base_response = self._provider_action_response(result)
                    return (*base_response, name_field_value, add_btn_state, accordion_state)

            else:
                # For other providers, store account and check login status
                self.security_mgr.store_account(account_name, provider_type, "", self.main_password)
                result = f"✓ Provider '{account_name}' ({provider_type}) added."

                login_success, login_msg = self._check_provider_login(provider_type, account_name)

                if login_success:
                    result += f" ✅ {login_msg}"
                else:
                    result += f" ⚠️ {login_msg}"

                    # Provide manual login instructions
                    auth_info = {
                        'anthropic': ('claude', 'Opens browser to authenticate with your Anthropic account'),
                        'gemini': ('gemini', 'Opens browser to authenticate with your Google account'),
                        'mistral': ('vibe --setup', 'Set up your Mistral API key')
                    }
                    auth_cmd, auth_desc = auth_info.get(provider_type, ('unknown', ''))
                    result += f" — manual login: run `{auth_cmd}` ({auth_desc})"

                # Only clear the form on successful add of non-OpenAI accounts
                name_field_value = ""
                add_btn_state = gr.update(interactive=False)
                accordion_state = gr.update(open=False)

            base_response = self._provider_action_response(result)
            return (*base_response, name_field_value, add_btn_state, accordion_state)
        except subprocess.TimeoutExpired:
            # Clean up on timeout
            import shutil
            codex_home_path = self._get_codex_home(account_name)
            if codex_home_path.exists():
                shutil.rmtree(codex_home_path, ignore_errors=True)
            base_response = self._provider_action_response(f"❌ Login timed out for '{account_name}'. Please try again.")
            return (*base_response, name_field_value, add_btn_state, accordion_state)
        except Exception as e:
            base_response = self._provider_action_response(f"❌ Error adding provider: {str(e)}")
            return (*base_response, name_field_value, add_btn_state, accordion_state)

    def _unassign_account_roles(self, account_name: str) -> None:
        """Remove all role assignments for an account."""
        role_assignments = self.security_mgr.list_role_assignments()
        for role, acct in list(role_assignments.items()):
            if acct == account_name:
                self.security_mgr.clear_role(role)

    def get_role_config_status(self) -> tuple[bool, str]:
        """Check if roles are properly configured for running tasks.

        Returns:
            Tuple of (is_ready, status_message)
        """
        role_assignments = self.security_mgr.list_role_assignments()
        coding_account = role_assignments.get('CODING')
        management_account = role_assignments.get('MANAGEMENT')

        missing = []
        if not coding_account:
            missing.append("CODING")
        if not management_account:
            missing.append("MANAGEMENT")

        if missing:
            return False, f"⚠️ Missing role assignments: {', '.join(missing)}. Configure in Providers tab."
        return True, "✓ Ready to run"

    def assign_role(self, account_name: str, role: str):
        """Assign a role to a provider and refresh the provider panel."""
        try:
            if not account_name:
                return self._provider_action_response("❌ Please select an account to assign a role")
            if not role or not str(role).strip():
                return self._provider_action_response("❌ Please select a role")

            accounts = self.security_mgr.list_accounts()
            if account_name not in accounts:
                return self._provider_action_response(f"❌ Provider '{account_name}' not found")

            if role == '(none)':
                # Clear all roles for this account
                self._unassign_account_roles(account_name)
                return self._provider_action_response(f"✓ Removed role assignments from {account_name}")

            # First, remove any existing roles for this account
            self._unassign_account_roles(account_name)

            if role.upper() == "BOTH":
                self.security_mgr.assign_role(account_name, "CODING")
                self.security_mgr.assign_role(account_name, "MANAGEMENT")
                return self._provider_action_response(f"✓ Assigned CODING and MANAGEMENT roles to {account_name}")
            else:
                self.security_mgr.assign_role(account_name, role.upper())
                return self._provider_action_response(f"✓ Assigned {role.upper()} role to {account_name}")
        except Exception as e:
            return self._provider_action_response(f"❌ Error assigning role: {str(e)}")

    def set_model(self, account_name: str, model: str):
        """Set the model for a provider account and refresh the provider panel."""
        try:
            if not account_name:
                return self._provider_action_response("❌ Please select an account")

            if not model:
                return self._provider_action_response("❌ Please select a model")

            accounts = self.security_mgr.list_accounts()
            if account_name not in accounts:
                return self._provider_action_response(f"❌ Provider '{account_name}' not found")

            self.security_mgr.set_account_model(account_name, model)
            return self._provider_action_response(f"✓ Set model to `{model}` for {account_name}")
        except Exception as e:
            return self._provider_action_response(f"❌ Error setting model: {str(e)}")

    def get_models_for_account(self, account_name: str) -> list[str]:
        """Get available models for an account based on its provider."""
        if not account_name:
            return ['default']

        accounts = self.security_mgr.list_accounts()
        provider = accounts.get(account_name, '')
        return self.PROVIDER_MODELS.get(provider, ['default'])

    def delete_provider(self, account_name: str, confirmed: bool = False):
        """Delete a provider after confirmation and refresh the provider panel."""
        import shutil

        try:
            if not account_name:
                return self._provider_action_response("❌ No provider selected")

            if not confirmed:
                return self._provider_action_response("Deletion cancelled.")

            # Check if it's an OpenAI account and clean up isolated home
            accounts = self.security_mgr.list_accounts()
            if accounts.get(account_name) == 'openai':
                codex_home = self._get_codex_home(account_name)
                if codex_home.exists():
                    shutil.rmtree(codex_home, ignore_errors=True)

            self.security_mgr.delete_account(account_name)
            return self._provider_action_response(f"✓ Provider '{account_name}' deleted")
        except Exception as e:
            return self._provider_action_response(f"❌ Error deleting provider: {str(e)}")

    def cancel_task(self) -> str:
        """Cancel the running task."""
        self.cancel_requested = True
        if self.session_manager:
            self.session_manager.stop_all()
            self.session_manager = None
        return "🛑 Task cancelled"

    def start_chad_task(  # noqa: C901
        self,
        project_path: str,
        task_description: str,
        insane_mode: bool = False
    ) -> Iterator[tuple[list, str, str, gr.Textbox, gr.TextArea, gr.Checkbox, gr.Button, gr.Button]]:
        """Start Chad task and stream updates.

        Flow: Management AI plans first, then coding AI executes.
        """
        chat_history = []
        message_queue = queue.Queue()
        self.cancel_requested = False

        # Helper to yield with UI state
        def make_yield(history, status, live_stream="", interactive=False):
            return (
                history,
                status,
                live_stream,
                gr.Textbox(interactive=interactive),
                gr.TextArea(interactive=interactive),
                gr.Checkbox(interactive=interactive),
                gr.Button(interactive=interactive),
                gr.Button(interactive=not interactive)  # Cancel button opposite
            )

        try:
            # Validate inputs
            if not project_path or not task_description:
                yield make_yield([], "❌ Please provide both project path and task description", interactive=True)
                return

            path = Path(project_path).expanduser().resolve()
            if not path.exists() or not path.is_dir():
                yield make_yield([], f"❌ Invalid project path: {project_path}", interactive=True)
                return

            # Get role assignments
            role_assignments = self.security_mgr.list_role_assignments()
            coding_account = role_assignments.get('CODING')
            management_account = role_assignments.get('MANAGEMENT')

            if not coding_account or not management_account:
                yield make_yield([], "❌ Please assign CODING and MANAGEMENT roles in the Provider Management tab first", interactive=True)  # noqa: E501
                return

            # Get provider info
            accounts = self.security_mgr.list_accounts()
            coding_provider = accounts[coding_account]
            management_provider = accounts[management_account]

            # Create configs with stored models
            coding_model = self.security_mgr.get_account_model(coding_account)
            management_model = self.security_mgr.get_account_model(management_account)

            coding_config = ModelConfig(
                provider=coding_provider,
                model_name=coding_model,
                account_name=coding_account
            )

            management_config = ModelConfig(
                provider=management_provider,
                model_name=management_model,
                account_name=management_account
            )

            coding_timeout = get_coding_timeout(coding_provider)
            management_timeout = get_management_timeout(management_provider)

            # Initialize status - start streaming immediately
            status_text = "**Starting Chad...**\n\n"
            status_text += f"• Project: {path}\n"
            status_text += f"• CODING: {coding_account} ({coding_provider})\n"
            status_text += f"• MANAGEMENT: {management_account} ({management_provider})\n"
            status_text += f"• Insane mode: {'ENABLED ⚠️' if insane_mode else 'DISABLED'}\n\n"

            yield make_yield([], status_text + "⏳ Initializing sessions...", interactive=False)

            # Create session manager with silent mode enabled
            session_manager = SessionManager(coding_config, management_config, insane_mode, silent=True)
            self.session_manager = session_manager

            # Start sessions
            if not session_manager.start_sessions(str(path), task_description):
                yield make_yield([], status_text + "❌ Failed to start sessions", interactive=True)
                return

            yield make_yield([], status_text + "✓ Sessions started\n\n⏳ Management AI is planning...", interactive=False)  # noqa: E501

            # Activity callback to capture live updates
            def on_activity(activity_type: str, detail: str):
                if activity_type == 'stream':
                    # Raw streaming chunk - send for live display
                    message_queue.put(('stream', detail))
                elif activity_type == 'tool':
                    message_queue.put(('activity', f"🔧 {detail}"))
                elif activity_type == 'thinking':
                    message_queue.put(('activity', f"🧠 {detail}"))
                elif activity_type == 'text' and detail:
                    message_queue.put(('activity', f"💭 {detail[:80]}"))

            session_manager.set_activity_callback(on_activity)

            # Relay loop in separate thread
            relay_complete = threading.Event()
            task_success = [False]
            completion_reason = [""]

            def relay_loop():  # noqa: C901
                """Run the state machine: Investigation -> Implementation -> Verification."""
                try:
                    # State machine variables
                    phase = TaskPhase.INVESTIGATION
                    plan = None
                    impl_notes = []
                    investigation_revisits = 0
                    max_investigation_revisits = 2

                    # Iteration limits per phase
                    max_investigation_iters = 10
                    max_implementation_iters = 30
                    max_verification_iters = 5

                    investigation_iter = 0
                    implementation_iter = 0
                    verification_iter = 0

                    def check_cancelled():
                        if self.cancel_requested:
                            message_queue.put(('status', "🛑 Task cancelled by user"))
                            return True
                        return False

                    def get_phase_status(p: TaskPhase) -> str:
                        phase_names = {
                            TaskPhase.INVESTIGATION: ("📋", "Investigate"),
                            TaskPhase.IMPLEMENTATION: ("🔨", "Implement"),
                            TaskPhase.VERIFICATION: ("✅", "Verify")
                        }
                        icon, name = phase_names[p]
                        return f"{icon} Phase {p.value}: {name}"

                    def add_phase_divider(phase: TaskPhase):
                        """Add a visual divider when entering a new phase."""
                        phase_names = {
                            TaskPhase.INVESTIGATION: ("📋", "INVESTIGATE"),
                            TaskPhase.IMPLEMENTATION: ("🔨", "IMPLEMENT"),
                            TaskPhase.VERIFICATION: ("✅", "VERIFY")
                        }
                        icon, name = phase_names[phase]
                        message_queue.put(('phase_divider', f"{icon} PHASE {phase.value}: {name}"))

                    # Build investigation prompt with safety constraints
                    investigation_system = INVESTIGATION_PROMPT.format(
                        task_description=task_description,
                        project_path=str(path)
                    )
                    if not insane_mode:
                        investigation_system += "\n\n" + SAFETY_CONSTRAINTS

                    # Add phase divider for investigation start
                    add_phase_divider(phase)

                    # Initialize management with investigation prompt
                    message_queue.put(('status', f"{get_phase_status(phase)} - Starting..."))
                    message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                    message_queue.put(('message_start', 'MANAGEMENT AI'))  # Placeholder while working
                    session_manager.send_to_management(investigation_system)
                    mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                    if check_cancelled() or not mgmt_response:
                        if not mgmt_response:
                            message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                        return

                    mgmt_text = extract_final_codex_response(mgmt_response)
                    message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))


                    # Main state machine loop
                    while session_manager.are_sessions_alive() and not self.cancel_requested:

                        # ==================== INVESTIGATION PHASE ====================
                        if phase == TaskPhase.INVESTIGATION:
                            investigation_iter += 1

                            if investigation_iter > max_investigation_iters:
                                message_queue.put(('status', "⚠️ Investigation taking too long, forcing plan"))
                                plan = f"1. Implement the task: {task_description}"
                                phase = TaskPhase.IMPLEMENTATION
                                continue

                            # Check if management has produced a PLAN:
                            plan_match = re.search(r'PLAN:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)

                            if plan_match:
                                plan = plan_match.group(1).strip()
                                phase = TaskPhase.IMPLEMENTATION
                                implementation_iter = 0

                                # Add phase divider
                                add_phase_divider(phase)
                                message_queue.put(('phase', f"📝 **Plan:**\n{plan}"))

                                # Build implementation prompt
                                impl_system = IMPLEMENTATION_PROMPT.format(
                                    task_description=task_description,
                                    project_path=str(path),
                                    plan=plan
                                )
                                if not insane_mode:
                                    impl_system += "\n\n" + SAFETY_CONSTRAINTS

                                # Send plan to coding AI to start implementation
                                message_queue.put(('status', f"{get_phase_status(phase)} - Coding AI executing..."))
                                coding_instruction = f"{CODING_IMPLEMENTATION_CONTEXT}\n\nORIGINAL TASK: {task_description}\n\nPLAN TO EXECUTE:\n{plan}\n\nExecute this plan to accomplish the task. Report back when complete."
                                session_manager.send_to_coding(coding_instruction)
                                continue

                            # Management wants more investigation - send to coding AI
                            message_queue.put(('status', f"{get_phase_status(phase)} - Coding AI investigating..."))
                            message_queue.put(('ai_switch', 'CODING AI'))
                            message_queue.put(('message_start', 'CODING AI'))
                            session_manager.send_to_coding(f"{CODING_INVESTIGATION_CONTEXT}\n\n{mgmt_text}")

                            coding_response = session_manager.get_coding_response(timeout=coding_timeout)
                            if check_cancelled() or not coding_response:
                                if not coding_response:
                                    message_queue.put(('status', "❌ No response from CODING AI"))
                                break

                            parsed_coding = parse_codex_output(coding_response)
                            message_queue.put(('message_complete', "CODING AI", parsed_coding))

                            # Send findings back to management
                            message_queue.put(('status', f"{get_phase_status(phase)} - Management reviewing findings..."))
                            message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                            message_queue.put(('message_start', 'MANAGEMENT AI'))
                            session_manager.send_to_management(f"CODING AI FINDINGS:\n{parsed_coding}\n\nIf you have enough info, output PLAN: with implementation steps. If not, ask for MORE info in ONE batched request (multiple files/searches at once).")
                            mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                            if check_cancelled() or not mgmt_response:
                                if not mgmt_response:
                                    message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                break

                            mgmt_text = extract_final_codex_response(mgmt_response)
                            message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))

                        # ==================== IMPLEMENTATION PHASE ====================
                        elif phase == TaskPhase.IMPLEMENTATION:
                            implementation_iter += 1

                            if implementation_iter > max_implementation_iters:
                                completion_reason[0] = "Reached maximum implementation iterations."
                                message_queue.put(('status', "⚠️ Implementation taking too long"))
                                break

                            # Get coding AI response
                            message_queue.put(('status', f"{get_phase_status(phase)} - Coding AI working..."))
                            message_queue.put(('ai_switch', 'CODING AI'))
                            message_queue.put(('message_start', 'CODING AI'))
                            coding_response = session_manager.get_coding_response(timeout=coding_timeout)

                            if check_cancelled() or not coding_response:
                                if not coding_response:
                                    message_queue.put(('status', "❌ No response from CODING AI"))
                                break

                            parsed_coding = parse_codex_output(coding_response)
                            message_queue.put(('message_complete', "CODING AI", parsed_coding))
                            impl_notes.append(parsed_coding[:10000])  # Keep more context for verification

                            # Send to management for supervision
                            message_queue.put(('status', f"{get_phase_status(phase)} - Management supervising..."))
                            message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                            message_queue.put(('message_start', 'MANAGEMENT AI'))
                            session_manager.send_to_management(f"CODING AI OUTPUT:\n{parsed_coding}\n\nRespond with CONTINUE: <guidance for remaining steps> or VERIFY if complete. Be direct - give ALL remaining instructions at once, not one step at a time.")
                            mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                            if check_cancelled() or not mgmt_response:
                                if not mgmt_response:
                                    message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                break

                            mgmt_text = extract_final_codex_response(mgmt_response)
                            message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))

                            # Check for phase transitions
                            mgmt_upper = mgmt_text.upper()
                            if "VERIFY" in mgmt_upper:
                                phase = TaskPhase.VERIFICATION
                                verification_iter = 0

                                # Add phase divider
                                add_phase_divider(phase)

                                # Build verification prompt
                                impl_summary = "\n".join(impl_notes[-5:])  # Last 5 notes
                                verify_system = VERIFICATION_PROMPT.format(
                                    task_description=task_description,
                                    project_path=str(path),
                                    plan=plan,
                                    impl_notes=impl_summary
                                )
                                if not insane_mode:
                                    verify_system += "\n\n" + SAFETY_CONSTRAINTS

                                message_queue.put(('status', f"{get_phase_status(phase)} - Management verifying..."))
                                message_queue.put(('message_start', 'MANAGEMENT AI'))
                                session_manager.send_to_management(verify_system)
                                mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                                if check_cancelled() or not mgmt_response:
                                    if not mgmt_response:
                                        message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                    break

                                mgmt_text = extract_final_codex_response(mgmt_response)
                                message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))
                                continue

                            # Management providing guidance - relay to coding AI
                            message_queue.put(('ai_switch', 'CODING AI'))
                            continue_match = re.search(r'CONTINUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                            if continue_match:
                                guidance = continue_match.group(1).strip()
                                session_manager.send_to_coding(f"{CODING_IMPLEMENTATION_CONTEXT}\n\n{guidance}")
                            else:
                                # Fallback: send the whole response as guidance
                                session_manager.send_to_coding(f"{CODING_IMPLEMENTATION_CONTEXT}\n\n{mgmt_text}")

                        # ==================== VERIFICATION PHASE ====================
                        elif phase == TaskPhase.VERIFICATION:
                            verification_iter += 1

                            if verification_iter > max_verification_iters:
                                completion_reason[0] = "Verification inconclusive after maximum attempts."
                                message_queue.put(('status', "⚠️ Verification inconclusive"))
                                break

                            mgmt_upper = mgmt_text.upper()

                            # Check for COMPLETE
                            if "COMPLETE" in mgmt_upper and "PLAN_ISSUE" not in mgmt_upper and "IMPL_ISSUE" not in mgmt_upper:
                                completion_reason[0] = "Management AI verified task completion."
                                message_queue.put(('status', "✓ Task verified complete!"))
                                task_success[0] = True
                                break

                            # Check for PLAN_ISSUE - return to investigation
                            plan_issue_match = re.search(r'PLAN_ISSUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                            if plan_issue_match:
                                investigation_revisits += 1
                                if investigation_revisits > max_investigation_revisits:
                                    completion_reason[0] = "Too many plan revisions needed."
                                    message_queue.put(('status', "⚠️ Too many plan issues"))
                                    break

                                issue = plan_issue_match.group(1).strip().split('\n')[0]
                                phase = TaskPhase.INVESTIGATION
                                investigation_iter = 0

                                # Add phase divider with issue note
                                add_phase_divider(phase)
                                message_queue.put(('phase', f"⚠️ Plan issue: {issue}"))

                                # Reinitialize investigation with context
                                reinvestigate_prompt = f"""The previous plan had issues: {issue}

{investigation_system}

Previous plan that failed:
{plan}

Create a better plan that addresses the issue."""
                                message_queue.put(('message_start', 'MANAGEMENT AI'))
                                session_manager.send_to_management(reinvestigate_prompt)
                                mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                                if check_cancelled() or not mgmt_response:
                                    break

                                mgmt_text = extract_final_codex_response(mgmt_response)
                                message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))
                                continue

                            # Check for IMPL_ISSUE - return to implementation with fix
                            impl_issue_match = re.search(r'IMPL_ISSUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                            if impl_issue_match:
                                issue = impl_issue_match.group(1).strip()
                                phase = TaskPhase.IMPLEMENTATION
                                # Don't reset implementation_iter to prevent infinite loops

                                # Add phase divider with issue note
                                add_phase_divider(phase)
                                message_queue.put(('phase', f"⚠️ Implementation issue: {issue[:100]}..."))

                                # Send fix instruction to coding AI
                                message_queue.put(('ai_switch', 'CODING AI'))
                                fix_instruction = f"{CODING_IMPLEMENTATION_CONTEXT}\n\nVERIFICATION FOUND AN ISSUE:\n{issue}\n\nPlease fix this and report back."
                                session_manager.send_to_coding(fix_instruction)
                                continue

                            # Check if management is confirming completion in natural language
                            completion_phrases = ['task is complete', 'task completed', 'successfully completed',
                                                  'has been completed', 'is done', 'task done', 'confirmed complete',
                                                  'yes, complete', 'verified complete', 'fulfills the requirement',
                                                  'fulfill the original task', 'satisfies the requirement']
                            if any(phrase in mgmt_text.lower() for phrase in completion_phrases):
                                completion_reason[0] = "Management AI confirmed task completion."
                                message_queue.put(('status', "✓ Task verified complete!"))
                                task_success[0] = True
                                break

                            # Management is doing direct verification - prompt for verdict if needed
                            if verification_iter >= 3:
                                completion_reason[0] = "Task appears complete (verification loop limit reached)."
                                message_queue.put(('status', "✓ Task complete (auto-verified)"))
                                task_success[0] = True
                                break

                            message_queue.put(('message_start', 'MANAGEMENT AI'))
                            session_manager.send_to_management("Output your verdict: COMPLETE, PLAN_ISSUE: <reason>, or IMPL_ISSUE: <reason>")
                            mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                            if check_cancelled() or not mgmt_response:
                                break

                            mgmt_text = extract_final_codex_response(mgmt_response)
                            message_queue.put(('message_complete', "MANAGEMENT AI", parse_codex_output(mgmt_response) or mgmt_text))

                except Exception as e:
                    message_queue.put(('status', f"❌ Error: {str(e)}"))
                finally:
                    session_manager.stop_all()
                    relay_complete.set()

            # Start relay thread
            relay_thread = threading.Thread(target=relay_loop, daemon=True)
            relay_thread.start()

            # Stream updates with live activity
            current_status = status_text + "⏳ Management AI is planning..."
            current_live_stream = ""
            yield make_yield(chat_history, current_status, current_live_stream, interactive=False)

            import time as time_module
            last_activity = ""
            streaming_buffer = ""
            current_ai = "MANAGEMENT AI"  # Track which AI is producing output
            last_yield_time = 0.0
            min_yield_interval = 0.05  # 50ms for responsive streaming

            def format_live_output(ai_name: str, content: str) -> str:
                """Format live output with header showing active AI."""
                if not content.strip():
                    return ""
                return f'<div class="live-output-header">▶ {ai_name}</div>\n<div class="live-output-content">{content}</div>'

            # Track pending message index for placeholder updates
            pending_message_idx = None

            while not relay_complete.is_set():
                try:
                    msg = message_queue.get(timeout=0.02)
                    msg_type = msg[0]

                    if msg_type == 'message':
                        # Legacy: direct message (no placeholder)
                        speaker, content = msg[1], msg[2]
                        chat_history.append(make_chat_message(speaker, content))
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        pending_message_idx = None
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'message_start':
                        # Add placeholder message showing AI is working
                        speaker = msg[1]
                        placeholder = {"role": "user" if "MANAGEMENT" in speaker else "assistant",
                                       "content": f"**{speaker}**\n\n⏳ *Working...*"}
                        chat_history.append(placeholder)
                        pending_message_idx = len(chat_history) - 1
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'message_complete':
                        # Replace placeholder with actual content
                        speaker, content = msg[1], msg[2]
                        if pending_message_idx is not None and pending_message_idx < len(chat_history):
                            chat_history[pending_message_idx] = make_chat_message(speaker, content)
                        else:
                            chat_history.append(make_chat_message(speaker, content))
                        pending_message_idx = None
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'status':
                        current_status = status_text + msg[1]
                        streaming_buffer = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'phase_divider':
                        # Phase divider - distinct visual separator
                        phase_name = msg[1]
                        divider = f"───────────── {phase_name} ─────────────"
                        # Use a special format that stands out from agent messages
                        chat_history.append({"role": "user", "content": divider})
                        streaming_buffer = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'phase':
                        # Phase info message (plan details, issues, etc.)
                        phase_msg = msg[1]
                        chat_history.append({"role": "user", "content": phase_msg})
                        streaming_buffer = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = time_module.time()

                    elif msg_type == 'ai_switch':
                        # Update which AI is currently active
                        current_ai = msg[1]
                        streaming_buffer = ""  # Clear buffer on AI switch

                    elif msg_type == 'stream':
                        # Accumulate streaming content
                        streaming_buffer += msg[1]
                        # Limit buffer size to last 2000 chars for display
                        if len(streaming_buffer) > 2000:
                            streaming_buffer = streaming_buffer[-2000:]
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            display_buffer = streaming_buffer[-1500:] if len(streaming_buffer) > 1500 else streaming_buffer
                            current_live_stream = format_live_output(current_ai, display_buffer)
                            yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                            last_yield_time = now

                    elif msg_type == 'activity':
                        last_activity = msg[1]
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            if streaming_buffer:
                                display_buffer = streaming_buffer[-1500:] if len(streaming_buffer) > 1500 else streaming_buffer
                                current_live_stream = format_live_output(current_ai, display_buffer + f"\n\n{last_activity}")
                            else:
                                current_live_stream = f"**Live:** {last_activity}"
                            yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                            last_yield_time = now

                except queue.Empty:
                    now = time_module.time()
                    if now - last_yield_time >= 0.3:
                        if streaming_buffer:
                            display_buffer = streaming_buffer[-1500:] if len(streaming_buffer) > 1500 else streaming_buffer
                            current_live_stream = format_live_output(current_ai, display_buffer)
                        elif last_activity:
                            current_live_stream = f"**Live:** {last_activity}"
                        yield make_yield(chat_history, current_status, current_live_stream, interactive=False)
                        last_yield_time = now

            # Final update with completion reason
            relay_thread.join(timeout=1)
            if task_success[0]:
                final_status = f"✓ Task completed!\n\n*{completion_reason[0]}*" if completion_reason[0] else "✓ Task completed!"
            else:
                final_status = (
                    f"❌ Task did not complete successfully\n\n*{completion_reason[0]}*"
                    if completion_reason[0]
                    else "❌ Task did not complete successfully"
                )
            yield make_yield(chat_history, status_text + final_status, "", interactive=True)

        except Exception as e:
            import traceback
            error_msg = f"❌ Error: {str(e)}\n\n```\n{traceback.format_exc()}\n```"
            yield make_yield(chat_history, error_msg, "", interactive=True)

    def create_interface(self) -> gr.Blocks:
        """Create the Gradio interface."""
        with gr.Blocks(title="Chad") as interface:
            # Inject custom CSS for provider styling
            gr.HTML(f"<style>{PROVIDER_PANEL_CSS}</style>")

            with gr.Tabs():
                # Run Task Tab (default)
                with gr.Tab("🚀 Run Task") as run_task_tab:
                    gr.Markdown("## Start a New Task")

                    # Check initial role configuration
                    is_ready, config_status = self.get_role_config_status()

                    with gr.Row():
                        with gr.Column():
                            project_path = gr.Textbox(
                                label="Project Path",
                                placeholder="/path/to/project",
                                value=str(Path.cwd())
                            )
                            task_description = gr.TextArea(
                                label="Task Description",
                                placeholder="Describe what you want done...",
                                lines=5
                            )
                            insane_mode = gr.Checkbox(
                                label="⚠️ INSANE MODE (disables safety constraints)",
                                value=False
                            )
                            # Show role configuration status
                            role_status = gr.Markdown(config_status, elem_id="role-config-status")
                            with gr.Row():
                                start_btn = gr.Button(
                                    "Start Task",
                                    variant="primary",
                                    interactive=is_ready
                                )
                                cancel_btn = gr.Button("🛑 Cancel", variant="stop")

                    gr.Markdown("## Agent Communication")
                    with gr.Row():
                        with gr.Column():
                            chatbot = gr.Chatbot(
                                label="Conversation History",
                                height=400,
                                elem_id="agent-chatbot",
                                autoscroll=False
                            )

                    gr.Markdown("## Live Output")
                    status_box = gr.Markdown("*Ready to start*", elem_id="status-box")
                    live_stream_box = gr.Markdown("", elem_id="live-stream-box")

                    # Connect task execution
                    start_btn.click(
                        self.start_chad_task,
                        inputs=[project_path, task_description, insane_mode],
                        outputs=[chatbot, status_box, live_stream_box, project_path, task_description, insane_mode, start_btn, cancel_btn]  # noqa: E501
                    )

                    cancel_btn.click(
                        self.cancel_task,
                        outputs=[status_box]
                    )

                # Providers Tab (combined management + usage)
                with gr.Tab("⚙️ Providers"):
                    account_items = list(self.security_mgr.list_accounts().items())
                    # Allow room for new providers without needing a reload
                    self.provider_card_count = max(12, len(account_items) + 8)

                    provider_feedback = gr.Markdown("")
                    gr.Markdown("### Providers", elem_classes=["provider-section-title"])

                    provider_list = gr.Markdown(self.list_providers(), elem_classes=["provider-summary"])
                    refresh_btn = gr.Button("🔄 Refresh", variant="secondary")
                    delete_confirm_state = gr.State(False)

                    provider_cards = []
                    for idx in range(self.provider_card_count):
                        if idx < len(account_items):
                            account_name, provider_type = account_items[idx]
                            visible = True
                            header_text = f'<span class="provider-card__header-text">{account_name} ({provider_type})</span>'
                            role_value = self._get_account_role(account_name) or "(none)"
                            model_choices = self.get_models_for_account(account_name)
                            stored_model = self.security_mgr.get_account_model(account_name)
                            model_value = stored_model if stored_model in model_choices else model_choices[0]
                            usage_text = self.get_provider_usage(account_name)
                        else:
                            account_name = ""
                            visible = False
                            header_text = ""
                            role_value = "(none)"
                            model_choices = ["default"]
                            model_value = "default"
                            usage_text = ""

                        with gr.Group(visible=visible, elem_classes=["provider-card"]) as card_group:
                            with gr.Row(elem_classes=["provider-card__header-row"]):
                                card_header = gr.Markdown(header_text, elem_classes=["provider-card__header"])
                                delete_btn = gr.Button("🗑️", variant="secondary", size="sm", min_width=0, scale=0, elem_classes=["provider-delete"])  # noqa: E501
                            account_state = gr.State(account_name)
                            with gr.Row(elem_classes=["provider-controls"]):
                                role_dropdown = gr.Dropdown(
                                    choices=["(none)", "CODING", "MANAGEMENT", "BOTH"],
                                    label="Role",
                                    value=role_value,
                                    scale=1
                                )
                                model_dropdown = gr.Dropdown(
                                    choices=model_choices,
                                    label="Preferred Model",
                                    value=model_value,
                                    allow_custom_value=True,
                                    scale=1
                                )

                            gr.Markdown("Usage", elem_classes=["provider-usage-title"])
                            usage_box = gr.Markdown(usage_text, elem_classes=["provider-usage"])

                        provider_cards.append({
                            "group": card_group,
                            "header": card_header,
                            "account_state": account_state,
                            "account_name": account_name,  # Store name for delete handler
                            "role_dropdown": role_dropdown,
                            "model_dropdown": model_dropdown,
                            "usage_box": usage_box,
                            "delete_btn": delete_btn
                        })

                    with gr.Accordion("Add New Provider", open=False) as add_provider_accordion:
                        gr.Markdown("Click to add another provider. Close the accordion to retract without adding.")
                        new_provider_name = gr.Textbox(
                            label="Provider Name",
                            placeholder="e.g., work-claude"
                        )
                        new_provider_type = gr.Dropdown(
                            choices=["anthropic", "openai", "gemini", "mistral"],
                            label="Provider Type",
                            value="anthropic"
                        )
                        add_btn = gr.Button("Add Provider", variant="primary", interactive=False)

                    provider_outputs = [provider_feedback, provider_list]
                    for card in provider_cards:
                        provider_outputs.extend([
                            card["group"],
                            card["header"],
                            card["account_state"],
                            card["role_dropdown"],
                            card["model_dropdown"],
                            card["usage_box"]
                        ])

                    # Add role status and start button to outputs so they update when roles change
                    provider_outputs_with_task_status = provider_outputs + [role_status, start_btn]

                    add_provider_outputs = provider_outputs + [new_provider_name, add_btn, add_provider_accordion]

                    def refresh_with_task_status():
                        base = self._provider_action_response("")
                        is_ready, config_msg = self.get_role_config_status()
                        return (*base, config_msg, gr.update(interactive=is_ready))

                    refresh_btn.click(
                        refresh_with_task_status,
                        outputs=provider_outputs_with_task_status
                    )

                    new_provider_name.change(
                        lambda name: gr.update(interactive=bool(name.strip())),
                        inputs=[new_provider_name],
                        outputs=[add_btn]
                    )

                    add_btn.click(
                        self.add_provider,
                        inputs=[new_provider_name, new_provider_type],
                        outputs=add_provider_outputs
                    )

                    def assign_role_with_task_status(account_name, role):
                        base = self.assign_role(account_name, role)
                        is_ready, config_msg = self.get_role_config_status()
                        return (*base, config_msg, gr.update(interactive=is_ready))

                    for card in provider_cards:
                        card["role_dropdown"].change(
                            assign_role_with_task_status,
                            inputs=[card["account_state"], card["role_dropdown"]],
                            outputs=provider_outputs_with_task_status
                        )

                        card["model_dropdown"].change(
                            self.set_model,
                            inputs=[card["account_state"], card["model_dropdown"]],
                            outputs=provider_outputs
                        )

                        # Get the stored account name for this specific card
                        card_account_name = card["account_name"]

                        # Create delete handler with account name baked into closure
                        def make_delete_handler(acc_name):
                            def handler(confirmed):
                                return self.delete_provider(acc_name, confirmed)
                            return handler

                        # Skip empty cards (no account)
                        if not card_account_name:
                            continue

                        # Use JS to show confirmation, then call Python handler with result
                        card["delete_btn"].click(
                            fn=None,
                            inputs=[],
                            outputs=[delete_confirm_state],
                            js=f"() => window.confirm('Please confirm you want to delete {card_account_name}')"
                        ).then(
                            fn=make_delete_handler(card_account_name),
                            inputs=[delete_confirm_state],
                            outputs=provider_outputs
                        )

            return interface


def launch_web_ui(password: str = None) -> None:
    """Launch the Chad web interface.

    Args:
        password: Main password. If not provided, will prompt via CLI
    """
    security_mgr = SecurityManager()

    # Get or verify password
    if security_mgr.is_first_run():
        if password:
            # Setup with provided password
            import bcrypt
            import base64
            password_hash = security_mgr.hash_password(password)
            encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()
            config = {
                'password_hash': password_hash,
                'encryption_salt': encryption_salt,
                'accounts': {}
            }
            security_mgr.save_config(config)
            main_password = password
        else:
            main_password = security_mgr.setup_main_password()
    else:
        # Always use verify_main_password which includes the reset flow
        main_password = security_mgr.verify_main_password()

    # Create and launch UI
    ui = ChadWebUI(security_mgr, main_password)
    app = ui.create_interface()

    print("\n" + "=" * 70)
    print("CHAD WEB UI")
    print("=" * 70)
    print("Opening web interface in your browser...")
    print("Press Ctrl+C to stop the server")
    print("=" * 70 + "\n")

    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
        quiet=False
    )

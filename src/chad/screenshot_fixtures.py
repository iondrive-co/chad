"""Synthetic data fixtures for MCP screenshot tool.

This module provides realistic mock data for UI screenshots, including:
- Multiple provider accounts (OpenAI, Anthropic, Gemini, Mistral)
- Various plan types (Free, Plus, Pro, Team, Enterprise, Max)
- Realistic usage patterns (fresh, moderate, near-limit, exhausted)
- Live view content with ANSI-colored output

Used by ui_playwright_runner.create_temp_env() when CHAD_SCREENSHOT_MODE=1.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


# =============================================================================
# Provider Account Fixtures
# =============================================================================

MOCK_ACCOUNTS = {
    # OpenAI accounts with different plans
    "codex-work": {
        "provider": "openai",
        "model": "o3",
        "reasoning": "high",
        "plan": "TEAM",
        "email": "dev@acme-corp.com",
        "usage": {
            "primary": {"used_percent": 15, "resets_hours": 3.5},
            "secondary": {"used_percent": 42, "resets_days": 4},
        },
    },
    "codex-personal": {
        "provider": "openai",
        "model": "o3-mini",
        "reasoning": "medium",
        "plan": "PLUS",
        "email": "alex@gmail.com",
        "usage": {
            "primary": {"used_percent": 67, "resets_hours": 1.2},
            "secondary": {"used_percent": 78, "resets_days": 2},
        },
    },
    "codex-free": {
        "provider": "openai",
        "model": "gpt-4.1",
        "reasoning": "default",
        "plan": "FREE",
        "email": "student@university.edu",
        "usage": {
            "primary": {"used_percent": 95, "resets_hours": 4.8},
            "secondary": None,
        },
    },
    # Anthropic accounts
    "claude-pro": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "reasoning": "default",
        "plan": "PRO",
        "usage": {
            "five_hour": {"utilization": 23, "resets_hours": 2.1},
            "seven_day": {"utilization": 55, "resets_days": 3},
        },
    },
    "claude-max": {
        "provider": "anthropic",
        "model": "claude-opus-4-20250514",
        "reasoning": "default",
        "plan": "MAX",
        "usage": {
            "five_hour": {"utilization": 8, "resets_hours": 4.5},
            "seven_day": {"utilization": 31, "resets_days": 5},
            "extra_usage": {"used_credits": 12, "monthly_limit": 100},
        },
    },
    "claude-team": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "reasoning": "default",
        "plan": "TEAM",
        "usage": {
            "five_hour": {"utilization": 100, "resets_hours": 0.5},
            "seven_day": {"utilization": 89, "resets_days": 1},
        },
    },
    # Gemini account
    "gemini-advanced": {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "reasoning": "default",
        "plan": "ADVANCED",
        "usage": {
            "models": {
                "gemini-2.5-pro": {"requests": 47, "input": 125000, "output": 89000},
                "gemini-2.5-flash": {"requests": 156, "input": 340000, "output": 220000},
            },
            "cached_tokens": 95000,
        },
    },
    # Mistral account
    "vibe-pro": {
        "provider": "mistral",
        "model": "codestral-25.01",
        "reasoning": "default",
        "plan": "PRO",
        "usage": {
            "sessions": 23,
            "input_tokens": 450000,
            "output_tokens": 180000,
            "cost": 2.34,
        },
    },
}


# =============================================================================
# Usage Text Generators
# =============================================================================

def _progress_bar(utilization_pct: float, width: int = 20) -> str:
    """Create a text progress bar."""
    filled = int(max(0.0, min(100.0, utilization_pct)) / (100 / width))
    return "█" * filled + "░" * (width - filled)


def generate_codex_usage(account_data: dict) -> str:
    """Generate realistic Codex usage text."""
    plan = account_data.get("plan", "UNKNOWN")
    email = account_data.get("email", "user@example.com")
    usage = account_data.get("usage", {})

    exp_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M")

    result = f"✅ **Logged in** ({plan} plan)\n\n"
    result += f"**Account:** {email}\n"
    result += f"**Token expires:** {exp_date}\n\n"
    result += "**Current Usage**\n\n"

    primary = usage.get("primary", {})
    if primary:
        util = primary.get("used_percent", 0)
        hours = primary.get("resets_hours", 5)
        reset_time = (datetime.now() + timedelta(hours=hours)).strftime("%I:%M%p")
        bar = _progress_bar(util)
        result += "**5-hour session**\n"
        result += f"[{bar}] {util}% used\n"
        result += f"Resets at {reset_time}\n\n"

    secondary = usage.get("secondary")
    if secondary:
        util = secondary.get("used_percent", 0)
        days = secondary.get("resets_days", 7)
        reset_date = (datetime.now() + timedelta(days=days)).strftime("%b %d")
        bar = _progress_bar(util)
        result += "**Weekly limit**\n"
        result += f"[{bar}] {util}% used\n"
        result += f"Resets {reset_date}\n\n"

    result += f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*\n"
    return result


def generate_claude_usage(account_data: dict) -> str:
    """Generate realistic Claude usage text."""
    plan = account_data.get("plan", "UNKNOWN")
    usage = account_data.get("usage", {})

    result = f"✅ **Logged in** ({plan} plan)\n\n"
    result += "**Current Usage**\n\n"

    five_hour = usage.get("five_hour", {})
    if five_hour:
        util = five_hour.get("utilization", 0)
        hours = five_hour.get("resets_hours", 5)
        reset_time = (datetime.now() + timedelta(hours=hours)).strftime("%I:%M%p")
        bar = _progress_bar(util)
        result += "**5-hour session**\n"
        result += f"[{bar}] {util}% used\n"
        result += f"Resets at {reset_time}\n\n"

    seven_day = usage.get("seven_day")
    if seven_day:
        util = seven_day.get("utilization", 0)
        days = seven_day.get("resets_days", 7)
        reset_date = (datetime.now() + timedelta(days=days)).strftime("%b %d")
        bar = _progress_bar(util)
        result += "**Weekly limit**\n"
        result += f"[{bar}] {util}% used\n"
        result += f"Resets {reset_date}\n\n"

    extra = usage.get("extra_usage")
    if extra:
        used = extra.get("used_credits", 0)
        limit = extra.get("monthly_limit", 100)
        util = (used / limit) * 100 if limit > 0 else 0
        bar = _progress_bar(util)
        result += "**Extra credits**\n"
        result += f"[{bar}] ${used} / ${limit} ({util:.1f}%)\n\n"

    return result


def generate_gemini_usage(account_data: dict) -> str:
    """Generate realistic Gemini usage text."""
    usage = account_data.get("usage", {})
    models = usage.get("models", {})

    result = "✅ **Logged in**\n\n"
    result += "**Model Usage**\n\n"
    result += "| Model | Reqs | Input | Output |\n"
    result += "|-------|------|-------|--------|\n"

    total_input = 0
    total_cached = usage.get("cached_tokens", 0)

    for model, data in models.items():
        reqs = data.get("requests", 0)
        inp = data.get("input", 0)
        out = data.get("output", 0)
        total_input += inp
        result += f"| {model} | {reqs:,} | {inp:,} | {out:,} |\n"

    if total_cached > 0 and total_input > 0:
        cache_pct = (total_cached / total_input) * 100
        result += f"\n**Cache savings:** {total_cached:,} ({cache_pct:.1f}%) tokens served from cache\n"

    return result


def generate_mistral_usage(account_data: dict) -> str:
    """Generate realistic Mistral usage text."""
    usage = account_data.get("usage", {})

    sessions = usage.get("sessions", 0)
    input_tok = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    total = input_tok + output_tok
    cost = usage.get("cost", 0.0)

    result = "✅ **Logged in**\n\n"
    result += "**Cumulative Usage**\n\n"
    result += f"**Sessions:** {sessions:,}\n"
    result += f"**Input tokens:** {input_tok:,}\n"
    result += f"**Output tokens:** {output_tok:,}\n"
    result += f"**Total tokens:** {total:,}\n"
    result += f"**Estimated cost:** ${cost:.4f}\n"

    return result


def get_mock_usage(account_name: str) -> str:
    """Get mock usage text for an account."""
    account = MOCK_ACCOUNTS.get(account_name)
    if not account:
        return "⚠️ Unknown provider"

    provider = account.get("provider")
    if provider == "openai":
        return generate_codex_usage(account)
    elif provider == "anthropic":
        return generate_claude_usage(account)
    elif provider == "gemini":
        return generate_gemini_usage(account)
    elif provider == "mistral":
        return generate_mistral_usage(account)
    return "⚠️ Unknown provider type"


# =============================================================================
# Live View Content
# =============================================================================

LIVE_VIEW_CONTENT = """<div class="live-output-content">
<span style="color: #56b6c2; font-weight: bold;">⏺ Agent working on task...</span>
<span style="color: #e5c07b;">• Reading src/chad/web_ui.py</span>
<span style="color: #5c6370;">  Lines 1-500 of 2500</span>
<span style="color: #98c379;">• Edit: src/chad/provider_ui.py</span>
<span style="color: #98c379;">+</span>     def get_remaining_capacity(self) -&gt; float:
<span style="color: #98c379;">+</span>         \"\"\"Calculate remaining API capacity.\"\"\"
<span style="color: #98c379;">+</span>         return max(0, 1.0 - self.current_usage)
<span style="color: #c678dd;">• Running: pytest tests/test_provider_ui.py -v</span>
<span style="color: #5c6370;">  tests/test_provider_ui.py::test_usage_display PASSED
  tests/test_provider_ui.py::test_capacity_calculation PASSED
  tests/test_provider_ui.py::test_progress_bar PASSED</span>
<span style="color: #98c379;">✓ All 3 tests passed</span>
<span style="color: #61afef; font-weight: bold;">━━━ Task Progress ━━━</span>
<span style="color: #98c379;">✓</span> Implement capacity tracking
<span style="color: #98c379;">✓</span> Add unit tests
<span style="color: #e5c07b;">◐</span> Update documentation
</div>"""


# =============================================================================
# Chat History
# =============================================================================

CHAT_HISTORY = [
    {
        "role": "user",
        "content": "Add a feature to show remaining API capacity for each provider"
    },
    {
        "role": "assistant",
        "content": """I'll implement a capacity tracking feature for the provider panel.

**Changes made:**

1. Added `get_remaining_capacity()` to `ProviderUIManager` - calculates remaining quota as 0.0-1.0
2. Updated provider cards to show capacity percentage with color coding
3. Added progress bar visualization for quick scanning

**Files modified:**
- `src/chad/provider_ui.py` - Core capacity calculation logic
- `src/chad/web_ui.py` - UI display updates
- `tests/test_provider_ui.py` - New unit tests

**Verification:**
```
✓ All 12 tests passed
✓ Lint clean
✓ Visual tests passed
```

The capacity indicator now shows next to each provider with color coding:
- 🟢 Green: >50% remaining
- 🟡 Yellow: 20-50% remaining
- 🔴 Red: <20% remaining

Ready for follow-up requests."""
    },
]


# =============================================================================
# Setup Functions
# =============================================================================

def setup_mock_accounts(security_mgr, password: str) -> None:
    """Register all mock accounts with the security manager."""
    for account_name, account_data in MOCK_ACCOUNTS.items():
        security_mgr.store_account(
            account_name,
            account_data["provider"],
            "",  # No real API key needed for mock
            password,
            account_data.get("model", "default")
        )
        reasoning = account_data.get("reasoning", "default")
        if reasoning != "default":
            security_mgr.set_account_reasoning(account_name, reasoning)

    # Assign first Claude account as default coding agent
    security_mgr.assign_role("claude-pro", "CODING")


def create_mock_codex_auth(home_dir: Path, account_data: dict) -> None:
    """Create mock Codex auth.json with JWT-like token."""
    import base64

    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    # Create a mock JWT payload (not a real token, just structured data)
    exp_timestamp = int((datetime.now() + timedelta(days=7)).timestamp())
    jwt_payload = {
        "https://api.openai.com/auth": {
            "chatgpt_plan_type": account_data.get("plan", "plus").lower()
        },
        "https://api.openai.com/profile": {
            "email": account_data.get("email", "user@example.com")
        },
        "exp": exp_timestamp
    }

    # Create fake JWT (header.payload.signature)
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(jwt_payload).encode()).decode().rstrip("=")
    fake_token = f"{header}.{payload}.fake_signature"

    auth_data = {
        "tokens": {
            "access_token": fake_token,
            "refresh_token": "mock_refresh_token"
        }
    }

    with open(codex_dir / "auth.json", "w") as f:
        json.dump(auth_data, f)


def create_mock_claude_creds(config_dir: Path, account_data: dict) -> None:
    """Create mock Claude credentials file."""
    config_dir.mkdir(parents=True, exist_ok=True)

    plan = account_data.get("plan", "PRO")
    creds = {
        "claudeAiOauth": {
            "accessToken": "mock_access_token",
            "refreshToken": "mock_refresh_token",
            "subscriptionType": plan.lower(),
            "expiresAt": int((datetime.now() + timedelta(hours=8)).timestamp() * 1000)
        }
    }

    with open(config_dir / ".credentials.json", "w") as f:
        json.dump(creds, f)


def create_mock_gemini_creds(gemini_dir: Path) -> None:
    """Create mock Gemini OAuth credentials."""
    gemini_dir.mkdir(parents=True, exist_ok=True)

    creds = {
        "installed": {
            "client_id": "mock_client_id",
            "client_secret": "mock_secret"
        }
    }

    with open(gemini_dir / "oauth_creds.json", "w") as f:
        json.dump(creds, f)


def create_mock_mistral_config(vibe_dir: Path) -> None:
    """Create mock Mistral/Vibe config."""
    vibe_dir.mkdir(parents=True, exist_ok=True)

    config_content = """
[api]
key = "mock_api_key"

[defaults]
model = "codestral-25.01"
"""
    with open(vibe_dir / "config.toml", "w") as f:
        f.write(config_content)

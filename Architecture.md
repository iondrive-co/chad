# Chad Architecture

Chad is an AI management relay system that orchestrates multiple AI providers for coding tasks. A management AI makes decisions while Chad relays messages between providers.

## Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web UI    │────▶│    Chad     │────▶│  Providers  │
│  (Gradio)   │     │   (Relay)   │     │ (AI Models) │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Task Execution State Machine

Chad uses a three-phase state machine for task execution:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  INVESTIGATION  │────▶│ IMPLEMENTATION  │────▶│  VERIFICATION   │
│                 │     │                 │     │                 │
│  Management AI  │     │  Management AI  │     │  Management AI  │
│  asks questions │     │  supervises     │     │  verifies work  │
│  Coding AI      │     │  Coding AI      │     │  reads files    │
│  explores       │     │  implements     │     │  delegates      │
│                 │     │                 │     │                 │
│  Output: PLAN:  │     │  Output: VERIFY │     │  Output:        │
│                 │     │                 │     │  - COMPLETE     │
│                 │◄────────────────────────────│  - PLAN_ISSUE   │
│                 │     │                 │◄────│  - IMPL_ISSUE   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Phase 1: Investigation
- Management AI cannot access filesystem directly
- Asks Coding AI to explore codebase, find files, understand structure
- Loops until Management outputs `PLAN:` with implementation steps
- Max 10 iterations

### Phase 2: Implementation
- Coding AI executes the plan
- Management AI supervises, answers questions, grants permissions
- Management outputs `CONTINUE:` for guidance or `VERIFY` when done
- Max 30 iterations

### Phase 3: Verification
- Management AI actively verifies using tools
- Can read files directly, search web, delegate tests to Coding AI
- Outputs:
  - `COMPLETE` - task verified done
  - `PLAN_ISSUE:` - return to Investigation with better context
  - `IMPL_ISSUE:` - return to Implementation with fix instructions
- Max 5 iterations, max 2 plan revisits

## Providers

To add a new AI provider:

1. Create a new class in `providers.py` that inherits from `AIProvider`
2. Implement all abstract methods
3. Add it to the `create_provider()` factory function

## Setup

### Install for development

```bash
cd chad
python3 -m venv venv
source venv/bin/activate  # On Linux/Mac
pip install -e ".[dev]"
```

### Run tests

```bash
PYTHONPATH=src python3 -m pytest -v
PYTHONPATH=src python3 -m pytest tests/test_providers.py -v
```


## Publishing to PyPI

### Build the package

```bash
pip install build twine
python3 -m build
```

This creates:
- `dist/chad-0.1.0-py3-none-any.whl`
- `dist/chad-0.1.0.tar.gz`

### Upload to PyPI

```bash
# Test PyPI first
python3 -m twine upload --repository testpypi dist/*

# Real PyPI
python3 -m twine upload dist/*
```

### Install from PyPI

Once published:
```bash
pip install chad-ai
chad --help
```

## Providers

### Anthropic (Claude Code)

**Status:** Fully Implemented with Usage API

**Authentication:**
- OAuth token stored in `~/.claude/.credentials.json`
- Format: `{"claudeAiOauth": {"accessToken": "sk-ant-oat01-...", "subscriptionType": "pro", ...}}`
- Users authenticate by running `claude` in terminal (browser-based OAuth)

**Usage API:**
- Endpoint: `https://api.anthropic.com/api/oauth/usage`
- Headers:
  - `Authorization: Bearer {accessToken}`
  - `anthropic-beta: oauth-2025-04-20`
  - `User-Agent: claude-code/2.0.32`
- Response:
  ```json
  {
    "five_hour": {"utilization": 57.0, "resets_at": "2025-12-08T17:59:59+00:00"},
    "seven_day": {"utilization": 35.0, "resets_at": "2025-12-11T00:00:00+00:00"},
    "extra_usage": {"is_enabled": true, "monthly_limit": 4000, "used_credits": 514.0}
  }
  ```

**CLI Integration:**
- Command: `claude -p --input-format stream-json --output-format stream-json --permission-mode bypassPermissions`
- Uses streaming JSON for multi-turn conversations

---

### OpenAI (Codex)

**Status:** Fully Implemented with Usage via Session Files + Multi-Account Support

**Authentication:**
- OAuth JWT token stored in isolated home directories per account
- Each account gets its own directory: `~/.chad/codex-homes/<account-name>/.codex/auth.json`
- Users authenticate via the web UI "Login to Codex Account" button
- Format: `{"tokens": {"access_token": "eyJ...", ...}, "last_refresh": "..."}`

**Multi-Account Support:**
Chad supports multiple OpenAI/Codex accounts by using isolated HOME directories:
- Each account name maps to `~/.chad/codex-homes/<account-name>/`
- Codex CLI respects the `HOME` environment variable
- Running Codex with a custom HOME creates isolated auth and session data
- This allows work and personal accounts to run simultaneously

**Usage Information:**
The JWT token contains account metadata:
- `chatgpt_plan_type`: "plus", "pro", "team", etc.
- `email`: User's email address
- `exp`: Token expiration timestamp

**Usage API:**
The Codex CLI stores usage data in session files at `<isolated-home>/.codex/sessions/YYYY/MM/DD/*.jsonl`.
Each session file contains JSONL entries with `rate_limits` data:
```json
{
  "type": "event_msg",
  "payload": {
    "type": "token_count",
    "rate_limits": {
      "primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 1765012711},
      "secondary": {"used_percent": 46.0, "window_minutes": 10080, "resets_at": 1765439179},
      "credits": {"has_credits": false, "unlimited": false, "balance": null}
    }
  }
}
```

Field mapping:
- `primary`: 5-hour rolling window (window_minutes: 300)
- `secondary`: Weekly rolling window (window_minutes: 10080)
- `resets_at`: Unix timestamp for when limit resets

**CLI Integration:**
- Command: `codex exec --full-auto --skip-git-repo-check -C {path} {message}`
- One-shot execution mode (no persistent session)
- Provider passes `env={'HOME': isolated_home}` to subprocess

---

### Google (Gemini)

**Status:** Fully Implemented with Usage via Session Files

**Authentication:**
- OAuth via browser when running `gemini` CLI
- Credentials stored in `~/.gemini/oauth_creds.json`

**Usage Information:**
The Gemini CLI stores session data in `~/.gemini/tmp/<project-hash>/chats/session-*.json`.
Each session file contains messages with token usage data:
```json
{
  "type": "gemini",
  "model": "gemini-2.5-pro",
  "tokens": {
    "input": 18732,
    "output": 46,
    "cached": 1818,
    "thoughts": 216,
    "tool": 0,
    "total": 18994
  }
}
```

Chad aggregates this data across all sessions to display:
- Token usage per model (requests, input tokens, output tokens)
- Cache savings (tokens served from cache)

**CLI Integration:**
- Command: `gemini -y` (YOLO mode for auto-approval)
- Requires `@google/gemini-cli` npm package

---

## File Structure

```
src/chad/
├── __main__.py      # Entry point, password handling
├── providers.py     # AI provider implementations
├── security.py      # Password hashing, API key encryption
├── session_manager.py # Multi-provider session orchestration
└── web_ui.py        # Gradio web interface
```

## Configuration

Config stored in `~/.chad.conf`:
```json
{
  "password_hash": "bcrypt hash",
  "encryption_salt": "base64 salt",
  "accounts": {
    "account-name": {"provider": "anthropic", "key": "encrypted", "model": "default"}
  },
  "role_assignments": {
    "CODING": "account-name",
    "MANAGEMENT": "account-name"
  }
}
```

### Model Selection

Each account can set its role and preferred model directly from its provider card in the Providers tab.
Available models per provider:
- **Anthropic:** claude-sonnet-4-20250514, claude-opus-4-20250514, default
- **OpenAI:** o3, o4-mini, codex-mini, default
- **Gemini:** gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite, default

Custom models can also be entered manually (allow_custom_value is enabled).

## Adding a New Provider

1. Create provider class in `providers.py` extending `AIProvider`
2. Implement: `start_session`, `send_message`, `get_response`, `stop_session`, `is_alive`
3. Add to `create_provider()` factory function
4. Add provider type to web UI dropdown in `web_ui.py`
5. Implement `_get_{provider}_usage()` method if usage API available
6. Document in this Architecture.md

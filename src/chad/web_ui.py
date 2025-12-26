"""Gradio web interface for Chad."""

import os
import re
import threading
import queue
from pathlib import Path
from typing import Iterator

import gradio as gr

from .provider_ui import ProviderUIManager
from .security import SecurityManager
from .session_logger import SessionLogger
from .session_manager import (
    CODING_IMPLEMENTATION_CONTEXT,
    CODING_INVESTIGATION_CONTEXT,
    IMPLEMENTATION_PROMPT,
    INVESTIGATION_PROMPT,
    SAFETY_CONSTRAINTS,
    SessionManager,
    TaskPhase,
    VERIFICATION_PROMPT,
    get_coding_timeout,
    get_management_timeout,
)
from .providers import ModelConfig, parse_codex_output, extract_final_codex_response
from .model_catalog import ModelCatalog


# Custom styling for the provider management area to improve contrast between
# the summary header and each provider card.
PROVIDER_PANEL_CSS = """
:root {
  --task-btn-bg: #8fd3ff;
  --task-btn-border: #74c3f6;
  --task-btn-text: #0a2236;
  --task-btn-hover: #7bc9ff;
}

#start-task-btn,
#cancel-task-btn {
  background: var(--task-btn-bg) !important;
  border: 1px solid var(--task-btn-border) !important;
  color: var(--task-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 32px !important;
  padding: 6px 12px !important;
}

#start-task-btn:hover,
#cancel-task-btn:hover {
  background: var(--task-btn-hover) !important;
}

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
  background: var(--task-btn-bg) !important;
  border: 1px solid var(--task-btn-border) !important;
  border-radius: 12px;
  padding: 0 10px;
  gap: 8px;
}

.provider-card .provider-card__header-row .provider-card__header,
.provider-card .provider-card__header {
  background: var(--task-btn-bg) !important;
  color: var(--task-btn-text) !important;
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  flex: 1;
  border-radius: 10px;
}

.provider-card .provider-card__header-row .provider-card__header-text,
.provider-card__header-row .provider-card__header-text {
  background: var(--task-btn-bg);
  color: var(--task-btn-text);
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
  color: var(--task-btn-text) !important;
  background: var(--task-btn-bg) !important;
  margin: 0;
  padding: 0;
}

.provider-card .provider-card__header-row .provider-card__header > *,
.provider-card .provider-card__header > * {
  background: var(--task-btn-bg) !important;
  color: var(--task-btn-text) !important;
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

/* Hide empty provider cards via CSS class */
.provider-card-hidden {
  display: none !important;
}

.provider-usage {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px 12px;
  color: #1e293b;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06);
}

/* Ensure all text in provider usage is readable */
.provider-usage * {
  color: #1e293b !important;
}

/* Warning text should be visible */
.provider-usage .warning-text,
.provider-usage:has(⚠️) {
  color: #b45309 !important;
}

.provider-card__header-row .provider-delete,
.provider-delete {
  margin-left: auto;
  margin-top: -1px;
  margin-bottom: -1px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  align-self: stretch !important;
  height: auto !important;
  min-height: 0 !important;
  width: 36px;
  min-width: 36px;
  max-width: 36px;
  flex-shrink: 0;
  padding: 4px;
  border-radius: 8px;
  background: var(--task-btn-bg) !important;
  border: 1px solid #f97373 !important;
  color: #000000 !important;
  font-size: 17px;
  line-height: 1;
  box-shadow: none;
}

/* Hide empty provider cards (Gradio's gr.update(visible=False) doesn't work on Columns) */
.gr-group:has(.provider-card__header-row):not(:has(.provider-card__header-text)) {
  display: none !important;
}
.column:has(.gr-group:has(.provider-card__header-row):not(:has(.provider-card__header-text))) {
  display: none !important;
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
  background: #1e1e2e !important;
  color: #e2e8f0 !important;
  border: 1px solid #555 !important;
  border-top: none !important;
  border-radius: 0 0 8px 8px !important;
  padding: 12px !important;
  margin: 0 !important;
  max-height: 400px;
  overflow-y: auto;
  overflow-anchor: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
}

/* Syntax highlighting colors for live stream */
#live-stream-box .live-output-content .diff-add {
  color: #98c379 !important;
  background: rgba(152, 195, 121, 0.1) !important;
}
#live-stream-box .live-output-content .diff-remove {
  color: #e06c75 !important;
  background: rgba(224, 108, 117, 0.1) !important;
}
#live-stream-box .live-output-content .diff-header {
  color: #61afef !important;
  font-weight: bold;
}
#live-stream-box .live-output-content .tool-call {
  color: #c678dd !important;
  font-weight: bold;
}
#live-stream-box .live-output-content .file-path {
  color: #e5c07b !important;
}
#live-stream-box .live-output-content .code-block {
  background: rgba(0, 0, 0, 0.2) !important;
  padding: 2px 4px;
  border-radius: 3px;
}

/* Base styling for live stream - light text on dark background */
#live-stream-box .live-output-content {
  color: #e2e8f0;
  background: #1e1e2e !important;
}

/* Code elements (rendered from backticks in Markdown) - bright pink */
#live-stream-box code,
#live-stream-box .live-output-content code,
#live-stream-box pre,
#live-stream-box .live-output-content pre {
  color: #f0abfc !important;
  background: rgba(0, 0, 0, 0.3) !important;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: inherit;
}

/* ANSI colored spans - let them keep their inline colors with brightness boost */
#live-stream-box .live-output-content span[style*="color"] {
  filter: brightness(1.3);
}

/* Override specific dark grey colors that are hard to read */
/* Handle various spacing formats: rgb(92, rgb(92,99 etc */
#live-stream-box .live-output-content span[style*="rgb(92"],
#live-stream-box .live-output-content span[style*="color:#5c6370"],
#live-stream-box .live-output-content span[style*="color: #5c6370"],
#live-stream-box .live-output-content span[style*="#5c6370"] {
  color: #9ca3af !important;
  filter: none !important;
}

/* Boost any dark colors (RGB values starting with low numbers) */
#live-stream-box .live-output-content span[style*="color: rgb(1"],
#live-stream-box .live-output-content span[style*="color: rgb(2"],
#live-stream-box .live-output-content span[style*="color: rgb(3"],
#live-stream-box .live-output-content span[style*="color: rgb(4"],
#live-stream-box .live-output-content span[style*="color: rgb(5"],
#live-stream-box .live-output-content span[style*="color: rgb(6"],
#live-stream-box .live-output-content span[style*="color: rgb(7"],
#live-stream-box .live-output-content span[style*="color: rgb(8"],
#live-stream-box .live-output-content span[style*="color: rgb(9"] {
  filter: brightness(1.5) !important;
}

/* Scroll position indicator */
#live-stream-box .scroll-indicator {
  position: absolute;
  bottom: 8px;
  right: 20px;
  background: rgba(97, 175, 239, 0.9);
  color: #1e1e2e;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  z-index: 10;
  display: none;
}
#live-stream-box .scroll-indicator:hover {
  background: rgba(97, 175, 239, 1);
}
#live-stream-box {
  position: relative;
}

/* Role status row: keep status and session log button on one line, aligned with button row below */
#role-status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 100%;
  overflow: hidden;
}

#role-config-status {
  flex: 1 1 0;  /* Grow, shrink, start from 0 width */
  margin: 0;
  min-width: 0;  /* Allow text to shrink so session log can have space */
  overflow: hidden;
  text-overflow: ellipsis;
}

#session-log-btn {
  flex: 0 0 auto;  /* Don't grow, don't shrink, auto width based on content */
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  padding: 0 8px !important;
  min-height: unset !important;
  height: auto !important;
  white-space: nowrap;
}

/* Agent communication chatbot - preserve scroll position */
.chatbot-container, [data-testid="chatbot"] {
  scroll-behavior: auto !important;
}

/* Agent communication chatbot - full-width speech bubbles */
#agent-chatbot .message-row,
#agent-chatbot .message {
  width: 100% !important;
  max-width: 100% !important;
  align-self: stretch !important;
}

#agent-chatbot .bubble-wrap,
#agent-chatbot .bubble,
#agent-chatbot .message-content,
#agent-chatbot .message .prose {
  width: 100% !important;
  max-width: 100% !important;
}
"""

# JavaScript to fix Gradio visibility updates and maintain scroll position
# Note: This is passed to gr.Blocks(js=...) to execute on page load
CUSTOM_JS = """
function() {
    // Fix for Gradio not properly updating column visibility after initial render
    function fixProviderCardVisibility() {
        const columns = document.querySelectorAll('.column');
        columns.forEach(col => {
            const header = col.querySelector('.provider-card__header');
            if (header) {
                const headerText = header.textContent.trim();
                if (headerText && headerText.length > 5) {
                    col.style.display = '';
                    col.style.visibility = '';
                }
            }
        });
    }
    setInterval(fixProviderCardVisibility, 500);
    const visObserver = new MutationObserver(fixProviderCardVisibility);
    visObserver.observe(document.body, { childList: true, subtree: true, attributes: true });

    // Live stream scroll preservation
    window._liveStreamScroll = window._liveStreamScroll || {
        userScrolledUp: false,
        savedScrollTop: 0,
        lastUserScrollTime: 0,
        ignoreNextScroll: false
    };
    const state = window._liveStreamScroll;

    function getScrollContainer() {
        const liveBox = document.getElementById('live-stream-box');
        if (!liveBox) return null;
        return liveBox.querySelector('.live-output-content') ||
               liveBox.querySelector('[data-testid="markdown"]') ||
               liveBox;
    }

    function handleUserScroll(e) {
        const container = e.target;
        if (!container || state.ignoreNextScroll) {
            state.ignoreNextScroll = false;
            return;
        }
        const now = Date.now();
        if (now - state.lastUserScrollTime < 50) return;
        state.lastUserScrollTime = now;

        const scrollBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
        const isAtBottom = scrollBottom < 50;

        // User is NOT at bottom = they scrolled away from auto-scroll position
        state.userScrolledUp = !isAtBottom;
        state.savedScrollTop = container.scrollTop;
    }

    function restoreScrollPosition(container) {
        if (!container) return;
        state.ignoreNextScroll = true;
        requestAnimationFrame(() => {
            if (!state.userScrolledUp) {
                container.scrollTop = container.scrollHeight;
            } else if (state.savedScrollTop > 0) {
                container.scrollTop = state.savedScrollTop;
            }
            setTimeout(() => { state.ignoreNextScroll = false; }, 100);
        });
    }

    function attachScrollListener(container) {
        if (!container || container._liveScrollAttached) return;
        container._liveScrollAttached = true;
        container.addEventListener('scroll', handleUserScroll, { passive: true });
    }

    function initScrollTracking() {
        const liveBox = document.getElementById('live-stream-box');
        if (!liveBox) {
            setTimeout(initScrollTracking, 200);
            return;
        }

        let lastContainer = null;
        const observer = new MutationObserver((mutations) => {
            const container = getScrollContainer();
            if (!container) return;
            if (container !== lastContainer) {
                attachScrollListener(container);
                lastContainer = container;
            }
            restoreScrollPosition(container);
        });

        observer.observe(liveBox, {
            childList: true,
            subtree: true,
            characterData: true
        });

        const container = getScrollContainer();
        if (container) {
            attachScrollListener(container);
            lastContainer = container;
        }
    }

    setTimeout(initScrollTracking, 100);
}
"""


def ansi_to_html(text: str) -> str:
    """Convert ANSI escape codes to HTML spans with colors.

    Preserves the terminal's native coloring instead of stripping it.
    """
    # ANSI color code to CSS color mapping
    # Note: dim colors (90-97) are brightened to be readable on dark backgrounds
    colors = {
        '30': '#6b7280', '31': '#e06c75', '32': '#98c379', '33': '#e5c07b',
        '34': '#61afef', '35': '#c678dd', '36': '#56b6c2', '37': '#d4d4d4',
        '90': '#9ca3af', '91': '#f87171', '92': '#a3e635', '93': '#fbbf24',
        '94': '#60a5fa', '95': '#e879f9', '96': '#22d3ee', '97': '#ffffff',
    }
    bg_colors = {
        '40': '#000000', '41': '#e06c75', '42': '#98c379', '43': '#e5c07b',
        '44': '#61afef', '45': '#c678dd', '46': '#56b6c2', '47': '#abb2bf',
    }

    result = []
    i = 0
    current_styles = []

    while i < len(text):
        # Check for ANSI escape sequence
        if text[i] == '\x1b' and i + 1 < len(text) and text[i + 1] == '[':
            # Find the end of the escape sequence
            j = i + 2
            while j < len(text) and text[j] not in 'mHJK':
                j += 1
            if j < len(text) and text[j] == 'm':
                # Parse the codes
                codes = text[i + 2:j].split(';')
                for code in codes:
                    if code == '0' or code == '':
                        # Reset
                        if current_styles:
                            result.append('</span>' * len(current_styles))
                            current_styles = []
                    elif code == '1':
                        result.append('<span style="font-weight:bold">')
                        current_styles.append('bold')
                    elif code == '3':
                        result.append('<span style="font-style:italic">')
                        current_styles.append('italic')
                    elif code == '4':
                        result.append('<span style="text-decoration:underline">')
                        current_styles.append('underline')
                    elif code in colors:
                        result.append(f'<span style="color:{colors[code]}">')
                        current_styles.append('color')
                    elif code in bg_colors:
                        result.append(f'<span style="background-color:{bg_colors[code]}">')
                        current_styles.append('bg')
                i = j + 1
                continue
            elif j < len(text):
                # Other escape sequence (cursor movement, etc.) - skip it
                i = j + 1
                continue

        # Regular character - escape HTML entities
        char = text[i]
        if char == '<':
            result.append('&lt;')
        elif char == '>':
            result.append('&gt;')
        elif char == '&':
            result.append('&amp;')
        else:
            result.append(char)

    return ''.join(result)


def summarize_content(content: str, max_length: int = 200) -> str:
    """Create a meaningful summary of content for collapsed view.

    Tries to extract the most informative sentence describing what was done.
    """
    import re

    # Remove markdown formatting for cleaner summary
    clean = content.replace('**', '').replace('`', '').replace('# ', '')

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', clean)

    # Action verbs that indicate a meaningful summary sentence
    action_patterns = [
        r"^I(?:'ve|'m| have| am| will| would|'ll)",
        r"^(?:Updated|Changed|Fixed|Added|Removed|Modified|Created|Implemented|Refactored)",
        r"^(?:The|This) (?:change|update|fix|modification)",
        r"^(?:Successfully|Done|Completed)",
    ]

    # Look for sentences with action verbs
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or len(sentence) < 20:
            continue
        for pattern in action_patterns:
            if re.match(pattern, sentence, re.IGNORECASE):
                # Found a good summary sentence
                if len(sentence) <= max_length:
                    return sentence
                return sentence[:max_length].rsplit(' ', 1)[0] + '...'

    # Look for sentences mentioning file paths
    for sentence in sentences:
        sentence = sentence.strip()
        if re.search(r'[a-zA-Z_]+\.(py|js|ts|tsx|css|html|md|json|yaml|yml)', sentence):
            if len(sentence) <= max_length:
                return sentence
            return sentence[:max_length].rsplit(' ', 1)[0] + '...'

    # Fallback: get first meaningful paragraph
    first_para = clean.split('\n\n')[0].strip()
    # Skip if it's just a header or very short
    if len(first_para) < 20:
        for para in clean.split('\n\n')[1:]:
            if len(para.strip()) >= 20:
                first_para = para.strip()
                break

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
        self._active_coding_provider = None
        self.provider_card_count = 10
        self.model_catalog = ModelCatalog(security_mgr)
        self.provider_ui = ProviderUIManager(security_mgr, main_password, self.model_catalog)
        self.session_logger = SessionLogger()
        self.current_session_log_path: Path | None = None

    SUPPORTED_PROVIDERS = ProviderUIManager.SUPPORTED_PROVIDERS
    OPENAI_REASONING_LEVELS = ProviderUIManager.OPENAI_REASONING_LEVELS

    def list_providers(self) -> str:
        return self.provider_ui.list_providers()

    def _get_account_role(self, account_name: str) -> str | None:
        return self.provider_ui._get_account_role(account_name)

    def get_provider_usage(self, account_name: str) -> str:
        return self.provider_ui.get_provider_usage(account_name)

    def _progress_bar(self, utilization_pct: float, width: int = 20) -> str:
        return self.provider_ui._progress_bar(utilization_pct, width)

    def get_remaining_usage(self, account_name: str) -> float:
        return self.provider_ui.get_remaining_usage(account_name)

    def _get_claude_remaining_usage(self) -> float:
        return self.provider_ui._get_claude_remaining_usage()

    def _get_codex_remaining_usage(self, account_name: str) -> float:
        return self.provider_ui._get_codex_remaining_usage(account_name)

    def _get_gemini_remaining_usage(self) -> float:
        return self.provider_ui._get_gemini_remaining_usage()

    def _get_mistral_remaining_usage(self) -> float:
        return self.provider_ui._get_mistral_remaining_usage()

    def _provider_state(self, pending_delete: str = None) -> tuple:
        return self.provider_ui.provider_state(self.provider_card_count, pending_delete=pending_delete)

    def _provider_action_response(self, feedback: str, pending_delete: str = None):
        return self.provider_ui.provider_action_response(
            feedback, self.provider_card_count, pending_delete=pending_delete
        )

    def _provider_state_with_confirm(self, pending_delete: str) -> tuple:
        return self.provider_ui.provider_state_with_confirm(pending_delete, self.provider_card_count)

    def _get_codex_home(self, account_name: str) -> Path:
        return self.provider_ui._get_codex_home(account_name)

    def _get_codex_usage(self, account_name: str) -> str:
        return self.provider_ui._get_codex_usage(account_name)

    def _get_codex_session_usage(self, account_name: str) -> str | None:  # noqa: C901
        return self.provider_ui._get_codex_session_usage(account_name)

    def _get_claude_usage(self) -> str:  # noqa: C901
        return self.provider_ui._get_claude_usage()

    def _get_gemini_usage(self) -> str:  # noqa: C901
        return self.provider_ui._get_gemini_usage()

    def _get_mistral_usage(self) -> str:
        return self.provider_ui._get_mistral_usage()

    def get_account_choices(self) -> list[str]:
        return self.provider_ui.get_account_choices()

    def _check_provider_login(self, provider_type: str, account_name: str) -> tuple[bool, str]:  # noqa: C901
        return self.provider_ui._check_provider_login(provider_type, account_name)

    def _setup_codex_account(self, account_name: str) -> str:
        return self.provider_ui._setup_codex_account(account_name)

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
        return self.provider_ui.add_provider(provider_name, provider_type, self.provider_card_count)

    def _unassign_account_roles(self, account_name: str) -> None:
        self.provider_ui._unassign_account_roles(account_name)

    def get_role_config_status(self) -> tuple[bool, str]:
        return self.provider_ui.get_role_config_status()

    def format_role_status(self) -> str:
        return self.provider_ui.format_role_status()

    def assign_role(self, account_name: str, role: str):
        return self.provider_ui.assign_role(account_name, role, self.provider_card_count)

    def set_model(self, account_name: str, model: str):
        return self.provider_ui.set_model(account_name, model, self.provider_card_count)

    def set_reasoning(self, account_name: str, reasoning: str):
        return self.provider_ui.set_reasoning(account_name, reasoning, self.provider_card_count)

    def get_models_for_account(self, account_name: str) -> list[str]:
        self.provider_ui.model_catalog = self.model_catalog
        return self.provider_ui.get_models_for_account(account_name, model_catalog_override=self.model_catalog)

    def get_reasoning_choices(self, provider: str, account_name: str | None = None) -> list[str]:
        return self.provider_ui.get_reasoning_choices(provider, account_name)

    def delete_provider(self, account_name: str, confirmed: bool = False):
        return self.provider_ui.delete_provider(account_name, confirmed, self.provider_card_count)

    def cancel_task(self) -> str:
        """Cancel the running task."""
        self.cancel_requested = True
        if self.session_manager:
            self.session_manager.stop_all()
            self.session_manager = None
        if self._active_coding_provider:
            self._active_coding_provider.stop_session()
            self._active_coding_provider = None
        return "🛑 Task cancelled"

    def start_chad_task(  # noqa: C901
        self,
        project_path: str,
        task_description: str,
        managed_mode: bool = False
    ) -> Iterator[tuple[list, str, gr.Markdown, gr.Textbox, gr.TextArea, gr.Button, gr.Button, gr.Markdown]]:
        """Start Chad task and stream updates.

        Flow: Management AI plans first, then coding AI executes.
        """
        chat_history = []
        message_queue = queue.Queue()
        self.cancel_requested = False
        session_log_path: Path | None = None
        current_phase_display = [""]  # Use list for mutability in nested functions

        def format_role_status_with_phase(phase: str = "") -> str:
            """Format role status with optional phase indicator."""
            base_status = self.format_role_status()
            if phase:
                return f"{base_status} | **Phase:** {phase}"
            return base_status

        def make_yield(
            history,
            status: str,
            live_stream: str = "",
            summary: str | None = None,
            interactive: bool = False
        ):
            """Format output tuple for Gradio with current UI state."""
            display_stream = live_stream
            is_error = '❌' in status
            # Include phase in role status if we have one
            display_role_status = format_role_status_with_phase(current_phase_display[0])
            # Session log download button update
            log_btn_update = gr.update(
                label=f"📄 {session_log_path.name}" if session_log_path else "Session Log",
                value=str(session_log_path) if session_log_path else None,
                visible=session_log_path is not None
            )
            return (
                history,
                display_stream,
                gr.update(value=status if is_error else "", visible=is_error),
                gr.update(value=project_path, interactive=interactive),
                gr.update(value=task_description, interactive=interactive),
                gr.update(interactive=interactive),
                gr.update(interactive=not interactive),
                gr.update(value=display_role_status),
                log_btn_update
            )

        try:
            # Validate inputs
            if not project_path or not task_description:
                error_msg = "❌ Please provide both project path and task description"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            path = Path(project_path).expanduser().resolve()
            if not path.exists() or not path.is_dir():
                error_msg = f"❌ Invalid project path: {project_path}"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            # Get role assignments
            role_assignments = self.security_mgr.list_role_assignments()
            coding_account = role_assignments.get('CODING')
            management_account = role_assignments.get('MANAGEMENT')

            if not coding_account or not management_account:
                msg = "❌ Please assign CODING and MANAGEMENT roles in the Provider Management tab first"
                yield make_yield([], msg, summary=msg, interactive=True)
                return

            # Get provider info
            accounts = self.security_mgr.list_accounts()
            coding_provider = accounts[coding_account]
            management_provider = accounts[management_account]

            # Create configs with stored models
            coding_model = self.security_mgr.get_account_model(coding_account)
            management_model = self.security_mgr.get_account_model(management_account)
            coding_reasoning = self.security_mgr.get_account_reasoning(coding_account)
            management_reasoning = self.security_mgr.get_account_reasoning(management_account)

            coding_config = ModelConfig(
                provider=coding_provider,
                model_name=coding_model,
                account_name=coding_account,
                reasoning_effort=None if coding_reasoning == 'default' else coding_reasoning
            )

            management_config = ModelConfig(
                provider=management_provider,
                model_name=management_model,
                account_name=management_account,
                reasoning_effort=None if management_reasoning == 'default' else management_reasoning
            )

            coding_timeout = get_coding_timeout(coding_provider)
            management_timeout = get_management_timeout(management_provider)

            # Initialize the session log (pre-created when launching UI, or create on demand)
            session_log_path = self.current_session_log_path or self.session_logger.precreate_log()
            self.current_session_log_path = session_log_path
            self.session_logger.initialize_log(
                session_log_path,
                task_description=task_description,
                project_path=str(path),
                coding_account=coding_account,
                coding_provider=coding_provider,
                management_account=management_account,
                management_provider=management_provider,
                managed_mode=managed_mode
            )

            status_prefix = "**Starting Chad...**\n\n"
            status_prefix += f"• Project: {path}\n"
            status_prefix += f"• CODING: {coding_account} ({coding_provider})\n"
            if managed_mode:
                status_prefix += f"• MANAGEMENT: {management_account} ({management_provider})\n"
            status_prefix += f"• Mode: {'Managed (AI supervision)' if managed_mode else 'Direct (coding AI only)'}\n\n"

            # Add task description as the first message in chat history
            chat_history.append({
                "role": "user",
                "content": f"**Task**\n\n{task_description}"
            })
            # Update session log with initial task message
            self.session_logger.update_log(session_log_path, chat_history)

            initial_status = f"{status_prefix}⏳ Initializing sessions..."
            yield make_yield(chat_history, initial_status, summary=initial_status, interactive=False)

            # Activity callback to capture live updates
            # Format in Claude Code style: ● ToolName(params) with ⎿ for results
            def format_tool_activity(detail: str) -> str:
                """Format tool activity in Claude Code style."""
                # Handle Claude format: "ToolName: args"
                if ': ' in detail:
                    tool_name, args = detail.split(': ', 1)
                    return f"● {tool_name}({args})"
                # Handle Codex format: "Running: description"
                if detail.startswith('Running: '):
                    return f"● {detail[9:]}"
                return f"● {detail}"

            def on_activity(activity_type: str, detail: str):
                if activity_type == 'stream':
                    message_queue.put(('stream', detail))
                elif activity_type == 'tool':
                    formatted = format_tool_activity(detail)
                    message_queue.put(('activity', formatted))
                elif activity_type == 'thinking':
                    message_queue.put(('activity', f"⋯ {detail}"))
                elif activity_type == 'text' and detail:
                    message_queue.put(('activity', f"  ⎿ {detail[:80]}"))

            # Create session manager with silent mode enabled
            session_manager = SessionManager(coding_config, management_config, insane_mode=False, silent=True)
            self.session_manager = session_manager

            # ==================== DIRECT MODE (default) ====================
            if not managed_mode:
                # Set phase for direct mode
                current_phase_display[0] = "🚀 EXECUTING"
                yield make_yield([], f"{status_prefix}⏳ Starting coding AI...", summary=status_prefix)

                from .providers import create_provider
                coding_provider_instance = create_provider(coding_config)
                self._active_coding_provider = coding_provider_instance
                coding_provider_instance.set_activity_callback(on_activity)

                if not coding_provider_instance.start_session(str(path)):
                    failure = f"{status_prefix}❌ Failed to start coding session"
                    yield make_yield([], failure, summary=failure, interactive=True)
                    return

                status_msg = f"{status_prefix}✓ Coding AI started\n\n⏳ Processing task..."
                yield make_yield([], status_msg, summary=status_msg, interactive=False)

                coding_provider_instance.send_message(task_description)

                relay_complete = threading.Event()
                task_success = [False]
                completion_reason = [""]

                def direct_loop():
                    try:
                        message_queue.put(('ai_switch', 'CODING AI'))
                        message_queue.put(('message_start', 'CODING AI'))
                        response = coding_provider_instance.get_response(timeout=coding_timeout)
                        if response:
                            parsed = parse_codex_output(response)
                            message_queue.put(('message_complete', 'CODING AI', parsed))
                            task_success[0] = True
                            completion_reason[0] = "Coding AI completed task"
                        else:
                            message_queue.put(('status', "❌ No response from coding AI"))
                            completion_reason[0] = "No response from coding AI"
                    except Exception as e:
                        message_queue.put(('status', f"❌ Error: {str(e)}"))
                        completion_reason[0] = str(e)
                    finally:
                        coding_provider_instance.stop_session()
                        self._active_coding_provider = None
                        relay_complete.set()

                relay_thread = threading.Thread(target=direct_loop, daemon=True)
                relay_thread.start()

                self.session_manager = None  # No session manager in direct mode

            # ==================== MANAGED MODE ====================
            else:
                if not session_manager.start_sessions(str(path), task_description):
                    failure = f"{status_prefix}❌ Failed to start sessions"
                    yield make_yield([], failure, summary=failure, interactive=True)
                    return

                planning_status = f"{status_prefix}✓ Sessions started\n\n⏳ Management AI is planning..."
                yield make_yield([], planning_status, summary=planning_status, interactive=False)

                session_manager.set_activity_callback(on_activity)

                relay_complete = threading.Event()
                task_success = [False]
                completion_reason = [""]

                def relay_loop():  # noqa: C901
                    """Run the state machine: Investigation -> Implementation -> Verification."""
                    try:
                        phase = TaskPhase.INVESTIGATION
                        plan = None
                        impl_notes = []
                        investigation_revisits = 0
                        max_investigation_revisits = 2

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
                            # Also update the phase indicator in the role status
                            message_queue.put(('phase_update', f"{icon} {name}"))

                        investigation_system = INVESTIGATION_PROMPT.format(
                            task_description=task_description,
                            project_path=str(path)
                        )
                        investigation_system += "\n\n" + SAFETY_CONSTRAINTS

                        add_phase_divider(phase)

                        message_queue.put(('status', f"{get_phase_status(phase)} - Starting..."))
                        message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                        message_queue.put(('message_start', 'MANAGEMENT AI'))
                        session_manager.send_to_management(investigation_system)
                        mgmt_response = session_manager.get_management_response(timeout=management_timeout)

                        if check_cancelled() or not mgmt_response:
                            if not mgmt_response:
                                message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                            return

                        mgmt_text = extract_final_codex_response(mgmt_response)
                        parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                        message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))

                        while session_manager.are_sessions_alive() and not self.cancel_requested:

                            if phase == TaskPhase.INVESTIGATION:
                                investigation_iter += 1

                                if investigation_iter > max_investigation_iters:
                                    message_queue.put(('status', "⚠️ Investigation taking too long, forcing plan"))
                                    plan = f"1. Implement the task: {task_description}"
                                    phase = TaskPhase.IMPLEMENTATION
                                    continue

                                plan_match = re.search(r'PLAN:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)

                                if plan_match:
                                    plan = plan_match.group(1).strip()
                                    phase = TaskPhase.IMPLEMENTATION
                                    implementation_iter = 0

                                    add_phase_divider(phase)
                                    message_queue.put(('phase', f"📝 **Plan:**\n{plan}"))

                                    impl_system = IMPLEMENTATION_PROMPT.format(
                                        task_description=task_description,
                                        project_path=str(path),
                                        plan=plan
                                    )
                                    impl_system += "\n\n" + SAFETY_CONSTRAINTS

                                    message_queue.put(
                                        ('status', f"{get_phase_status(phase)} - Coding AI executing...")
                                    )
                                    coding_instruction = (
                                        f"{CODING_IMPLEMENTATION_CONTEXT}\n\n"
                                        f"ORIGINAL TASK: {task_description}\n\n"
                                        f"PLAN TO EXECUTE:\n{plan}\n\n"
                                        "Execute this plan to accomplish the task. Report back when complete."
                                    )
                                    session_manager.send_to_coding(coding_instruction)
                                    continue

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

                                status_msg = f"{get_phase_status(phase)} - Management reviewing findings..."
                                message_queue.put(('status', status_msg))
                                message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                                message_queue.put(('message_start', 'MANAGEMENT AI'))
                                findings_msg = (
                                    f"CODING AI FINDINGS:\n{parsed_coding}\n\n"
                                    "If you have enough info, output PLAN: with implementation steps. "
                                    "If not, ask for MORE info in ONE batched request "
                                    "(multiple files/searches at once)."
                                )
                                session_manager.send_to_management(findings_msg)
                                mgmt_response = session_manager.get_management_response(
                                    timeout=management_timeout
                                )

                                if check_cancelled() or not mgmt_response:
                                    if not mgmt_response:
                                        message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                    break

                                mgmt_text = extract_final_codex_response(mgmt_response)
                                parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                                message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))

                            elif phase == TaskPhase.IMPLEMENTATION:
                                implementation_iter += 1

                                if implementation_iter > max_implementation_iters:
                                    completion_reason[0] = "Reached maximum implementation iterations."
                                    message_queue.put(('status', "⚠️ Implementation taking too long"))
                                    break

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
                                impl_notes.append(parsed_coding[:10000])

                                status_msg = f"{get_phase_status(phase)} - Management supervising..."
                                message_queue.put(('status', status_msg))
                                message_queue.put(('ai_switch', 'MANAGEMENT AI'))
                                message_queue.put(('message_start', 'MANAGEMENT AI'))
                                supervision_msg = (
                                    f"CODING AI OUTPUT:\n{parsed_coding}\n\n"
                                    "Respond with CONTINUE: <guidance for remaining steps> or VERIFY "
                                    "if complete. Be direct - give ALL remaining instructions at once, "
                                    "not one step at a time."
                                )
                                session_manager.send_to_management(supervision_msg)
                                mgmt_response = session_manager.get_management_response(
                                    timeout=management_timeout
                                )

                                if check_cancelled() or not mgmt_response:
                                    if not mgmt_response:
                                        message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                    break

                                mgmt_text = extract_final_codex_response(mgmt_response)
                                parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                                message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))

                                mgmt_upper = mgmt_text.upper()
                                if "VERIFY" in mgmt_upper:
                                    phase = TaskPhase.VERIFICATION
                                    verification_iter = 0

                                    add_phase_divider(phase)

                                    impl_summary = "\n".join(impl_notes[-5:])
                                    verify_system = VERIFICATION_PROMPT.format(
                                        task_description=task_description,
                                        project_path=str(path),
                                        plan=plan,
                                        impl_notes=impl_summary
                                    )
                                    verify_system += "\n\n" + SAFETY_CONSTRAINTS

                                    status_msg = f"{get_phase_status(phase)} - Management verifying..."
                                    message_queue.put(('status', status_msg))
                                    message_queue.put(('message_start', 'MANAGEMENT AI'))
                                    session_manager.send_to_management(verify_system)
                                    mgmt_response = session_manager.get_management_response(
                                        timeout=management_timeout
                                    )

                                    if check_cancelled() or not mgmt_response:
                                        if not mgmt_response:
                                            message_queue.put(('status', "❌ No response from MANAGEMENT AI"))
                                        break

                                    mgmt_text = extract_final_codex_response(mgmt_response)
                                    parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                                    message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))
                                    continue

                                message_queue.put(('ai_switch', 'CODING AI'))
                                continue_match = re.search(r'CONTINUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                                if continue_match:
                                    guidance = continue_match.group(1).strip()
                                    session_manager.send_to_coding(f"{CODING_IMPLEMENTATION_CONTEXT}\n\n{guidance}")
                                else:
                                    session_manager.send_to_coding(f"{CODING_IMPLEMENTATION_CONTEXT}\n\n{mgmt_text}")

                            elif phase == TaskPhase.VERIFICATION:
                                verification_iter += 1

                                if verification_iter > max_verification_iters:
                                    completion_reason[0] = "Verification inconclusive after maximum attempts."
                                    message_queue.put(('status', "⚠️ Verification inconclusive"))
                                    break

                                mgmt_upper = mgmt_text.upper()
                                is_complete = ("COMPLETE" in mgmt_upper and
                                               "PLAN_ISSUE" not in mgmt_upper and
                                               "IMPL_ISSUE" not in mgmt_upper)
                                if is_complete:
                                    completion_reason[0] = "Management AI verified task completion."
                                    message_queue.put(('status', "✓ Task verified complete!"))
                                    task_success[0] = True
                                    break

                                plan_issue_match = re.search(
                                    r'PLAN_ISSUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                                if plan_issue_match:
                                    investigation_revisits += 1
                                    if investigation_revisits > max_investigation_revisits:
                                        completion_reason[0] = "Too many plan revisions needed."
                                        message_queue.put(('status', "⚠️ Too many plan issues"))
                                        break

                                    issue = plan_issue_match.group(1).strip().split('\n')[0]
                                    phase = TaskPhase.INVESTIGATION
                                    investigation_iter = 0

                                    add_phase_divider(phase)
                                    message_queue.put(('phase', f"⚠️ Plan issue: {issue}"))

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
                                    parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                                    message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))
                                    continue

                                impl_issue_match = re.search(
                                    r'IMPL_ISSUE:\s*(.+)', mgmt_text, re.IGNORECASE | re.DOTALL)
                                if impl_issue_match:
                                    issue = impl_issue_match.group(1).strip()
                                    phase = TaskPhase.IMPLEMENTATION

                                    add_phase_divider(phase)
                                    phase_msg = f"⚠️ Implementation issue: {issue[:100]}..."
                                    message_queue.put(('phase', phase_msg))

                                    message_queue.put(('ai_switch', 'CODING AI'))
                                    fix_instruction = (
                                        f"{CODING_IMPLEMENTATION_CONTEXT}\n\n"
                                        f"VERIFICATION FOUND AN ISSUE:\n{issue}\n\n"
                                        "Please fix this and report back."
                                    )
                                    session_manager.send_to_coding(fix_instruction)
                                    continue

                                completion_phrases = [
                                    'task is complete', 'task completed', 'successfully completed',
                                    'has been completed', 'is done', 'task done', 'confirmed complete',
                                    'yes, complete', 'verified complete', 'fulfills the requirement',
                                    'fulfill the original task', 'satisfies the requirement'
                                ]
                                if any(phrase in mgmt_text.lower() for phrase in completion_phrases):
                                    completion_reason[0] = "Management AI confirmed task completion."
                                    message_queue.put(('status', "✓ Task verified complete!"))
                                    task_success[0] = True
                                    break

                                if verification_iter >= 3:
                                    completion_reason[0] = "Task appears complete (verification loop limit reached)."
                                    message_queue.put(('status', "✓ Task complete (auto-verified)"))
                                    task_success[0] = True
                                    break

                                message_queue.put(('message_start', 'MANAGEMENT AI'))
                                verdict_prompt = (
                                    "Output your verdict: COMPLETE, PLAN_ISSUE: <reason>, "
                                    "or IMPL_ISSUE: <reason>"
                                )
                                session_manager.send_to_management(verdict_prompt)
                                mgmt_response = session_manager.get_management_response(
                                    timeout=management_timeout
                                )

                                if check_cancelled() or not mgmt_response:
                                    break

                                mgmt_text = extract_final_codex_response(mgmt_response)
                                parsed_mgmt = parse_codex_output(mgmt_response) or mgmt_text
                                message_queue.put(('message_complete', "MANAGEMENT AI", parsed_mgmt))

                    except Exception as e:
                        message_queue.put(('status', f"❌ Error: {str(e)}"))
                    finally:
                        session_manager.stop_all()
                        relay_complete.set()

                relay_thread = threading.Thread(target=relay_loop, daemon=True)
                relay_thread.start()

            # Stream updates with live activity
            if managed_mode:
                current_status = f"{status_prefix}⏳ Management AI is planning..."
                current_ai = "MANAGEMENT AI"
            else:
                current_status = f"{status_prefix}⏳ Coding AI is working..."
                current_ai = "CODING AI"
            current_live_stream = ""
            yield make_yield(
                chat_history, current_status, current_live_stream, summary=current_status, interactive=False
            )

            import time as time_module
            last_activity = ""
            streaming_buffer = ""
            full_history = []  # Infinite history - list of (ai_name, content) tuples
            last_yield_time = 0.0
            min_yield_interval = 0.05

            def format_live_output(ai_name: str, content: str) -> str:
                """Format live output with header showing active AI."""
                if not content.strip():
                    return ""
                # Convert ANSI codes to HTML for native terminal colors
                html_content = ansi_to_html(content)
                header = f'<div class="live-output-header">▶ {ai_name} (Live Stream)</div>'
                body = f'<div class="live-output-content">{html_content}</div>'
                return f'{header}\n{body}'

            def get_display_content() -> str:
                """Build display content from full history, showing last portion."""
                if not full_history:
                    return ""
                # Combine all history entries
                combined = []
                for ai_name, chunk in full_history:
                    combined.append(chunk)
                full_content = ''.join(combined)
                # Show last portion for display performance (but history is preserved)
                if len(full_content) > 50000:
                    return full_content[-50000:]
                return full_content

            pending_message_idx = None

            while not relay_complete.is_set() and not self.cancel_requested:
                try:
                    msg = message_queue.get(timeout=0.02)
                    msg_type = msg[0]

                    if msg_type == 'message':
                        speaker, content = msg[1], msg[2]
                        chat_history.append(make_chat_message(speaker, content))
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == 'message_start':
                        speaker = msg[1]
                        placeholder = {"role": "user" if "MANAGEMENT" in speaker else "assistant",
                                       "content": f"**{speaker}**\n\n⏳ *Working...*"}
                        chat_history.append(placeholder)
                        pending_message_idx = len(chat_history) - 1
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == 'message_complete':
                        speaker, content = msg[1], msg[2]
                        if pending_message_idx is not None and pending_message_idx < len(chat_history):
                            chat_history[pending_message_idx] = make_chat_message(speaker, content)
                        else:
                            chat_history.append(make_chat_message(speaker, content))
                        pending_message_idx = None
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        self.session_logger.update_log(session_log_path, chat_history)
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == 'status':
                        current_status = f"{status_prefix}{msg[1]}"
                        streaming_buffer = ""
                        current_live_stream = ""
                        summary_text = current_status
                        yield make_yield(chat_history, current_status, current_live_stream, summary=summary_text)
                        last_yield_time = time_module.time()

                    elif msg_type == 'phase_divider':
                        phase_name = msg[1]
                        divider = f"───────────── {phase_name} ─────────────"
                        chat_history.append({"role": "user", "content": divider})
                        streaming_buffer = ""
                        current_live_stream = ""
                        self.session_logger.update_log(session_log_path, chat_history)
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == 'phase_update':
                        # Update the phase display in the role status line
                        current_phase_display[0] = msg[1]
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == 'phase':
                        phase_msg = msg[1]
                        chat_history.append({"role": "user", "content": phase_msg})
                        streaming_buffer = ""
                        current_live_stream = ""
                        summary_text = f"{status_prefix}{phase_msg}"
                        self.session_logger.update_log(session_log_path, chat_history)
                        yield make_yield(chat_history, current_status, current_live_stream, summary=summary_text)
                        last_yield_time = time_module.time()

                    elif msg_type == 'ai_switch':
                        current_ai = msg[1]
                        streaming_buffer = ""
                        # Add separator to history for AI switch
                        full_history.append((current_ai, f"\n--- {current_ai} ---\n"))

                    elif msg_type == 'stream':
                        chunk = msg[1]
                        # Filter out empty/whitespace-only chunks
                        if chunk.strip():
                            streaming_buffer += chunk
                            # Add to infinite history (no truncation)
                            full_history.append((current_ai, chunk))
                            now = time_module.time()
                            if now - last_yield_time >= min_yield_interval:
                                # Get display content from full history
                                display_content = get_display_content()
                                current_live_stream = format_live_output(
                                    current_ai, display_content
                                )
                                yield make_yield(
                                    chat_history, current_status, current_live_stream
                                )
                                last_yield_time = now

                    elif msg_type == 'activity':
                        last_activity = msg[1]
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            display_content = get_display_content()
                            if display_content:
                                content = display_content + f"\n\n{last_activity}"
                                current_live_stream = format_live_output(
                                    current_ai, content
                                )
                            else:
                                current_live_stream = f"**Live:** {last_activity}"
                            yield make_yield(
                                chat_history, current_status, current_live_stream
                            )
                            last_yield_time = now

                except queue.Empty:
                    now = time_module.time()
                    if now - last_yield_time >= 0.3:
                        display_content = get_display_content()
                        if display_content:
                            current_live_stream = format_live_output(
                                current_ai, display_content
                            )
                        elif last_activity:
                            current_live_stream = f"**Live:** {last_activity}"
                        yield make_yield(
                            chat_history, current_status, current_live_stream
                        )
                        last_yield_time = now

            # If cancelled, don't process remaining messages - just mark as cancelled
            if self.cancel_requested:
                # Find the last assistant message and mark it as cancelled
                for idx in range(len(chat_history) - 1, -1, -1):
                    msg = chat_history[idx]
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        chat_history[idx] = {
                            "role": "assistant",
                            "content": "**CODING AI**\n\n🛑 *Cancelled*"
                        }
                        break
                yield make_yield(chat_history, "🛑 Task cancelled", "", summary="🛑 Task cancelled")
            else:
                # Process any remaining message_complete messages
                while True:
                    try:
                        msg = message_queue.get_nowait()
                        msg_type = msg[0]
                        if msg_type == 'message_complete':
                            speaker, content = msg[1], msg[2]
                            if pending_message_idx is not None and pending_message_idx < len(chat_history):
                                chat_history[pending_message_idx] = make_chat_message(speaker, content)
                            else:
                                chat_history.append(make_chat_message(speaker, content))
                            self.session_logger.update_log(session_log_path, chat_history)
                            yield make_yield(chat_history, current_status, "")
                    except queue.Empty:
                        break

            relay_thread.join(timeout=1)
            # Clear the phase indicator when task finishes
            current_phase_display[0] = ""
            if self.cancel_requested:
                final_status = "🛑 Task cancelled by user"
                # Add cancellation indicator to timeline
                chat_history.append({
                    "role": "user",
                    "content": "───────────── 🛑 TASK CANCELLED ─────────────"
                })
            elif task_success[0]:
                if completion_reason[0]:
                    final_status = f"✓ Task completed!\n\n*{completion_reason[0]}*"
                else:
                    final_status = "✓ Task completed!"
                # Add completion indicator to timeline
                completion_msg = "───────────── ✅ TASK COMPLETED ─────────────"
                if completion_reason[0]:
                    completion_msg += f"\n\n*{completion_reason[0]}*"
                chat_history.append({
                    "role": "user",
                    "content": completion_msg
                })
            else:
                final_status = (
                    f"❌ Task did not complete successfully\n\n*{completion_reason[0]}*"
                    if completion_reason[0]
                    else "❌ Task did not complete successfully"
                )
                # Add failure indicator to timeline
                failure_msg = "───────────── ❌ TASK FAILED ─────────────"
                if completion_reason[0]:
                    failure_msg += f"\n\n*{completion_reason[0]}*"
                chat_history.append({
                    "role": "user",
                    "content": failure_msg
                })

            # Build streaming transcript from full_history
            streaming_transcript = ''.join(chunk for _, chunk in full_history) if full_history else None

            self.session_logger.update_log(
                session_log_path,
                chat_history,
                streaming_transcript=streaming_transcript,
                success=task_success[0],
                completion_reason=completion_reason[0],
                status="completed" if task_success[0] else "failed"
            )
            if session_log_path:
                final_status += f"\n\n*Session log: {session_log_path}*"
            final_summary = f"{status_prefix}{final_status}"

            yield make_yield(chat_history, final_summary, "", summary=final_summary, interactive=True)

        except Exception as e:
            import traceback
            error_msg = f"❌ Error: {str(e)}\n\n```\n{traceback.format_exc()}\n```"
            yield make_yield(chat_history, error_msg, summary=error_msg, interactive=True)

    def create_interface(self) -> gr.Blocks:
        """Create the Gradio interface."""
        # Pre-create the session log so it's ready to display
        self.current_session_log_path = self.session_logger.precreate_log()

        with gr.Blocks(title="Chad") as interface:
            # Inject custom CSS
            gr.HTML(f"<style>{PROVIDER_PANEL_CSS}</style>")

            # Execute custom JavaScript on page load
            interface.load(fn=None, js=CUSTOM_JS)

            with gr.Tabs():
                # Run Task Tab (default)
                with gr.Tab("🚀 Run Task"):
                    gr.Markdown("## Start a New Task")

                    # Check initial role configuration
                    is_ready, _ = self.get_role_config_status()
                    config_status = self.format_role_status()

                    with gr.Row():
                        with gr.Column():
                            # Allow override via env var (for screenshots)
                            default_path = os.environ.get('CHAD_PROJECT_PATH', str(Path.cwd()))
                            project_path = gr.Textbox(
                                label="Project Path",
                                placeholder="/path/to/project",
                                value=default_path
                            )
                            task_description = gr.TextArea(
                                label="Task Description",
                                placeholder="Describe what you want done...",
                                lines=5
                            )
                            # Show role configuration status with session log download
                            with gr.Row(elem_id="role-status-row"):
                                role_status = gr.Markdown(config_status, elem_id="role-config-status")
                                log_path = self.current_session_log_path
                                session_log_btn = gr.DownloadButton(
                                    label=f"📄 {log_path.name}" if log_path else "Session Log",
                                    value=str(log_path) if log_path else None,
                                    visible=log_path is not None,
                                    variant="secondary",
                                    size="sm",
                                    scale=0,
                                    min_width=120,
                                    elem_id="session-log-btn"
                                )
                            with gr.Row():
                                start_btn = gr.Button(
                                    "Start Task",
                                    variant="primary",
                                    interactive=is_ready,
                                    elem_id="start-task-btn"
                                )
                                cancel_btn = gr.Button(
                                    "🛑 Cancel",
                                    variant="stop",
                                    interactive=False,
                                    elem_id="cancel-task-btn"
                                )

                    # Task status header (shows selected task description and status)
                    task_status_header = gr.Markdown("", elem_id="task-status-header", visible=False)

                    # Agent communication view
                    with gr.Row():
                        with gr.Column():
                            chatbot = gr.Chatbot(
                                label="Agent Communication",
                                height=400,
                                elem_id="agent-chatbot",
                                autoscroll=False
                            )

                    # Live activity stream
                    live_stream_box = gr.Markdown("", elem_id="live-stream-box")

                # Providers Tab (combined management + usage)
                with gr.Tab("⚙️ Providers"):
                    account_items = list(self.security_mgr.list_accounts().items())
                    # Allow room for new providers without needing a reload
                    self.provider_card_count = max(12, len(account_items) + 8)

                    provider_feedback = gr.Markdown("")
                    gr.Markdown("### Providers", elem_classes=["provider-section-title"])

                    provider_list = gr.Markdown(self.list_providers(), elem_classes=["provider-summary"])
                    refresh_btn = gr.Button("🔄 Refresh", variant="secondary")
                    pending_delete_state = gr.State(None)  # Tracks which account is pending deletion

                    provider_cards = []
                    for idx in range(self.provider_card_count):
                        if idx < len(account_items):
                            account_name, provider_type = account_items[idx]
                            visible = True
                            header_text = (
                                f'<span class="provider-card__header-text">'
                                f'{account_name} ({provider_type})</span>'
                            )
                            role_value = self._get_account_role(account_name) or "(none)"
                            model_choices = self.get_models_for_account(account_name)
                            stored_model = self.security_mgr.get_account_model(account_name)
                            model_value = stored_model if stored_model in model_choices else model_choices[0]
                            reasoning_choices = self.get_reasoning_choices(provider_type, account_name)
                            stored_reasoning = self.security_mgr.get_account_reasoning(account_name)
                            reasoning_value = (
                                stored_reasoning if stored_reasoning in reasoning_choices
                                else reasoning_choices[0]
                            )
                            usage_text = self.get_provider_usage(account_name)
                        else:
                            account_name = ""
                            visible = False
                            header_text = ""
                            role_value = "(none)"
                            model_choices = ["default"]
                            model_value = "default"
                            reasoning_choices = ["default"]
                            reasoning_value = "default"
                            usage_text = ""

                        # Always create columns visible - use CSS classes to show/hide
                        # gr.Column(visible=False) prevents proper rendering updates
                        card_group_classes = ["provider-card"] if visible else ["provider-card", "provider-card-empty"]
                        with gr.Column(visible=True) as card_column:
                            with gr.Group(elem_classes=card_group_classes) as card_group:
                                with gr.Row(elem_classes=["provider-card__header-row"]):
                                    card_header = gr.Markdown(header_text, elem_classes=["provider-card__header"])
                                    delete_btn = gr.Button("🗑︎", variant="secondary", size="sm", min_width=0, scale=0, elem_classes=["provider-delete"])  # noqa: E501
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
                                    reasoning_dropdown = gr.Dropdown(
                                        choices=reasoning_choices,
                                        label="Reasoning Effort",
                                        value=reasoning_value,
                                        allow_custom_value=True,
                                        scale=1
                                    )

                                gr.Markdown("Usage", elem_classes=["provider-usage-title"])
                                usage_box = gr.Markdown(usage_text, elem_classes=["provider-usage"])

                        provider_cards.append({
                            "column": card_column,
                            "group": card_group,  # Use group for visibility control
                            "header": card_header,
                            "account_state": account_state,
                            "account_name": account_name,  # Store name for delete handler
                            "role_dropdown": role_dropdown,
                            "model_dropdown": model_dropdown,
                            "reasoning_dropdown": reasoning_dropdown,
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
                            card["group"],  # Use group for visibility control (Column visibility doesn't update)
                            card["header"],
                            card["account_state"],
                            card["role_dropdown"],
                            card["model_dropdown"],
                            card["reasoning_dropdown"],
                            card["usage_box"],
                            card["delete_btn"]
                        ])

                    # Add role status and start button to outputs so they update when roles change
                    provider_outputs_with_task_status = provider_outputs + [role_status, start_btn]

                    # Include task status in add_provider outputs so Run Task tab updates
                    add_provider_outputs = (
                        provider_outputs +
                        [new_provider_name, add_btn, add_provider_accordion, role_status, start_btn]
                    )

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

                    def add_provider_with_task_status(provider_name, provider_type):
                        """Add provider and also return updated task status."""
                        base = self.add_provider(provider_name, provider_type)
                        is_ready, config_msg = self.get_role_config_status()
                        return (*base, config_msg, gr.update(interactive=is_ready))

                    add_btn.click(
                        add_provider_with_task_status,
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
                        card["reasoning_dropdown"].change(
                            self.set_reasoning,
                            inputs=[card["account_state"], card["reasoning_dropdown"]],
                            outputs=provider_outputs
                        )

                        # Two-step delete using dynamic account_state (not captured name)
                        # This ensures handlers work correctly after cards shift due to deletions
                        def make_delete_handler():
                            def handler(pending_delete, current_account):
                                # Skip if card has no account (empty slot)
                                if not current_account:
                                    return (pending_delete, *self._provider_action_response(""))

                                if pending_delete == current_account:
                                    # Second click - actually delete
                                    result = self.delete_provider(current_account, confirmed=True)
                                    return (None, *result)  # Reset pending state + provider outputs
                                else:
                                    # First click - show confirmation button (tick icon)
                                    result = self._provider_action_response(
                                        f"Click the ✓ icon in '{current_account}' titlebar to confirm deletion",
                                        pending_delete=current_account
                                    )
                                    return (current_account, *result)  # Set pending state + provider outputs
                            return handler

                        # Outputs include pending_delete_state + all provider outputs
                        delete_outputs = [pending_delete_state] + provider_outputs

                        card["delete_btn"].click(
                            fn=make_delete_handler(),
                            inputs=[pending_delete_state, card["account_state"]],
                            outputs=delete_outputs
                        )

            # Connect task execution (outside tabs)
            start_btn.click(
                self.start_chad_task,
                inputs=[project_path, task_description],
                outputs=[chatbot, live_stream_box, task_status_header, project_path, task_description, start_btn, cancel_btn, role_status, session_log_btn]  # noqa: E501
            )

            cancel_btn.click(
                self.cancel_task,
                outputs=[live_stream_box]
            )

            return interface


def launch_web_ui(password: str = None, port: int = 7860) -> tuple[None, int]:
    """Launch the Chad web interface.

    Args:
        password: Main password. If not provided, will prompt via CLI
        port: Port to run on. Use 0 for ephemeral port.

    Returns:
        Tuple of (None, actual_port) where actual_port is the port used
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
        if password is not None:
            # Use provided password (for automation/screenshots)
            main_password = password
        else:
            # Interactive mode - verify password which includes the reset flow
            main_password = security_mgr.verify_main_password()

    # Create and launch UI
    ui = ChadWebUI(security_mgr, main_password)
    app = ui.create_interface()

    # Find a free port if ephemeral requested
    ephemeral = port == 0
    if ephemeral:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]

    print("\n" + "=" * 70)
    print("CHAD WEB UI")
    print("=" * 70)
    if not ephemeral:
        print("Opening web interface in your browser...")
    print("Press Ctrl+C to stop the server")
    print("=" * 70 + "\n")

    # Print port marker for scripts to parse (before launch blocks)
    print(f"CHAD_PORT={port}", flush=True)

    app.launch(
        server_name="127.0.0.1",
        server_port=port,
        share=False,
        inbrowser=not ephemeral,  # Don't open browser for screenshot mode
        quiet=False
    )

    return None, port

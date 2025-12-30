"""Gradio web interface for Chad."""

import os
import re
import socket
import threading
import queue
from pathlib import Path
from typing import Iterator

import gradio as gr

from .provider_ui import ProviderUIManager
from .security import SecurityManager
from .session_logger import SessionLogger
from .providers import ModelConfig, parse_codex_output, create_provider
from .model_catalog import ModelCatalog
from .prompts import build_coding_prompt, get_verification_prompt, parse_verification_response, VerificationParseError

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")

DEFAULT_CODING_TIMEOUT = 1800.0


def _find_free_port() -> int:
    """Bind to an ephemeral port and return it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
    except PermissionError:
        # Sandbox environments may disallow binding sockets; fall back to default UI port
        return 7860


def _resolve_port(port: int) -> tuple[int, bool, bool]:
    """Return (port, is_ephemeral, conflicted_with_request)."""
    if port == 0:
        return _find_free_port(), True, False

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port, False, False
            except OSError:
                pass
    except PermissionError:
        fallback = port or _find_free_port()
        return fallback, port == 0 or fallback != port, False

    return _find_free_port(), True, True


# Custom styling for the provider management area to improve contrast between
# the summary header and each provider card.
PROVIDER_PANEL_CSS = """
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');

:root {
  --task-btn-bg: #8fd3ff;
  --task-btn-border: #74c3f6;
  --task-btn-text: #0a2236;
  --task-btn-hover: #7bc9ff;
  --cancel-btn-bg: #f74a4a;
  --cancel-btn-border: #cf2f2f;
  --cancel-btn-text: #ffffff;
  --cancel-btn-hover: #ff6a6a;
}

@media (prefers-color-scheme: light) {
  :root {
    --cancel-btn-bg: #e53935;
    --cancel-btn-border: #c62828;
    --cancel-btn-hover: #f25b55;
    --cancel-btn-text: #ffffff;
  }
}

@media (prefers-color-scheme: dark) {
  :root {
    --cancel-btn-bg: #ff5c5c;
    --cancel-btn-border: #ff8686;
    --cancel-btn-hover: #ff7c7c;
    --cancel-btn-text: #ffffff;
  }
}

body, .gradio-container, .gradio-container * {
  font-family: 'JetBrains Mono', monospace !important;
}

#start-task-btn,
#start-task-btn button {
  background: var(--task-btn-bg) !important;
  border: 1px solid var(--task-btn-border) !important;
  color: var(--task-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 32px !important;
  padding: 6px 12px !important;
}

#cancel-task-btn {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  margin: 0 !important;
  width: auto !important;
  min-width: 0 !important;
  max-width: fit-content !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: flex-end !important;
  flex: 0 0 auto !important;
}

#cancel-task-btn button {
  background: var(--cancel-btn-bg) !important;
  border: 1px solid var(--cancel-btn-border) !important;
  color: var(--cancel-btn-text) !important;
  -webkit-text-fill-color: var(--cancel-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 28px !important;
  min-width: 110px !important;
  padding: 6px 12px !important;
  line-height: 1.1 !important;
  width: auto !important;
  max-width: none !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 6px !important;
  opacity: 1 !important;
}

#cancel-task-btn:is(button) {
  background: var(--cancel-btn-bg) !important;
  border: 1px solid var(--cancel-btn-border) !important;
  color: var(--cancel-btn-text) !important;
  -webkit-text-fill-color: var(--cancel-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 28px !important;
  min-width: 110px !important;
  padding: 6px 12px !important;
  line-height: 1.1 !important;
  width: auto !important;
  max-width: none !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 6px !important;
  opacity: 1 !important;
}

#cancel-task-btn button span,
#cancel-task-btn span {
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  opacity: 1 !important;
  padding: 0 !important;
  margin: 0 !important;
}

#cancel-task-btn button span *,
#cancel-task-btn span * {
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  opacity: 1 !important;
}

#cancel-task-btn button:disabled,
#cancel-task-btn button[disabled],
#cancel-task-btn button[aria-disabled="true"],
#cancel-task-btn button.disabled,
#cancel-task-btn:disabled,
#cancel-task-btn[disabled],
#cancel-task-btn[aria-disabled="true"],
#cancel-task-btn.disabled {
  background: var(--cancel-btn-bg) !important;
  border: 1px solid var(--cancel-btn-border) !important;
  color: var(--cancel-btn-text) !important;
  opacity: 1 !important;
  filter: none !important;
}

#start-task-btn:hover,
#start-task-btn button:hover {
  background: var(--task-btn-hover) !important;
}

#cancel-task-btn:hover,
#cancel-task-btn button:hover {
  background: var(--cancel-btn-hover) !important;
}

.provider-section-title {
  color: #e2e8f0;
  letter-spacing: 0.01em;
}

.provider-summary {
  background: #1a1f2e;
  border: 1px solid #2d3748;
  border-radius: 14px;
  padding: 12px 14px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
}

.provider-summary,
.provider-summary * {
  color: #e2e8f0 !important;
}

.provider-summary strong {
  color: #63b3ed !important;
}

.provider-summary code {
  background: #2d3748 !important;
  color: #a0aec0 !important;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}

.provider-card {
  background: linear-gradient(135deg, #0c1424 0%, #0a1a32 100%);
  border: 1px solid #1f2b46;
  border-radius: 16px;
  margin-bottom: 0 !important;
  padding: 10px 12px;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.28);
  gap: 4px;
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
  margin-top: 6px !important;
  color: #475569;
  border-top: 1px solid #e2e8f0;
  padding-top: 4px;
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
  padding: 6px 8px;
  color: #1e293b;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06);
}

.add-provider-accordion {
  margin-top: calc(var(--block-gap, 0px) * -1 - 4px) !important;
  padding-top: 0 !important;
}

.add-provider-accordion > summary,
.add-provider-accordion summary,
.add-provider-accordion .label,
.add-provider-accordion .label-wrap {
  font-size: 1.125rem !important;
  font-weight: 800 !important;
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

/* Hide empty provider cards - groups are hidden via CSS, columns via JavaScript */
.gr-group:has(.provider-card__header-row):not(:has(.provider-card__header-text)) {
  display: none !important;
}

/* Two-column layout for provider cards */
.provider-cards-row {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 12px !important;
  align-items: stretch !important;
}

.provider-cards-row > .column {
  flex: 0 0 calc(50% - 6px) !important;
  max-width: calc(50% - 6px) !important;
}

/* On smaller screens, switch to single column */
@media (max-width: 1024px) {
  .provider-cards-row > .column {
    flex: 0 0 100% !important;
    max-width: 100% !important;
  }
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

/* Normalize all heading sizes in live stream - no large headers */
#live-stream-box h1,
#live-stream-box h2,
#live-stream-box h3,
#live-stream-box h4,
#live-stream-box h5,
#live-stream-box h6,
#live-stream-box .live-output-content h1,
#live-stream-box .live-output-content h2,
#live-stream-box .live-output-content h3,
#live-stream-box .live-output-content h4,
#live-stream-box .live-output-content h5,
#live-stream-box .live-output-content h6 {
  font-size: 13px !important;
  font-weight: 600 !important;
  margin: 0 !important;
  padding: 0 !important;
  line-height: 1.5 !important;
}

/* Override Tailwind prose class that Gradio applies - it sets dark text colors */
#live-stream-box .prose,
#live-stream-box .prose *:not([style*="color"]),
#live-stream-box .md,
#live-stream-box .md *:not([style*="color"]),
#live-stream-box p,
#live-stream-box span:not([style*="color"]),
#live-stream-box div:not([style*="color"]) {
  color: #e2e8f0 !important;
}

/* Ensure live-output-content and ALL its children have light text */
#live-stream-box .live-output-content,
#live-stream-box .live-output-content *:not([style*="color"]) {
  color: #e2e8f0 !important;
}

/* Code elements (rendered from backticks in Markdown) - bright pink */
#live-stream-box code,
#live-stream-box .live-output-content code,
#live-stream-box pre,
#live-stream-box .live-output-content pre {
  color: #f0abfc !important;
  background: none !important;
  padding: 0 !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  font-family: inherit;
  font-weight: 600;
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

/* Task entry styled like a user chat bubble */
.task-entry-bubble {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  box-shadow: none;
  margin: 0;
}

.task-entry-bubble .task-entry-header {
  display: none;
}

.task-entry-bubble .task-entry-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-entry-bubble .task-entry-actions {
  display: flex;
  justify-content: flex-end;
}

.agent-panel {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  box-shadow: none;
}

/* Follow-up input - styled as a compact chat continuation */
#followup-row {
  margin-top: 8px;
  padding: 0;
  background: transparent;
  border: none;
}

#followup-row > div {
  gap: 8px !important;
}

#followup-row .followup-header {
  color: #888;
  font-size: 0.85rem;
  margin-bottom: 4px;
  font-style: italic;
}

#followup-input {
  flex: 1;
}

#followup-input textarea {
  background: var(--neutral-50) !important;
  border: 1px solid var(--border-color-primary) !important;
  border-radius: 8px !important;
  min-height: 60px !important;
  resize: vertical !important;
}

#send-followup-btn {
  align-self: flex-end;
  margin-bottom: 4px;
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
            const headerRow = col.querySelector('.provider-card__header-row');
            const headerText = col.querySelector('.provider-card__header-text');

            if (headerRow) {
                // This is a provider card column
                if (headerText && headerText.textContent.trim().length > 0) {
                    // Has content - show it
                    col.style.display = '';
                    col.style.visibility = '';
                } else {
                    // Empty card - hide it to prevent gap
                    col.style.display = 'none';
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


def _brighten_color(r: int, g: int, b: int, min_brightness: int = 140) -> tuple[int, int, int]:
    """Brighten a color if it's too dark for a dark background.

    Uses perceived brightness (ITU-R BT.709) and boosts dark colors.
    """
    brightness = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if brightness < min_brightness:
        # Boost the color to be readable
        if brightness < 10:
            # Nearly black - use light grey
            return (156, 163, 175)  # #9ca3af
        # Scale up to reach minimum brightness
        factor = min_brightness / max(brightness, 1)
        return (
            min(255, int(r * factor)),
            min(255, int(g * factor)),
            min(255, int(b * factor))
        )
    return (r, g, b)


def _256_to_rgb(n: int) -> tuple[int, int, int]:
    """Convert 256-color palette index to RGB."""
    if n < 8:
        # Standard colors 0-7
        return [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
                (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229)][n]
    elif n < 16:
        # Bright colors 8-15
        return [(127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
                (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)][n - 8]
    elif n < 232:
        # 6x6x6 color cube (16-231)
        n -= 16
        r = (n // 36) % 6
        g = (n // 6) % 6
        b = n % 6
        return (r * 51, g * 51, b * 51)
    else:
        # Grayscale (232-255)
        gray = (n - 232) * 10 + 8
        return (gray, gray, gray)


def ansi_to_html(text: str) -> str:
    """Convert ANSI escape codes to HTML spans with colors.

    Preserves the terminal's native coloring instead of stripping it.
    Handles CSI sequences (ESC[), OSC sequences (ESC]), and other escapes.
    Automatically brightens dark colors for readability on dark backgrounds.
    """
    # ANSI 16-color to RGB mapping
    basic_colors = {
        '30': (0, 0, 0), '31': (224, 108, 117), '32': (152, 195, 121), '33': (229, 192, 123),
        '34': (97, 175, 239), '35': (198, 120, 221), '36': (86, 182, 194), '37': (171, 178, 191),
        '90': (92, 99, 112), '91': (224, 108, 117), '92': (152, 195, 121), '93': (229, 192, 123),
        '94': (97, 175, 239), '95': (198, 120, 221), '96': (86, 182, 194), '97': (255, 255, 255),
    }
    bg_colors = {
        '40': '#1e1e2e', '41': '#e06c75', '42': '#98c379', '43': '#e5c07b',
        '44': '#61afef', '45': '#c678dd', '46': '#56b6c2', '47': '#abb2bf',
    }

    # CSI sequence ending characters (covers most terminal sequences)
    CSI_ENDINGS = 'ABCDEFGHJKLMPSTXZcfghlmnpqrstuz'

    result = []
    i = 0
    current_styles = []

    while i < len(text):
        # Check for escape character
        if text[i] == '\x1b':
            if i + 1 < len(text):
                next_char = text[i + 1]

                # CSI sequence: ESC[...
                if next_char == '[':
                    j = i + 2
                    while j < len(text) and text[j] not in CSI_ENDINGS:
                        j += 1
                    if j < len(text):
                        if text[j] == 'm':
                            # SGR (color/style) sequence - parse it
                            codes = text[i + 2:j].split(';')
                            idx = 0
                            while idx < len(codes):
                                code = codes[idx]
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
                                elif code == '38':
                                    # Extended foreground color
                                    if idx + 1 < len(codes):
                                        if codes[idx + 1] == '5' and idx + 2 < len(codes):
                                            # 256-color: 38;5;N
                                            try:
                                                n = int(codes[idx + 2])
                                                r, g, b = _brighten_color(*_256_to_rgb(n))
                                                result.append(f'<span style="color:rgb({r},{g},{b})">')
                                                current_styles.append('color')
                                            except ValueError:
                                                pass
                                            idx += 2
                                        elif codes[idx + 1] == '2' and idx + 4 < len(codes):
                                            # True color: 38;2;R;G;B
                                            try:
                                                r = int(codes[idx + 2])
                                                g = int(codes[idx + 3])
                                                b = int(codes[idx + 4])
                                                r, g, b = _brighten_color(r, g, b)
                                                result.append(f'<span style="color:rgb({r},{g},{b})">')
                                                current_styles.append('color')
                                            except ValueError:
                                                pass
                                            idx += 4
                                elif code == '48':
                                    # Extended background color (skip, don't change bg)
                                    if idx + 1 < len(codes):
                                        if codes[idx + 1] == '5':
                                            idx += 2
                                        elif codes[idx + 1] == '2':
                                            idx += 4
                                elif code in basic_colors:
                                    r, g, b = _brighten_color(*basic_colors[code])
                                    result.append(f'<span style="color:rgb({r},{g},{b})">')
                                    current_styles.append('color')
                                elif code in bg_colors:
                                    result.append(f'<span style="background-color:{bg_colors[code]}">')
                                    current_styles.append('bg')
                                idx += 1
                        # Skip the entire CSI sequence (including non-SGR ones)
                        i = j + 1
                        continue

                # OSC sequence: ESC]...BEL or ESC]...ST
                elif next_char == ']':
                    j = i + 2
                    while j < len(text):
                        # BEL (0x07) or ST (ESC\) terminates OSC
                        if text[j] == '\x07':
                            j += 1
                            break
                        if text[j] == '\x1b' and j + 1 < len(text) and text[j + 1] == '\\':
                            j += 2
                            break
                        j += 1
                    i = j
                    continue

            # Unrecognized escape - skip just the escape char
            i += 1
            continue

        # Regular character - escape HTML entities
        char = text[i]
        if char == '<':
            result.append('&lt;')
        elif char == '>':
            result.append('&gt;')
        elif char == '&':
            result.append('&amp;')
        elif char == '\n':
            result.append('\n')
        else:
            result.append(char)
        i += 1

    # Close any remaining spans
    if current_styles:
        result.append('</span>' * len(current_styles))

    return ''.join(result)


def highlight_diffs(html_content: str) -> str:
    """Add diff highlighting CSS classes to diff-style lines.

    Detects unified diff format lines and wraps them in appropriate classes.
    """
    import re

    lines = html_content.split('\n')
    result = []

    for line in lines:
        # Strip HTML tags to check the actual text content for diff patterns
        text_only = re.sub(r'<[^>]+>', '', line)

        if re.match(r'^@@\s.*\s@@', text_only):
            # Diff header line like "@@ -1,5 +1,7 @@"
            result.append(f'<span class="diff-header">{line}</span>')
        elif text_only.startswith('+') and not text_only.startswith('+++'):
            # Added line
            result.append(f'<span class="diff-add">{line}</span>')
        elif text_only.startswith('-') and not text_only.startswith('---'):
            # Removed line
            result.append(f'<span class="diff-remove">{line}</span>')
        else:
            result.append(line)

    return '\n'.join(result)


def normalize_live_stream_spacing(content: str) -> str:
    """Remove all blank lines from live stream output for compact display."""
    if not content:
        return ""

    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    # Remove all blank lines - keep output compact
    lines = [line for line in normalized.split("\n") if line.strip()]
    return "\n".join(lines)


def build_live_stream_html(content: str, ai_name: str = "CODING AI") -> str:
    """Render live stream text as HTML with consistent spacing and header."""
    cleaned = normalize_live_stream_spacing(content)
    if not cleaned.strip():
        return ""
    html_content = highlight_diffs(ansi_to_html(cleaned))
    header = f'<div class="live-output-header">▶ {ai_name} (Live Stream)</div>'
    body = f'<div class="live-output-content">{html_content}</div>'
    return f"{header}\n{body}"


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
        speaker: The speaker name (e.g., "CODING AI")
        content: The message content
        collapsible: Whether to make long messages collapsible with a summary
    """
    # All AI messages are assistant role
    role = "assistant"

    # For long content, make it collapsible with a summary
    if collapsible and len(content) > 300:
        summary = summarize_content(content)
        formatted = f"**{speaker}**\n\n{summary}\n\n<details><summary>Show full output</summary>\n\n{content}\n\n</details>"  # noqa: E501
    else:
        formatted = f"**{speaker}**\n\n{content}"

    return {"role": role, "content": formatted}


class ChadWebUI:
    """Web interface for Chad using Gradio."""

    # Constant for verification agent dropdown default
    SAME_AS_CODING = "(Same as Coding Agent)"

    def __init__(self, security_mgr: SecurityManager, main_password: str):
        self.security_mgr = security_mgr
        self.main_password = main_password
        self.active_sessions = {}
        self.cancel_requested = False
        self._active_coding_provider = None
        self.provider_card_count = 10
        self.model_catalog = ModelCatalog(security_mgr)
        self.provider_ui = ProviderUIManager(security_mgr, main_password, self.model_catalog)
        self.session_logger = SessionLogger()
        self.current_session_log_path: Path | None = None
        # Session continuation state
        self._current_chat_history: list = []
        self._current_project_path: str | None = None
        self._session_active: bool = False
        self._current_coding_account: str | None = None
        self._active_coding_config: ModelConfig | None = None

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

    def _read_project_docs(self, project_path: Path) -> str | None:
        """Read project documentation if present.

        Reads AGENTS.md, .claude/CLAUDE.md, or CLAUDE.md from the project.
        Returns the first file found, or None if no documentation exists.
        """
        doc_files = [
            project_path / "AGENTS.md",
            project_path / ".claude" / "CLAUDE.md",
            project_path / "CLAUDE.md",
        ]

        for doc_file in doc_files:
            if doc_file.exists():
                try:
                    content = doc_file.read_text(encoding='utf-8')
                    # Limit content to avoid overwhelming the context
                    if len(content) > 8000:
                        content = content[:8000] + "\n\n[...truncated...]"
                    return content
                except (OSError, UnicodeDecodeError):
                    continue

        return None

    def _run_verification(
        self,
        project_path: str,
        coding_output: str,
        verification_account: str,
        on_activity: callable = None,
        timeout: float = 300.0
    ) -> tuple[bool, str]:
        """Run the verification agent to review the coding agent's work.

        Args:
            project_path: Path to the project directory
            coding_output: The output from the coding agent
            verification_account: Account name to use for verification
            on_activity: Optional callback for activity updates
            timeout: Timeout for verification (default 5 minutes)

        Returns:
            Tuple of (verified: bool, feedback: str)
            - verified=True means the work passed verification
            - verified=False means revisions are needed, feedback contains issues
        """
        accounts = self.security_mgr.list_accounts()
        if verification_account not in accounts:
            return True, "Verification skipped: account not found"

        verification_provider = accounts[verification_account]
        verification_model = self.security_mgr.get_account_model(verification_account)
        verification_reasoning = self.security_mgr.get_account_reasoning(verification_account)

        verification_config = ModelConfig(
            provider=verification_provider,
            model_name=verification_model,
            account_name=verification_account,
            reasoning_effort=None if verification_reasoning == 'default' else verification_reasoning
        )

        verification_prompt = get_verification_prompt(coding_output)

        try:
            verifier = create_provider(verification_config)
            if on_activity:
                verifier.set_activity_callback(on_activity)

            if not verifier.start_session(project_path, None):
                return True, "Verification skipped: failed to start session"

            max_parse_attempts = 2
            last_error = None

            for attempt in range(max_parse_attempts):
                verifier.send_message(verification_prompt)
                response = verifier.get_response(timeout=timeout)

                if not response:
                    last_error = "No response from verification agent"
                    continue

                try:
                    passed, summary, issues = parse_verification_response(response)

                    verifier.stop_session()

                    if passed:
                        return True, summary
                    else:
                        feedback = summary
                        if issues:
                            feedback += "\n\nIssues:\n" + "\n".join(f"- {issue}" for issue in issues)
                        return False, feedback

                except VerificationParseError as e:
                    last_error = str(e)
                    if attempt < max_parse_attempts - 1:
                        # Retry with a reminder to use JSON format
                        verification_prompt = (
                            "Your previous response was not valid JSON. "
                            "You MUST respond with ONLY a JSON object like:\n"
                            '```json\n{"passed": true, "summary": "explanation"}\n```\n\n'
                            "Try again."
                        )
                    continue

            verifier.stop_session()
            # All attempts failed - return error
            return None, f"Verification failed: {last_error}"

        except Exception as e:
            return None, f"Verification error: {str(e)}"

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

        cli_ok, cli_detail = self.provider_ui._ensure_provider_cli('openai')
        if not cli_ok:
            return f"❌ {cli_detail}"
        codex_cli = cli_detail or "codex"

        # Setup isolated home
        codex_home = self._setup_codex_account(account_name)

        # Create environment with isolated HOME
        env = os.environ.copy()
        env['HOME'] = codex_home

        # First logout any existing session
        subprocess.run([codex_cli, 'logout'], env=env, capture_output=True, timeout=10)

        # Now run login - this will open a browser
        result = subprocess.run(
            [codex_cli, 'login'],
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
        if self._active_coding_provider:
            self._active_coding_provider.stop_session()
            self._active_coding_provider = None
        self._active_coding_config = None
        return "🛑 Task cancelled"

    def start_chad_task(  # noqa: C901
        self,
        project_path: str,
        task_description: str,
        coding_agent: str,
        verification_agent: str = "(Same as Coding Agent)",
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
    ) -> Iterator[tuple[
        list, str, gr.Markdown, gr.Textbox, gr.TextArea, gr.Button, gr.Button, gr.Markdown,
        gr.update, gr.Row, gr.Button
    ]]:
        """Start Chad task and stream updates with optional verification."""
        chat_history = []
        message_queue = queue.Queue()
        self.cancel_requested = False
        session_log_path: Path | None = None
        self._active_coding_config = None

        def make_yield(
            history,
            status: str,
            live_stream: str = "",
            summary: str | None = None,
            interactive: bool = False,
            show_followup: bool = False
        ):
            """Format output tuple for Gradio with current UI state."""
            display_stream = live_stream
            is_error = '❌' in status
            display_role_status = self.format_role_status()
            log_btn_update = gr.update(
                label=f"📄 {session_log_path.name}" if session_log_path else "Session Log",
                value=str(session_log_path) if session_log_path else None,
                visible=session_log_path is not None
            )
            display_history = history
            if history and isinstance(history[0], dict):
                content = history[0].get("content", "")
                if isinstance(content, str) and content.startswith("**Task**"):
                    display_history = history[1:]
            return (
                display_history,
                display_stream,
                gr.update(value=status if is_error else "", visible=is_error),
                gr.update(value=project_path, interactive=interactive),
                gr.update(value=task_description, interactive=interactive),
                gr.update(interactive=interactive),
                gr.update(interactive=not interactive),
                gr.update(value=display_role_status),
                log_btn_update,
                gr.update(value=""),  # Clear followup input
                gr.update(visible=show_followup),  # Show/hide followup row
                gr.update(interactive=show_followup)  # Enable/disable send button
            )

        try:
            if not project_path or not task_description:
                error_msg = "❌ Please provide both project path and task description"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            path_obj = Path(project_path).expanduser().resolve()
            if not path_obj.exists() or not path_obj.is_dir():
                error_msg = f"❌ Invalid project path: {project_path}"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            if not coding_agent:
                msg = "❌ Please select a Coding Agent above"
                yield make_yield([], msg, summary=msg, interactive=True)
                return

            accounts = self.security_mgr.list_accounts()
            if coding_agent not in accounts:
                msg = f"❌ Coding agent '{coding_agent}' not found"
                yield make_yield([], msg, summary=msg, interactive=True)
                return

            coding_account = coding_agent
            coding_provider = accounts[coding_account]
            self.security_mgr.assign_role(coding_account, "CODING")

            selected_model = coding_model or self.security_mgr.get_account_model(coding_account) or "default"
            selected_reasoning = (
                coding_reasoning or self.security_mgr.get_account_reasoning(coding_account) or "default"
            )

            try:
                self.security_mgr.set_account_model(coding_account, selected_model)
            except Exception:
                pass
            try:
                self.security_mgr.set_account_reasoning(coding_account, selected_reasoning)
            except Exception:
                pass

            coding_config = ModelConfig(
                provider=coding_provider,
                model_name=selected_model,
                account_name=coding_account,
                reasoning_effort=None if selected_reasoning == 'default' else selected_reasoning
            )

            coding_timeout = DEFAULT_CODING_TIMEOUT

            session_log_path = self.current_session_log_path or self.session_logger.precreate_log()
            self.current_session_log_path = session_log_path
            self.session_logger.initialize_log(
                session_log_path,
                task_description=task_description,
                project_path=str(path_obj),
                coding_account=coding_account,
                coding_provider=coding_provider,
            )

            status_prefix = "**Starting Chad...**\n\n"
            status_prefix += f"• Project: {path_obj}\n"
            status_prefix += f"• CODING: {coding_account} ({coding_provider})\n"
            if selected_model and selected_model != "default":
                status_prefix += f"• Model: {selected_model}\n"
            if selected_reasoning and selected_reasoning != "default":
                status_prefix += f"• Reasoning: {selected_reasoning}\n"
            status_prefix += "• Mode: Direct (coding AI only)\n\n"

            chat_history.append({
                "role": "user",
                "content": f"**Task**\n\n{task_description}"
            })
            self.session_logger.update_log(session_log_path, chat_history)

            initial_status = f"{status_prefix}⏳ Initializing session..."
            yield make_yield(chat_history, initial_status, summary=initial_status, interactive=False)

            def format_tool_activity(detail: str) -> str:
                if ': ' in detail:
                    tool_name, args = detail.split(': ', 1)
                    return f"● {tool_name}({args})"
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

            coding_provider_instance = create_provider(coding_config)
            self._active_coding_provider = coding_provider_instance
            coding_provider_instance.set_activity_callback(on_activity)

            # Read project documentation (AGENTS.md, CLAUDE.md, etc.)
            project_docs = self._read_project_docs(path_obj)

            if not coding_provider_instance.start_session(str(path_obj), None):
                failure = f"{status_prefix}❌ Failed to start coding session"
                self._active_coding_provider = None
                self._active_coding_config = None
                yield make_yield([], failure, summary=failure, interactive=True)
                return

            status_msg = f"{status_prefix}✓ Coding AI started\n\n⏳ Processing task..."
            yield make_yield([], status_msg, summary=status_msg, interactive=False)

            # Build the complete prompt with project docs + workflow + task
            full_prompt = build_coding_prompt(task_description, project_docs)
            coding_provider_instance.send_message(full_prompt)

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
                except Exception as exc:  # pragma: no cover - runtime safety
                    message_queue.put(('status', f"❌ Error: {str(exc)}"))
                    completion_reason[0] = str(exc)
                    # Stop session on error
                    coding_provider_instance.stop_session()
                    self._active_coding_provider = None
                    self._session_active = False
                finally:
                    # Keep session alive for follow-ups if provider supports multi-turn
                    # and task succeeded
                    if not coding_provider_instance.supports_multi_turn() or not task_success[0]:
                        coding_provider_instance.stop_session()
                        self._active_coding_provider = None
                        self._session_active = False
                    else:
                        # Keep session alive for follow-ups
                        self._session_active = True
                    relay_complete.set()

            relay_thread = threading.Thread(target=direct_loop, daemon=True)
            relay_thread.start()

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
            pending_message_idx = None

            def get_display_content() -> str:
                if not full_history:
                    return ""
                combined = []
                for _, chunk in full_history:
                    combined.append(chunk)
                full_content = ''.join(combined)
                if len(full_content) > 50000:
                    return full_content[-50000:]
                return full_content

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
                        placeholder = {
                            "role": "assistant",
                            "content": f"**{speaker}**\n\n⏳ *Working...*"
                        }
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

                    elif msg_type == 'ai_switch':
                        current_ai = msg[1]
                        streaming_buffer = ""
                        full_history.append((current_ai, "Processing request\n"))

                    elif msg_type == 'stream':
                        chunk = msg[1]
                        if chunk.strip():
                            streaming_buffer += chunk
                            full_history.append((current_ai, chunk))
                            now = time_module.time()
                            if now - last_yield_time >= min_yield_interval:
                                display_content = get_display_content()
                                current_live_stream = build_live_stream_html(
                                    display_content, current_ai
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
                                current_live_stream = build_live_stream_html(
                                    content, current_ai
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
                            current_live_stream = build_live_stream_html(
                                display_content, current_ai
                            )
                        elif last_activity:
                            current_live_stream = f"**Live:** {last_activity}"
                        yield make_yield(
                            chat_history, current_status, current_live_stream
                        )
                        last_yield_time = now

            if self.cancel_requested:
                for idx in range(len(chat_history) - 1, -1, -1):
                    msg = chat_history[idx]
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        chat_history[idx] = {
                            "role": "assistant",
                            "content": "**CODING AI**\n\n🛑 *Cancelled*"
                        }
                        break
                self._session_active = False
                yield make_yield(chat_history, "🛑 Task cancelled", "", summary="🛑 Task cancelled", show_followup=False)
            else:
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

            # Track the active configuration only when the session can continue
            self._active_coding_config = coding_config if self._session_active else None

            # Determine the verification account to use
            actual_verification_account = (
                coding_account if verification_agent == self.SAME_AS_CODING else verification_agent
            )

            if self.cancel_requested:
                final_status = "🛑 Task cancelled by user"
                chat_history.append({
                    "role": "user",
                    "content": "───────────── 🛑 TASK CANCELLED ─────────────"
                })
            elif task_success[0]:
                # Get the last coding output for verification
                last_coding_output = completion_reason[0] or ""
                # Also get any accumulated text from the coding agent
                if full_history:
                    last_coding_output = ''.join(chunk for _, chunk in full_history[-50:])

                # Run verification loop
                max_verification_attempts = 3
                verification_attempt = 0
                verified = False
                verification_feedback = ""

                while not verified and verification_attempt < max_verification_attempts and not self.cancel_requested:
                    verification_attempt += 1

                    # Show verification status
                    verify_status = (
                        f"{status_prefix}🔍 Running verification "
                        f"(attempt {verification_attempt}/{max_verification_attempts})..."
                    )
                    yield make_yield(chat_history, verify_status, "")

                    # Add verification message to chat
                    chat_history.append({
                        "role": "user",
                        "content": f"───────────── 🔍 VERIFICATION (Attempt {verification_attempt}) ─────────────"
                    })

                    # Run verification
                    def verification_activity(activity_type: str, detail: str):
                        content = detail if activity_type == 'stream' else f"[{activity_type}] {detail}\n"
                        message_queue.put(('stream', content))

                    verified, verification_feedback = self._run_verification(
                        str(path_obj),
                        last_coding_output,
                        actual_verification_account,
                        on_activity=verification_activity,
                        timeout=300.0
                    )

                    # Add verification result to chat
                    if verified is None:
                        # Verification error - show error and stop
                        chat_history.append({
                            "role": "assistant",
                            "content": f"**VERIFICATION AI**\n\n❌ {verification_feedback}"
                        })
                        chat_history.append({
                            "role": "user",
                            "content": "───────────── ❌ VERIFICATION ERROR ─────────────"
                        })
                        break
                    elif verified:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                        chat_history.append({
                            "role": "user",
                            "content": "───────────── ✅ VERIFICATION PASSED ─────────────"
                        })
                    else:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))

                        # If not verified and session is still active, send feedback to coding agent
                        can_revise = (
                            self._session_active
                            and coding_provider_instance.is_alive()
                            and verification_attempt < max_verification_attempts
                        )
                        if can_revise:
                            revision_content = (
                                "───────────── 🔄 REVISION REQUESTED ─────────────\n\n"
                                "*Sending verification feedback to coding agent...*"
                            )
                            chat_history.append({
                                "role": "user",
                                "content": revision_content
                            })
                            revision_status = f"{status_prefix}🔄 Sending revision request to coding agent..."
                            yield make_yield(chat_history, revision_status, "")

                            # Send feedback to coding agent via session continuation
                            revision_request = (
                                "The verification agent found issues with your work. "
                                "Please address them:\n\n"
                                f"{verification_feedback}\n\n"
                                "Please fix these issues and confirm when done."
                            )

                            # Add placeholder for coding agent response
                            chat_history.append({
                                "role": "assistant",
                                "content": "**CODING AI**\n\n⏳ *Working on revisions...*"
                            })
                            revision_pending_idx = len(chat_history) - 1
                            yield make_yield(chat_history, f"{status_prefix}⏳ Coding agent working on revisions...", "")

                            # Send the revision request
                            coding_provider_instance.send_message(revision_request)
                            revision_response = coding_provider_instance.get_response(timeout=coding_timeout)

                            if revision_response:
                                parsed_revision = parse_codex_output(revision_response)
                                chat_history[revision_pending_idx] = make_chat_message("CODING AI", parsed_revision)
                                last_coding_output = parsed_revision
                            else:
                                chat_history[revision_pending_idx] = {
                                    "role": "assistant",
                                    "content": "**CODING AI**\n\n❌ *No response to revision request*"
                                }
                                break

                            yield make_yield(chat_history, f"{status_prefix}✓ Revision complete, re-verifying...", "")
                        else:
                            # Can't continue - session not active or max attempts reached
                            break

                    self.session_logger.update_log(session_log_path, chat_history)

                if verified is True:
                    final_status = "✓ Task completed and verified!"
                    completion_msg = "───────────── ✅ TASK COMPLETED (VERIFIED) ─────────────"
                    chat_history.append({
                        "role": "user",
                        "content": completion_msg
                    })
                elif verified is None:
                    # Verification errored - already added error message above
                    final_status = "❌ Task completed but verification errored"
                else:
                    final_status = (
                        f"⚠️ Task completed but verification failed "
                        f"after {verification_attempt} attempt(s)"
                    )
                    completion_msg = "───────────── ⚠️ TASK COMPLETED (UNVERIFIED) ─────────────"
                    if verification_feedback:
                        if len(verification_feedback) > 200:
                            completion_msg += f"\n\n*{verification_feedback[:200]}...*"
                        else:
                            completion_msg += f"\n\n*{verification_feedback}*"
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
                failure_msg = "───────────── ❌ TASK FAILED ─────────────"
                if completion_reason[0]:
                    failure_msg += f"\n\n*{completion_reason[0]}*"
                chat_history.append({
                    "role": "user",
                    "content": failure_msg
                })

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

            # Store session state for follow-up messages
            self._current_chat_history = chat_history
            self._current_project_path = project_path
            self._current_coding_account = coding_account

            # Show follow-up input if session can continue (Claude with successful task)
            can_continue = self._session_active and task_success[0]
            if can_continue:
                final_status += "\n\n*Session active - you can send follow-up messages*"
                final_summary = f"{status_prefix}{final_status}"

            yield make_yield(
                chat_history, final_summary, "", summary=final_summary,
                interactive=True, show_followup=can_continue
            )

        except Exception as e:  # pragma: no cover - defensive
            import traceback
            error_msg = f"❌ Error: {str(e)}\n\n```\n{traceback.format_exc()}\n```"
            self._session_active = False
            self._active_coding_provider = None
            self._active_coding_config = None
            yield make_yield(chat_history, error_msg, summary=error_msg, interactive=True, show_followup=False)

    def send_followup(  # noqa: C901
        self,
        followup_message: str,
        current_history: list,
        coding_agent: str = "",
        verification_agent: str = "",
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
    ) -> Iterator[tuple[list, str, gr.update, gr.update, gr.update]]:
        """Send a follow-up message, with optional provider handoff and verification.

        Args:
            followup_message: The follow-up message to send
            current_history: Current chat history from the UI
            coding_agent: Currently selected coding agent from dropdown
            verification_agent: Currently selected verification agent from dropdown
            coding_model: Preferred model selected in the Run tab
            coding_reasoning: Reasoning effort selected in the Run tab

        Yields:
            Tuples of (chat_history, live_stream, followup_input, followup_row, send_btn)
        """
        message_queue = queue.Queue()
        self.cancel_requested = False

        # Use stored history as base, but prefer current_history if it has more messages
        chat_history = current_history if len(current_history) >= len(self._current_chat_history) else self._current_chat_history.copy()  # noqa: E501

        def make_followup_yield(history, live_stream: str = "", show_followup: bool = True, working: bool = False):
            """Format output for follow-up responses."""
            return (
                history,
                live_stream,
                gr.update(value="" if not working else followup_message),  # Clear input when not working
                gr.update(visible=show_followup),  # Follow-up row visibility
                gr.update(interactive=not working)  # Send button interactivity
            )

        if not followup_message or not followup_message.strip():
            yield make_followup_yield(chat_history, "", show_followup=True)
            return

        accounts = self.security_mgr.list_accounts()
        has_account = bool(coding_agent and coding_agent in accounts)

        def normalize_model_value(value: str | None) -> str:
            return value if value else "default"

        def normalize_reasoning_value(value: str | None) -> str:
            return value if value else "default"

        requested_model = normalize_model_value(
            coding_model if coding_model is not None else (
                self.security_mgr.get_account_model(coding_agent) if has_account else "default"
            )
        )
        requested_reasoning = normalize_reasoning_value(
            coding_reasoning if coding_reasoning is not None else (
                self.security_mgr.get_account_reasoning(coding_agent) if has_account else "default"
            )
        )

        if has_account:
            try:
                self.security_mgr.set_account_model(coding_agent, requested_model)
                self.security_mgr.set_account_reasoning(coding_agent, requested_reasoning)
            except Exception:
                pass

        if not self._session_active:
            self._active_coding_config = None

        # Check if we need provider handoff
        provider_changed = (
            has_account
            and coding_agent != self._current_coding_account
        )

        active_model = normalize_model_value(
            self._active_coding_config.model_name if self._active_coding_config else None
        )
        active_reasoning = normalize_reasoning_value(
            self._active_coding_config.reasoning_effort if self._active_coding_config else None
        )
        pref_changed = (
            has_account
            and not provider_changed
            and self._session_active
            and self._current_coding_account == coding_agent
            and (
                active_model != requested_model
                or active_reasoning != requested_reasoning
            )
        )

        handoff_needed = provider_changed or pref_changed

        if handoff_needed:
            # Stop old session if active
            if self._active_coding_provider:
                try:
                    self._active_coding_provider.stop_session()
                except Exception:
                    pass
                self._active_coding_provider = None
                self._session_active = False

            # Start new provider
            coding_provider_type = accounts[coding_agent]
            coding_config = ModelConfig(
                provider=coding_provider_type,
                model_name=requested_model,
                account_name=coding_agent,
                reasoning_effort=None if requested_reasoning == 'default' else requested_reasoning
            )

            handoff_detail = f"{coding_agent} ({coding_provider_type}"
            if requested_model and requested_model != "default":
                handoff_detail += f", {requested_model}"
            if requested_reasoning and requested_reasoning != "default":
                handoff_detail += f", {requested_reasoning} reasoning"
            handoff_detail += ")"

            handoff_title = "PROVIDER HANDOFF" if provider_changed else "PREFERENCE UPDATE"
            handoff_msg = (
                f"───────────── 🔄 {handoff_title} ─────────────\n\n"
                f"*Switching to {handoff_detail}*"
            )
            chat_history.append({"role": "user", "content": handoff_msg})
            yield make_followup_yield(chat_history, "🔄 Switching providers...", working=True)

            new_provider = create_provider(coding_config)
            project_path = self._current_project_path or str(Path.cwd())

            if not new_provider.start_session(project_path, None):
                chat_history.append({
                    "role": "assistant",
                    "content": f"**CODING AI**\n\n❌ *Failed to start {coding_agent} session*"
                })
                self._active_coding_config = None
                yield make_followup_yield(chat_history, "", show_followup=False)
                return

            self._active_coding_provider = new_provider
            self._current_coding_account = coding_agent
            self._session_active = True
            self._active_coding_config = coding_config

            # Include conversation context for the new provider
            context_summary = self._build_handoff_context(chat_history)
            followup_message = f"{context_summary}\n\n# Follow-up Request\n\n{followup_message}"

        if not self._session_active or not self._active_coding_provider:
            chat_history.append({
                "role": "user",
                "content": f"**Follow-up**\n\n{followup_message}"
            })
            chat_history.append({
                "role": "assistant",
                "content": "**CODING AI**\n\n❌ *Session expired. Please start a new task.*"
            })
            self._session_active = False
            yield make_followup_yield(chat_history, "", show_followup=False)
            return

        # Add user's follow-up message to history
        if provider_changed:
            # Extract just the user's actual message after handoff context
            display_msg = followup_message.split('# Follow-up Request')[-1].strip()
            user_content = f"**Follow-up** (via {coding_agent})\n\n{display_msg}"
        else:
            user_content = f"**Follow-up**\n\n{followup_message}"
        chat_history.append({"role": "user", "content": user_content})

        # Add placeholder for AI response
        chat_history.append({
            "role": "assistant",
            "content": "**CODING AI**\n\n⏳ *Working...*"
        })
        pending_idx = len(chat_history) - 1

        yield make_followup_yield(chat_history, "⏳ Processing follow-up...", working=True)

        # Set up activity callback
        def on_activity(activity_type: str, detail: str):
            if activity_type == 'stream':
                message_queue.put(('stream', detail))
            elif activity_type == 'tool':
                message_queue.put(('activity', f"● {detail}"))
            elif activity_type == 'text' and detail:
                message_queue.put(('activity', f"  ⎿ {detail[:80]}"))

        coding_provider = self._active_coding_provider
        coding_provider.set_activity_callback(on_activity)

        # Send message and wait for response in background
        relay_complete = threading.Event()
        response_holder = [None]
        error_holder = [None]

        def relay_loop():
            try:
                coding_provider.send_message(followup_message)
                response = coding_provider.get_response(timeout=DEFAULT_CODING_TIMEOUT)
                response_holder[0] = response
            except Exception as e:
                error_holder[0] = str(e)
            finally:
                relay_complete.set()

        relay_thread = threading.Thread(target=relay_loop, daemon=True)
        relay_thread.start()

        # Stream updates while waiting
        import time as time_module
        full_history = []
        last_yield_time = 0.0
        min_yield_interval = 0.05

        while not relay_complete.is_set() and not self.cancel_requested:
            try:
                msg = message_queue.get(timeout=0.02)
                msg_type = msg[0]

                if msg_type == 'stream':
                    chunk = msg[1]
                    if chunk.strip():
                        full_history.append(chunk)
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            display_content = ''.join(full_history)
                            if len(display_content) > 50000:
                                display_content = display_content[-50000:]
                            live_stream = build_live_stream_html(display_content)
                            yield make_followup_yield(chat_history, live_stream, working=True)
                            last_yield_time = now

                elif msg_type == 'activity':
                    now = time_module.time()
                    if now - last_yield_time >= min_yield_interval:
                        display_content = ''.join(full_history)
                        if display_content:
                            content = display_content + f"\n\n{msg[1]}"
                            live_stream = build_live_stream_html(content)
                        else:
                            live_stream = f"**Live:** {msg[1]}"
                        yield make_followup_yield(chat_history, live_stream, working=True)
                        last_yield_time = now

            except queue.Empty:
                now = time_module.time()
                if now - last_yield_time >= 0.3:
                    display_content = ''.join(full_history)
                    if display_content:
                        live_stream = build_live_stream_html(display_content)
                        yield make_followup_yield(chat_history, live_stream, working=True)
                        last_yield_time = now

        relay_thread.join(timeout=1)

        # Update chat history with final response
        if error_holder[0]:
            chat_history[pending_idx] = {
                "role": "assistant",
                "content": f"**CODING AI**\n\n❌ *Error: {error_holder[0]}*"
            }
            self._session_active = False
            self._active_coding_provider = None
            self._active_coding_config = None
            self._update_session_log(chat_history, full_history)
            yield make_followup_yield(chat_history, "", show_followup=False)
            return

        if not response_holder[0]:
            chat_history[pending_idx] = {
                "role": "assistant",
                "content": "**CODING AI**\n\n❌ *No response received*"
            }
            self._update_session_log(chat_history, full_history)
            yield make_followup_yield(chat_history, "", show_followup=True)
            return

        parsed = parse_codex_output(response_holder[0])
        chat_history[pending_idx] = make_chat_message("CODING AI", parsed)
        last_coding_output = parsed

        # Update stored history
        self._current_chat_history = chat_history
        self._update_session_log(chat_history, full_history)

        yield make_followup_yield(chat_history, "", show_followup=True, working=True)

        # Run verification on follow-up
        actual_verification_account = (
            self._current_coding_account
            if verification_agent == self.SAME_AS_CODING
            else verification_agent
        )

        if actual_verification_account and actual_verification_account in accounts:
            # Verification loop (like start_chad_task)
            max_verification_attempts = 3
            verification_attempt = 0
            verified = False

            while not verified and verification_attempt < max_verification_attempts and not self.cancel_requested:
                verification_attempt += 1
                verify_status = (
                    f"🔍 Running verification "
                    f"(attempt {verification_attempt}/{max_verification_attempts})..."
                )
                yield make_followup_yield(chat_history, verify_status, working=True)

                chat_history.append({
                    "role": "user",
                    "content": f"───────────── 🔍 VERIFICATION (Attempt {verification_attempt}) ─────────────"
                })

                def verification_activity(activity_type: str, detail: str):
                    pass  # Quiet verification

                verified, verification_feedback = self._run_verification(
                    self._current_project_path or str(Path.cwd()),
                    last_coding_output,
                    actual_verification_account,
                    on_activity=verification_activity,
                    timeout=300.0
                )

                if verified is None:
                    # Verification error - stop
                    chat_history.append({
                        "role": "assistant",
                        "content": f"**VERIFICATION AI**\n\n❌ {verification_feedback}"
                    })
                    chat_history.append({
                        "role": "user",
                        "content": "───────────── ❌ VERIFICATION ERROR ─────────────"
                    })
                    self._update_session_log(chat_history, full_history)
                    break
                elif verified:
                    chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                    chat_history.append({
                        "role": "user",
                        "content": "───────────── ✅ VERIFICATION PASSED ─────────────"
                    })
                else:
                    chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))

                    # Check if we can revise
                    can_revise = (
                        self._session_active
                        and coding_provider.is_alive()
                        and verification_attempt < max_verification_attempts
                    )
                    if can_revise:
                        chat_history.append({
                            "role": "user",
                            "content": "───────────── 🔄 REVISION REQUESTED ─────────────"
                        })
                        chat_history.append({
                            "role": "assistant",
                            "content": "**CODING AI**\n\n⏳ *Working on revisions...*"
                        })
                        revision_idx = len(chat_history) - 1
                        yield make_followup_yield(chat_history, "🔄 Revision in progress...", working=True)

                        revision_request = (
                            "The verification agent found issues with your work. "
                            "Please address them:\n\n"
                            f"{verification_feedback}\n\n"
                            "Please fix these issues and confirm when done."
                        )
                        coding_provider.send_message(revision_request)
                        revision_response = coding_provider.get_response(timeout=DEFAULT_CODING_TIMEOUT)

                        if revision_response:
                            parsed_revision = parse_codex_output(revision_response)
                            chat_history[revision_idx] = make_chat_message("CODING AI", parsed_revision)
                            last_coding_output = parsed_revision
                        else:
                            chat_history[revision_idx] = {
                                "role": "assistant",
                                "content": "**CODING AI**\n\n❌ *No response to revision request*"
                            }
                            self._update_session_log(chat_history, full_history)
                            break

                        yield make_followup_yield(chat_history, "✓ Revision complete, re-verifying...", working=True)
                    else:
                        # Can't continue - add failure message
                        chat_history.append({
                            "role": "user",
                            "content": "───────────── ❌ VERIFICATION FAILED ─────────────"
                        })
                        self._update_session_log(chat_history, full_history)
                        break

                # Incremental session log update after each verification attempt
                self._update_session_log(chat_history, full_history)

        # Always update stored history and session log after follow-up completes
        self._current_chat_history = chat_history
        self._update_session_log(chat_history, full_history)

        yield make_followup_yield(chat_history, "", show_followup=True)

    def _build_handoff_context(self, chat_history: list) -> str:
        """Build a context summary for provider handoff.

        Args:
            chat_history: The current chat history

        Returns:
            A summary of the conversation for the new provider
        """
        # Extract key messages from history
        context_parts = ["# Previous Conversation Summary\n"]

        for msg in chat_history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Skip dividers and status messages
            if "─────" in content:
                continue

            if role == "user" and content.startswith("**Task**"):
                context_parts.append(f"**Original Task:**\n{content.replace('**Task**', '').strip()}\n")
            elif role == "assistant" and "CODING AI" in content:
                # Summarize the response (first 500 chars)
                summary = content.replace("**CODING AI**", "").strip()[:500]
                if len(summary) == 500:
                    summary += "..."
                context_parts.append(f"**Previous Response (summary):**\n{summary}\n")

        return "\n".join(context_parts)

    def _update_session_log(self, chat_history: list, streaming_history: list = None):
        """Update the session log with current state.

        Args:
            chat_history: Current chat history
            streaming_history: Optional streaming transcript chunks
        """
        if self.current_session_log_path:
            streaming_transcript = ''.join(streaming_history) if streaming_history else None
            self.session_logger.update_log(
                self.current_session_log_path,
                chat_history,
                streaming_transcript=streaming_transcript,
                status="continued"
            )

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
                    # Check initial role configuration
                    is_ready, _ = self.get_role_config_status()
                    config_status = self.format_role_status()

                    # Allow override via env var (for screenshots)
                    default_path = os.environ.get('CHAD_PROJECT_PATH', str(Path.cwd()))

                    accounts_map = self.security_mgr.list_accounts()
                    account_choices = list(accounts_map.keys())
                    role_assignments = self.security_mgr.list_role_assignments()
                    initial_coding = role_assignments.get("CODING", "")

                    # Verification agent dropdown choices: "(Same as Coding Agent)" + all accounts
                    verification_choices = [self.SAME_AS_CODING] + account_choices
                    stored_verification = self.security_mgr.get_verification_agent()
                    initial_verification = (
                        stored_verification if stored_verification in account_choices else self.SAME_AS_CODING
                    )

                    def get_preference_state(account: str, persist: bool = False):
                        """Return provider, choices, and selected values for coding preferences."""
                        accounts_state = self.security_mgr.list_accounts()
                        provider_type = accounts_state.get(account, "")
                        model_choices = self.get_models_for_account(account) if account else ["default"]
                        if not model_choices:
                            model_choices = ["default"]
                        stored_model = self.security_mgr.get_account_model(account) if account else "default"
                        model_value = stored_model if stored_model in model_choices else model_choices[0]
                        reasoning_choices = self.get_reasoning_choices(provider_type, account) if provider_type else ["default"]  # noqa: E501
                        if not reasoning_choices:
                            reasoning_choices = ["default"]
                        stored_reasoning = self.security_mgr.get_account_reasoning(account) if account else "default"
                        reasoning_value = (
                            stored_reasoning if stored_reasoning in reasoning_choices else reasoning_choices[0]
                        )

                        if persist and account:
                            try:
                                self.security_mgr.set_account_model(account, model_value)
                                self.security_mgr.set_account_reasoning(account, reasoning_value)
                            except Exception:
                                pass

                        return provider_type, model_choices, model_value, reasoning_choices, reasoning_value

                    (
                        _initial_provider_type,
                        initial_model_choices,
                        initial_model_value,
                        initial_reasoning_choices,
                        initial_reasoning_value
                    ) = get_preference_state(initial_coding, persist=True)

                    with gr.Row(elem_id="run-top-row", equal_height=True):
                        with gr.Column(elem_id="run-top-main", scale=1):
                            with gr.Row(elem_id="run-top-inputs", equal_height=True):
                                with gr.Column(scale=3, min_width=260):
                                    project_path = gr.Textbox(
                                        label="Project Path",
                                        placeholder="/path/to/project",
                                        value=default_path,
                                        scale=3
                                    )
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
                                            min_width=140,
                                            elem_id="session-log-btn"
                                        )
                                with gr.Column(scale=1, min_width=200):
                                    coding_agent_dropdown = gr.Dropdown(
                                        choices=account_choices,
                                        value=initial_coding if initial_coding in account_choices else None,
                                        label="Coding Agent",
                                        scale=1,
                                        min_width=200
                                    )
                                    coding_model_dropdown = gr.Dropdown(
                                        choices=initial_model_choices,
                                        value=initial_model_value,
                                        label="Preferred Model",
                                        allow_custom_value=True,
                                        scale=1,
                                        min_width=200,
                                        interactive=bool(initial_coding and initial_coding in account_choices)
                                    )
                                with gr.Column(scale=1, min_width=200):
                                    verification_agent_dropdown = gr.Dropdown(
                                        choices=verification_choices,
                                        value=initial_verification,
                                        label="Verification Agent",
                                        scale=1,
                                        min_width=200
                                    )
                                    coding_reasoning_dropdown = gr.Dropdown(
                                        choices=initial_reasoning_choices,
                                        value=initial_reasoning_value,
                                        label="Reasoning Effort",
                                        allow_custom_value=True,
                                        scale=1,
                                        min_width=200,
                                        interactive=bool(initial_coding and initial_coding in account_choices)
                                    )
                        cancel_btn = gr.Button(
                            "🛑 Cancel",
                            variant="stop",
                            interactive=False,
                            elem_id="cancel-task-btn",
                            min_width=40,
                            scale=0
                        )

                    # Task status header (shows selected task description and status)
                    task_status_header = gr.Markdown("", elem_id="task-status-header", visible=False)

                    # Agent communication view
                    with gr.Row():
                        with gr.Column():
                            with gr.Column(elem_classes=["agent-panel"]):
                                gr.Markdown("### Agent Communication")
                                with gr.Column(elem_classes=["task-entry-bubble"]):
                                    with gr.Row(elem_classes=["task-entry-header"]):
                                        gr.Markdown("#### 🗒️ Enter Task")
                                    with gr.Column(elem_classes=["task-entry-body"]):
                                        task_description = gr.TextArea(
                                            label="Task Description",
                                            placeholder="Describe what you want done...",
                                            lines=5
                                        )
                                        with gr.Row(elem_classes=["task-entry-actions"]):
                                            start_btn = gr.Button(
                                                "Start Task",
                                                variant="primary",
                                                interactive=is_ready,
                                                elem_id="start-task-btn"
                                            )
                                # In screenshot mode, pre-populate with sample conversation
                                chat_value = None
                                if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
                                    from .screenshot_fixtures import CHAT_HISTORY
                                    chat_value = CHAT_HISTORY

                                chatbot = gr.Chatbot(
                                    value=chat_value,
                                    label="Agent Communication",
                                    show_label=False,
                                    height=400,
                                    elem_id="agent-chatbot",
                                    autoscroll=False
                                )

                                # Follow-up input (hidden initially, shown after task completion)
                                with gr.Row(visible=False, elem_id="followup-row") as followup_row:
                                    followup_input = gr.TextArea(
                                        label="Continue conversation...",
                                        placeholder="Ask for changes or additional work...",
                                        lines=2,
                                        elem_id="followup-input",
                                        scale=5
                                    )
                                    send_followup_btn = gr.Button(
                                        "Send ➤",
                                        variant="primary",
                                        elem_id="send-followup-btn",
                                        interactive=False,
                                        scale=1,
                                        min_width=80
                                    )

                    # Live activity stream - pre-populate in screenshot mode
                    live_content = ""
                    if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
                        from .screenshot_fixtures import LIVE_VIEW_CONTENT
                        live_content = LIVE_VIEW_CONTENT

                    live_stream_box = gr.Markdown(live_content, elem_id="live-stream-box", sanitize_html=False)

                    def compute_coding_updates(selected_account: str):
                        """Return updated coding controls for the selected account."""
                        _, model_choices, model_value, reasoning_choices, reasoning_value = get_preference_state(
                            selected_account, persist=True
                        )
                        accounts_state = self.security_mgr.list_accounts()
                        has_account = bool(selected_account and selected_account in accounts_state)
                        if has_account:
                            provider_type = accounts_state.get(selected_account, "")
                            status = f"✓ Ready — **Coding:** {selected_account} ({provider_type}"
                            if model_value != "default":
                                status += f", {model_value}"
                            if reasoning_value != "default":
                                status += f", {reasoning_value} reasoning"
                            status += ")"
                            start_update = gr.update(interactive=True)
                        else:
                            status = "⚠️ Please select a Coding Agent"
                            start_update = gr.update(interactive=False)

                        model_update = gr.update(
                            choices=model_choices,
                            value=model_value,
                            interactive=has_account
                        )
                        reasoning_update = gr.update(
                            choices=reasoning_choices,
                            value=reasoning_value,
                            interactive=has_account
                        )
                        return status, start_update, model_update, reasoning_update

                    def update_ready_status(account: str, model: str, reasoning: str):
                        """Persist preference changes and refresh status/start button."""
                        accounts_state = self.security_mgr.list_accounts()
                        has_account = bool(account and account in accounts_state)
                        if has_account:
                            model_value = model or "default"
                            reasoning_value = reasoning or "default"
                            try:
                                self.security_mgr.set_account_model(account, model_value)
                                self.security_mgr.set_account_reasoning(account, reasoning_value)
                            except Exception:
                                pass
                            provider_type = accounts_state.get(account, "")
                            status = f"✓ Ready — **Coding:** {account} ({provider_type}"
                            if model_value != "default":
                                status += f", {model_value}"
                            if reasoning_value != "default":
                                status += f", {reasoning_value} reasoning"
                            status += ")"
                            return status, gr.update(interactive=True)

                        return "⚠️ Please select a Coding Agent", gr.update(interactive=False)

                # Providers Tab (configuration + usage)
                with gr.Tab("⚙️ Providers"):
                    account_items = list(self.security_mgr.list_accounts().items())
                    # Allow room for new providers without needing a reload
                    self.provider_card_count = max(12, len(account_items) + 8)

                    provider_feedback = gr.Markdown("")
                    gr.Markdown("### Providers", elem_classes=["provider-section-title"])

                    provider_list = gr.Markdown(
                        self.list_providers(),
                        elem_id="provider-summary-panel",
                        elem_classes=["provider-summary"]
                    )
                    refresh_btn = gr.Button("🔄 Refresh", variant="secondary")
                    pending_delete_state = gr.State(None)  # Tracks which account is pending deletion

                    provider_cards = []
                    with gr.Row(equal_height=True, elem_classes=["provider-cards-row"]):
                        for idx in range(self.provider_card_count):
                            if idx < len(account_items):
                                account_name, provider_type = account_items[idx]
                                visible = True
                                header_text = (
                                    f'<span class="provider-card__header-text">'
                                    f'{account_name} ({provider_type})</span>'
                                )
                                usage_text = self.get_provider_usage(account_name)
                            else:
                                account_name = ""
                                visible = False
                                header_text = ""
                                usage_text = ""

                            # Always create columns visible - use CSS classes to show/hide
                            # gr.Column(visible=False) prevents proper rendering updates
                            card_group_classes = (
                                ["provider-card"] if visible else ["provider-card", "provider-card-empty"]
                            )
                            with gr.Column(visible=True, scale=1) as card_column:
                                card_elem_id = f"provider-card-{idx}"
                                with gr.Group(elem_id=card_elem_id, elem_classes=card_group_classes) as card_group:
                                    with gr.Row(elem_classes=["provider-card__header-row"]):
                                        card_header = gr.Markdown(header_text, elem_classes=["provider-card__header"])
                                        delete_btn = gr.Button(
                                            "🗑︎", variant="secondary", size="sm",
                                            min_width=0, scale=0, elem_classes=["provider-delete"]
                                        )
                                    account_state = gr.State(account_name)

                                    gr.Markdown("Usage", elem_classes=["provider-usage-title"])
                                    usage_box = gr.Markdown(usage_text, elem_classes=["provider-usage"])

                            provider_cards.append({
                                "column": card_column,
                                "group": card_group,  # Use group for visibility control
                                "header": card_header,
                                "account_state": account_state,
                                "account_name": account_name,  # Store name for delete handler
                                "usage_box": usage_box,
                                "delete_btn": delete_btn
                            })

                    with gr.Accordion(
                        "Add New Provider",
                        open=False,
                        elem_id="add-provider-panel",
                        elem_classes=["add-provider-accordion"]
                    ) as add_provider_accordion:
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
                            card["usage_box"],
                            card["delete_btn"]
                        ])

                    # Add role status and start button to outputs so they update when roles change
                    provider_outputs_with_task_status = provider_outputs + [
                        role_status,
                        start_btn,
                        coding_model_dropdown,
                        coding_reasoning_dropdown
                    ]

                    # Include task status and agent dropdowns in add_provider outputs so Run Task tab updates
                    add_provider_outputs = (
                        provider_outputs
                        + [
                            new_provider_name,
                            add_btn,
                            add_provider_accordion,
                            role_status,
                            start_btn,
                            coding_agent_dropdown,
                            verification_agent_dropdown,
                            coding_model_dropdown,
                            coding_reasoning_dropdown,
                        ]
                    )

                    def refresh_with_task_status(current_coding):
                        base = self._provider_action_response("")
                        status, start_update, model_update, reasoning_update = compute_coding_updates(current_coding)
                        return (*base, status, start_update, model_update, reasoning_update)

                    refresh_btn.click(
                        refresh_with_task_status,
                        inputs=[coding_agent_dropdown],
                        outputs=provider_outputs_with_task_status
                    )

                    new_provider_name.change(
                        lambda name: gr.update(interactive=bool(name.strip())),
                        inputs=[new_provider_name],
                        outputs=[add_btn]
                    )

                    def add_provider_with_task_status(provider_name, provider_type, current_coding):
                        """Add provider and also return updated task status and agent dropdown choices."""
                        base = self.add_provider(provider_name, provider_type)
                        # Get updated account choices for agent dropdowns
                        new_choices = list(self.security_mgr.list_accounts().keys())
                        new_verification_choices = [self.SAME_AS_CODING] + new_choices
                        selected_coding = current_coding if current_coding in new_choices else ""
                        status, start_update, model_update, reasoning_update = compute_coding_updates(selected_coding)
                        return (
                            *base,
                            status,
                            start_update,
                            gr.update(choices=new_choices, value=selected_coding or None),
                            gr.update(choices=new_verification_choices),
                            model_update,
                            reasoning_update
                        )

                    add_btn.click(
                        add_provider_with_task_status,
                        inputs=[new_provider_name, new_provider_type, coding_agent_dropdown],
                        outputs=add_provider_outputs
                    )

                    for card in provider_cards:
                        # Two-step delete using dynamic account_state (not captured name)
                        # This ensures handlers work correctly after cards shift due to deletions
                        def make_delete_handler():
                            def handler(pending_delete, current_account, current_coding):
                                # Skip if card has no account (empty slot)
                                if not current_account:
                                    new_choices = list(self.security_mgr.list_accounts().keys())
                                    new_verification_choices = [self.SAME_AS_CODING] + new_choices
                                    selected_coding = current_coding if current_coding in new_choices else ""
                                    status, start_update, model_update, reasoning_update = compute_coding_updates(
                                        selected_coding
                                    )
                                    return (
                                        pending_delete,
                                        *self._provider_action_response(""),
                                        gr.update(choices=new_choices, value=selected_coding or None),
                                        gr.update(choices=new_verification_choices),
                                        status,
                                        start_update,
                                        model_update,
                                        reasoning_update
                                    )

                                if pending_delete == current_account:
                                    # Second click - actually delete
                                    result = self.delete_provider(current_account, confirmed=True)
                                    pending_value = None
                                else:
                                    # First click - show confirmation button (tick icon)
                                    result = self._provider_action_response(
                                        f"Click the ✓ icon in '{current_account}' titlebar to confirm deletion",
                                        pending_delete=current_account
                                    )
                                    pending_value = current_account

                                new_choices = list(self.security_mgr.list_accounts().keys())
                                new_verification_choices = [self.SAME_AS_CODING] + new_choices
                                selected_coding = current_coding if current_coding in new_choices else ""
                                status, start_update, model_update, reasoning_update = compute_coding_updates(
                                    selected_coding
                                )
                                return (
                                    pending_value,
                                    *result,
                                    gr.update(choices=new_choices, value=selected_coding or None),
                                    gr.update(choices=new_verification_choices),
                                    status,
                                    start_update,
                                    model_update,
                                    reasoning_update
                                )
                            return handler

                        # Outputs include pending_delete_state + provider outputs + agent dropdowns and status
                        delete_outputs = (
                            [pending_delete_state]
                            + provider_outputs
                            + [
                                coding_agent_dropdown,
                                verification_agent_dropdown,
                                role_status,
                                start_btn,
                                coding_model_dropdown,
                                coding_reasoning_dropdown
                            ]
                        )

                        card["delete_btn"].click(
                            fn=make_delete_handler(),
                            inputs=[pending_delete_state, card["account_state"], coding_agent_dropdown],
                            outputs=delete_outputs
                        )

            # Connect task execution (outside tabs)
            start_btn.click(
                self.start_chad_task,
                inputs=[
                    project_path,
                    task_description,
                    coding_agent_dropdown,
                    verification_agent_dropdown,
                    coding_model_dropdown,
                    coding_reasoning_dropdown
                ],
                outputs=[chatbot, live_stream_box, task_status_header, project_path, task_description, start_btn, cancel_btn, role_status, session_log_btn, followup_input, followup_row, send_followup_btn]  # noqa: E501
            )

            cancel_btn.click(
                self.cancel_task,
                outputs=[live_stream_box]
            )

            # Connect follow-up message handling
            send_followup_btn.click(
                self.send_followup,
                inputs=[
                    followup_input,
                    chatbot,
                    coding_agent_dropdown,
                    verification_agent_dropdown,
                    coding_model_dropdown,
                    coding_reasoning_dropdown
                ],
                outputs=[chatbot, live_stream_box, followup_input, followup_row, send_followup_btn]
            )

            # Update role status and start button when agent dropdowns change
            def update_agent_selection(coding):
                """Update coding controls when the agent selection changes."""
                return compute_coding_updates(coding)

            coding_agent_dropdown.change(
                update_agent_selection,
                inputs=[coding_agent_dropdown],
                outputs=[role_status, start_btn, coding_model_dropdown, coding_reasoning_dropdown]
            )

            coding_model_dropdown.change(
                update_ready_status,
                inputs=[coding_agent_dropdown, coding_model_dropdown, coding_reasoning_dropdown],
                outputs=[role_status, start_btn]
            )

            coding_reasoning_dropdown.change(
                update_ready_status,
                inputs=[coding_agent_dropdown, coding_model_dropdown, coding_reasoning_dropdown],
                outputs=[role_status, start_btn]
            )

            # Handle verification agent dropdown changes
            def update_verification_selection(verification):
                """Persist verification agent selection when changed."""
                if verification == self.SAME_AS_CODING:
                    # Reset to default (use coding agent)
                    self.security_mgr.set_verification_agent(None)
                else:
                    # Persist the explicit selection
                    try:
                        self.security_mgr.set_verification_agent(verification)
                    except ValueError:
                        pass  # Account doesn't exist, ignore

            verification_agent_dropdown.change(
                update_verification_selection,
                inputs=[verification_agent_dropdown]
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

    requested_port = port
    port, ephemeral, conflicted = _resolve_port(port)
    open_browser = not (requested_port == 0 and ephemeral)
    if conflicted:
        print(f"Port {requested_port} already in use; launching on ephemeral port {port}")

    print("\n" + "=" * 70)
    print("CHAD WEB UI")
    print("=" * 70)
    if open_browser:
        print("Opening web interface in your browser...")
    print("Press Ctrl+C to stop the server")
    print("=" * 70 + "\n")

    # Print port marker for scripts to parse (before launch blocks)
    print(f"CHAD_PORT={port}", flush=True)

    app.launch(
        server_name="127.0.0.1",
        server_port=port,
        share=False,
        inbrowser=open_browser,  # Don't open browser for screenshot mode
        quiet=False
    )

    return None, port

"""Gradio web interface for Chad."""

import base64
import os
import json
import re
import socket
import threading
import queue
import uuid
import html
from datetime import datetime, UTC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import gradio as gr

from .provider_ui import ProviderUIManager
from .security import SecurityManager
from .session_logger import SessionLogger
from .providers import ModelConfig, parse_codex_output, create_provider
from .model_catalog import ModelCatalog
from .prompts import (
    build_coding_prompt,
    extract_coding_summary,
    get_verification_prompt,
    parse_verification_response,
    VerificationParseError,
)
from .git_worktree import GitWorktreeManager, MergeConflict, FileDiff
from .verification.ui_playwright_runner import cleanup_all_test_servers


@dataclass
class Session:
    """Per-session state for concurrent task execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "New Session"
    cancel_requested: bool = False
    active: bool = False
    provider: object = None
    config: object = None
    log_path: Path | None = None
    chat_history: list = field(default_factory=list)
    task_description: str | None = None
    project_path: str | None = None
    coding_account: str | None = None
    # Git worktree support
    worktree_path: Path | None = None
    worktree_branch: str | None = None
    worktree_base_commit: str | None = None  # Commit SHA worktree was created from
    has_worktree_changes: bool = False
    merge_conflicts: list[MergeConflict] | None = None


def _history_entry(agent: str, content: str) -> tuple[str, str, str]:
    """Create a streaming history entry with a timestamp."""
    return (agent, content, datetime.now(UTC).isoformat())


def _history_contents(history: list[tuple]) -> list[str]:
    """Extract textual content from streaming history entries."""
    contents: list[str] = []
    for entry in history or []:
        if isinstance(entry, tuple) and len(entry) >= 2:
            contents.append(entry[1])
        elif isinstance(entry, dict) and "content" in entry:
            contents.append(str(entry["content"]))
    return contents


@dataclass
class VerificationDropdownState:
    """Resolved state for verification model/reasoning dropdowns."""

    model_choices: list[str]
    model_value: str
    reasoning_choices: list[str]
    reasoning_value: str
    interactive: bool


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")

DEFAULT_CODING_TIMEOUT = 900.0
DEFAULT_VERIFICATION_TIMEOUT = 600.0
MAX_VERIFICATION_PROMPT_CHARS = 6000


def _find_free_port() -> int:
    """Bind to an ephemeral port and return it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
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
                s.bind(("127.0.0.1", port))
                return port, False, False
            except OSError:
                pass
    except PermissionError:
        fallback = port or _find_free_port()
        return fallback, port == 0 or fallback != port, False

    return _find_free_port(), True, True


# Custom styling for the provider management area to improve contrast between
# the summary header and each provider card.
# fmt: off
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

.start-task-btn,
.start-task-btn button {
  background: var(--task-btn-bg) !important;
  border: 1px solid var(--task-btn-border) !important;
  color: var(--task-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 32px !important;
  padding: 6px 12px !important;
}

.cancel-task-btn {
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

.cancel-task-btn button {
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

.cancel-task-btn:is(button) {
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

.cancel-task-btn button span,
.cancel-task-btn span {
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  opacity: 1 !important;
  padding: 0 !important;
  margin: 0 !important;
}

.cancel-task-btn button span *,
.cancel-task-btn span * {
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  opacity: 1 !important;
}

.cancel-task-btn button:disabled,
.cancel-task-btn button[disabled],
.cancel-task-btn button[aria-disabled="true"],
.cancel-task-btn button.disabled,
.cancel-task-btn:disabled,
.cancel-task-btn[disabled],
.cancel-task-btn[aria-disabled="true"],
.cancel-task-btn.disabled {
  background: var(--cancel-btn-bg) !important;
  border: 1px solid var(--cancel-btn-border) !important;
  color: var(--cancel-btn-text) !important;
  opacity: 1 !important;
  filter: none !important;
}

.start-task-btn:hover,
.start-task-btn button:hover {
  background: var(--task-btn-hover) !important;
}

.cancel-task-btn:hover,
.cancel-task-btn button:hover {
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
.provider-card__header-row .provider-card__header-text,
.provider-card .provider-card__header-row .provider-card__header-text-secondary,
.provider-card__header-row .provider-card__header-text-secondary {
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

/* Hide merge/conflict sections completely when merged/discarded */
.merge-section-hidden,
.conflict-section-hidden {
  display: none !important;
  visibility: hidden !important;
}

/* Ensure ALL children of hidden merge section are also hidden */
.merge-section-hidden *,
.conflict-section-hidden * {
  display: none !important;
  visibility: hidden !important;
}

/* Visually hidden but still in DOM for JS access */
.visually-hidden {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
  border: 0 !important;
}

/* Hide task status when empty (keeps element in DOM for JS detection) */
.task-status-header:empty {
  display: none !important;
}

/* Auto-hide provider cards with empty headers (fallback for when JavaScript doesn't work) */
.provider-card:has(.provider-card__header-text:empty),
.provider-card:has(.provider-card__header-text-secondary:empty),
.column:has(.provider-card__header-text:empty),
.column:has(.provider-card__header-text-secondary:empty) {
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

#live-stream-box,
.live-stream-box {
  margin-top: 8px;
  /* keep position relative for scroll indicator anchoring */
  position: relative;
}

#live-stream-box .live-output-header,
.live-stream-box .live-output-header {
  background: #2a2a3e;
  color: #a8d4ff;
  padding: 6px 12px;
  border-radius: 8px 8px 0 0;
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.05em;
  margin: 0;
}

#live-stream-box .live-output-content,
.live-stream-box .live-output-content {
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
#live-stream-box .live-output-content .diff-add,
.live-stream-box .live-output-content .diff-add {
  color: #98c379 !important;
  background: rgba(152, 195, 121, 0.1) !important;
}
#live-stream-box .live-output-content .diff-remove,
.live-stream-box .live-output-content .diff-remove {
  color: #e06c75 !important;
  background: rgba(224, 108, 117, 0.1) !important;
}
#live-stream-box .live-output-content .diff-header,
.live-stream-box .live-output-content .diff-header {
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
.live-stream-box h1,
.live-stream-box h2,
.live-stream-box h3,
.live-stream-box h4,
.live-stream-box h5,
.live-stream-box h6,
.live-stream-box .live-output-content h1,
.live-stream-box .live-output-content h2,
.live-stream-box .live-output-content h3,
.live-stream-box .live-output-content h4,
.live-stream-box .live-output-content h5,
.live-stream-box .live-output-content h6 {
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
#live-stream-box div:not([style*="color"]),
.live-stream-box .prose,
.live-stream-box .prose *:not([style*="color"]),
.live-stream-box .md,
.live-stream-box .md *:not([style*="color"]),
.live-stream-box p,
.live-stream-box span:not([style*="color"]),
.live-stream-box div:not([style*="color"]) {
  color: #e2e8f0 !important;
}

/* Ensure live-output-content has light text */
#live-stream-box .live-output-content,
.live-stream-box .live-output-content {
  color: #e2e8f0;
}

/* Children without inline colors or syntax classes also get light text */
#live-stream-box .live-output-content *:not([style*="color"]):not(.keyword):not(.string):not(.comment):not(.function)
:not(.class-name):not(.number):not(.operator):not(.variable):not(.type):not(.module):not(.builtin)
:not(.method):not(.property):not(.param):not(.constant):not(code),
.live-stream-box .live-output-content *:not([style*="color"]):not(.keyword):not(.string):not(.comment):not(.function)
:not(.class-name):not(.number):not(.operator):not(.variable):not(.type):not(.module):not(.builtin)
:not(.method):not(.property):not(.param):not(.constant):not(code) {
  color: #e2e8f0;
}

/* Code elements (rendered from backticks in Markdown) - bright pink */
#live-stream-box code,
#live-stream-box .live-output-content code,
#live-stream-box pre,
#live-stream-box .live-output-content pre,
.live-stream-box code,
.live-stream-box .live-output-content code,
.live-stream-box pre,
.live-stream-box .live-output-content pre {
  color: #f0abfc !important;
  background: none !important;
  padding: 0 !important;
  margin: 0 !important;
  border: 0 !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  font-family: inherit;
  font-weight: 600;
}

#live-stream-box pre,
#live-stream-box .live-output-content pre,
.live-stream-box pre,
.live-stream-box .live-output-content pre {
  white-space: pre-wrap !important;
  word-break: break-word !important;
}

/* Syntax highlighting for code blocks - matches common CLI tools */
#live-stream-box .live-output-content code .keyword,
#live-stream-box .live-output-content .keyword,
.live-stream-box .live-output-content code .keyword,
.live-stream-box .live-output-content .keyword {
  color: #c678dd !important; font-weight: 600;
}  /* Purple for keywords */
#live-stream-box .live-output-content code .string,
#live-stream-box .live-output-content .string,
.live-stream-box .live-output-content code .string,
.live-stream-box .live-output-content .string { color: #98c379 !important; }  /* Green for strings */
#live-stream-box .live-output-content code .comment,
#live-stream-box .live-output-content .comment,
.live-stream-box .live-output-content code .comment,
.live-stream-box .live-output-content .comment {
  color: #5c6370 !important; font-style: italic;
}  /* Grey for comments */
#live-stream-box .live-output-content code .function,
#live-stream-box .live-output-content .function,
.live-stream-box .live-output-content code .function,
.live-stream-box .live-output-content .function { color: #61afef !important; }  /* Blue for functions */
#live-stream-box .live-output-content code .class-name,
#live-stream-box .live-output-content .class-name,
.live-stream-box .live-output-content code .class-name,
.live-stream-box .live-output-content .class-name { color: #e5c07b !important; }  /* Yellow for classes */
#live-stream-box .live-output-content code .number,
#live-stream-box .live-output-content .number,
.live-stream-box .live-output-content code .number,
.live-stream-box .live-output-content .number { color: #d19a66 !important; }  /* Orange for numbers */
#live-stream-box .live-output-content code .operator,
#live-stream-box .live-output-content .operator,
.live-stream-box .live-output-content code .operator,
.live-stream-box .live-output-content .operator { color: #56b6c2 !important; }  /* Cyan for operators */
#live-stream-box .live-output-content code .variable,
#live-stream-box .live-output-content .variable,
.live-stream-box .live-output-content code .variable,
.live-stream-box .live-output-content .variable { color: #e06c75 !important; }  /* Red for variables */
#live-stream-box .live-output-content code .type,
#live-stream-box .live-output-content .type,
.live-stream-box .live-output-content code .type,
.live-stream-box .live-output-content .type { color: #e5c07b !important; }  /* Yellow for types */
#live-stream-box .live-output-content code .module,
#live-stream-box .live-output-content .module,
.live-stream-box .live-output-content code .module,
.live-stream-box .live-output-content .module { color: #61afef !important; }  /* Blue for modules */
#live-stream-box .live-output-content code .builtin,
#live-stream-box .live-output-content .builtin,
.live-stream-box .live-output-content code .builtin,
.live-stream-box .live-output-content .builtin { color: #56b6c2 !important; }  /* Cyan for builtins */
#live-stream-box .live-output-content code .method,
#live-stream-box .live-output-content .method,
.live-stream-box .live-output-content code .method,
.live-stream-box .live-output-content .method { color: #61afef !important; }  /* Blue for methods */
#live-stream-box .live-output-content code .property,
#live-stream-box .live-output-content .property,
.live-stream-box .live-output-content code .property,
.live-stream-box .live-output-content .property { color: #d19a66 !important; }  /* Orange for properties */
#live-stream-box .live-output-content code .param,
#live-stream-box .live-output-content .param,
.live-stream-box .live-output-content code .param,
.live-stream-box .live-output-content .param { color: #abb2bf !important; }  /* Light grey for params */
#live-stream-box .live-output-content code .constant,
#live-stream-box .live-output-content .constant,
.live-stream-box .live-output-content code .constant,
.live-stream-box .live-output-content .constant { color: #d19a66 !important; }  /* Orange for constants */

/* ANSI colored spans - let them keep their inline colors with brightness boost */
#live-stream-box .live-output-content span[style*="color"],
.live-stream-box .live-output-content span[style*="color"] {
  filter: brightness(1.3);
}

/* Override specific dark grey colors that are hard to read */
/* Handle various spacing formats: rgb(92, rgb(92,99 etc */
#live-stream-box .live-output-content span[style*="rgb(92"],
#live-stream-box .live-output-content span[style*="color:#5c6370"],
#live-stream-box .live-output-content span[style*="color: #5c6370"],
#live-stream-box .live-output-content span[style*="#5c6370"],
.live-stream-box .live-output-content span[style*="rgb(92"],
.live-stream-box .live-output-content span[style*="color:#5c6370"],
.live-stream-box .live-output-content span[style*="color: #5c6370"],
.live-stream-box .live-output-content span[style*="#5c6370"] {
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
#live-stream-box .live-output-content span[style*="color: rgb(9"],
.live-stream-box .live-output-content span[style*="color: rgb(1"],
.live-stream-box .live-output-content span[style*="color: rgb(2"],
.live-stream-box .live-output-content span[style*="color: rgb(3"],
.live-stream-box .live-output-content span[style*="color: rgb(4"],
.live-stream-box .live-output-content span[style*="color: rgb(5"],
.live-stream-box .live-output-content span[style*="color: rgb(6"],
.live-stream-box .live-output-content span[style*="color: rgb(7"],
.live-stream-box .live-output-content span[style*="color: rgb(8"],
.live-stream-box .live-output-content span[style*="color: rgb(9"] {
  filter: brightness(1.5) !important;
}

/* Scroll position indicator */
#live-stream-box .scroll-indicator,
.live-stream-box .scroll-indicator {
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
#live-stream-box .scroll-indicator:hover,
.live-stream-box .scroll-indicator:hover {
  background: rgba(97, 175, 239, 1);
}

/* Inline live content in chat bubbles */
#agent-chatbot .inline-live-header {
  background: #2a2a3e;
  color: #a8d4ff;
  padding: 4px 10px;
  border-radius: 6px 6px 0 0;
  font-weight: 600;
  font-size: 11px;
  letter-spacing: 0.05em;
  margin: 8px 0 0 0;
}
#agent-chatbot .inline-live-content {
  background: #1e1e2e !important;
  color: #e2e8f0 !important;
  border: 1px solid #555 !important;
  border-top: none !important;
  border-radius: 0 0 6px 6px !important;
  padding: 10px !important;
  margin: 0 !important;
  max-height: 350px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.4;
}
/* Diff highlighting in inline live content */
#agent-chatbot .inline-live-content .diff-add {
  color: #98c379 !important;
  background: rgba(152, 195, 121, 0.1) !important;
}
#agent-chatbot .inline-live-content .diff-remove {
  color: #e06c75 !important;
  background: rgba(224, 108, 117, 0.1) !important;
}
#agent-chatbot .inline-live-content .diff-header {
  color: #61afef !important;
  font-weight: bold;
}
/* Syntax highlighting in inline live content */
#agent-chatbot .inline-live-content .keyword { color: #c678dd !important; }
#agent-chatbot .inline-live-content .string { color: #98c379 !important; }
#agent-chatbot .inline-live-content .comment { color: #5c6370 !important; font-style: italic; }
#agent-chatbot .inline-live-content .function { color: #61afef !important; }
#agent-chatbot .inline-live-content .number { color: #d19a66 !important; }

/* Screenshot comparison in chat bubbles */
#agent-chatbot .screenshot-comparison {
  display: flex;
  gap: 12px;
  margin: 12px 0;
  flex-wrap: wrap;
}
#agent-chatbot .screenshot-panel {
  flex: 1 1 45%;
  min-width: 200px;
  max-width: 100%;
}
#agent-chatbot .screenshot-single {
  margin: 12px 0;
  max-width: 100%;
}
#agent-chatbot .screenshot-label {
  background: #2a2a3e;
  color: #a8d4ff;
  padding: 4px 10px;
  border-radius: 6px 6px 0 0;
  font-weight: 600;
  font-size: 11px;
  letter-spacing: 0.05em;
}
#agent-chatbot .screenshot-comparison img,
#agent-chatbot .screenshot-single img {
  width: 100%;
  height: auto;
  border: 1px solid #555;
  border-top: none;
  border-radius: 0 0 6px 6px;
  display: block;
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

#session-log-btn,
.session-log-btn {
  flex: 0 0 auto;  /* Don't grow, don't shrink, auto width based on content */
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  padding: 0 8px !important;
  min-height: unset !important;
  height: auto !important;
  white-space: nowrap !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 6px !important;
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

/* Session tab bar styling */
.session-tab-bar {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 4px !important;
  padding: 8px 0 !important;
  border-bottom: 2px solid var(--border-color-primary) !important;
  margin-bottom: 16px !important;
}

.session-tab-bar button {
  border-radius: 8px 8px 0 0 !important;
  border: 1px solid var(--border-color-primary) !important;
  border-bottom: none !important;
  padding: 8px 16px !important;
  margin-bottom: -2px !important;
  background: var(--background-fill-secondary) !important;
  color: var(--body-text-color) !important;
  font-weight: 500 !important;
  transition: background 0.15s ease !important;
}

.session-tab-bar button:hover {
  background: var(--background-fill-primary) !important;
}

.session-tab-bar button.selected,
.session-tab-bar button[data-selected="true"] {
  background: var(--background-fill-primary) !important;
  border-bottom: 2px solid var(--background-fill-primary) !important;
  font-weight: 600 !important;
}

.session-tab-bar .add-session-btn {
  background: transparent !important;
  border: 1px dashed var(--border-color-primary) !important;
  color: var(--body-text-color-subdued) !important;
  min-width: 40px !important;
}

.session-tab-bar .add-session-btn:hover {
  background: var(--background-fill-secondary) !important;
  border-style: solid !important;
}

/* Fix status text wrapping to prevent cancel button width changes */
.role-status-row {
  flex-wrap: nowrap !important;
  overflow: hidden !important;
}

.role-config-status {
  flex: 1 1 auto !important;
  min-width: 0 !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
}

.role-config-status p {
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
  margin: 0 !important;
}

/* Ensure cancel button has fixed width */
.cancel-task-btn {
  flex-shrink: 0 !important;
}

/* Merge section styling */
.accept-merge-btn,
.accept-merge-btn button {
  background: #22c55e !important;
  border: 1px solid #16a34a !important;
  color: white !important;
}

.accept-merge-btn:hover,
.accept-merge-btn button:hover {
  background: #16a34a !important;
}

/* Conflict viewer styling */
.conflict-viewer {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 0.85rem;
  border: 1px solid #3b4252;
  border-radius: 8px;
  overflow: hidden;
  margin: 12px 0;
}

.conflict-file {
  border-bottom: 1px solid #3b4252;
}

.conflict-file:last-child {
  border-bottom: none;
}

.conflict-file-header {
  background: #2e3440;
  padding: 8px 12px;
  margin: 0;
  font-size: 0.9rem;
  color: #88c0d0;
  border-bottom: 1px solid #3b4252;
}

.conflict-hunk {
  margin: 0;
  border-bottom: 1px solid #4c566a;
}

.conflict-hunk:last-child {
  border-bottom: none;
}

.conflict-context {
  padding: 4px 12px;
  background: #2e3440;
  color: #d8dee9;
}

.conflict-context pre {
  margin: 2px 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.conflict-comparison {
  display: flex;
}

.conflict-side {
  flex: 1;
  padding: 8px 12px;
  overflow-x: auto;
  min-width: 0;
}

.conflict-side pre {
  margin: 2px 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.conflict-original {
  background: #3b2828;
  border-right: 1px solid #4c566a;
}

.conflict-incoming {
  background: #283b28;
}

.conflict-side-header {
  font-weight: bold;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid #4c566a;
}

.conflict-original .conflict-side-header {
  color: #bf616a;
}

.conflict-incoming .conflict-side-header {
  color: #a3be8c;
}

.conflict-side-content {
  color: #e5e9f0;
}

/* Side-by-side diff viewer */
.diff-viewer {
  font-family: 'JetBrains Mono', 'Fira Code', 'Source Code Pro', monospace;
  font-size: 12px;
  background: #2e3440;
  border-radius: 8px;
  overflow: hidden;
}

.diff-file {
  margin-bottom: 12px;
  border: 1px solid #4c566a;
  border-radius: 4px;
}

.diff-file-header {
  background: #3b4252;
  padding: 8px 12px;
  font-weight: bold;
  color: #88c0d0;
  border-bottom: 1px solid #4c566a;
}

.diff-file-header .new-file {
  color: #a3be8c;
  font-weight: normal;
  margin-left: 8px;
}

.diff-file-header .deleted-file {
  color: #bf616a;
  font-weight: normal;
  margin-left: 8px;
}

.diff-hunk {
  border-top: 1px solid #4c566a;
}

.diff-hunk:first-child {
  border-top: none;
}

.diff-comparison {
  display: flex;
}

.diff-side {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
}

.diff-side-left {
  border-right: 1px solid #4c566a;
}

.diff-side-header {
  background: #3b4252;
  padding: 4px 8px;
  font-size: 11px;
  color: #81a1c1;
  border-bottom: 1px solid #4c566a;
}

.diff-line {
  display: flex;
  min-height: 20px;
}

.diff-line-no {
  width: 40px;
  min-width: 40px;
  padding: 0 8px;
  text-align: right;
  color: #4c566a;
  background: #2e3440;
  border-right: 1px solid #3b4252;
  user-select: none;
}

.diff-line-content {
  flex: 1;
  padding: 0 8px;
  white-space: pre;
  overflow-x: auto;
}

.diff-line.added {
  background: #283b28;
}

.diff-line.added .diff-line-content {
  color: #a3be8c;
}

.diff-line.removed {
  background: #3b2828;
}

.diff-line.removed .diff-line-content {
  color: #bf616a;
}

.diff-line.context {
  background: #2e3440;
}

.diff-line.context .diff-line-content {
  color: #d8dee9;
}

.diff-line.empty {
  background: #3b4252;
}

.diff-line.empty .diff-line-content {
  color: #4c566a;
}

.diff-binary {
  padding: 12px;
  color: #ebcb8b;
  font-style: italic;
}
"""
# fmt: on

# JavaScript to fix Gradio visibility updates and maintain scroll position
# Note: This is passed to gr.Blocks(js=...) to execute on page load
SCREENSHOT_MODE_JS = "true" if os.environ.get("CHAD_SCREENSHOT_MODE") == "1" else "false"
SCREENSHOT_LIVE_VIEW_HTML = "null"
if os.environ.get("CHAD_SCREENSHOT_MODE") == "1":
    from .verification.screenshot_fixtures import LIVE_VIEW_CONTENT

    SCREENSHOT_LIVE_VIEW_HTML = json.dumps(LIVE_VIEW_CONTENT)
CUSTOM_JS = (
    """
function() {
    const screenshotMode = """
    + SCREENSHOT_MODE_JS
    + """;
    const screenshotLiveViewHtml = """
    + SCREENSHOT_LIVE_VIEW_HTML
    + """;
    // Fix for Gradio not properly updating column visibility after initial render
    function fixProviderCardVisibility() {
        const columns = document.querySelectorAll('.column');
        columns.forEach(col => {
            const headerRow = col.querySelector('.provider-card__header-row');
            const headerText = col.querySelector(
                '.provider-card__header-text, .provider-card__header-text-secondary'
            );

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
    function normalizeProviderHeaderClasses() {
        if (!screenshotMode) return;
        const headers = document.querySelectorAll('.provider-card__header-text');
        headers.forEach((header, idx) => {
            if (idx === 0) return;
            header.classList.remove('provider-card__header-text');
            header.classList.add('provider-card__header-text-secondary');
        });
    }
    function getLiveStreamBoxes() {
        return Array.from(document.querySelectorAll('#live-stream-box, .live-stream-box'));
    }
    function ensureLiveStreamVisible() {
        if (!screenshotMode) return;
        const liveBoxes = getLiveStreamBoxes();
        if (!liveBoxes.length) return;
        liveBoxes.forEach((liveBox) => {
            liveBox.classList.remove('hide-container');
            liveBox.style.display = 'block';
            liveBox.style.visibility = 'visible';
            liveBox.removeAttribute('hidden');
            if (screenshotLiveViewHtml && !liveBox.querySelector('.live-output-content')) {
                liveBox.innerHTML = screenshotLiveViewHtml;
            }
        });
    }
    function ensureTabAriaLinks() {
        const tabHost = document.getElementById('main-tabs')?.parentElement || document;
        const tablist = tabHost.querySelector('[role=\"tablist\"]');
        const tabs = tablist ? Array.from(tablist.querySelectorAll('[role=\"tab\"]')) : [];
        const panels = Array.from(tabHost.querySelectorAll('[role=\"tabpanel\"]'));
        if (!tabs.length || !panels.length) return;
        const usedPanelIds = new Set();
        const usedTabIds = new Set();
        tabs.forEach((tab, idx) => {
            const panel = panels[idx];
            if (!panel) return;
            let panelId = panel.id && panel.id.trim() ? panel.id : `tabpanel-${idx}`;
            if (usedPanelIds.has(panelId)) {
                panelId = `tabpanel-${idx}-${idx}`;
            }
            let tabId = tab.id && tab.id.trim() ? tab.id : `tab-${idx}`;
            if (usedTabIds.has(tabId)) {
                tabId = `tab-${idx}-${idx}`;
            }
            panel.id = panelId;
            tab.id = tabId;
            usedPanelIds.add(panelId);
            usedTabIds.add(tabId);
            tab.setAttribute('aria-controls', panelId);
            panel.setAttribute('aria-labelledby', tabId);
            if (!panel.getAttribute('role')) {
                panel.setAttribute('role', 'tabpanel');
            }
        });
    }
    function ensureTabListVisible() {
        const tablist = document.querySelector('[role=\"tablist\"]');
        if (!tablist) return;
        tablist.style.display = 'flex';
        tablist.style.visibility = 'visible';
        tablist.removeAttribute('hidden');
    }
    setInterval(() => {
        normalizeProviderHeaderClasses();
        fixProviderCardVisibility();
        ensureLiveStreamVisible();
        ensureTabAriaLinks();
        ensureTabListVisible();
    }, 500);
    const visObserver = new MutationObserver(() => {
        fixProviderCardVisibility();
        ensureTabAriaLinks();
        ensureTabListVisible();
    });
    visObserver.observe(document.body, { childList: true, subtree: true, attributes: true });
    setTimeout(ensureLiveStreamVisible, 100);
    setTimeout(ensureTabAriaLinks, 100);

    // Live stream scroll preservation
    window._liveStreamScroll = window._liveStreamScroll || new WeakMap();
    const scrollStates = window._liveStreamScroll;

    function getScrollState(container) {
        if (!container) return null;
        if (!scrollStates.has(container)) {
            scrollStates.set(container, {
                userScrolledUp: false,
                savedScrollTop: null,  // null = no user scroll yet, number = user's scroll position
                lastUserScrollTime: 0,
                ignoreNextScroll: false
            });
        }
        return scrollStates.get(container);
    }

    function getScrollContainer(liveBox) {
        if (!liveBox) return null;
        return liveBox.querySelector('.live-output-content') ||
               liveBox.querySelector('[data-testid="markdown"]') ||
               liveBox;
    }

    function handleUserScroll(e) {
        const container = e.target;
        const state = getScrollState(container);
        if (!container || !state || state.ignoreNextScroll) {
            if (state) state.ignoreNextScroll = false;
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
        const state = getScrollState(container);
        if (!container || !state) return;
        state.ignoreNextScroll = true;
        requestAnimationFrame(() => {
            // null = user hasn't scrolled, use container's current position (allow auto-scroll)
            // number = user scrolled to that position, restore it (including 0 for top)
            const targetScrollTop =
                state.savedScrollTop !== null ? state.savedScrollTop : container.scrollTop;
            container.scrollTop = targetScrollTop;
            setTimeout(() => { state.ignoreNextScroll = false; }, 100);
        });
    }

    function attachScrollListener(container) {
        if (!container || container._liveScrollAttached) return;
        container._liveScrollAttached = true;
        container.addEventListener('scroll', handleUserScroll, { passive: true });
    }

    function initScrollTracking() {
        const liveBoxes = getLiveStreamBoxes();
        if (!liveBoxes.length) {
            setTimeout(initScrollTracking, 200);
            return;
        }

        liveBoxes.forEach((liveBox) => {
            let lastContainer = null;
            const syncContainer = () => {
                const container = getScrollContainer(liveBox);
                if (!container) return;
                if (container !== lastContainer) {
                    attachScrollListener(container);
                    lastContainer = container;
                }
                restoreScrollPosition(container);
            };

            const observer = new MutationObserver(syncContainer);
            observer.observe(liveBox, {
                childList: true,
                subtree: true,
                characterData: true
            });

            syncContainer();
        });
    }

    setTimeout(initScrollTracking, 100);

    // Auto-click "Add New Task" button when + tab is clicked
    function setupPlusTabAutoClick() {
        const tabButtons = document.querySelectorAll('button[role="tab"]');
        tabButtons.forEach(tab => {
            if (tab.textContent.trim() === '➕' && !tab._plusClickSetup) {
                tab._plusClickSetup = true;
                tab.addEventListener('click', () => {
                    // Small delay to let tab panel become visible
                    setTimeout(() => {
                        const addBtn = document.getElementById('add-new-task-btn');
                        if (addBtn) {
                            addBtn.click();
                        }
                    }, 50);
                });
            }
        });
    }
    function ensurePlusTabExists() {
        const existing = Array.from(document.querySelectorAll('[role=\"tab\"]')).find(
            (tab) => tab.textContent && tab.textContent.trim() === '➕'
        );
        if (existing) return;
        const addBtn = document.getElementById('add-new-task-btn');
        if (!addBtn) return;
        let fallback = document.getElementById('fallback-plus-tab');
        if (!fallback) {
            fallback = document.createElement('button');
            fallback.id = 'fallback-plus-tab';
            fallback.setAttribute('role', 'tab');
            fallback.setAttribute('aria-label', '➕');
            fallback.textContent = '➕';
            fallback.style.position = 'fixed';
            fallback.style.top = '12px';
            fallback.style.right = '12px';
            fallback.style.zIndex = '9999';
            fallback.style.padding = '6px 10px';
            fallback.style.fontSize = '16px';
            fallback.style.cursor = 'pointer';
            document.body.appendChild(fallback);
        }
        fallback.onclick = () => addBtn.click();
    }

    // Run setup periodically to catch dynamically added tabs
    setInterval(setupPlusTabAutoClick, 500);
    setTimeout(setupPlusTabAutoClick, 100);
    setInterval(ensurePlusTabExists, 500);
    setTimeout(ensurePlusTabExists, 100);

    // Fix for Gradio Column visibility not updating after merge/discard
    // Since gr.update(visible=False) doesn't work for Columns in Gradio 6.x,
    // we use a hidden state element to control visibility reliably via JavaScript
    function syncMergeSectionVisibility() {
        // Find all merge sections in all task panels
        const mergeSections = document.querySelectorAll('.merge-section');
        mergeSections.forEach(mergeSection => {
            // Primary method: Check the visibility state element (more reliable)
            // Query handles both container mode (input inside .merge-visibility-state)
            // and container=False mode (class directly on input/textarea)
            let stateInput =
                mergeSection.querySelector('.merge-visibility-state input, .merge-visibility-state textarea');
            if (!stateInput) {
                // Fallback: container=False puts class directly on the element
                stateInput =
                    mergeSection.querySelector('input.merge-visibility-state, textarea.merge-visibility-state');
            }
            if (!stateInput) {
                // Last resort: find by ID pattern
                stateInput = mergeSection.querySelector(
                    '[id^="merge-visibility-"] input, [id^="merge-visibility-"] textarea,
                      input[id^="merge-visibility-"], textarea[id^="merge-visibility-"]'
                );
            }
            let shouldHide = false;

            if (stateInput) {
                // Use the explicit visibility state from Python
                shouldHide = stateInput.value === 'hidden' || stateInput.value === '';
            } else {
                // Fallback: Check status text (for backward compatibility)
                const taskPanel = mergeSection.closest('.tabitem, [role="tabpanel"]');
                if (taskPanel) {
                    const statusEl = taskPanel.querySelector('.task-status-header') ||
                                    taskPanel.querySelector('[id*="task-status"]') ||
                                    taskPanel.querySelector('[class*="task-status"]');
                    const statusText = statusEl ? (statusEl.textContent || '') : '';
                    shouldHide = statusText.includes('merged') || statusText.includes('discarded');
                }
            }

            if (shouldHide) {
                // Hide the entire section and ALL its children completely
                mergeSection.classList.add('merge-section-hidden');
                mergeSection.style.cssText = 'display: none !important; visibility: hidden !important;';
                // Hide all child elements to ensure nothing bleeds through
                mergeSection.querySelectorAll('*').forEach(child => {
                    if (!child.classList.contains('merge-visibility-state') &&
                        !child.closest('.merge-visibility-state')) {
                        child.style.cssText = 'display: none !important; visibility: hidden !important;';
                    }
                });
            } else if (stateInput && stateInput.value === 'visible') {
                // Explicitly show the section and restore all children
                mergeSection.classList.remove('merge-section-hidden');
                mergeSection.style.cssText = '';
                mergeSection.querySelectorAll('*').forEach(child => {
                    if (!child.classList.contains('merge-visibility-state') &&
                        !child.classList.contains('visually-hidden') &&
                        !child.closest('.visually-hidden')) {
                        child.style.cssText = '';
                    }
                });
            }
        });

        // Also handle conflict sections (still using status text for these)
        const conflictSections = document.querySelectorAll('.conflict-section');
        conflictSections.forEach(conflictSection => {
            const taskPanel = conflictSection.closest('.tabitem, [role="tabpanel"]');
            if (!taskPanel) return;

            const statusEl = taskPanel.querySelector('.task-status-header') ||
                            taskPanel.querySelector('[id*="task-status"]') ||
                            taskPanel.querySelector('[class*="task-status"]');
            const statusText = statusEl ? (statusEl.textContent || '') : '';
            const shouldHide = statusText.includes('merged') || statusText.includes('discarded');

            if (shouldHide) {
                conflictSection.classList.add('conflict-section-hidden');
                conflictSection.style.cssText = 'display: none !important; visibility: hidden !important;';
            } else if (conflictSection.classList.contains('conflict-section-hidden')) {
                conflictSection.classList.remove('conflict-section-hidden');
                conflictSection.style.cssText = '';
            }
        });
    }

    // Run frequently and on DOM changes for responsive UI
    setInterval(syncMergeSectionVisibility, 100);
    const mergeSectionObserver = new MutationObserver(syncMergeSectionVisibility);
    mergeSectionObserver.observe(document.body,
        { childList: true, subtree: true, characterData: true, attributes: true });
}
"""
)


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
            min(255, int(b * factor)),
        )
    return (r, g, b)


def _256_to_rgb(n: int) -> tuple[int, int, int]:
    """Convert 256-color palette index to RGB."""
    if n < 8:
        # Standard colors 0-7
        return [
            (0, 0, 0),
            (205, 0, 0),
            (0, 205, 0),
            (205, 205, 0),
            (0, 0, 238),
            (205, 0, 205),
            (0, 205, 205),
            (229, 229, 229),
        ][n]
    elif n < 16:
        # Bright colors 8-15
        return [
            (127, 127, 127),
            (255, 0, 0),
            (0, 255, 0),
            (255, 255, 0),
            (92, 92, 255),
            (255, 0, 255),
            (0, 255, 255),
            (255, 255, 255),
        ][n - 8]
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
        "30": (0, 0, 0),
        "31": (224, 108, 117),
        "32": (152, 195, 121),
        "33": (229, 192, 123),
        "34": (97, 175, 239),
        "35": (198, 120, 221),
        "36": (86, 182, 194),
        "37": (171, 178, 191),
        "90": (92, 99, 112),
        "91": (224, 108, 117),
        "92": (152, 195, 121),
        "93": (229, 192, 123),
        "94": (97, 175, 239),
        "95": (198, 120, 221),
        "96": (86, 182, 194),
        "97": (255, 255, 255),
    }

    # CSI sequence ending characters (covers most terminal sequences)
    CSI_ENDINGS = "ABCDEFGHJKLMPSTXZcfghlmnpqrstuz"

    result = []
    i = 0
    current_styles = []

    while i < len(text):
        # Check for escape character
        if text[i] == "\x1b":
            if i + 1 < len(text):
                next_char = text[i + 1]

                # CSI sequence: ESC[...
                if next_char == "[":
                    j = i + 2
                    while j < len(text) and text[j] not in CSI_ENDINGS:
                        j += 1
                    if j < len(text):
                        if text[j] == "m":
                            # SGR (color/style) sequence - parse it
                            codes = text[i + 2 : j].split(";")
                            idx = 0
                            while idx < len(codes):
                                code = codes[idx]
                                if code == "0" or code == "":
                                    # Reset
                                    if current_styles:
                                        result.append("</span>" * len(current_styles))
                                        current_styles = []
                                elif code == "1":
                                    result.append('<span style="font-weight:bold">')
                                    current_styles.append("bold")
                                elif code == "3":
                                    result.append('<span style="font-style:italic">')
                                    current_styles.append("italic")
                                elif code == "4":
                                    result.append('<span style="text-decoration:underline">')
                                    current_styles.append("underline")
                                elif code == "38":
                                    # Extended foreground color
                                    if idx + 1 < len(codes):
                                        if codes[idx + 1] == "5" and idx + 2 < len(codes):
                                            # 256-color: 38;5;N
                                            try:
                                                n = int(codes[idx + 2])
                                                r, g, b = _brighten_color(*_256_to_rgb(n))
                                                result.append(f'<span style="color:rgb({r},{g},{b})">')
                                                current_styles.append("color")
                                            except ValueError:
                                                pass
                                            idx += 2
                                        elif codes[idx + 1] == "2" and idx + 4 < len(codes):
                                            # True color: 38;2;R;G;B
                                            try:
                                                r = int(codes[idx + 2])
                                                g = int(codes[idx + 3])
                                                b = int(codes[idx + 4])
                                                r, g, b = _brighten_color(r, g, b)
                                                result.append(f'<span style="color:rgb({r},{g},{b})">')
                                                current_styles.append("color")
                                            except ValueError:
                                                pass
                                            idx += 4
                                elif code == "48":
                                    # Extended background color (skip, don't change bg)
                                    if idx + 1 < len(codes):
                                        if codes[idx + 1] == "5":
                                            idx += 2
                                        elif codes[idx + 1] == "2":
                                            idx += 4
                                elif code in basic_colors:
                                    r, g, b = _brighten_color(*basic_colors[code])
                                    result.append(f'<span style="color:rgb({r},{g},{b})">')
                                    current_styles.append("color")
                                # Skip basic background colors (40-47) - they clash with our dark theme
                                idx += 1
                        # Skip the entire CSI sequence (including non-SGR ones)
                        i = j + 1
                        continue

                # OSC sequence: ESC]...BEL or ESC]...ST
                elif next_char == "]":
                    j = i + 2
                    while j < len(text):
                        # BEL (0x07) or ST (ESC\) terminates OSC
                        if text[j] == "\x07":
                            j += 1
                            break
                        if text[j] == "\x1b" and j + 1 < len(text) and text[j + 1] == "\\":
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
        if char == "<":
            result.append("&lt;")
        elif char == ">":
            result.append("&gt;")
        elif char == "&":
            result.append("&amp;")
        elif char == "\n":
            result.append("\n")
        else:
            result.append(char)
        i += 1

    # Close any remaining spans
    if current_styles:
        result.append("</span>" * len(current_styles))

    return "".join(result)


def highlight_diffs(html_content: str) -> str:
    """Add diff highlighting CSS classes to diff-style lines.

    Detects unified diff format lines and wraps them in appropriate classes.
    """
    import re

    lines = html_content.split("\n")
    result = []

    for line in lines:
        # Strip HTML tags to check the actual text content for diff patterns
        text_only = re.sub(r"<[^>]+>", "", line)

        if re.match(r"^@@\s.*\s@@", text_only):
            # Diff header line like "@@ -1,5 +1,7 @@"
            result.append(f'<span class="diff-header">{line}</span>')
        elif text_only.startswith("+") and not text_only.startswith("+++"):
            # Added line
            result.append(f'<span class="diff-add">{line}</span>')
        elif text_only.startswith("-") and not text_only.startswith("---"):
            # Removed line
            result.append(f'<span class="diff-remove">{line}</span>')
        else:
            result.append(line)

    return "\n".join(result)


def normalize_live_stream_spacing(content: str) -> str:
    """Remove all blank lines from live stream output for compact display."""
    if not content:
        return ""

    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    # Remove all blank lines - keep output compact
    lines = [line for line in normalized.split("\n") if line.strip()]
    return "\n".join(lines)


class LiveStreamDisplayBuffer:
    """Rolling buffer for live stream display content."""

    def __init__(self, max_chars: int = 50000) -> None:
        self.max_chars = max_chars
        self.content = ""

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        self.content += chunk
        if len(self.content) > self.max_chars:
            self.content = self.content[-self.max_chars :]


class LiveStreamRenderState:
    """Track live stream rendering to prevent duplicate updates."""

    def __init__(self) -> None:
        self.last_rendered_stream = ""

    def reset(self) -> None:
        self.last_rendered_stream = ""

    def should_render(self, rendered_stream: str) -> bool:
        return rendered_stream != self.last_rendered_stream

    def record(self, rendered_stream: str) -> None:
        self.last_rendered_stream = rendered_stream


def highlight_code_syntax(html_content: str) -> str:
    """Apply syntax highlighting to code blocks in HTML content.

    Detects common programming patterns and wraps them in syntax classes.
    Works on content inside <code> tags and preserves existing HTML structure.
    """
    import re

    # Python/general programming keywords (excluding 'def' which is handled in function definitions)
    keywords = (
        r"\b(import|from|if|else|elif|for|while|return|try|except|finally|with|as|"
        r"pass|break|continue|in|is|not|and|or|lambda|yield|global|nonlocal|assert|raise|"
        r"del|True|False|None)\b"
    )

    # Function/method definitions and calls
    function_def = r"\b(def)\s+(\w+)\s*\("
    class_def = r"\b(class)\s+(\w+)"

    # String patterns (handles quotes)
    strings = r'(["\'])(?:(?=(\\?))\2.)*?\1'

    # Comments
    comments = r"(#.*?)(?=\n|$)"

    # Numbers
    numbers = r"\b\d+\.?\d*\b"

    # Class names (capitalized words)
    class_names = r"\b[A-Z]\w*\b"

    # Process code blocks
    def process_code_block(match):
        full_match = match.group(0)
        code_content = match.group(1)

        # Skip if already has manual syntax highlighting spans in content
        if '<span class="' in code_content:
            return full_match

        # Apply highlighting in order of precedence
        # 1. Comments (highest precedence)
        code_content = re.sub(comments, r'<span class="comment">\1</span>', code_content)

        # 2. Strings
        code_content = re.sub(strings, r'<span class="string">\g<0></span>', code_content)

        # 3. Function definitions (before general keywords to avoid conflicts)
        code_content = re.sub(
            function_def,
            r'<span class="keyword">\1</span> <span class="function">\2</span>(',
            code_content,
        )

        # 4. Class definitions
        code_content = re.sub(
            class_def,
            r'<span class="keyword">\1</span> <span class="class-name">\2</span>',
            code_content,
        )

        # 5. Keywords
        code_content = re.sub(keywords, r'<span class="keyword">\1</span>', code_content)

        # 6. Numbers
        code_content = re.sub(numbers, r'<span class="number">\g<0></span>', code_content)

        # 7. Class names
        code_content = re.sub(class_names, r'<span class="class-name">\g<0></span>', code_content)

        return f"<code>{code_content}</code>"

    # Apply to all code blocks
    html_content = re.sub(r"<code[^>]*>(.*?)</code>", process_code_block, html_content, flags=re.DOTALL)

    # Also handle pre/code blocks specifically for better formatting
    def process_pre_code_block(match):
        pre_tag = match.group(1)
        code_content = match.group(2)

        # Skip if already has manual syntax highlighting spans in content
        if '<span class="' in code_content:
            return match.group(0)

        # More aggressive highlighting for pre/code blocks
        # Apply the same patterns but ensure we catch everything
        code_content = re.sub(comments, r'<span class="comment">\1</span>', code_content)
        code_content = re.sub(strings, r'<span class="string">\g<0></span>', code_content)
        code_content = re.sub(
            function_def,
            r'<span class="keyword">\1</span> <span class="function">\2</span>(',
            code_content,
        )
        code_content = re.sub(
            class_def,
            r'<span class="keyword">\1</span> <span class="class-name">\2</span>',
            code_content,
        )
        code_content = re.sub(keywords, r'<span class="keyword">\1</span>', code_content)
        code_content = re.sub(numbers, r'<span class="number">\g<0></span>', code_content)
        code_content = re.sub(class_names, r'<span class="class-name">\g<0></span>', code_content)

        # Also highlight common builtins
        builtins = (
            r"\b(print|len|range|str|int|float|list|dict|set|tuple|open|type|isinstance|" r"hasattr|getattr|setattr)\b"
        )
        code_content = re.sub(builtins, r'<span class="builtin">\1</span>', code_content)

        return f"{pre_tag}{code_content}</code></pre>"

    # Handle pre/code blocks
    html_content = re.sub(
        r"(<pre[^>]*><code[^>]*>)(.*?)(</code></pre>)",
        process_pre_code_block,
        html_content,
        flags=re.DOTALL,
    )

    return html_content


def build_live_stream_html(content: str, ai_name: str = "CODING AI") -> str:
    """Render live stream text as HTML with consistent spacing and header."""
    cleaned = normalize_live_stream_spacing(content)
    if not cleaned.strip():
        return ""
    html_content = ansi_to_html(cleaned)
    # Preserve intended HTML structure after ANSI conversion so syntax highlighting works
    html_content = html.unescape(html_content)
    html_content = highlight_diffs(html_content)
    # Apply syntax highlighting to code blocks
    html_content = highlight_code_syntax(html_content)
    header = f'<div class="live-output-header">▶ {ai_name} (Live Stream)</div>'
    body = f'<div class="live-output-content">{html_content}</div>'
    return f"{header}\n{body}"


def build_inline_live_html(content: str, ai_name: str = "CODING AI") -> str:
    """Render live stream as inline HTML for embedding in chat bubbles.

    This formats the streaming content to display directly in the Chatbot
    component instead of a separate live stream panel.
    """
    cleaned = normalize_live_stream_spacing(content)
    if not cleaned.strip():
        return f"**{ai_name}**\n\n*Working...*"
    html_content = ansi_to_html(cleaned)
    html_content = html.unescape(html_content)
    html_content = highlight_diffs(html_content)
    html_content = highlight_code_syntax(html_content)
    # Format for inline display in chat bubble
    header = f'<div class="inline-live-header">▶ {ai_name} (Live)</div>'
    body = f'<div class="inline-live-content">{html_content}</div>'
    return f"**{ai_name}**\n\n{header}\n{body}"


def image_to_data_url(path: str) -> str | None:
    """Convert an image file to a base64 data URL for inline display.

    Args:
        path: Path to the image file

    Returns:
        Data URL string or None if file doesn't exist or can't be read
    """
    try:
        p = Path(path)
        if not p.exists():
            return None
        suffix = p.suffix.lower()
        mime_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
        mime = mime_types.get(suffix, "image/png")
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def summarize_content(content: str, max_length: int = 200) -> str:
    """Create a meaningful summary of content for collapsed view.

    Tries to extract the most informative sentence describing what was done.
    """
    import re

    # Remove markdown formatting for cleaner summary
    clean = content.replace("**", "").replace("`", "").replace("# ", "")

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", clean)

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
                return sentence[:max_length].rsplit(" ", 1)[0] + "..."

    # Look for sentences mentioning file paths
    for sentence in sentences:
        sentence = sentence.strip()
        if re.search(r"[a-zA-Z_]+\.(py|js|ts|tsx|css|html|md|json|yaml|yml)", sentence):
            if len(sentence) <= max_length:
                return sentence
            return sentence[:max_length].rsplit(" ", 1)[0] + "..."

    # Fallback: get first meaningful paragraph
    first_para = clean.split("\n\n")[0].strip()
    # Skip if it's just a header or very short
    if len(first_para) < 20:
        for para in clean.split("\n\n")[1:]:
            if len(para.strip()) >= 20:
                first_para = para.strip()
                break

    if len(first_para) <= max_length:
        return first_para
    return first_para[:max_length].rsplit(" ", 1)[0] + "..."


def make_chat_message(speaker: str, content: str, collapsible: bool = True) -> dict:
    """Create a Gradio 6.x compatible chat message.

    Args:
        speaker: The speaker name (e.g., "CODING AI")
        content: The message content
        collapsible: Whether to make long messages collapsible with a summary
    """
    role = "assistant"

    if collapsible and len(content) > 300:
        coding_summary = extract_coding_summary(content)
        if coding_summary:
            summary_text = coding_summary.change_summary
            extra_parts = []
            if coding_summary.hypothesis:
                extra_parts.append(f"**Hypothesis:** {coding_summary.hypothesis}")
            # Display before/after screenshots inline side by side
            before_url = (
                image_to_data_url(coding_summary.before_screenshot)) if coding_summary.before_screenshot else None
            after_url = image_to_data_url(coding_summary.after_screenshot) if coding_summary.after_screenshot else None
            if before_url and after_url:
                # Both screenshots - display side by side
                extra_parts.append(
                    '<div class="screenshot-comparison">'
                    f'<div class="screenshot-panel"><div class="screenshot-label">Before</div>'
                    f'<img src="{before_url}" alt="Before screenshot"></div>'
                    f'<div class="screenshot-panel"><div class="screenshot-label">After</div>'
                    f'<img src="{after_url}" alt="After screenshot"></div>'
                    '</div>'
                )
            elif before_url:
                extra_parts.append(
                    f'<div class="screenshot-single"><div class="screenshot-label">Before</div>'
                    f'<img src="{before_url}" alt="Before screenshot"></div>'
                )
            elif after_url:
                extra_parts.append(
                    f'<div class="screenshot-single"><div class="screenshot-label">After</div>'
                    f'<img src="{after_url}" alt="After screenshot"></div>'
                )
            if extra_parts:
                summary_text = f"{summary_text}\n\n" + "\n\n".join(extra_parts)
        else:
            summary_text = summarize_content(content)
        formatted = (
            f"**{speaker}**\n\n{summary_text}\n\n"
            f"<details><summary>Show full output</summary>\n\n{content}\n\n</details>"
        )
    else:
        formatted = f"**{speaker}**\n\n{content}"

    return {"role": role, "content": formatted}


def _truncate_verification_output(text: str, limit: int = MAX_VERIFICATION_PROMPT_CHARS) -> str:
    """Compact the coding agent output for verification prompts."""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned

    indicator = f"...[truncated {len(cleaned) - limit} chars]..."
    keep = max(limit - len(indicator) - 4, 0)
    head_len = int(keep * 0.6)
    tail_len = keep - head_len
    head = cleaned[:head_len].rstrip()
    tail = cleaned[-tail_len:].lstrip() if tail_len > 0 else ""
    parts = [head, indicator]
    if tail:
        parts.append(tail)
    return "\n\n".join(part for part in parts if part)


class ChadWebUI:
    """Web interface for Chad using Gradio."""

    # Constant for verification agent dropdown default
    SAME_AS_CODING = "(Same as Coding Agent)"
    VERIFICATION_NONE = "__verification_none__"
    VERIFICATION_NONE_LABEL = "None"

    def __init__(self, security_mgr: SecurityManager, main_password: str):
        self.security_mgr = security_mgr
        self.main_password = main_password
        self.sessions: dict[str, Session] = {}
        self.provider_card_count = 10
        self.model_catalog = ModelCatalog(security_mgr)
        self.provider_ui = ProviderUIManager(security_mgr, main_password, self.model_catalog)
        self.session_logger = SessionLogger()
        # Store dropdown references for cross-tab updates
        self._session_dropdowns: dict[str, dict] = {}
        # Store provider card delete events for chaining dropdown updates
        self._provider_delete_events: list = []

    def get_session(self, session_id: str) -> Session:
        """Get or create a session by ID."""
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(id=session_id)
        return self.sessions[session_id]

    def create_session(self, name: str = "New Session") -> Session:
        """Create a new session with a unique ID."""
        session = Session(name=name)
        self.sessions[session.id] = session
        return session

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

    def _get_claude_remaining_usage(self, account_name: str) -> float:
        return self.provider_ui._get_claude_remaining_usage(account_name)

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
                    content = doc_file.read_text(encoding="utf-8")
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
        task_description: str,
        verification_account: str,
        on_activity: callable = None,
        timeout: float = DEFAULT_VERIFICATION_TIMEOUT,
        verification_model: str | None = None,
        verification_reasoning: str | None = None,
    ) -> tuple[bool | None, str]:
        """Run the verification agent to review the coding agent's work.

        First runs MCP verification (flake8 + tests), then if that passes,
        runs the LLM verification agent.

        Args:
            project_path: Path to the project directory
            coding_output: The output from the coding agent
            task_description: The original task description
            verification_account: Account name to use for verification
            on_activity: Optional callback for activity updates
            timeout: Timeout for verification (default 5 minutes)

        Returns:
            Tuple of (verified: bool | None, feedback: str)
            - verified=True means the work passed verification
            - verified=False means revisions are needed, feedback contains issues
            - verified=None means verification aborted due to missing inputs
        """
        accounts = self.security_mgr.list_accounts()
        if verification_account not in accounts:
            return True, "Verification skipped: account not found"

        verification_provider = accounts[verification_account]
        stored_model = self.security_mgr.get_account_model(verification_account)
        stored_reasoning = self.security_mgr.get_account_reasoning(verification_account)
        verification_model = verification_model or stored_model
        verification_reasoning = verification_reasoning or stored_reasoning

        verification_config = ModelConfig(
            provider=verification_provider,
            model_name=verification_model,
            account_name=verification_account,
            reasoning_effort=None if verification_reasoning == "default" else verification_reasoning,
        )

        if not task_description.strip():
            return None, "Verification aborted: missing task description. Rerun with a task description."

        if not coding_output.strip():
            return None, ("Verification aborted: coding agent output was empty. "
                          "Rerun after capturing the coding response.")

        # First run automated verification (flake8 + tests)
        try:
            from .verification.tools import verify as run_verify
            if on_activity:
                on_activity("system", "Running verification (flake8 + tests)...")

            verify_result = run_verify()
            if not verify_result.get("success", False):
                issues: list[str] = []
                failure_message = verify_result.get("message") or verify_result.get("error")
                if failure_message:
                    issues.append(failure_message)

                phases = verify_result.get("phases", {})

                lint_phase = phases.get("lint", {})
                if not lint_phase.get("success", True):
                    lint_issues = lint_phase.get("issues") or []
                    if lint_issues:
                        joined = "\n".join(f"- {issue}" for issue in lint_issues[:5])
                        issues.append(f"Flake8 errors:\n{joined}")
                    else:
                        issues.append(f"Flake8 failed with {lint_phase.get('issue_count', 0)} errors")

                pip_phase = phases.get("pip_check", {})
                if not pip_phase.get("success", True):
                    pip_issues = pip_phase.get("issues") or []
                    if pip_issues:
                        joined = "\n".join(f"- {issue}" for issue in pip_issues[:5])
                        issues.append(f"Dependency issues:\n{joined}")
                    else:
                        issues.append("Package dependency issues found")

                test_phase = phases.get("tests", {})
                if not test_phase.get("success", True):
                    failed_count = test_phase.get("failed", 0)
                    passed_count = test_phase.get("passed", 0)
                    output_lines = (test_phase.get("output") or "").strip().splitlines()
                    snippet = "\n".join(output_lines[-5:]) if output_lines else ""
                    summary = f"Tests failed ({failed_count} failed, {passed_count} passed)"
                    if snippet:
                        summary += f":\n{snippet}"
                    issues.append(summary)

                if not issues:
                    issues.append("Verification failed")

                feedback = "Verification failed:\n" + "\n\n".join(issues)
                return False, feedback
        except Exception as e:
            # If verification fails to run, log but continue with LLM verification
            if on_activity:
                on_activity("system", f"Warning: Verification could not run: {str(e)}")

        coding_summary = extract_coding_summary(coding_output)
        change_summary = coding_summary.change_summary if coding_summary else None
        trimmed_output = _truncate_verification_output(coding_output)
        verification_prompt = get_verification_prompt(trimmed_output, task_description, change_summary)

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

        if accounts[account_name] != "openai":
            return f"❌ Account '{account_name}' is not an OpenAI account"

        cli_ok, cli_detail = self.provider_ui._ensure_provider_cli("openai")
        if not cli_ok:
            return f"❌ {cli_detail}"
        codex_cli = cli_detail or "codex"

        # Setup isolated home
        codex_home = self._setup_codex_account(account_name)

        # Create environment with isolated HOME
        env = os.environ.copy()
        env["HOME"] = codex_home

        # First logout any existing session
        subprocess.run([codex_cli, "logout"], env=env, capture_output=True, timeout=10)

        # Now run login - this will open a browser
        result = subprocess.run([codex_cli, "login"], env=env, capture_output=True, text=True, timeout=120)

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

    def cancel_task(self, session_id: str) -> str:
        """Cancel the running task for a specific session."""
        session = self.get_session(session_id)
        session.cancel_requested = True
        if session.provider:
            session.provider.stop_session()
            session.provider = None
        session.config = None

        # Clean up any spawned test server processes (e.g., from visual tests)
        try:
            cleanup_all_test_servers()
        except Exception:
            pass  # Best effort cleanup

        # Clean up worktree if it exists
        if session.worktree_path and session.project_path:
            try:
                git_mgr = GitWorktreeManager(Path(session.project_path))
                git_mgr.delete_worktree(session_id)
            except Exception:
                pass  # Best effort cleanup
            session.worktree_path = None
            session.worktree_base_commit = None

        return "🛑 Task cancelled"

    def _resolve_verification_preferences(
        self,
        coding_account: str,
        coding_model: str,
        coding_reasoning: str,
        verification_agent: str,
        verification_model: str | None = None,
        verification_reasoning: str | None = None,
    ) -> tuple[str | None, str, str]:
        """Resolve verification account/model/reasoning selections without mutating coding prefs."""
        accounts = self.security_mgr.list_accounts()
        if verification_agent == self.VERIFICATION_NONE:
            return None, coding_model, coding_reasoning
        actual_account = coding_account if verification_agent == self.SAME_AS_CODING else verification_agent
        if not actual_account or actual_account not in accounts:
            return None, coding_model, coding_reasoning

        def normalize(value: str | None, fallback: str) -> str:
            if not value or value == self.SAME_AS_CODING:
                return fallback
            return value

        if verification_agent == self.SAME_AS_CODING:
            resolved_model = normalize(coding_model, "default")
            resolved_reasoning = normalize(coding_reasoning, "default")
            return actual_account, resolved_model, resolved_reasoning

        account_model = self.security_mgr.get_account_model(actual_account)
        account_reasoning = self.security_mgr.get_account_reasoning(actual_account)
        resolved_model = normalize(verification_model, account_model)
        resolved_reasoning = normalize(verification_reasoning, account_reasoning)

        # Persist explicit verification preferences to the verification account only
        try:
            if verification_model and verification_model != self.SAME_AS_CODING:
                self.security_mgr.set_account_model(actual_account, resolved_model)
            if verification_reasoning and verification_reasoning != self.SAME_AS_CODING:
                self.security_mgr.set_account_reasoning(actual_account, resolved_reasoning)
        except Exception:
            pass

        return actual_account, resolved_model, resolved_reasoning

    def _build_verification_dropdown_state(
        self,
        coding_agent: str | None,
        verification_agent: str | None,
        coding_model_value: str | None,
        coding_reasoning_value: str | None,
        current_verification_model: str | None = None,
        current_verification_reasoning: str | None = None,
    ) -> VerificationDropdownState:
        """Resolve verification dropdown values based on current selections."""
        if verification_agent == self.VERIFICATION_NONE:
            return VerificationDropdownState(
                ["default"],
                "default",
                ["default"],
                "default",
                False,
            )
        accounts_map = self.security_mgr.list_accounts()
        account_choices = list(accounts_map.keys())
        actual_account = coding_agent if verification_agent == self.SAME_AS_CODING else verification_agent
        interactive = bool(
            actual_account and actual_account in account_choices and verification_agent != self.SAME_AS_CODING
        )

        if not actual_account or actual_account not in account_choices:
            return VerificationDropdownState(
                ["default"],
                "default",
                ["default"],
                "default",
                False,
            )

        provider_type = accounts_map.get(actual_account, "")
        model_choices = self.get_models_for_account(actual_account) or ["default"]
        reasoning_choices = self.get_reasoning_choices(provider_type, actual_account) or ["default"]

        def value_or_default(preferred: str | None, choices: list[str], allow_custom: bool = False) -> str:
            if preferred:
                if preferred in choices or allow_custom:
                    return preferred
            return choices[0]

        if verification_agent == self.SAME_AS_CODING:
            model_value = value_or_default(coding_model_value, model_choices, allow_custom=False)
            reasoning_value = value_or_default(coding_reasoning_value, reasoning_choices, allow_custom=False)
        else:
            stored_model = self.security_mgr.get_account_model(actual_account) or "default"
            preferred_model = current_verification_model or stored_model
            model_value = value_or_default(preferred_model, model_choices)

            stored_reasoning = self.security_mgr.get_account_reasoning(actual_account) or "default"
            preferred_reasoning = current_verification_reasoning or stored_reasoning
            reasoning_value = value_or_default(preferred_reasoning, reasoning_choices)

        return VerificationDropdownState(
            model_choices=model_choices,
            model_value=model_value,
            reasoning_choices=reasoning_choices,
            reasoning_value=reasoning_value,
            interactive=interactive,
        )

    def start_chad_task(  # noqa: C901
        self,
        session_id: str,
        project_path: str,
        task_description: str,
        coding_agent: str,
        verification_agent: str = "(Same as Coding Agent)",
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        verification_model: str | None = None,
        verification_reasoning: str | None = None,
    ) -> Iterator[
        tuple[
            list,
            str,
            gr.Markdown,
            gr.Textbox,
            gr.TextArea,
            gr.Button,
            gr.Button,
            gr.Markdown,
            gr.update,
            gr.Row,
            gr.Button,
        ]
    ]:
        """Start Chad task and stream updates with optional verification."""
        session = self.get_session(session_id)
        chat_history = []
        message_queue = queue.Queue()
        session.cancel_requested = False
        session.config = None

        def make_yield(
            history,
            status: str,
            live_stream: str = "",
            summary: str | None = None,
            interactive: bool = False,
            show_followup: bool = False,
            show_merge: bool = False,
            merge_summary: str = "",
            branch_choices: list[str] | None = None,
            diff_full: str = "",
        ):
            """Format output tuple for Gradio with current UI state."""
            display_stream = live_stream
            is_error = "❌" in status
            display_role_status = self.format_role_status()
            log_btn_update = gr.update(
                label=f"📄 {session.log_path.name}" if session.log_path else "Session Log",
                value=str(session.log_path) if session.log_path else None,
                visible=session.log_path is not None,
            )
            display_history = history
            if history and isinstance(history[0], dict):
                content = history[0].get("content", "")
                if isinstance(content, str) and content.startswith("**Task**"):
                    display_history = history[1:]
            # Build branch dropdown update
            if branch_choices:
                branch_update = gr.update(choices=branch_choices, value=branch_choices[0])
            else:
                branch_update = gr.update()
            # Determine visibility state for merge section (controls via JS workaround)
            visibility_state = "visible" if show_merge else "hidden"
            header_text = "### Changes Ready to Merge" if show_merge else ""
            return (
                display_history,
                display_stream,
                # Task status - always visible in DOM, CSS :empty hides when blank
                gr.update(value=status if is_error else ""),
                gr.update(value=project_path, interactive=interactive),
                gr.update(value=task_description, interactive=interactive),
                gr.update(interactive=interactive),
                gr.update(interactive=not interactive),
                gr.update(value=display_role_status),
                log_btn_update,
                gr.update(value=""),  # Clear followup input
                gr.update(visible=show_followup),  # Show/hide followup row
                gr.update(interactive=show_followup),  # Enable/disable send button
                gr.update(visible=show_merge),  # Show/hide merge section (Gradio - may not work)
                gr.update(value=merge_summary),  # Merge changes summary
                branch_update,  # Branch dropdown choices
                gr.update(value=diff_full),  # Full diff content
                visibility_state,  # merge_visibility_state - controls via JS
                header_text,  # merge_section_header - dynamic header
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

            # Check if project is a git repository
            git_mgr = GitWorktreeManager(path_obj)
            if not git_mgr.is_git_repo():
                error_msg = f"❌ Project must be a git repository: {project_path}"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            session.task_description = task_description

            # Create worktree for this task
            try:
                worktree_path, base_commit = git_mgr.create_worktree(session_id)
                session.worktree_path = worktree_path
                session.worktree_branch = git_mgr._branch_name(session_id)
                session.worktree_base_commit = base_commit
                session.project_path = str(path_obj)
                task_working_dir = worktree_path
            except Exception as e:
                error_msg = f"❌ Failed to create worktree: {e}"
                yield make_yield([], error_msg, summary=error_msg, interactive=True)
                return

            coding_account = coding_agent
            coding_provider = accounts[coding_account]
            self.security_mgr.assign_role(coding_account, "CODING")

            selected_model = coding_model or self.security_mgr.get_account_model(coding_account) or "default"
            selected_reasoning = (
                coding_reasoning or self.security_mgr.get_account_reasoning(coding_account) or "default"
            )
            verification_model_value = verification_model or self.SAME_AS_CODING
            verification_reasoning_value = verification_reasoning or self.SAME_AS_CODING
            (
                actual_verification_account,
                resolved_verification_model,
                resolved_verification_reasoning,
            ) = self._resolve_verification_preferences(
                coding_account,
                selected_model,
                selected_reasoning,
                verification_agent,
                verification_model_value,
                verification_reasoning_value,
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
                reasoning_effort=None if selected_reasoning == "default" else selected_reasoning,
            )

            coding_timeout = DEFAULT_CODING_TIMEOUT

            session.log_path = session.log_path or self.session_logger.precreate_log()
            self.session_logger.initialize_log(
                session.log_path,
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

            chat_history.append({"role": "user", "content": f"**Task**\n\n{task_description}"})
            self.session_logger.update_log(
                session.log_path, chat_history, last_event=self._last_event_info(session)
            )

            initial_status = f"{status_prefix}⏳ Initializing session..."
            yield make_yield(chat_history, initial_status, summary=initial_status, interactive=False)

            def format_tool_activity(detail: str) -> str:
                if ": " in detail:
                    tool_name, args = detail.split(": ", 1)
                    return f"● {tool_name}({args})"
                if detail.startswith("Running: "):
                    return f"● {detail[9:]}"
                return f"● {detail}"

            def on_activity(activity_type: str, detail: str):
                if activity_type == "stream":
                    message_queue.put(("stream", detail))
                elif activity_type == "tool":
                    formatted = format_tool_activity(detail)
                    message_queue.put(("activity", formatted))
                elif activity_type == "thinking":
                    message_queue.put(("activity", f"⋯ {detail}"))
                elif activity_type == "text" and detail:
                    message_queue.put(("activity", f"  ⎿ {detail[:80]}"))

            coding_provider_instance = create_provider(coding_config)
            session.provider = coding_provider_instance
            coding_provider_instance.set_activity_callback(on_activity)

            # Read project documentation from worktree (AGENTS.md, CLAUDE.md, etc.)
            project_docs = self._read_project_docs(task_working_dir)

            if not coding_provider_instance.start_session(str(task_working_dir), None):
                failure = f"{status_prefix}❌ Failed to start coding session"
                session.provider = None
                session.config = None
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
            coding_final_output: list[str] = [""]

            def direct_loop():
                try:
                    message_queue.put(("ai_switch", "CODING AI"))
                    message_queue.put(("message_start", "CODING AI"))
                    response = coding_provider_instance.get_response(timeout=coding_timeout)
                    if response:
                        parsed = parse_codex_output(response)
                        coding_final_output[0] = parsed
                        message_queue.put(("message_complete", "CODING AI", parsed))
                        task_success[0] = True
                        completion_reason[0] = "Coding AI completed task"
                    else:
                        message_queue.put(("status", "❌ No response from coding AI"))
                        completion_reason[0] = "No response from coding AI"
                except Exception as exc:  # pragma: no cover - runtime safety
                    message_queue.put(("status", f"❌ Error: {str(exc)}"))
                    completion_reason[0] = str(exc)
                    # Stop session on error
                    coding_provider_instance.stop_session()
                    session.provider = None
                    session.active = False
                finally:
                    # Keep session alive for follow-ups if provider supports multi-turn
                    # and task succeeded
                    if not coding_provider_instance.supports_multi_turn() or not task_success[0]:
                        coding_provider_instance.stop_session()
                        session.provider = None
                        session.active = False
                    else:
                        # Keep session alive for follow-ups
                        session.active = True
                    relay_complete.set()

            relay_thread = threading.Thread(target=direct_loop, daemon=True)
            relay_thread.start()

            current_status = f"{status_prefix}⏳ Coding AI is working..."
            current_ai = "CODING AI"
            current_live_stream = ""
            yield make_yield(
                chat_history,
                current_status,
                current_live_stream,
                summary=current_status,
                interactive=False,
            )

            import time as time_module

            last_activity = ""
            streaming_buffer = ""
            full_history = []  # Infinite history - list of (ai_name, content, timestamp) tuples
            display_buffer = LiveStreamDisplayBuffer()
            last_yield_time = 0.0
            last_log_update_time = time_module.time()
            log_update_interval = 10.0  # Update session log every 10 seconds
            min_yield_interval = 0.05
            pending_message_idx = None
            render_state = LiveStreamRenderState()

            while not relay_complete.is_set() and not session.cancel_requested:
                try:
                    msg = message_queue.get(timeout=0.02)
                    msg_type = msg[0]

                    if msg_type == "message":
                        speaker, content = msg[1], msg[2]
                        chat_history.append(make_chat_message(speaker, content))
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        render_state.reset()
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == "message_start":
                        speaker = msg[1]
                        placeholder = {
                            "role": "assistant",
                            "content": f"**{speaker}**\n\n⏳ *Working...*",
                        }
                        chat_history.append(placeholder)
                        pending_message_idx = len(chat_history) - 1
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        render_state.reset()
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == "message_complete":
                        speaker, content = msg[1], msg[2]
                        if pending_message_idx is not None and pending_message_idx < len(chat_history):
                            chat_history[pending_message_idx] = make_chat_message(speaker, content)
                        else:
                            chat_history.append(make_chat_message(speaker, content))
                        pending_message_idx = None
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        render_state.reset()
                        self.session_logger.update_log(
                            session.log_path, chat_history, last_event=self._last_event_info(session)
                        )
                        yield make_yield(chat_history, current_status, current_live_stream)
                        last_yield_time = time_module.time()

                    elif msg_type == "status":
                        current_status = f"{status_prefix}{msg[1]}"
                        streaming_buffer = ""
                        current_live_stream = ""
                        render_state.reset()
                        summary_text = current_status
                        yield make_yield(
                            chat_history,
                            current_status,
                            current_live_stream,
                            summary=summary_text,
                        )
                        last_yield_time = time_module.time()

                    elif msg_type == "ai_switch":
                        current_ai = msg[1]
                        streaming_buffer = ""
                        full_history.append(_history_entry(current_ai, "Processing request\n"))
                        display_buffer.append("Processing request\n")

                    elif msg_type == "stream":
                        chunk = msg[1]
                        if chunk.strip():
                            streaming_buffer += chunk
                            full_history.append(_history_entry(current_ai, chunk))
                            display_buffer.append(chunk)
                            now = time_module.time()
                            if now - last_yield_time >= min_yield_interval:
                                # Update chat bubble with inline live content
                                inline_html = build_inline_live_html(display_buffer.content, current_ai)
                                if render_state.should_render(inline_html):
                                    if pending_message_idx is not None:
                                        chat_history[pending_message_idx] = {
                                            "role": "assistant",
                                            "content": inline_html,
                                        }
                                    yield make_yield(chat_history, current_status, "")
                                    render_state.record(inline_html)
                                    last_yield_time = now

                    elif msg_type == "activity":
                        last_activity = msg[1]
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            display_content = display_buffer.content
                            if display_content:
                                content = display_content + f"\n\n{last_activity}"
                            else:
                                content = last_activity
                            # Update chat bubble with inline live content
                            inline_html = build_inline_live_html(content, current_ai)
                            if render_state.should_render(inline_html):
                                if pending_message_idx is not None:
                                    chat_history[pending_message_idx] = {
                                        "role": "assistant",
                                        "content": inline_html,
                                    }
                                yield make_yield(chat_history, current_status, "")
                                render_state.record(inline_html)
                                last_yield_time = now

                except queue.Empty:
                    now = time_module.time()
                    if now - last_yield_time >= 0.3:
                        display_content = display_buffer.content
                        if display_content or last_activity:
                            content = display_content if display_content else last_activity
                            inline_html = build_inline_live_html(content, current_ai)
                            if render_state.should_render(inline_html):
                                if pending_message_idx is not None:
                                    chat_history[pending_message_idx] = {
                                        "role": "assistant",
                                        "content": inline_html,
                                    }
                                yield make_yield(chat_history, current_status, "")
                                render_state.record(inline_html)
                                last_yield_time = now

                    # Periodically update session log with streaming history
                    if full_history and now - last_log_update_time >= log_update_interval:
                        self._update_session_log(session, chat_history, full_history)
                        last_log_update_time = now

            if session.cancel_requested:
                for idx in range(len(chat_history) - 1, -1, -1):
                    msg = chat_history[idx]
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        chat_history[idx] = {
                            "role": "assistant",
                            "content": "**CODING AI**\n\n🛑 *Cancelled*",
                        }
                        break
                session.active = False
                yield make_yield(
                    chat_history,
                    "🛑 Task cancelled",
                    "",
                    summary="🛑 Task cancelled",
                    show_followup=False,
                )
            else:
                while True:
                    try:
                        msg = message_queue.get_nowait()
                        msg_type = msg[0]
                        if msg_type == "message_complete":
                            speaker, content = msg[1], msg[2]
                            if pending_message_idx is not None and pending_message_idx < len(chat_history):
                                chat_history[pending_message_idx] = make_chat_message(speaker, content)
                            else:
                                chat_history.append(make_chat_message(speaker, content))
                            self.session_logger.update_log(
                                session.log_path, chat_history, last_event=self._last_event_info(session)
                            )
                            yield make_yield(chat_history, current_status, "")
                    except queue.Empty:
                        break

            relay_thread.join(timeout=1)

            # Track the active configuration only when the session can continue
            session.config = coding_config if session.active else None

            verification_enabled = verification_agent != self.VERIFICATION_NONE
            verification_account_for_run = actual_verification_account if verification_enabled else None
            verification_log: list[dict[str, object]] = []
            verified: bool | None = None  # Track verification result

            if session.cancel_requested:
                final_status = "🛑 Task cancelled by user"
                chat_history.append(
                    {
                        "role": "user",
                        "content": "───────────── 🛑 TASK CANCELLED ─────────────",
                    }
                )
            elif task_success[0] and not verification_enabled:
                final_status = "✓ Task completed (verification disabled)"
                completion_msg = "───────────── ✅ TASK COMPLETED (VERIFICATION DISABLED) ─────────────"
                chat_history.append({"role": "user", "content": completion_msg})
            elif task_success[0]:
                # Get the last coding output for verification
                last_coding_output = (coding_final_output[0] or "").strip()

                if not last_coding_output and chat_history:
                    for msg in reversed(chat_history):
                        if isinstance(msg, dict) and msg.get("role") == "assistant" and "CODING AI" in msg.get(
                            "content", ""
                        ):
                            content = msg.get("content", "")
                            last_coding_output = content.replace("**CODING AI**\n\n", "")
                            break

                if not last_coding_output and full_history:
                    last_coding_output = "".join(_history_contents(full_history[-50:])).strip()

                if not last_coding_output:
                    last_coding_output = completion_reason[0] or ""

                # No longer need to warn about verification not being mentioned
                # since we now run it automatically during verification phase

                # Run verification loop
                max_verification_attempts = 3
                verification_attempt = 0
                verified = False
                verification_feedback = ""
                verification_log: list[dict[str, object]] = []

                while (
                    not verified and verification_attempt < max_verification_attempts and not session.cancel_requested
                ):
                    verification_attempt += 1

                    chat_history.append(
                        {
                            "role": "user",
                            "content": f"───────────── 🔍 VERIFICATION (Attempt {verification_attempt}) ─────────────",
                        }
                    )
                    self.session_logger.update_log(
                        session.log_path, chat_history, last_event=self._last_event_info(session)
                    )

                    # Show verification status
                    verify_status = (
                        f"{status_prefix}🔍 Running verification "
                        f"(attempt {verification_attempt}/{max_verification_attempts})..."
                    )
                    yield make_yield(chat_history, verify_status, "")

                    # Run verification in a thread so we can stream output to live view
                    def verification_activity(activity_type: str, detail: str):
                        content = detail if activity_type == "stream" else f"[{activity_type}] {detail}\n"
                        message_queue.put(("stream", content))

                    verification_path = str(session.worktree_path or path_obj)
                    verification_result: list = [None, None]  # [verified, feedback]
                    verification_complete = threading.Event()

                    def run_verification_thread():
                        try:
                            v, f = self._run_verification(
                                verification_path,
                                last_coding_output,
                                task_description,
                                verification_account_for_run,
                                on_activity=verification_activity,
                                verification_model=resolved_verification_model,
                                verification_reasoning=resolved_verification_reasoning,
                            )
                            verification_result[0] = v
                            verification_result[1] = f
                        except Exception as exc:
                            verification_result[0] = None
                            verification_result[1] = f"Verification error: {exc}"
                        finally:
                            verification_complete.set()

                    verification_thread = threading.Thread(target=run_verification_thread, daemon=True)
                    verification_thread.start()

                    # Poll message queue while verification runs
                    # Note: verification streaming is silent (no inline display) since it's usually fast
                    while not verification_complete.is_set() and not session.cancel_requested:
                        try:
                            message_queue.get(timeout=0.05)  # Drain queue
                        except queue.Empty:
                            pass

                    verification_thread.join(timeout=1.0)
                    verified, verification_feedback = verification_result[0], verification_result[1]
                    status_label = "error" if verified is None else ("passed" if verified else "failed")
                    verification_log.append(
                        {
                            "attempt": verification_attempt,
                            "status": status_label,
                            "feedback": verification_feedback,
                            "account": verification_account_for_run,
                        }
                    )

                    # Add verification result to chat
                    if verified is None:
                        # Verification error - show error and stop
                        chat_history.append(
                            {
                                "role": "assistant",
                                "content": f"**VERIFICATION AI**\n\n❌ {verification_feedback}",
                            }
                        )
                        chat_history.append(
                            {
                                "role": "user",
                                "content": "───────────── ❌ VERIFICATION ERROR ─────────────",
                            }
                        )
                        self.session_logger.update_log(
                            session.log_path,
                            chat_history,
                            verification_attempts=verification_log,
                            last_event=self._last_event_info(session),
                        )
                        break
                    elif verified:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                        chat_history.append(
                            {
                                "role": "user",
                                "content": "───────────── ✅ VERIFICATION PASSED ─────────────",
                            }
                        )
                        self.session_logger.update_log(
                            session.log_path,
                            chat_history,
                            verification_attempts=verification_log,
                            last_event=self._last_event_info(session),
                        )
                    else:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                        self.session_logger.update_log(
                            session.log_path,
                            chat_history,
                            verification_attempts=verification_log,
                            last_event=self._last_event_info(session),
                        )

                        # If not verified and session is still active, send feedback to coding agent
                        can_revise = (
                            session.active
                            and coding_provider_instance.is_alive()
                            and verification_attempt < max_verification_attempts
                        )
                        if can_revise:
                            revision_content = (
                                "───────────── 🔄 REVISION REQUESTED ─────────────\n\n"
                                "*Sending verification feedback to coding agent...*"
                            )
                            chat_history.append({"role": "user", "content": revision_content})
                            self.session_logger.update_log(
                                session.log_path, chat_history, last_event=self._last_event_info(session)
                            )
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
                            chat_history.append(
                                {
                                    "role": "assistant",
                                    "content": "**CODING AI**\n\n⏳ *Working on revisions...*",
                                }
                            )
                            revision_pending_idx = len(chat_history) - 1
                            self.session_logger.update_log(
                                session.log_path, chat_history, last_event=self._last_event_info(session)
                            )
                            revision_status_msg = f"{status_prefix}⏳ Coding agent working on revisions..."
                            yield make_yield(chat_history, revision_status_msg, "")

                            # Run revision in a thread so we can stream output to live view
                            revision_result: list = [None, None]  # [response, error]
                            revision_complete = threading.Event()

                            def run_revision_thread():
                                try:
                                    coding_provider_instance.send_message(revision_request)
                                    resp = coding_provider_instance.get_response(timeout=coding_timeout)
                                    revision_result[0] = resp
                                except Exception as exc:
                                    revision_result[1] = exc
                                finally:
                                    revision_complete.set()

                            revision_thread = threading.Thread(target=run_revision_thread, daemon=True)
                            revision_thread.start()

                            # Poll message queue while revision runs (live stream updates)
                            rev_display_buffer = LiveStreamDisplayBuffer()
                            rev_render_state = LiveStreamRenderState()
                            rev_last_yield = 0.0
                            while not revision_complete.is_set() and not session.cancel_requested:
                                try:
                                    msg = message_queue.get(timeout=0.05)
                                    if msg[0] == "stream":
                                        chunk = msg[1]
                                        if chunk.strip():
                                            rev_display_buffer.append(chunk)
                                            now = time_module.time()
                                            if now - rev_last_yield >= min_yield_interval:
                                                rendered = build_live_stream_html(
                                                    rev_display_buffer.content, "CODING AI"
                                                )
                                                if rev_render_state.should_render(rendered):
                                                    yield make_yield(chat_history, revision_status_msg, rendered)
                                                    rev_render_state.record(rendered)
                                                    rev_last_yield = now
                                except queue.Empty:
                                    pass

                            revision_thread.join(timeout=1.0)
                            revision_response = revision_result[0]
                            revision_error = revision_result[1]

                            if revision_error:
                                chat_history[revision_pending_idx] = {
                                    "role": "assistant",
                                    "content": f"**CODING AI**\n\n❌ *Error: {revision_error}*",
                                }
                                session.active = False
                                session.provider = None
                                session.config = None
                                self.session_logger.update_log(
                                    session.log_path,
                                    chat_history,
                                    verification_attempts=verification_log,
                                    last_event=self._last_event_info(session),
                                )
                                break

                            if revision_response:
                                parsed_revision = parse_codex_output(revision_response)
                                chat_history[revision_pending_idx] = make_chat_message("CODING AI", parsed_revision)
                                last_coding_output = parsed_revision
                                self.session_logger.update_log(
                                    session.log_path,
                                    chat_history,
                                    verification_attempts=verification_log,
                                    last_event=self._last_event_info(session),
                                )
                            else:
                                chat_history[revision_pending_idx] = {
                                    "role": "assistant",
                                    "content": "**CODING AI**\n\n❌ *No response to revision request*",
                                }
                                self.session_logger.update_log(
                                    session.log_path,
                                    chat_history,
                                    verification_attempts=verification_log,
                                    last_event=self._last_event_info(session),
                                )
                                break

                            yield make_yield(
                                chat_history,
                                f"{status_prefix}✓ Revision complete, re-verifying...",
                                "",
                            )
                        else:
                            # Can't continue - session not active or max attempts reached
                            break

                    self.session_logger.update_log(
                        session.log_path,
                        chat_history,
                        verification_attempts=verification_log,
                        last_event=self._last_event_info(session),
                    )

                if verified is True:
                    final_status = "✓ Task completed and verified!"
                    completion_msg = "───────────── ✅ TASK COMPLETED (VERIFIED) ─────────────"
                    chat_history.append({"role": "user", "content": completion_msg})
                elif verified is None:
                    # Verification errored - already added error message above
                    final_status = "❌ Task failed - verification error"
                else:
                    final_status = (
                        f"❌ Task failed - verification failed " f"after {verification_attempt} attempt(s)"
                    )
                    completion_msg = "───────────── ❌ TASK FAILED (VERIFICATION) ─────────────"
                    if verification_feedback:
                        if len(verification_feedback) > 200:
                            completion_msg += f"\n\n*{verification_feedback[:200]}...*"
                        else:
                            completion_msg += f"\n\n*{verification_feedback}*"
                    chat_history.append({"role": "user", "content": completion_msg})
            else:
                final_status = (
                    f"❌ Task did not complete successfully\n\n*{completion_reason[0]}*"
                    if completion_reason[0]
                    else "❌ Task did not complete successfully"
                )
                failure_msg = "───────────── ❌ TASK FAILED ─────────────"
                if completion_reason[0]:
                    failure_msg += f"\n\n*{completion_reason[0]}*"
                chat_history.append({"role": "user", "content": failure_msg})

            if final_status:
                full_history.append(_history_entry("SYSTEM", f"\n\n[FINAL STATUS] {final_status}"))

            # Determine final session status based on both task completion and verification
            overall_success = False
            if session.cancel_requested:
                session_status = "cancelled"
            elif not task_success[0]:
                session_status = "failed"
            elif not verification_enabled:
                session_status = "completed"
                overall_success = True
            elif verified is True:
                session_status = "completed"
                overall_success = True
            else:
                # Task succeeded but verification failed or errored
                session_status = "failed"
                overall_success = False

            self.session_logger.update_log(
                session.log_path,
                chat_history,
                streaming_history=full_history if full_history else None,
                success=overall_success,
                completion_reason=completion_reason[0],
                status=session_status,
                verification_attempts=verification_log,
                final_status=final_status,
                last_event=self._last_event_info(session),
            )
            if session.log_path:
                final_status += f"\n\n*Session log: {session.log_path}*"
            final_summary = f"{status_prefix}{final_status}"

            # Store session state for follow-up messages
            session.chat_history = chat_history
            session.coding_account = coding_account

            # Show follow-up input if session can continue (Claude with successful task and verification)
            can_continue = session.active and overall_success
            if can_continue:
                final_status += "\n\n*Session active - you can send follow-up messages*"
                final_summary = f"{status_prefix}{final_status}"

            # Check for worktree changes to show merge section
            has_changes, merge_summary_text = self.check_worktree_changes(session_id)
            show_merge = has_changes and overall_success

            # Get available branches and rendered diff for merge target
            branches = []
            diff_html = ""
            if show_merge and session.project_path:
                try:
                    git_mgr = GitWorktreeManager(Path(session.project_path))
                    branches = git_mgr.get_branches()
                    parsed_diff = git_mgr.get_parsed_diff(session_id, session.worktree_base_commit)
                    diff_html = self._render_diff_html(parsed_diff)
                except Exception:
                    branches = ["main"]

            yield make_yield(
                chat_history,
                final_summary,
                "",
                summary=final_summary,
                interactive=False,  # Task description locked after work begins
                show_followup=can_continue,
                show_merge=show_merge,
                merge_summary=merge_summary_text if show_merge else "",
                branch_choices=branches if show_merge else None,
                diff_full=diff_html,
            )

        except Exception as e:  # pragma: no cover - defensive
            import traceback

            error_msg = f"❌ Error: {str(e)}\n\n```\n{traceback.format_exc()}\n```"
            session.active = False
            session.provider = None
            session.config = None
            yield make_yield(
                chat_history,
                error_msg,
                summary=error_msg,
                interactive=False,  # Task description locked after work begins
                show_followup=False,
                show_merge=False,
                merge_summary="",
            )

    def send_followup(  # noqa: C901
        self,
        session_id: str,
        followup_message: str,
        current_history: list,
        coding_agent: str = "",
        verification_agent: str = "",
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        verification_model: str | None = None,
        verification_reasoning: str | None = None,
    ) -> Iterator[tuple[list, str, gr.update, gr.update, gr.update]]:
        """Send a follow-up message, with optional provider handoff and verification.

        Args:
            session_id: The session ID for this follow-up
            followup_message: The follow-up message to send
            current_history: Current chat history from the UI
            coding_agent: Currently selected coding agent from dropdown
            verification_agent: Currently selected verification agent from dropdown
            coding_model: Preferred model selected in the Run tab
            coding_reasoning: Reasoning effort selected in the Run tab

        Yields:
            Tuples of (chat_history, live_stream, followup_input, followup_row, send_btn)
        """
        session = self.get_session(session_id)
        message_queue = queue.Queue()
        session.cancel_requested = False

        # Use stored history as base, but prefer current_history if it has more messages
        chat_history = (
            current_history if len(current_history) >= len(session.chat_history) else session.chat_history.copy()
        )
        task_description = session.task_description or ""
        verification_log: list[dict[str, object]] = []

        def make_followup_yield(
            history,
            live_stream: str = "",
            show_followup: bool = True,
            working: bool = False,
        ):
            """Format output for follow-up responses."""
            return (
                history,
                live_stream,
                gr.update(value="" if not working else followup_message),  # Clear input when not working
                gr.update(visible=show_followup),  # Follow-up row visibility
                gr.update(interactive=not working),  # Send button interactivity
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
            coding_model
            if coding_model is not None
            else (self.security_mgr.get_account_model(coding_agent) if has_account else "default")
        )
        requested_reasoning = normalize_reasoning_value(
            coding_reasoning
            if coding_reasoning is not None
            else (self.security_mgr.get_account_reasoning(coding_agent) if has_account else "default")
        )
        verification_model_value = verification_model or self.SAME_AS_CODING
        verification_reasoning_value = verification_reasoning or self.SAME_AS_CODING
        (
            actual_verification_account,
            resolved_verification_model,
            resolved_verification_reasoning,
        ) = self._resolve_verification_preferences(
            coding_agent if has_account else "",
            requested_model,
            requested_reasoning,
            verification_agent or self.SAME_AS_CODING,
            verification_model_value,
            verification_reasoning_value,
        )

        if has_account:
            try:
                self.security_mgr.set_account_model(coding_agent, requested_model)
                self.security_mgr.set_account_reasoning(coding_agent, requested_reasoning)
            except Exception:
                pass

        if not session.active:
            session.config = None

        # Check if we need provider handoff
        provider_changed = has_account and coding_agent != session.coding_account

        active_model = normalize_model_value(session.config.model_name if session.config else None)
        active_reasoning = normalize_reasoning_value(session.config.reasoning_effort if session.config else None)
        pref_changed = (
            has_account
            and not provider_changed
            and session.active
            and session.coding_account == coding_agent
            and (active_model != requested_model or active_reasoning != requested_reasoning)
        )

        handoff_needed = provider_changed or pref_changed

        if handoff_needed:
            # Stop old session if active
            if session.provider:
                try:
                    session.provider.stop_session()
                except Exception:
                    pass
                session.provider = None
                session.active = False

            # Start new provider
            coding_provider_type = accounts[coding_agent]
            coding_config = ModelConfig(
                provider=coding_provider_type,
                model_name=requested_model,
                account_name=coding_agent,
                reasoning_effort=None if requested_reasoning == "default" else requested_reasoning,
            )

            handoff_detail = f"{coding_agent} ({coding_provider_type}"
            if requested_model and requested_model != "default":
                handoff_detail += f", {requested_model}"
            if requested_reasoning and requested_reasoning != "default":
                handoff_detail += f", {requested_reasoning} reasoning"
            handoff_detail += ")"

            handoff_title = "PROVIDER HANDOFF" if provider_changed else "PREFERENCE UPDATE"
            handoff_msg = f"───────────── 🔄 {handoff_title} ─────────────\n\n" f"*Switching to {handoff_detail}*"
            chat_history.append({"role": "user", "content": handoff_msg})
            yield make_followup_yield(chat_history, "🔄 Switching providers...", working=True)

            new_provider = create_provider(coding_config)
            # Use worktree path if available, otherwise fall back to project path
            if session.worktree_path:
                working_dir = str(session.worktree_path)
            else:
                working_dir = session.project_path or str(Path.cwd())

            if not new_provider.start_session(working_dir, None):
                chat_history.append(
                    {
                        "role": "assistant",
                        "content": f"**CODING AI**\n\n❌ *Failed to start {coding_agent} session*",
                    }
                )
                session.config = None
                yield make_followup_yield(chat_history, "", show_followup=False)
                return

            session.provider = new_provider
            session.coding_account = coding_agent
            session.active = True
            session.config = coding_config

            # Include conversation context for the new provider
            context_summary = self._build_handoff_context(chat_history)
            followup_message = f"{context_summary}\n\n# Follow-up Request\n\n{followup_message}"

        if not session.active or not session.provider:
            chat_history.append({"role": "user", "content": f"**Follow-up**\n\n{followup_message}"})
            chat_history.append(
                {
                    "role": "assistant",
                    "content": "**CODING AI**\n\n❌ *Session expired. Please start a new task.*",
                }
            )
            session.active = False
            yield make_followup_yield(chat_history, "", show_followup=False)
            return

        # Add user's follow-up message to history
        if provider_changed:
            # Extract just the user's actual message after handoff context
            display_msg = followup_message.split("# Follow-up Request")[-1].strip()
            user_content = f"**Follow-up** (via {coding_agent})\n\n{display_msg}"
        else:
            user_content = f"**Follow-up**\n\n{followup_message}"
        chat_history.append({"role": "user", "content": user_content})

        # Add placeholder for AI response
        chat_history.append({"role": "assistant", "content": "**CODING AI**\n\n⏳ *Working...*"})
        pending_idx = len(chat_history) - 1

        yield make_followup_yield(chat_history, "⏳ Processing follow-up...", working=True)

        # Set up activity callback
        def on_activity(activity_type: str, detail: str):
            if activity_type == "stream":
                message_queue.put(("stream", detail))
            elif activity_type == "tool":
                message_queue.put(("activity", f"● {detail}"))
            elif activity_type == "text" and detail:
                message_queue.put(("activity", f"  ⎿ {detail[:80]}"))

        coding_provider = session.provider
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

        full_history = []  # List of (ai_name, content, timestamp) tuples
        current_ai = "CODING AI"
        display_buffer = LiveStreamDisplayBuffer()
        last_yield_time = 0.0
        min_yield_interval = 0.05
        render_state = LiveStreamRenderState()

        while not relay_complete.is_set() and not session.cancel_requested:
            try:
                msg = message_queue.get(timeout=0.02)
                msg_type = msg[0]

                if msg_type == "stream":
                    chunk = msg[1]
                    if chunk.strip():
                        full_history.append(_history_entry(current_ai, chunk))
                        display_buffer.append(chunk)
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            # Update chat bubble with inline live content
                            inline_html = build_inline_live_html(display_buffer.content, current_ai)
                            if render_state.should_render(inline_html):
                                chat_history[pending_idx] = {
                                    "role": "assistant",
                                    "content": inline_html,
                                }
                                yield make_followup_yield(chat_history, "", working=True)
                                render_state.record(inline_html)
                                last_yield_time = now

                elif msg_type == "activity":
                    now = time_module.time()
                    if now - last_yield_time >= min_yield_interval:
                        display_content = display_buffer.content
                        if display_content:
                            content = display_content + f"\n\n{msg[1]}"
                        else:
                            content = msg[1]
                        # Update chat bubble with inline live content
                        inline_html = build_inline_live_html(content, current_ai)
                        if render_state.should_render(inline_html):
                            chat_history[pending_idx] = {
                                "role": "assistant",
                                "content": inline_html,
                            }
                            yield make_followup_yield(chat_history, "", working=True)
                            render_state.record(inline_html)
                            last_yield_time = now

            except queue.Empty:
                now = time_module.time()
                if now - last_yield_time >= 0.3:
                    display_content = display_buffer.content
                    if display_content:
                        inline_html = build_inline_live_html(display_content, current_ai)
                        if render_state.should_render(inline_html):
                            chat_history[pending_idx] = {
                                "role": "assistant",
                                "content": inline_html,
                            }
                            yield make_followup_yield(chat_history, "", working=True)
                            render_state.record(inline_html)
                            last_yield_time = now

        relay_thread.join(timeout=1)

        # Update chat history with final response
        if error_holder[0]:
            chat_history[pending_idx] = {
                "role": "assistant",
                "content": f"**CODING AI**\n\n❌ *Error: {error_holder[0]}*",
            }
            session.active = False
            session.provider = None
            session.config = None
            self._update_session_log(session, chat_history, full_history)
            yield make_followup_yield(chat_history, "", show_followup=False)
            return

        if not response_holder[0]:
            chat_history[pending_idx] = {
                "role": "assistant",
                "content": "**CODING AI**\n\n❌ *No response received*",
            }
            self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)
            yield make_followup_yield(chat_history, "", show_followup=True)
            return

        parsed = parse_codex_output(response_holder[0])
        chat_history[pending_idx] = make_chat_message("CODING AI", parsed)
        last_coding_output = parsed

        # Update stored history
        session.chat_history = chat_history
        self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)

        yield make_followup_yield(chat_history, "", show_followup=True, working=True)

        # Run verification on follow-up
        verification_enabled = verification_agent != self.VERIFICATION_NONE
        verification_account_for_run = actual_verification_account if verification_enabled else None

        if verification_account_for_run and verification_account_for_run in accounts:
            # Verification loop (like start_chad_task)
            max_verification_attempts = 3
            verification_attempt = 0
            verified = False

            while not verified and verification_attempt < max_verification_attempts and not session.cancel_requested:
                verification_attempt += 1
                chat_history.append(
                    {
                        "role": "user",
                        "content": f"───────────── 🔍 VERIFICATION (Attempt {verification_attempt}) ─────────────",
                    }
                )
                self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)

                verify_status = (
                    f"🔍 Running verification " f"(attempt {verification_attempt}/{max_verification_attempts})..."
                )
                yield make_followup_yield(chat_history, verify_status, working=True)

                def verification_activity(activity_type: str, detail: str):
                    pass  # Quiet verification

                # Run verification in worktree so it can see the changes
                verification_path = str(session.worktree_path or session.project_path or Path.cwd())
                verified, verification_feedback = self._run_verification(
                    verification_path,
                    last_coding_output,
                    task_description,
                    verification_account_for_run,
                    on_activity=verification_activity,
                    verification_model=resolved_verification_model,
                    verification_reasoning=resolved_verification_reasoning,
                )

                status_label = "error" if verified is None else ("passed" if verified else "failed")
                verification_log.append(
                    {
                        "attempt": verification_attempt,
                        "status": status_label,
                        "feedback": verification_feedback,
                        "account": verification_account_for_run,
                    }
                )

                if verified is None:
                    # Verification error - stop
                    chat_history.append(
                        {
                            "role": "assistant",
                            "content": f"**VERIFICATION AI**\n\n❌ {verification_feedback}",
                        }
                    )
                    chat_history.append(
                        {
                            "role": "user",
                            "content": "───────────── ❌ VERIFICATION ERROR ─────────────",
                        }
                    )
                    self._update_session_log(
                        session, chat_history, full_history, verification_attempts=verification_log
                    )
                    break
                elif verified:
                    chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                    chat_history.append(
                        {
                            "role": "user",
                            "content": "───────────── ✅ VERIFICATION PASSED ─────────────",
                        }
                    )
                    self._update_session_log(
                        session, chat_history, full_history, verification_attempts=verification_log
                    )
                else:
                    chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                    self._update_session_log(
                        session, chat_history, full_history, verification_attempts=verification_log
                    )

                    # Check if we can revise
                    can_revise = (
                        session.active
                        and coding_provider.is_alive()
                        and verification_attempt < max_verification_attempts
                    )
                    if can_revise:
                        chat_history.append(
                            {
                                "role": "user",
                                "content": "───────────── 🔄 REVISION REQUESTED ─────────────",
                            }
                        )
                        chat_history.append(
                            {
                                "role": "assistant",
                                "content": "**CODING AI**\n\n⏳ *Working on revisions...*",
                            }
                        )
                        revision_idx = len(chat_history) - 1
                        self._update_session_log(
                            session, chat_history, full_history, verification_attempts=verification_log
                        )
                        yield make_followup_yield(chat_history, "🔄 Revision in progress...", working=True)

                        revision_request = (
                            "The verification agent found issues with your work. "
                            "Please address them:\n\n"
                            f"{verification_feedback}\n\n"
                            "Please fix these issues and confirm when done."
                        )
                        try:
                            coding_provider.send_message(revision_request)
                            revision_response = coding_provider.get_response(timeout=DEFAULT_CODING_TIMEOUT)
                        except Exception as exc:
                            chat_history[revision_idx] = {
                                "role": "assistant",
                                "content": f"**CODING AI**\n\n❌ *Error: {exc}*",
                            }
                            session.active = False
                            session.provider = None
                            session.config = None
                            self._update_session_log(
                                session, chat_history, full_history, verification_attempts=verification_log
                            )
                            break

                        if revision_response:
                            parsed_revision = parse_codex_output(revision_response)
                            chat_history[revision_idx] = make_chat_message("CODING AI", parsed_revision)
                            last_coding_output = parsed_revision
                            self._update_session_log(
                                session, chat_history, full_history, verification_attempts=verification_log
                            )
                        else:
                            chat_history[revision_idx] = {
                                "role": "assistant",
                                "content": "**CODING AI**\n\n❌ *No response to revision request*",
                            }
                            self._update_session_log(
                                session, chat_history, full_history, verification_attempts=verification_log
                            )
                            break

                        yield make_followup_yield(
                            chat_history,
                            "✓ Revision complete, re-verifying...",
                            working=True,
                        )
                    else:
                        # Can't continue - add failure message
                        chat_history.append(
                            {
                                "role": "user",
                                "content": "───────────── ❌ VERIFICATION FAILED ─────────────",
                            }
                        )
                        self._update_session_log(
                            session, chat_history, full_history, verification_attempts=verification_log
                        )
                        break

                # Incremental session log update after each verification attempt
                self._update_session_log(
                    session, chat_history, full_history, verification_attempts=verification_log
                )

        # Always update stored history and session log after follow-up completes
        session.chat_history = chat_history
        self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)

        yield make_followup_yield(chat_history, "", show_followup=True)

    def is_project_git_repo(self, project_path: str) -> bool:
        """Check if project path is a valid git repository."""
        if not project_path:
            return False
        path = Path(project_path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            return False
        git_mgr = GitWorktreeManager(path)
        return git_mgr.is_git_repo()

    def check_worktree_changes(self, session_id: str) -> tuple[bool, str]:
        """Check if worktree has changes and return summary."""
        session = self.get_session(session_id)
        if not session.worktree_path or not session.project_path:
            return False, ""

        git_mgr = GitWorktreeManager(Path(session.project_path))
        has_changes = git_mgr.has_changes(session_id)
        session.has_worktree_changes = has_changes

        if has_changes:
            summary = git_mgr.get_diff_summary(session_id, session.worktree_base_commit)
            return True, summary
        return False, ""

    def attempt_merge(
        self,
        session_id: str,
        commit_message: str = "",
        target_branch: str = "",
    ) -> tuple:
        """Attempt to merge worktree changes to a target branch.

        Returns 15 values for merge_outputs:
        [merge_section, changes_summary, conflict_section, conflict_info, conflicts_html,
         task_status, chatbot, start_btn, cancel_btn, live_stream, followup_row, task_description,
         merge_visibility_state, merge_section_header, diff_content]
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.worktree_path or not session.project_path:
            return (
                gr.update(visible=False), no_change, gr.update(visible=False),
                no_change, no_change, gr.update(value="❌ No worktree to merge.", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                "hidden", "", "",  # merge_visibility_state, merge_section_header, diff_content
            )

        try:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            msg = commit_message.strip() if commit_message else None
            branch = target_branch.strip() if target_branch else None
            success, conflicts, error_msg = git_mgr.merge_to_main(session_id, msg, branch)

            target_name = branch or git_mgr.get_main_branch()
            if success:
                # Cleanup worktree after successful merge
                git_mgr.cleanup_after_merge(session_id)
                session.worktree_path = None
                session.worktree_branch = None
                session.has_worktree_changes = False
                session.worktree_base_commit = None
                session.task_description = ""
                session.chat_history = []
                # Full reset - return tab to initial state
                # Use direct values where possible to match working make_yield pattern
                return (
                    gr.update(visible=False),                    # merge_section
                    "",                                          # changes_summary - direct value
                    gr.update(visible=False),                    # conflict_section
                    "",                                          # conflict_info - direct value
                    "",                                          # conflicts_html - direct value
                    gr.update(value=f"✓ Changes merged to {target_name}.", visible=True),
                    [],                                          # chatbot - direct empty list
                    gr.update(interactive=True),                 # start_btn - enable
                    gr.update(interactive=False),                # cancel_btn - disable
                    "",                                          # live_stream - direct value
                    gr.update(visible=False),                    # followup_row - hide
                    "",                                          # task_description - direct value
                    "hidden",                                    # merge_visibility_state - hide via JS
                    "",                                          # merge_section_header - clear
                    "",                                          # diff_content - clear diff view
                )
            elif conflicts:
                session.merge_conflicts = conflicts
                conflict_count = sum(len(c.hunks) for c in (conflicts or []))
                file_count = len(conflicts or [])
                conflict_msg = f"**{file_count} file(s)** with **{conflict_count} conflict(s)** need resolution."
                return (
                    gr.update(visible=False),                    # merge_section
                    no_change,                                   # changes_summary
                    gr.update(visible=True),                     # conflict_section
                    gr.update(value=conflict_msg),               # conflict_info
                    gr.update(value=self._render_conflicts_html(conflicts or [])),
                    no_change,                                   # task_status
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    "hidden", "", "",                       # merge_visibility_state, merge_section_header, diff_content
                )
            else:
                error_detail = error_msg or "Merge failed. Check git status and commit hooks."
                return (
                    gr.update(visible=True),                     # merge_section remains visible
                    no_change,                                   # changes_summary unchanged
                    gr.update(visible=False),                    # conflict_section hidden
                    gr.update(value=""),                         # conflict_info cleared
                    gr.update(value=""),                         # conflicts_html cleared
                    gr.update(value=f"❌ {error_detail}", visible=True),
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    "visible", no_change, no_change,        # merge_visibility_state, merge_section_header, diff_content
                )
        except Exception as e:
            return (
                no_change, no_change, no_change, no_change, no_change,
                gr.update(value=f"❌ Merge error: {e}", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change, no_change,            # merge_visibility_state, merge_section_header, diff_content
            )

    def _render_conflicts_html(self, conflicts: list[MergeConflict]) -> str:
        """Render conflicts as HTML for side-by-side display with inline styles."""
        if not conflicts:
            return "<p style='color: #d8dee9;'>No conflicts to display.</p>"

        # Inline styles for conflict viewer (ensures visibility regardless of CSS loading)
        styles = {
            "viewer": "font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; "
            "border: 1px solid #3b4252; border-radius: 8px; overflow: hidden; margin: 12px 0;",
            "file": "border-bottom: 1px solid #3b4252;",
            "file_header": "background: #2e3440; padding: 8px 12px; margin: 0; "
            "font-size: 0.9rem; color: #88c0d0; border-bottom: 1px solid #3b4252;",
            "hunk": "margin: 0; border-bottom: 1px solid #4c566a;",
            "context": "padding: 4px 12px; background: #2e3440; color: #d8dee9;",
            "comparison": "display: flex;",
            "side": "flex: 1; padding: 8px 12px; overflow-x: auto; min-width: 0;",
            "original": "background: #3b2828; border-right: 1px solid #4c566a;",
            "incoming": "background: #283b28;",
            "side_header": "font-weight: bold; margin-bottom: 8px; padding-bottom: 4px; "
            "border-bottom: 1px solid #4c566a;",
            "original_header": "color: #bf616a;",
            "incoming_header": "color: #a3be8c;",
            "content": "color: #e5e9f0;",
            "pre": "margin: 2px 0; white-space: pre-wrap; word-break: break-all;",
        }

        html_parts = [f'<div class="conflict-viewer" style="{styles["viewer"]}">']

        for conflict in conflicts:
            html_parts.append(f'<div class="conflict-file" style="{styles["file"]}">')
            file_path_escaped = html.escape(conflict.file_path)
            html_parts.append(
                f'<h4 class="conflict-file-header" style="{styles["file_header"]}">'
                f"{file_path_escaped}</h4>"
            )

            for hunk in conflict.hunks:
                hunk_attrs = f'data-file="{file_path_escaped}" data-hunk="{hunk.hunk_index}"'
                html_parts.append(f'<div class="conflict-hunk" style="{styles["hunk"]}" {hunk_attrs}>')

                # Context before
                if hunk.context_before:
                    html_parts.append(f'<div class="conflict-context" style="{styles["context"]}">')
                    for line in hunk.context_before:
                        html_parts.append(f'<pre style="{styles["pre"]}">{html.escape(line)}</pre>')
                    html_parts.append("</div>")

                # Side-by-side comparison
                html_parts.append(f'<div class="conflict-comparison" style="{styles["comparison"]}">')

                # Original (HEAD) side
                html_parts.append(
                    f'<div class="conflict-side conflict-original" '
                    f'style="{styles["side"]} {styles["original"]}">'
                )
                html_parts.append(
                    f'<div class="conflict-side-header" '
                    f'style="{styles["side_header"]} {styles["original_header"]}">Original (HEAD)</div>'
                )
                html_parts.append(f'<div class="conflict-side-content" style="{styles["content"]}">')
                for line in hunk.original_lines:
                    html_parts.append(f'<pre style="{styles["pre"]}">{html.escape(line)}</pre>')
                html_parts.append("</div></div>")

                # Incoming (worktree) side
                html_parts.append(
                    f'<div class="conflict-side conflict-incoming" '
                    f'style="{styles["side"]} {styles["incoming"]}">'
                )
                html_parts.append(
                    f'<div class="conflict-side-header" '
                    f'style="{styles["side_header"]} {styles["incoming_header"]}">Incoming (Changes)</div>'
                )
                html_parts.append(f'<div class="conflict-side-content" style="{styles["content"]}">')
                for line in hunk.incoming_lines:
                    html_parts.append(f'<pre style="{styles["pre"]}">{html.escape(line)}</pre>')
                html_parts.append("</div></div>")

                html_parts.append("</div>")  # conflict-comparison

                # Context after
                if hunk.context_after:
                    html_parts.append(f'<div class="conflict-context" style="{styles["context"]}">')
                    for line in hunk.context_after:
                        html_parts.append(f'<pre style="{styles["pre"]}">{html.escape(line)}</pre>')
                    html_parts.append("</div>")

                html_parts.append("</div>")  # conflict-hunk

            html_parts.append("</div>")  # conflict-file

        html_parts.append("</div>")  # conflict-viewer
        return "\n".join(html_parts)

    def _render_diff_html(self, file_diffs: list[FileDiff]) -> str:
        """Render file diffs as side-by-side HTML."""
        if not file_diffs:
            return "<p style='color: #d8dee9;'>No changes to display.</p>"

        html_parts = ['<div class="diff-viewer">']

        for file_diff in file_diffs:
            html_parts.append('<div class="diff-file">')

            # File header
            file_path_escaped = html.escape(file_diff.new_path)
            header_extra = ""
            if file_diff.is_new:
                header_extra = '<span class="new-file">(new file)</span>'
            elif file_diff.is_deleted:
                header_extra = '<span class="deleted-file">(deleted)</span>'

            html_parts.append(f'<div class="diff-file-header">{file_path_escaped} {header_extra}</div>')

            if file_diff.is_binary:
                html_parts.append('<div class="diff-binary">Binary file changed</div>')
                html_parts.append("</div>")
                continue

            # Render each hunk side-by-side
            for hunk in file_diff.hunks:
                html_parts.append('<div class="diff-hunk">')
                html_parts.append('<div class="diff-comparison">')

                # Build side-by-side lines
                left_lines: list[tuple[int | None, str, str]] = []  # (line_no, content, type)
                right_lines: list[tuple[int | None, str, str]] = []

                # Process lines to build left/right sides
                for diff_line in hunk.lines:
                    if diff_line.line_type == "context":
                        left_lines.append((diff_line.old_line_no, diff_line.content, "context"))
                        right_lines.append((diff_line.new_line_no, diff_line.content, "context"))
                    elif diff_line.line_type == "removed":
                        left_lines.append((diff_line.old_line_no, diff_line.content, "removed"))
                    elif diff_line.line_type == "added":
                        right_lines.append((diff_line.new_line_no, diff_line.content, "added"))

                # Pad shorter side to match
                max_len = max(len(left_lines), len(right_lines))
                while len(left_lines) < max_len:
                    left_lines.append((None, "", "empty"))
                while len(right_lines) < max_len:
                    right_lines.append((None, "", "empty"))

                # Left side (original)
                html_parts.append('<div class="diff-side diff-side-left">')
                html_parts.append('<div class="diff-side-header">Original</div>')
                for line_no, content, line_type in left_lines:
                    line_no_str = str(line_no) if line_no else ""
                    content_escaped = html.escape(content)
                    html_parts.append(
                        f'<div class="diff-line {line_type}">'
                        f'<span class="diff-line-no">{line_no_str}</span>'
                        f'<span class="diff-line-content">{content_escaped}</span>'
                        f"</div>"
                    )
                html_parts.append("</div>")

                # Right side (modified)
                html_parts.append('<div class="diff-side diff-side-right">')
                html_parts.append('<div class="diff-side-header">Modified</div>')
                for line_no, content, line_type in right_lines:
                    line_no_str = str(line_no) if line_no else ""
                    content_escaped = html.escape(content)
                    html_parts.append(
                        f'<div class="diff-line {line_type}">'
                        f'<span class="diff-line-no">{line_no_str}</span>'
                        f'<span class="diff-line-content">{content_escaped}</span>'
                        f"</div>"
                    )
                html_parts.append("</div>")

                html_parts.append("</div>")  # diff-comparison
                html_parts.append("</div>")  # diff-hunk

            html_parts.append("</div>")  # diff-file

        html_parts.append("</div>")  # diff-viewer
        return "\n".join(html_parts)

    def resolve_all_conflicts(self, session_id: str, use_incoming: bool) -> tuple:
        """Resolve all conflicts by choosing all original or all incoming.

        Returns 15 values for merge_outputs.
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.project_path:
            return (
                no_change, no_change, gr.update(visible=False),
                no_change, no_change, gr.update(value="❌ No project path set.", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change, no_change,  # merge_visibility_state, merge_section_header, diff_content
            )

        try:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            git_mgr.resolve_all_conflicts(use_incoming)

            # Complete the merge
            if git_mgr.complete_merge():
                git_mgr.cleanup_after_merge(session_id)
                session.worktree_path = None
                session.worktree_branch = None
                session.merge_conflicts = None
                session.has_worktree_changes = False
                session.worktree_base_commit = None
                session.task_description = ""
                session.chat_history = []
                # Full reset - return tab to initial state
                # Use direct values where possible to match working make_yield pattern
                return (
                    gr.update(visible=False),                    # merge_section
                    "",                                          # changes_summary - direct value
                    gr.update(visible=False),                    # conflict_section
                    "",                                          # conflict_info - direct value
                    "",                                          # conflicts_html - direct value
                    gr.update(value="✓ All conflicts resolved. Merge complete.", visible=True),
                    [],                                          # chatbot - direct empty list
                    gr.update(interactive=True),                 # start_btn - enable
                    gr.update(interactive=False),                # cancel_btn - disable
                    "",                                          # live_stream - direct value
                    gr.update(visible=False),                    # followup_row - hide
                    "",                                          # task_description - direct value
                    "hidden",                                    # merge_visibility_state - hide via JS
                    "",                                          # merge_section_header - clear
                    "",                                          # diff_content - clear diff view
                )
            else:
                return (
                    no_change, no_change, no_change, no_change, no_change,
                    gr.update(value="❌ Failed to complete merge. Check git status.", visible=True),
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    no_change, no_change, no_change,  # merge_visibility_state, merge_section_header, diff_content
                )
        except Exception as e:
            return (
                no_change, no_change, no_change, no_change, no_change,
                gr.update(value=f"❌ Error resolving conflicts: {e}", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change, no_change,  # merge_visibility_state, merge_section_header, diff_content
            )

    def abort_merge_action(self, session_id: str) -> tuple:
        """Abort an in-progress merge, return to merge section.

        Returns 15 values for merge_outputs.
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.project_path:
            return (
                no_change, no_change, gr.update(visible=False),
                no_change, no_change, no_change,
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change, no_change,  # merge_visibility_state, merge_section_header, diff_content
            )

        git_mgr = GitWorktreeManager(Path(session.project_path))
        git_mgr.abort_merge()
        session.merge_conflicts = None

        # Check if worktree still has changes - show merge section if so
        has_changes, summary = self.check_worktree_changes(session_id)
        visibility_state = "visible" if has_changes else "hidden"
        header_text = "### Changes Ready to Merge" if has_changes else ""

        return (
            gr.update(visible=has_changes),              # merge_section
            gr.update(value=summary if has_changes else ""),  # changes_summary
            gr.update(visible=False),                    # conflict_section
            no_change,                                   # conflict_info
            no_change,                                   # conflicts_html
            gr.update(value="⚠️ Merge aborted. Changes remain in worktree.", visible=True),
            no_change, no_change, no_change, no_change, no_change, no_change,  # no tab reset on abort
            visibility_state,                            # merge_visibility_state
            header_text,                                 # merge_section_header
            no_change,                                   # diff_content - keep as is
        )

    def discard_worktree_changes(self, session_id: str) -> tuple:
        """Discard worktree and all changes, reset merge UI but keep task description.

        Returns 15 values for merge_outputs. Task description is preserved so user
        can retry the task with the same description.
        """
        session = self.get_session(session_id)
        if session.worktree_path and session.project_path:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            git_mgr.delete_worktree(session_id)
            session.worktree_path = None
            session.worktree_branch = None
            session.has_worktree_changes = False
            session.merge_conflicts = None
            session.worktree_base_commit = None
            session.chat_history = []

        # Reset merge UI but keep task description for retry
        # Use direct values where possible to match working make_yield pattern
        return (
            gr.update(visible=False),                    # merge_section
            "",                                          # changes_summary - direct value
            gr.update(visible=False),                    # conflict_section
            "",                                          # conflict_info - direct value
            "",                                          # conflicts_html - direct value
            gr.update(value="🗑️ Changes discarded.", visible=True),  # task_status
            [],                                          # chatbot - direct empty list
            gr.update(interactive=True),                 # start_btn - enable
            gr.update(interactive=False),                # cancel_btn - disable
            "",                                          # live_stream - direct value
            gr.update(visible=False),                    # followup_row - hide
            gr.update(value=session.task_description or "", interactive=True),  # task_description - enable editing
            "hidden",                                    # merge_visibility_state - hide via JS
            "",                                          # merge_section_header - clear
            "",                                          # diff_content - clear diff view
        )

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

    def _last_event_info(self, session: Session) -> dict | None:
        """Return the last event snapshot from the provider if available."""
        provider = getattr(session, "provider", None)
        info = getattr(provider, "last_event_info", None) if provider else None
        return info if info else None

    def _update_session_log(
        self,
        session: Session,
        chat_history: list,
        streaming_history: list[tuple[str, str] | tuple[str, str, str]] | None = None,
        verification_attempts: list | None = None,
    ):
        """Update the session log with current state.

        Args:
            session: The session to update
            chat_history: Current chat history
            streaming_history: Optional streaming history as (ai_name, content, timestamp) tuples
        """
        if session.log_path:
            self.session_logger.update_log(
                session.log_path,
                chat_history,
                streaming_history=streaming_history,
                status="continued",
                verification_attempts=verification_attempts,
                last_event=self._last_event_info(session),
            )

    def _create_session_ui(self, session_id: str, is_first: bool = False):
        """Create UI components for a single session within @gr.render.

        Args:
            session_id: The session ID to create UI for
            is_first: Whether this is the first session (adds elem_ids for tests)
        """
        session = self.get_session(session_id)
        default_path = os.environ.get("CHAD_PROJECT_PATH", str(Path.cwd()))

        accounts_map = self.security_mgr.list_accounts()
        account_choices = list(accounts_map.keys())
        role_assignments = self.security_mgr.list_role_assignments()
        initial_coding = role_assignments.get("CODING", "")

        # Auto-select first provider if no coding agent is assigned
        if (not initial_coding or initial_coding not in account_choices) and account_choices:
            initial_coding = account_choices[0]
            # Persist the auto-selection
            try:
                self.security_mgr.assign_role(initial_coding, "CODING")
            except Exception:
                pass

        # Get ready status after any auto-assignment
        is_ready, _ = self.get_role_config_status()

        none_label = (
            "None (disable verification)"
            if self.VERIFICATION_NONE_LABEL in account_choices
            else self.VERIFICATION_NONE_LABEL
        )
        verification_choices = [
            (self.SAME_AS_CODING, self.SAME_AS_CODING),
            (none_label, self.VERIFICATION_NONE),
            *[(account, account) for account in account_choices],
        ]
        stored_verification = self.security_mgr.get_verification_agent()
        initial_verification = stored_verification if stored_verification in account_choices else self.SAME_AS_CODING

        # Get initial model/reasoning choices for coding agent
        coding_model_choices = self.get_models_for_account(initial_coding) if initial_coding else ["default"]
        if not coding_model_choices:
            coding_model_choices = ["default"]
        stored_coding_model = self.security_mgr.get_account_model(initial_coding) if initial_coding else "default"
        coding_model_value = (
            stored_coding_model if stored_coding_model in coding_model_choices else coding_model_choices[0]
        )

        coding_provider_type = accounts_map.get(initial_coding, "")
        coding_reasoning_choices = (
            self.get_reasoning_choices(coding_provider_type, initial_coding) if coding_provider_type else ["default"]
        )
        if not coding_reasoning_choices:
            coding_reasoning_choices = ["default"]
        stored_coding_reasoning = (
            self.security_mgr.get_account_reasoning(initial_coding) if initial_coding else "default"
        )
        coding_reasoning_value = (
            stored_coding_reasoning
            if stored_coding_reasoning in coding_reasoning_choices
            else coding_reasoning_choices[0]
        )

        verif_state = self._build_verification_dropdown_state(
            initial_coding,
            initial_verification,
            coding_model_value,
            coding_reasoning_value,
        )

        with gr.Row(
            elem_id="run-top-row" if is_first else None,
            elem_classes=["run-top-row"],
            equal_height=True,
        ):
            with gr.Column(scale=1):
                with gr.Row(equal_height=True):
                    with gr.Column(scale=3, min_width=260):
                        project_path = gr.Textbox(
                            label="Project Path",
                            placeholder="/path/to/project",
                            value=default_path,
                            scale=3,
                            key=f"project-path-{session_id}",
                        )
                        with gr.Row(
                            elem_id="role-status-row" if is_first else None,
                            elem_classes=["role-status-row"],
                        ):
                            role_status = gr.Markdown(
                                self.format_role_status(),
                                key=f"role-status-{session_id}",
                                elem_id="role-config-status" if is_first else None,
                                elem_classes=["role-config-status"],
                            )
                            log_path = session.log_path
                            session_log_btn = gr.DownloadButton(
                                label="Session Log" if not log_path else f"📄 {log_path.name}",
                                value=str(log_path) if log_path else None,
                                visible=log_path is not None,
                                variant="secondary",
                                size="sm",
                                scale=0,
                                min_width=140,
                                key=f"log-btn-{session_id}",
                                elem_id="session-log-btn" if is_first else None,
                                elem_classes=["session-log-btn"],
                            )
                    with gr.Column(scale=1, min_width=200):
                        coding_agent = gr.Dropdown(
                            choices=account_choices,
                            value=initial_coding if initial_coding else None,
                            label="Coding Agent",
                            scale=1,
                            min_width=200,
                            key=f"coding-agent-{session_id}",
                        )
                        coding_model = gr.Dropdown(
                            choices=coding_model_choices,
                            value=coding_model_value,
                            label="Preferred Model",
                            allow_custom_value=True,
                            scale=1,
                            min_width=200,
                            key=f"coding-model-{session_id}",
                            interactive=bool(initial_coding and initial_coding in account_choices),
                        )
                        coding_reasoning = gr.Dropdown(
                            choices=coding_reasoning_choices,
                            value=coding_reasoning_value,
                            label="Reasoning Effort",
                            allow_custom_value=True,
                            scale=1,
                            min_width=200,
                            key=f"coding-reasoning-{session_id}",
                            interactive=bool(initial_coding and initial_coding in account_choices),
                        )
                    with gr.Column(scale=1, min_width=200):
                        verification_agent = gr.Dropdown(
                            choices=verification_choices,
                            value=initial_verification,
                            label="Verification Agent",
                            scale=1,
                            min_width=200,
                            key=f"verification-agent-{session_id}",
                        )
                        verification_model = gr.Dropdown(
                            choices=verif_state.model_choices,
                            value=verif_state.model_value,
                            label="Verification Preferred Model",
                            allow_custom_value=True,
                            scale=1,
                            min_width=200,
                            key=f"verification-model-{session_id}",
                            interactive=verif_state.interactive,
                        )
                        verification_reasoning = gr.Dropdown(
                            choices=verif_state.reasoning_choices,
                            value=verif_state.reasoning_value,
                            label="Verification Reasoning Effort",
                            allow_custom_value=True,
                            scale=1,
                            min_width=200,
                            key=f"verification-reasoning-{session_id}",
                            interactive=verif_state.interactive,
                        )
            cancel_btn = gr.Button(
                "🛑 Cancel",
                variant="stop",
                interactive=False,
                key=f"cancel-btn-{session_id}",
                elem_id="cancel-task-btn" if is_first else None,
                elem_classes=["cancel-task-btn"],
                min_width=40,
                scale=0,
            )

        # Task status header - always in DOM but CSS hides when empty
        # This ensures JavaScript can find it for merge section visibility logic
        task_status = gr.Markdown(
            "",
            visible=True,
            key=f"task-status-{session_id}",
            elem_id="task-status-header" if is_first else None,
            elem_classes=["task-status-header"],
        )

        # Agent communication view
        with gr.Column(elem_classes=["agent-panel"]):
            gr.Markdown("### Agent Communication")
            with gr.Column(elem_classes=["task-entry-bubble"] if is_first else []):
                task_description = gr.TextArea(
                    label="Task Description",
                    placeholder="Describe what you want done...",
                    lines=4,
                    key=f"task-desc-{session_id}",
                )
                start_btn = gr.Button(
                    "▶ Start Task",
                    variant="primary",
                    interactive=is_ready,
                    key=f"start-btn-{session_id}",
                    elem_id="start-task-btn" if is_first else None,
                    elem_classes=["start-task-btn"],
                )

            chatbot = gr.Chatbot(
                height=400,
                key=f"chatbot-{session_id}",
                elem_id="agent-chatbot" if is_first else None,
                allow_tags=["img", "div", "span", "pre", "code"],  # Allow inline screenshots and code
            )

        # Live stream panel hidden - content now streams inline in chat bubbles
        live_stream = gr.Markdown("", visible=False, elem_id="live-stream-box" if is_first else None)

        with gr.Row(visible=False, key=f"followup-row-{session_id}") as followup_row:
            followup_input = gr.TextArea(
                label="Continue conversation...",
                placeholder="Ask for changes or additional work...",
                lines=2,
                scale=5,
                key=f"followup-input-{session_id}",
            )
            send_followup_btn = gr.Button(
                "Send",
                variant="primary",
                interactive=False,
                scale=1,
                key=f"send-followup-{session_id}",
            )

        # Merge section - shown when worktree has changes
        # Note: visibility controlled via merge_visibility_state due to Gradio 6 Column visibility bug
        with gr.Column(visible=False, key=f"merge-section-{session_id}",
                       elem_classes=["merge-section"]) as merge_section:
            # Hidden state element controls visibility - JS watches this value
            # "visible" = show section, "hidden" = hide section
            # Note: Using visible=True with CSS hiding so element is always in DOM for JS to find
            merge_visibility_state = gr.Textbox(
                value="hidden",
                visible=True,
                elem_classes=["merge-visibility-state", "visually-hidden"],
                elem_id=f"merge-visibility-{session_id}",
                key=f"merge-visibility-{session_id}",
                container=False,
            )
            merge_section_header = gr.Markdown("")  # Populated when changes exist
            changes_summary = gr.Markdown(
                "",
                key=f"changes-summary-{session_id}",
            )
            with gr.Accordion("View Changes", open=False):
                diff_content = gr.HTML(
                    value="",
                    elem_id=f"diff-content-{session_id}",
                )
            with gr.Row():
                merge_commit_msg = gr.Textbox(
                    label="Commit Message",
                    placeholder="Describe the changes being merged...",
                    value="",
                    scale=3,
                    key=f"merge-commit-msg-{session_id}",
                )
                merge_target_branch = gr.Dropdown(
                    label="Target Branch",
                    choices=["main"],
                    value="main",
                    scale=1,
                    key=f"merge-target-branch-{session_id}",
                )
            with gr.Row():
                accept_merge_btn = gr.Button(
                    "✓ Accept & Merge",
                    variant="primary",
                    interactive=True,
                    key=f"accept-merge-{session_id}",
                    elem_classes=["accept-merge-btn"],
                )
                discard_btn = gr.Button(
                    "✗ Discard Changes",
                    variant="stop",
                    key=f"discard-{session_id}",
                )

        # Conflict resolution section - shown when merge has conflicts
        with gr.Column(visible=False, key=f"conflict-section-{session_id}",
                       elem_classes=["conflict-section"]) as conflict_section:
            gr.Markdown("### Merge Conflicts Detected")
            conflict_info = gr.Markdown(
                "",
                key=f"conflict-info-{session_id}",
            )
            conflicts_html = gr.HTML(
                "",
                key=f"conflict-display-{session_id}",
            )
            with gr.Row():
                accept_all_ours_btn = gr.Button(
                    "Accept All Original",
                    variant="secondary",
                    key=f"accept-ours-{session_id}",
                )
                accept_all_theirs_btn = gr.Button(
                    "Accept All Incoming",
                    variant="secondary",
                    key=f"accept-theirs-{session_id}",
                )
                abort_merge_btn = gr.Button(
                    "Abort Merge",
                    variant="stop",
                    key=f"abort-merge-{session_id}",
                )

        # Event handlers - must be defined inside @gr.render
        def start_task_wrapper(
            proj_path,
            task_desc,
            coding,
            verification,
            c_model,
            c_reason,
            v_model,
            v_reason,
        ):
            yield from self.start_chad_task(
                session_id,
                proj_path,
                task_desc,
                coding,
                verification,
                c_model,
                c_reason,
                v_model,
                v_reason,
            )

        def cancel_wrapper():
            return self.cancel_task(session_id)

        def followup_wrapper(msg, history, coding, verification, c_model, c_reason, v_model, v_reason):
            yield from self.send_followup(
                session_id,
                msg,
                history,
                coding,
                verification,
                c_model,
                c_reason,
                v_model,
                v_reason,
            )

        def verification_dropdown_updates(
            coding_value,
            verification_value,
            coding_model_value,
            coding_reasoning_value,
            current_verif_model,
            current_verif_reasoning,
        ):
            state = self._build_verification_dropdown_state(
                coding_value,
                verification_value,
                coding_model_value,
                coding_reasoning_value,
                current_verif_model,
                current_verif_reasoning,
            )
            return (
                gr.update(
                    choices=state.model_choices,
                    value=state.model_value,
                    interactive=state.interactive,
                ),
                gr.update(
                    choices=state.reasoning_choices,
                    value=state.reasoning_value,
                    interactive=state.interactive,
                ),
            )

        start_btn.click(
            start_task_wrapper,
            inputs=[
                project_path,
                task_description,
                coding_agent,
                verification_agent,
                coding_model,
                coding_reasoning,
                verification_model,
                verification_reasoning,
            ],
            outputs=[
                chatbot,
                live_stream,
                task_status,
                project_path,
                task_description,
                start_btn,
                cancel_btn,
                role_status,
                session_log_btn,
                followup_input,
                followup_row,
                send_followup_btn,
                merge_section,
                changes_summary,
                merge_target_branch,
                diff_content,
                merge_visibility_state,  # Controls visibility via JS
                merge_section_header,    # Dynamic header text
            ],
        )

        cancel_btn.click(cancel_wrapper, outputs=[live_stream])

        send_followup_btn.click(
            followup_wrapper,
            inputs=[
                followup_input,
                chatbot,
                coding_agent,
                verification_agent,
                coding_model,
                coding_reasoning,
                verification_model,
                verification_reasoning,
            ],
            outputs=[
                chatbot,
                live_stream,
                followup_input,
                followup_row,
                send_followup_btn,
            ],
        )

        # Merge button handlers
        def merge_wrapper(commit_msg, target_branch):
            return self.attempt_merge(session_id, commit_msg, target_branch)

        def discard_wrapper():
            return self.discard_worktree_changes(session_id)

        def accept_ours_wrapper():
            return self.resolve_all_conflicts(session_id, use_incoming=False)

        def accept_theirs_wrapper():
            return self.resolve_all_conflicts(session_id, use_incoming=True)

        def abort_wrapper():
            return self.abort_merge_action(session_id)

        # Full reset outputs - includes components needed to reset tab to initial state
        # Note: merge_visibility_state at index 12 controls visibility via JS (Gradio 6 Column bug workaround)
        merge_outputs = [
            merge_section,          # 0: Hide merge section (Gradio visibility - may not work)
            changes_summary,        # 1: Clear summary
            conflict_section,       # 2: Hide conflict section
            conflict_info,          # 3: Clear conflict info
            conflicts_html,         # 4: Clear conflicts display
            task_status,            # 5: Show status message
            chatbot,                # 6: Clear chat history
            start_btn,              # 7: Re-enable start button
            cancel_btn,             # 8: Disable cancel button
            live_stream,            # 9: Clear live stream
            followup_row,           # 10: Hide followup row
            task_description,       # 11: Clear task description
            merge_visibility_state,  # 12: Set to "hidden" to hide section via JS
            merge_section_header,    # 13: Clear header when hiding
            diff_content,            # 14: Clear diff view inside accordion
        ]

        accept_merge_btn.click(
            merge_wrapper,
            inputs=[merge_commit_msg, merge_target_branch],
            outputs=merge_outputs,
        )

        discard_btn.click(
            discard_wrapper,
            outputs=merge_outputs,
        )

        accept_all_ours_btn.click(
            accept_ours_wrapper,
            outputs=merge_outputs,
        )

        accept_all_theirs_btn.click(
            accept_theirs_wrapper,
            outputs=merge_outputs,
        )

        abort_merge_btn.click(
            abort_wrapper,
            outputs=merge_outputs,
        )

        # Handler for coding agent selection change
        def on_coding_agent_change(selected_account, verification_value, current_verif_model, current_verif_reasoning):
            """Update role assignment, status, and dropdowns when coding agent changes."""
            if not selected_account:
                verif_model_update, verif_reasoning_update = verification_dropdown_updates(
                    None,
                    verification_value,
                    None,
                    None,
                    current_verif_model,
                    current_verif_reasoning,
                )
                return (
                    gr.update(value=self.format_role_status()),
                    gr.update(interactive=False),
                    gr.update(choices=["default"], value="default", interactive=False),
                    gr.update(choices=["default"], value="default", interactive=False),
                    verif_model_update,
                    verif_reasoning_update,
                )

            # Assign the coding role
            try:
                self.security_mgr.assign_role(selected_account, "CODING")
            except Exception:
                pass

            # Get updated status
            is_ready, _ = self.get_role_config_status()
            status_text = self.format_role_status()

            # Get model choices for the selected account
            model_choices = self.get_models_for_account(selected_account)
            if not model_choices:
                model_choices = ["default"]
            stored_model = self.security_mgr.get_account_model(selected_account) or "default"
            model_value = stored_model if stored_model in model_choices else model_choices[0]

            # Get reasoning choices
            accounts = self.security_mgr.list_accounts()
            provider_type = accounts.get(selected_account, "")
            reasoning_choices = self.get_reasoning_choices(provider_type, selected_account)
            if not reasoning_choices:
                reasoning_choices = ["default"]
            stored_reasoning = self.security_mgr.get_account_reasoning(selected_account) or "default"
            reasoning_value = stored_reasoning if stored_reasoning in reasoning_choices else reasoning_choices[0]

            verif_model_update, verif_reasoning_update = verification_dropdown_updates(
                selected_account,
                verification_value,
                model_value,
                reasoning_value,
                current_verif_model,
                current_verif_reasoning,
            )

            return (
                gr.update(value=status_text),
                gr.update(interactive=is_ready),
                gr.update(choices=model_choices, value=model_value, interactive=True),
                gr.update(choices=reasoning_choices, value=reasoning_value, interactive=True),
                verif_model_update,
                verif_reasoning_update,
            )

        coding_agent.change(
            on_coding_agent_change,
            inputs=[coding_agent, verification_agent, verification_model, verification_reasoning],
            outputs=[
                role_status,
                start_btn,
                coding_model,
                coding_reasoning,
                verification_model,
                verification_reasoning,
            ],
        )

        def on_verification_agent_change(
            selected_verification,
            coding_value,
            coding_model_value,
            coding_reasoning_value,
            current_verif_model,
            current_verif_reasoning,
        ):
            return verification_dropdown_updates(
                coding_value,
                selected_verification,
                coding_model_value,
                coding_reasoning_value,
                current_verif_model,
                current_verif_reasoning,
            )

        verification_agent.change(
            on_verification_agent_change,
            inputs=[
                verification_agent,
                coding_agent,
                coding_model,
                coding_reasoning,
                verification_model,
                verification_reasoning,
            ],
            outputs=[verification_model, verification_reasoning],
        )

        def on_coding_model_change(
            model_value,
            coding_value,
            verification_value,
            coding_reasoning_value,
            current_verif_model,
            current_verif_reasoning,
        ):
            return verification_dropdown_updates(
                coding_value,
                verification_value,
                model_value,
                coding_reasoning_value,
                current_verif_model,
                current_verif_reasoning,
            )

        coding_model.change(
            on_coding_model_change,
            inputs=[
                coding_model,
                coding_agent,
                verification_agent,
                coding_reasoning,
                verification_model,
                verification_reasoning,
            ],
            outputs=[verification_model, verification_reasoning],
        )

        def on_coding_reasoning_change(
            reasoning_value,
            coding_value,
            verification_value,
            coding_model_value,
            current_verif_model,
            current_verif_reasoning,
        ):
            return verification_dropdown_updates(
                coding_value,
                verification_value,
                coding_model_value,
                reasoning_value,
                current_verif_model,
                current_verif_reasoning,
            )

        coding_reasoning.change(
            on_coding_reasoning_change,
            inputs=[
                coding_reasoning,
                coding_agent,
                verification_agent,
                coding_model,
                verification_model,
                verification_reasoning,
            ],
            outputs=[verification_model, verification_reasoning],
        )

        # Store dropdown references for cross-tab updates when providers change
        self._session_dropdowns[session_id] = {
            "coding_agent": coding_agent,
            "verification_agent": verification_agent,
        }

    def _create_providers_ui(self):
        """Create the Providers tab UI within @gr.render."""
        account_items = self.provider_ui.get_provider_card_items()
        self.provider_card_count = max(12, len(account_items) + 8)

        provider_feedback = gr.Markdown("")
        gr.Markdown("### Providers", elem_classes=["provider-section-title"])

        provider_list = gr.Markdown(
            self.list_providers(),
            elem_id="provider-summary-panel",
            elem_classes=["provider-summary"],
        )
        refresh_btn = gr.Button("🔄 Refresh", variant="secondary")
        pending_delete_state = gr.State(None)

        provider_cards = []
        with gr.Row(equal_height=True, elem_classes=["provider-cards-row"]):
            for idx in range(self.provider_card_count):
                if idx < len(account_items):
                    account_name, provider_type = account_items[idx]
                    visible = True
                    header_text = self.provider_ui.format_provider_header(account_name, provider_type, idx)
                    usage_text = self.get_provider_usage(account_name)
                else:
                    account_name = ""
                    visible = False
                    header_text = ""
                    usage_text = ""

                card_group_classes = ["provider-card"] if visible else ["provider-card", "provider-card-empty"]
                with gr.Column(visible=visible, scale=1) as card_column:
                    card_elem_id = f"provider-card-{idx}"
                    with gr.Group(elem_id=card_elem_id, elem_classes=card_group_classes) as card_group:
                        with gr.Row(elem_classes=["provider-card__header-row"]):
                            card_header = gr.Markdown(header_text, elem_classes=["provider-card__header"])
                            delete_btn = gr.Button(
                                "🗑︎",
                                variant="secondary",
                                size="sm",
                                min_width=0,
                                scale=0,
                                elem_classes=["provider-delete"],
                            )
                        account_state = gr.State(account_name)
                        gr.Markdown("Usage", elem_classes=["provider-usage-title"])
                        usage_box = gr.Markdown(usage_text, elem_classes=["provider-usage"])

                provider_cards.append(
                    {
                        "column": card_column,
                        "group": card_group,
                        "header": card_header,
                        "account_state": account_state,
                        "account_name": account_name,
                        "usage_box": usage_box,
                        "delete_btn": delete_btn,
                    }
                )

        with gr.Accordion(
            "Add New Provider",
            open=False,
            elem_id="add-provider-panel",
            elem_classes=["add-provider-accordion"],
        ) as add_provider_accordion:
            gr.Markdown("Click to add another provider. Close the accordion to retract without adding.")
            new_provider_name = gr.Textbox(label="Provider Name", placeholder="e.g., work-claude")
            new_provider_type = gr.Dropdown(
                choices=["anthropic", "openai", "gemini", "mistral"],
                label="Provider Type",
                value="anthropic",
            )
            add_btn = gr.Button("Add Provider", variant="primary", interactive=False)

        provider_outputs = [provider_feedback, provider_list]
        for card in provider_cards:
            provider_outputs.extend(
                [
                    card["column"],
                    card["group"],
                    card["header"],
                    card["account_state"],
                    card["usage_box"],
                    card["delete_btn"],
                ]
            )

        def refresh_providers():
            return self._provider_action_response("")

        refresh_btn.click(refresh_providers, outputs=provider_outputs)

        new_provider_name.change(
            lambda name: gr.update(interactive=bool(name.strip())),
            inputs=[new_provider_name],
            outputs=[add_btn],
        )

        def add_provider_handler(provider_name, provider_type):
            base = self.add_provider(provider_name, provider_type)
            return (
                *base[: len(provider_outputs)],
                "",
                gr.update(interactive=False),
                gr.update(open=False),
            )

        add_btn.click(
            add_provider_handler,
            inputs=[new_provider_name, new_provider_type],
            outputs=provider_outputs + [new_provider_name, add_btn, add_provider_accordion],
        )

        for card in provider_cards:

            def make_delete_handler():
                def handler(pending_delete, current_account):
                    if not current_account:
                        return (pending_delete, *self._provider_action_response(""))
                    if pending_delete == current_account:
                        # Second click - actually delete
                        result = self.delete_provider(current_account, confirmed=True)
                        return (None, *result)
                    else:
                        # First click - show confirmation (tick icon)
                        return (
                            current_account,
                            *self._provider_action_response(
                                f"Click the ✓ icon in '{current_account}' titlebar to confirm deletion",
                                pending_delete=current_account,
                            ),
                        )

                return handler

            delete_outputs = [pending_delete_state] + provider_outputs
            delete_event = card["delete_btn"].click(
                fn=make_delete_handler(),
                inputs=[pending_delete_state, card["account_state"]],
                outputs=delete_outputs,
            )
            self._provider_delete_events.append(delete_event)

    def create_interface(self) -> gr.Blocks:
        """Create the Gradio interface."""
        # Create initial session
        initial_session = self.create_session("Task 1")
        initial_session.log_path = self.session_logger.precreate_log()

        with gr.Blocks(title="Chad") as interface:
            # Inject custom CSS
            gr.HTML(f"<style>{PROVIDER_PANEL_CSS}</style>")

            gr.HTML(
                """
<button role="tab" id="initial-static-plus-tab" aria-label="➕" style="position:fixed;top:8px;right:8px;z-index:9999;
padding:6px 10px;font-size:16px;cursor:pointer;">➕</button>
<script>
(function() {
  const getRoot = () => {
    const app = document.querySelector('gradio-app');
    return (app && app.shadowRoot) ? app.shadowRoot : document;
  };
  const isPlus = (el) => {
    const label = (el.textContent || el.getAttribute('aria-label') || '').trim();
    return label === '➕';
  };
  const hideButton = (btn) => {
    btn.style.visibility = 'hidden';
    btn.style.opacity = '0';
  };
  const triggerAdd = () => {
    let attempts = 0;
    const tick = () => {
      const root = getRoot();
      const btn = root.querySelector('#add-new-task-btn');
      if (btn) {
        hideButton(btn);
        btn.click();
        return;
      }
      if (attempts++ < 15) setTimeout(tick, 80);
    };
    tick();
  };
  const wirePlus = () => {
    const root = getRoot();
    const tabs = [
      ...root.querySelectorAll('[role=\"tab\"]'),
      ...document.querySelectorAll('#initial-static-plus-tab, #fallback-plus-tab, #static-plus-tab')
    ];
    tabs.forEach((tab) => {
      if (!tab || tab._plusClickSetup || !isPlus(tab)) return;
      tab._plusClickSetup = true;
      tab.addEventListener('click', () => setTimeout(triggerAdd, 60));
    });
    const activePlus = tabs.find((tab) => tab && isPlus(tab) && tab.getAttribute('aria-selected') === 'true');
    if (activePlus) triggerAdd();
    const btn = root.querySelector('#add-new-task-btn');
    if (btn) hideButton(btn);
  };
  setInterval(wirePlus, 400);
  setTimeout(wirePlus, 80);
  setInterval(ensureDiscardEditable, 200);
  setTimeout(ensureDiscardEditable, 120);
})();
</script>
"""
            )

            # JavaScript injection moved back to launch() parameter

            # Custom JavaScript is now passed to launch() in Gradio 6.x
            interface.load(
                fn=None,
                js="""
                () => {
                  const getRoot = () => {
                    const app = document.querySelector('gradio-app');
                    return (app && app.shadowRoot) ? app.shadowRoot : document;
                  };
                  const isPlus = (el) => ((el.textContent || el.getAttribute('aria-label') || '').trim() === '➕');
                  const hideButton = (btn) => {
                    btn.style.visibility = 'hidden';
                    btn.style.opacity = '0';
                  };
                  const collectAll = (selector) => {
                    const seen = new Set();
                    const results = [];
                    const walk = (node) => {
                      if (!node) return;
                      node.querySelectorAll(selector).forEach((el) => {
                        if (seen.has(el)) return;
                        seen.add(el);
                        results.push(el);
                      });
                      node.querySelectorAll('*').forEach((el) => {
                        if (el.shadowRoot) walk(el.shadowRoot);
                      });
                    };
                    walk(document);
                    return results;
                  };
                  const ensureDiscardEditable = () => {
                    const statuses = collectAll('.task-status-header, [id*=\"task-status\"], [class*=\"task-status\"]');
                    const hasDiscarded =
                        statuses.some((el) => (el.textContent || '').toLowerCase().includes('discarded'));
                    if (!hasDiscarded) return;
                    const textareas = collectAll('textarea');
                    textareas.forEach((ta) => {
                      const ph = (ta.getAttribute('placeholder') || '').toLowerCase();
                      if (!ph.includes('task') || !ph.includes('description')) return;
                      ta.removeAttribute('disabled');
                      ta.removeAttribute('aria-disabled');
                      ta.disabled = false;
                      const fieldset = ta.closest('fieldset');
                      if (fieldset) {
                        fieldset.removeAttribute('disabled');
                        fieldset.removeAttribute('aria-disabled');
                      }
                    });
                  };
                  const fixAriaLinks = () => {
                    const root = getRoot();
                    const tabs = collectAll('[role=\"tab\"]');
                    const panels = collectAll('.tabitem, [role=\"tabpanel\"]');
                    const textOf = (el) => (el.textContent || '').trim();
                    const addPanel = panels.find((p) => textOf(p).includes('Add New Task'));
                    const taskPanels = panels.filter((p) => textOf(p).includes('Task Description'));
                    const panelByData = new Map();
                    panels.forEach((panel) => {
                      const dataId = panel.getAttribute('data-tab-id');
                      if (dataId) panelByData.set(dataId, panel);
                    });
                    let taskIdx = 0;
                    const usedPanelIds = new Set();
                    const usedTabIds = new Set();

                    tabs.forEach((tab, idx) => {
                      const label = textOf(tab);
                      const tabData = tab.getAttribute('data-tab-id');
                      let panel = null;
                      if (tabData && panelByData.has(tabData)) {
                        panel = panelByData.get(tabData);
                      } else if (isPlus(tab) && addPanel) {
                        panel = addPanel;
                      } else if (label.startsWith('Task') && taskPanels.length) {
                        panel = taskPanels[Math.min(taskIdx, taskPanels.length - 1)];
                        taskIdx += 1;
                      } else {
                        panel = panels.length ? panels[idx % panels.length] : null;
                      }
                      if (!panel) return;
                      const panelIndex = panels.indexOf(panel);
                      const dataId = tab.getAttribute('data-tab-id') || panelIndex || idx;
                      let panelId = panel.id && panel.id.trim() ? panel.id : `tabpanel-${dataId}`;
                      if (usedPanelIds.has(panelId)) {
                        panelId = `tabpanel-${dataId}-${idx}`;
                      }
                      let tabId = tab.id && tab.id.trim() ? tab.id : `tab-${dataId}`;
                      if (usedTabIds.has(tabId)) {
                        tabId = `tab-${dataId}-${idx}`;
                      }
                      panel.id = panelId;
                      panel.setAttribute('role', 'tabpanel');
                      tab.id = tabId;
                      usedPanelIds.add(panelId);
                      usedTabIds.add(tabId);
                      tab.setAttribute('aria-controls', panelId);
                      panel.setAttribute('aria-labelledby', tabId);
                    });
                  };
                  const triggerAdd = () => {
                    let attempts = 0;
                    const tick = () => {
                      const root = getRoot();
                      const btn = root.querySelector('#add-new-task-btn');
                      if (btn) {
                        hideButton(btn);
                        btn.click();
                        return;
                      }
                      if (attempts++ < 15) setTimeout(tick, 80);
                    };
                    tick();
                  };
                  const wirePlus = () => {
                    const root = getRoot();
                    const tabs = [
                      ...root.querySelectorAll('[role="tab"]'),
                      ...document.querySelectorAll('#initial-static-plus-tab, #fallback-plus-tab, #static-plus-tab')
                    ];
                    tabs.forEach((tab) => {
                      if (!tab || tab._plusClickSetup || !isPlus(tab)) return;
                      tab._plusClickSetup = true;
                      tab.addEventListener('click', () => setTimeout(triggerAdd, 60));
                    });
                    const activePlus = tabs.find((tab) => tab && isPlus(tab) &&
                        tab.getAttribute('aria-selected') === 'true');
                    if (activePlus) triggerAdd();
                    const btn = root.querySelector('#add-new-task-btn');
                    if (btn) hideButton(btn);
                  };
                  const tickAll = () => {
                    wirePlus();
                    fixAriaLinks();
                  };
                  setInterval(tickAll, 400);
                  setTimeout(tickAll, 80);
                  document.addEventListener('click', (event) => {
                    const tab = event.target && event.target.closest
                      ? event.target.closest('[role=\"tab\"]')
                      : null;
                    if (!tab) return;
                    setTimeout(fixAriaLinks, 0);
                  }, true);
                }
                """,
            )

            # Maximum number of task tabs we can have
            MAX_TASKS = 10

            # Pre-create sessions for all potential tabs
            all_sessions = [initial_session]
            for i in range(1, MAX_TASKS):
                s = self.create_session(f"Task {i + 1}")
                s.log_path = self.session_logger.precreate_log()
                all_sessions.append(s)

            # Track how many tabs are currently visible (start with 1)
            visible_count = gr.State(1)

            # Tab index 1 = Task 1 (since Providers is index 0)
            with gr.Tabs(selected=1, elem_id="main-tabs") as main_tabs:
                # Providers tab (first, but not default selected)
                with gr.Tab("⚙️ Providers", id=0):
                    self._create_providers_ui()

                # Pre-create ALL task tabs - only first visible initially
                task_tabs = []
                for i in range(MAX_TASKS):
                    tab_id = i + 1  # Providers is 0, tasks start at 1
                    is_visible = (i == 0)  # Only first tab visible initially
                    with gr.Tab(f"Task {i + 1}", id=tab_id, visible=is_visible) as task_tab:
                        self._create_session_ui(all_sessions[i].id, is_first=(i == 0))
                    task_tabs.append(task_tab)

                # "+" tab to add new tasks - contains a button that triggers task creation
                add_tab_id = MAX_TASKS + 1
                with gr.Tab("➕", id=add_tab_id):
                    add_task_btn = gr.Button(
                        "➕ Add New Task",
                        variant="primary",
                        size="lg",
                        elem_id="add-new-task-btn",
                    )

            # Handle clicking the Add New Task button
            def on_add_task_click(current_count):
                if current_count >= MAX_TASKS:
                    # At max, switch back to last task tab
                    return [gr.Tabs(selected=MAX_TASKS), current_count] + [gr.update() for _ in range(MAX_TASKS)]

                # Create new task: increment count, show new tab, switch to it
                new_count = current_count + 1
                new_tab_id = new_count  # Task tabs are 1-indexed

                # Build visibility updates - show tabs 1 through new_count
                updates = [gr.Tabs(selected=new_tab_id), new_count]
                for i in range(MAX_TASKS):
                    if i < new_count:
                        updates.append(gr.update(visible=True))
                    else:
                        updates.append(gr.update(visible=False))
                return updates

            add_task_btn.click(
                on_add_task_click,
                inputs=[visible_count],
                outputs=[main_tabs, visible_count] + task_tabs,
            )

            # Refresh dropdowns when switching to any task tab (after adding providers)
            def on_tab_select(evt: gr.SelectData):
                """Refresh agent dropdowns when switching to a task tab."""
                # Only refresh for task tabs (id >= 1, not providers tab id=0)
                if evt.index == 0:
                    return [gr.update() for _ in range(len(self._session_dropdowns) * 2)]

                # Get current account choices
                accounts = self.security_mgr.list_accounts()
                account_choices = list(accounts.keys())
                none_label = (
                    "None (disable verification)"
                    if self.VERIFICATION_NONE_LABEL in account_choices
                    else self.VERIFICATION_NONE_LABEL
                )
                verification_choices = [
                    (self.SAME_AS_CODING, self.SAME_AS_CODING),
                    (none_label, self.VERIFICATION_NONE),
                    *[(account, account) for account in account_choices],
                ]

                # Update all session dropdowns
                updates = []
                for session_id in self._session_dropdowns:
                    updates.append(gr.update(choices=account_choices))  # coding_agent
                    updates.append(gr.update(choices=verification_choices))  # verification_agent
                return updates

            # Collect all dropdown outputs for the select handler
            all_dropdown_outputs = []
            for session_id in sorted(self._session_dropdowns.keys()):
                dropdowns = self._session_dropdowns[session_id]
                all_dropdown_outputs.append(dropdowns["coding_agent"])
                all_dropdown_outputs.append(dropdowns["verification_agent"])

            if all_dropdown_outputs:
                main_tabs.select(on_tab_select, outputs=all_dropdown_outputs)

            # Chain dropdown refresh to provider delete events
            # This ensures dropdowns update when providers are deleted
            def refresh_dropdowns_after_delete():
                """Refresh all session dropdowns after a provider is deleted."""
                accounts = self.security_mgr.list_accounts()
                account_choices = list(accounts.keys())
                none_label = (
                    "None (disable verification)"
                    if self.VERIFICATION_NONE_LABEL in account_choices
                    else self.VERIFICATION_NONE_LABEL
                )
                verification_choices = [
                    (self.SAME_AS_CODING, self.SAME_AS_CODING),
                    (none_label, self.VERIFICATION_NONE),
                    *[(account, account) for account in account_choices],
                ]

                updates = []
                for session_id in self._session_dropdowns:
                    # Update choices; reset value to first valid choice if current is invalid
                    first_choice = account_choices[0] if account_choices else None
                    updates.append(gr.update(choices=account_choices, value=first_choice))
                    updates.append(gr.update(choices=verification_choices))
                return updates

            if all_dropdown_outputs and self._provider_delete_events:
                for delete_event in self._provider_delete_events:
                    delete_event.then(
                        fn=refresh_dropdowns_after_delete,
                        outputs=all_dropdown_outputs,
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
    # Ensure downstream agents inherit a consistent project root
    try:
        from .config import ensure_project_root_env

        ensure_project_root_env()
    except Exception:
        # Non-fatal; continue without forcing env
        pass

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
                "password_hash": password_hash,
                "encryption_salt": encryption_salt,
                "accounts": {},
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
        quiet=False,
        js="""
        () => {
            const getRoot = () => {
                const app = document.querySelector('gradio-app');
                return (app && app.shadowRoot) ? app.shadowRoot : document;
            };
            const isPlus = (el) => {
                const label = (el.textContent || el.getAttribute('aria-label') || '').trim();
                return label === '➕';
            };
            const hideButton = (btn) => {
                btn.style.visibility = 'hidden';
                btn.style.opacity = '0';
            };
            const clickAddTask = () => {
                const root = getRoot();
                let attempts = 0;
                const tryClick = () => {
                    const btn = root.querySelector('#add-new-task-btn');
                    if (btn) {
                        hideButton(btn);
                        btn.click();
                        return true;
                    }
                    attempts += 1;
                    if (attempts <= 15) setTimeout(tryClick, 80);
                    return false;
                };
                return tryClick();
            };
            const isPlusSelected = () => {
                const root = getRoot();
                return Array.from(root.querySelectorAll('[role=\"tab\"]')).some(
                    (tab) => isPlus(tab) && tab.getAttribute('aria-selected') === 'true'
                );
            };
            const wirePlusButtons = () => {
                const root = getRoot();
                const candidates = [
                    ...root.querySelectorAll('[role=\"tab\"]'),
                    ...document.querySelectorAll('#initial-static-plus-tab, #fallback-plus-tab, #static-plus-tab')
                ];
                candidates.forEach((tab) => {
                    if (!tab || tab._plusClickSetup || !isPlus(tab)) return;
                    tab._plusClickSetup = true;
                    tab.addEventListener('click', () => setTimeout(clickAddTask, 60));
                });
                if (isPlusSelected()) setTimeout(clickAddTask, 60);
                const addBtn = root.querySelector('#add-new-task-btn');
                if (addBtn) hideButton(addBtn);
            };

            const observer = new MutationObserver(() => {
                if (isPlusSelected()) clickAddTask();
            });
            observer.observe(document, { childList: true, subtree: true });
            const rootObserverTarget = getRoot();
            if (rootObserverTarget && rootObserverTarget !== document) {
                observer.observe(rootObserverTarget, { childList: true, subtree: true });
            }

            setInterval(wirePlusButtons, 400);
            setTimeout(wirePlusButtons, 80);
        }
        """,
    )

    return None, port

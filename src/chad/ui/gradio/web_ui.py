"""Gradio web interface for Chad."""

import base64
import os
import json
import re
import socket
import threading
import time
import queue
import uuid
import html
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

import gradio as gr

from .provider_ui import ProviderUIManager

if TYPE_CHECKING:
    from chad.ui.client import APIClient

from chad.ui.client.stream_client import SyncStreamClient
from chad.ui.terminal_emulator import TerminalEmulator, TERMINAL_COLS, TERMINAL_ROWS
from chad.server.services.task_executor import ClaudeStreamJsonParser
from chad.util.event_log import (
    EventLog,
    SessionStartedEvent,
    SessionEndedEvent,
    UserMessageEvent,
    AssistantMessageEvent,
    VerificationAttemptEvent,
    ProviderSwitchedEvent,
)
from chad.util.handoff import (
    log_handoff_checkpoint,
    build_resume_prompt,
    is_quota_exhaustion_error,
    get_quota_error_reason,
)
from chad.util.providers import ModelConfig, parse_codex_output, create_provider
from chad.util.model_catalog import ModelCatalog
from chad.util.prompts import (
    build_exploration_prompt,
    build_implementation_prompt,
    extract_coding_summary,
    extract_progress_update,
    check_verification_mentioned,
    get_verification_exploration_prompt,
    get_verification_conclusion_prompt,
    parse_verification_response,
    ProgressUpdate,
    VerificationParseError,
    EXPLORATION_PROMPT,
    IMPLEMENTATION_PROMPT,
    VERIFICATION_EXPLORATION_PROMPT,
)
from chad.util.project_setup import (
    detect_verification_commands,
    detect_doc_paths,
    validate_command,
    load_project_config,
    save_project_settings,
)
from chad.util.git_worktree import GitWorktreeManager, MergeConflict, FileDiff
from .verification.ui_playwright_runner import cleanup_all_test_servers


@dataclass
class Session:
    """Per-session state for concurrent task execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    # Server-side session ID when using API execution
    server_session_id: str | None = None
    name: str = "New Session"
    cancel_requested: bool = False
    active: bool = False
    provider: object = None
    config: object = None
    event_log: EventLog | None = None  # Structured JSONL event log
    chat_history: list = field(default_factory=list)
    task_description: str | None = None
    project_path: str | None = None
    coding_account: str | None = None
    # Track provider switches for UI indication
    switched_from: str | None = None  # Previous provider after a handoff
    # Git worktree support
    worktree_path: Path | None = None
    worktree_branch: str | None = None
    worktree_base_commit: str | None = None  # Commit SHA worktree was created from
    has_worktree_changes: bool = False
    merge_conflicts: list[MergeConflict] | None = None
    # Prompt tracking for display
    last_exploration_prompt: str | None = None
    last_implementation_prompt: str | None = None
    last_verification_prompt: str | None = None
    # Live stream DOM patching support - track if initial render is done
    has_initial_live_render: bool = False
    # Persistent live stream content for tab switch restoration
    last_live_stream: str = ""
    # Track file modifications from last coding run (for revision context)
    last_work_done: dict | None = None

    @property
    def log_path(self) -> Path | None:
        """Get the event log path for backwards compatibility."""
        if self.server_session_id:
            from chad.util.event_log import EventLog

            return EventLog.get_log_dir() / f"{self.server_session_id}.jsonl"
        return self.event_log.log_path if self.event_log else None


def _history_entry(agent: str, content: str) -> tuple[str, str, str]:
    """Create a streaming history entry with a timestamp."""
    return (agent, content, datetime.now(timezone.utc).isoformat())


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
DEFAULT_VERIFICATION_TIMEOUT = 1800.0  # 30 minutes to match provider defaults
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

.run-top-row {
  gap: 4px !important;
  align-items: flex-start !important;
}

.run-top-row .row,
.run-top-row .column {
  gap: 4px !important;
}

.run-top-row .block {
  margin-bottom: 2px !important;
}


.project-setup-column {
  display: grid;
  gap: 4px;
}

.project-path-input label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  font-weight: 600;
}

.project-commands-row {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  gap: 10px !important;
  align-items: start !important;
  margin-bottom: 0 !important;
}

.command-column {
  display: grid;
  gap: 4px;
}

.command-column .block {
  margin-bottom: 0 !important;
}

.command-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0;
}

.command-label {
  margin: 0;
  font-weight: 600;
  font-size: 0.9rem;
  line-height: 1.2;
}

.command-test-btn button,
.command-test-btn {
  min-height: 28px !important;
  padding: 6px 10px !important;
}

.command-input textarea,
.command-input input {
  min-height: 44px;
}

.command-status {
  margin: 0 !important;
  font-size: 0.9rem;
}

/* Only show min-height when there's actual status content */
.command-status:has(p:not(:empty)) {
  min-height: 18px;
}

.doc-paths-row {
  gap: 8px !important;
  margin-top: 0 !important;
  padding-top: 0 !important;
}


.agent-config {
  display: grid !important;
  gap: 8px !important;
  align-content: start;
}

.agent-config .block,
.agent-config .form,
.agent-config .wrap {
  margin-bottom: 0 !important;
}

.project-save-btn button,
.project-save-btn {
  min-height: 34px !important;
  width: auto !important;
  padding: 8px 14px !important;
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
  flex: 0 0 auto !important;
}

.cancel-task-btn button {
  background: var(--cancel-btn-bg) !important;
  border: 1px solid var(--cancel-btn-border) !important;
  color: var(--cancel-btn-text) !important;
  -webkit-text-fill-color: var(--cancel-btn-text) !important;
  font-size: 0.85rem !important;
  min-height: 34px !important;
  padding: 8px 14px !important;
  opacity: 1 !important;
}

.cancel-task-btn button:disabled {
  opacity: 0.5 !important;
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

/* Hide merge/conflict sections when hidden class is present */
.merge-section-hidden,
.conflict-section-hidden {
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


/* Live stream box visibility controlled by :empty pseudo-selector */
.live-stream-box:empty {
  display: none !important;
}

.live-patch-trigger {
  display: none !important;
  visibility: hidden !important;
  width: 0 !important;
  height: 0 !important;
  overflow: hidden !important;
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
  max-height: 500px !important;
  min-height: 100px !important;
  /* Prevent container from expanding parent - contain within available width */
  max-width: 100%;
  min-width: 0;
  overflow-y: auto !important;
  overflow-x: auto !important;
  overflow-anchor: none !important;
  /* Use pre to preserve exact terminal layout from pyte - no word wrapping */
  white-space: pre;
  font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.4;
}

/* Prevent nested elements from creating their own scrollbars (causes double scrollbar issue) */
#live-stream-box .live-output-content *,
.live-stream-box .live-output-content * {
  overflow: visible !important;
  max-height: none !important;
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

/* Screenshot comparison in chat bubbles */
.agent-chatbot .screenshot-comparison {
  display: flex;
  gap: 12px;
  margin: 12px 0;
  flex-wrap: wrap;
}
.agent-chatbot .screenshot-panel {
  flex: 1 1 45%;
  min-width: 200px;
  max-width: 100%;
}
.agent-chatbot .screenshot-single {
  margin: 12px 0;
  max-width: 100%;
}
.agent-chatbot .screenshot-label {
  background: #2a2a3e;
  color: #a8d4ff;
  padding: 4px 10px;
  border-radius: 6px 6px 0 0;
  font-weight: 600;
  font-size: 11px;
  letter-spacing: 0.05em;
}
.agent-chatbot .screenshot-comparison img,
.agent-chatbot .screenshot-single img {
  width: 100%;
  height: auto;
  border: 1px solid #555;
  border-top: none;
  border-radius: 0 0 6px 6px;
  display: block;
}

/* Full-width screenshot for progress updates and single after screenshots */
.agent-chatbot .screenshot-full-width {
  margin: 12px 0;
  width: 100%;
}
.agent-chatbot .screenshot-full-width img {
  width: 100%;
  max-height: 600px;
  object-fit: contain;
  border: 1px solid #555;
  border-top: none;
  border-radius: 0 0 6px 6px;
  display: block;
}
.agent-chatbot .screenshot-description {
  background: #1e1e2e;
  color: #cdd6f4;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.4;
  border: 1px solid #555;
  border-top: none;
  border-radius: 0 0 6px 6px;
}
/* When description follows image, remove image's bottom radius */
.agent-chatbot img + .screenshot-description {
  margin-top: 0;
}
.agent-chatbot img:has(+ .screenshot-description) {
  border-radius: 0;
}

/* Container that anchors action controls to the right under agent selectors */
#role-status-row-container,
.role-status-row-container {
  display: flex;
  justify-content: flex-end;
  width: 100%;
  margin-top: -4px !important;
  margin-bottom: 0 !important;
}

/* Action row: compact inline controls */
#role-status-row,
.role-status-row {
  display: inline-flex !important;
  align-items: center;
  gap: 4px;
  flex-wrap: nowrap;
  width: auto !important;
  flex: 0 0 auto;
  max-width: 100%;
}

#role-config-status {
  width: 100%;
  margin: 0;
}

.role-status-row button,
.role-status-row .download-button,
.role-status-row a[download] {
  white-space: nowrap !important;
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

#workspace-display,
.workspace-display {
  margin: 0 !important;
  min-width: 0 !important;
  max-width: 230px;
  width: auto !important;
  overflow: hidden !important;
}

#workspace-display .workspace-inline,
.workspace-display .workspace-inline {
  margin: 0 !important;
  font-size: 12px;
  color: #cdd6f4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Agent communication chatbot - preserve scroll position */
.chatbot-container, [data-testid="chatbot"] {
  scroll-behavior: auto !important;
}

/* Agent communication chatbot - prevent horizontal scroll */
.agent-chatbot {
  overflow-x: hidden !important;
}

/* Agent communication chatbot - full-width speech bubbles */
.agent-chatbot .message-row,
.agent-chatbot .message {
  width: 100% !important;
  max-width: 100% !important;
  align-self: stretch !important;
  overflow-x: hidden !important;
}

.agent-chatbot .bubble-wrap,
.agent-chatbot .bubble,
.agent-chatbot .message-content,
.agent-chatbot .message .prose {
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
  overflow-wrap: break-word !important;
  word-break: break-word !important;
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

.task-input-row {
  gap: 12px !important;
  align-items: flex-start !important;
}

.task-input-row .task-desc-input textarea {
  min-height: 120px !important;
}

/* Start button alignment */
.start-task-btn {
  align-self: stretch;
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

/* Status line can wrap to multiple lines and span full width */
.role-config-status {
  width: 100% !important;
  margin: 0 !important;
}

.role-config-status p {
  margin: 0 !important;
  word-wrap: break-word !important;
  white-space: normal !important;
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
  color: #e5e9f0;
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
  overflow-x: auto;
  overflow-y: hidden;
}

.diff-file {
  margin-bottom: 12px;
  border: 1px solid #4c566a;
  border-radius: 4px;
  overflow-x: auto;
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
  min-width: 100%;
  width: max-content;
  overflow-x: auto;
}

.diff-side {
  flex: 1;
  min-width: 0;
  overflow: visible;
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
  overflow: visible;
  min-width: max-content;
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
    """Buffer for live stream display content with infinite history."""

    def __init__(self) -> None:
        self.content = ""

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        self.content += chunk


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


def build_live_stream_html(content: str, ai_name: str = "CODING AI", live_id: str | None = None) -> str:
    """Render live stream text as HTML with consistent spacing and header.

    Args:
        content: Text content to render
        ai_name: Name shown in the header
        live_id: Optional ID for DOM patching (enables scroll preservation)
    """
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
    wrapper_attr = f' data-live-id="{live_id}"' if live_id else ''
    return f'<div class="live-output-wrapper"{wrapper_attr}>{header}\n{body}</div>'


def build_live_stream_html_from_pyte(content_html: str, ai_name: str = "CODING AI", live_id: str | None = None) -> str:
    """Render live stream HTML generated by pyte with a consistent header.

    Args:
        content_html: HTML content from pyte terminal emulator or escaped text
        ai_name: Name shown in the header
        live_id: Optional ID for DOM patching (enables scroll preservation)
    """
    if not content_html or not content_html.strip():
        return ""
    # Normalize spacing - remove blank lines for compact display
    # Works for both pyte HTML and escaped text content
    lines = content_html.split('\n')
    normalized_lines = [line for line in lines if line.strip()]
    content_html = '\n'.join(normalized_lines)
    if not content_html.strip():
        return ""
    header = f'<div class="live-output-header">▶ {ai_name} (Live Stream)</div>'
    body = f'<div class="live-output-content">{content_html}</div>'
    wrapper_attr = f' data-live-id="{live_id}"' if live_id else ''
    return f'<div class="live-output-wrapper"{wrapper_attr}>{header}\n{body}</div>'


def image_to_data_url(path: str | None) -> str | None:
    """Convert an image file to a base64 data URL for inline display.

    Args:
        path: Path to the image file, or None

    Returns:
        Data URL string or None if file doesn't exist or can't be read
    """
    if not path:
        return None
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
    if collapsible and len(content) > 300:
        coding_summary = extract_coding_summary(content)
        if coding_summary:
            summary_text = coding_summary.change_summary
            extra_parts = []
            if coding_summary.hypothesis:
                extra_parts.append(f"**Hypothesis:** {coding_summary.hypothesis}")
            before_url = image_to_data_url(coding_summary.before_screenshot) if coding_summary.before_screenshot else None
            after_url = image_to_data_url(coding_summary.after_screenshot) if coding_summary.after_screenshot else None
            before_desc = coding_summary.before_description
            after_desc = coding_summary.after_description
            if before_url or after_url:
                # Use side-by-side comparison when both exist, full-width for single screenshot
                if before_url and after_url:
                    screenshot_html = '<div class="screenshot-comparison">'
                    before_desc_html = f'<div class="screenshot-description">{before_desc}</div>' if before_desc else ''
                    after_desc_html = f'<div class="screenshot-description">{after_desc}</div>' if after_desc else ''
                    screenshot_html += (
                        f'<div class="screenshot-panel"><div class="screenshot-label">Before</div>'
                        f'<img src="{before_url}" alt="Before screenshot">{before_desc_html}</div>'
                    )
                    screenshot_html += (
                        f'<div class="screenshot-panel"><div class="screenshot-label">After</div>'
                        f'<img src="{after_url}" alt="After screenshot">{after_desc_html}</div>'
                    )
                    screenshot_html += '</div>'
                elif after_url:
                    # Single after screenshot - use full width
                    after_desc_html = f'<div class="screenshot-description">{after_desc}</div>' if after_desc else ''
                    screenshot_html = (
                        f'<div class="screenshot-full-width"><div class="screenshot-label">After</div>'
                        f'<img src="{after_url}" alt="After screenshot">{after_desc_html}</div>'
                    )
                else:
                    # Single before screenshot - use full width
                    before_desc_html = f'<div class="screenshot-description">{before_desc}</div>' if before_desc else ''
                    screenshot_html = (
                        f'<div class="screenshot-full-width"><div class="screenshot-label">Before</div>'
                        f'<img src="{before_url}" alt="Before screenshot">{before_desc_html}</div>'
                    )
                extra_parts.append(screenshot_html)
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

    return {"role": "assistant", "content": formatted}


def make_progress_message(progress: ProgressUpdate) -> dict:
    """Create a chat message for an intermediate progress update.

    Shows the before screenshot full-width with a summary, location, and next step.
    """
    screenshot_url = image_to_data_url(progress.before_screenshot)
    parts = ["**CODING AI** *(Progress)*"]
    if progress.summary:
        parts.append(f"\n\n{progress.summary}")
    if progress.location:
        parts.append(f"\n\n**Location:** `{progress.location}`")
    if progress.next_step:
        parts.append(f"\n\n**Next:** {progress.next_step}")
    if screenshot_url:
        desc_html = f'<div class="screenshot-description">{progress.before_description}</div>' if progress.before_description else ''
        parts.append(
            f'\n\n<div class="screenshot-full-width">'
            f'<div class="screenshot-label">Before</div>'
            f'<img src="{screenshot_url}" alt="Before screenshot">{desc_html}</div>'
        )
    return {"role": "assistant", "content": "".join(parts)}


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

    def __init__(self, api_client: "APIClient", dev_mode: bool = False):
        self.api_client = api_client
        self.dev_mode = dev_mode
        self.sessions: dict[str, Session] = {}
        self.provider_card_count = 10
        self.model_catalog = ModelCatalog(api_client)
        self.provider_ui = ProviderUIManager(api_client, self.model_catalog, dev_mode=dev_mode)
        # Store dropdown references for cross-tab updates
        self._session_dropdowns: dict[str, dict] = {}
        # Store live patch triggers for tab rehydration
        self._session_live_patches: dict[str, gr.HTML] = {}
        # Store live stream components for direct tab-switch restoration
        self._session_live_streams: dict[str, gr.HTML] = {}
        # Store provider card delete events for chaining dropdown updates
        self._provider_delete_events: list = []
        # Stream client for API-based task execution (same method as CLI)
        self._stream_client: SyncStreamClient | None = None

    @staticmethod
    def _format_project_label(project_type: str) -> str:
        """Return compact project path label including detected type."""
        type_display = project_type or "unknown"
        return f"Project Path (Type: {type_display})"

    def _get_stream_client(self) -> SyncStreamClient:
        """Get or create the stream client for API-based task execution.

        Uses the same SSE streaming method as the CLI for unified streaming.
        """
        if self._stream_client is None:
            self._stream_client = SyncStreamClient(self.api_client.base_url)
        return self._stream_client

    def stream_task_output(
        self, session_id: str, include_terminal: bool = True
    ) -> Iterator[tuple[str, str | None, int | None]]:
        """Stream task output from the API using pyte terminal emulation.

        This method provides the same streaming interface as the CLI,
        enabling unified streaming across both UI types. Uses pyte for
        accurate terminal layout preservation.

        Args:
            session_id: Session to stream from
            include_terminal: Whether to include raw terminal output

        Yields:
            Tuple of (event_type, data, exit_code):
            - ("terminal", html_content, None) - Terminal output as HTML
            - ("complete", None, exit_code) - Task completed
            - ("error", error_message, None) - Error occurred
        """
        stream_client = self._get_stream_client()
        terminal = TerminalEmulator(cols=TERMINAL_COLS, rows=TERMINAL_ROWS)

        for event in stream_client.stream_events(session_id, include_terminal=include_terminal):
            if event.event_type == "terminal":
                # Feed terminal data (base64 for live PTY, plain text for logs)
                raw_data = event.data.get("data", "")
                if event.data.get("text"):
                    terminal.feed(raw_data or "")
                else:
                    terminal.feed_base64(raw_data)
                # Render screen to HTML with proper layout
                html_output = terminal.render_html()
                yield ("terminal", html_output, None)

            elif event.event_type == "complete":
                exit_code = event.data.get("exit_code", 0)
                yield ("complete", None, exit_code)
                break

            elif event.event_type == "error":
                error_msg = event.data.get("error", "Unknown error")
                yield ("error", error_msg, None)
                break

            elif event.event_type == "ping":
                # Keepalive, ignore
                continue

    def run_task_via_api(
        self,
        session_id: str,
        project_path: str,
        task_description: str,
        coding_account: str,
        message_queue: "queue.Queue",
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        server_session_id: str | None = None,
        terminal_cols: int | None = None,
        screenshots: list[str] | None = None,
    ) -> tuple[bool, str, str | None, dict | None]:
        """Run a task via the API and post events to the message queue.

        This method enables unified streaming - both CLI and Gradio UI use
        the same SSE endpoint for task execution.

        Args:
            session_id: Local session ID (used for naming, not for API calls)
            project_path: Path to the project directory
            task_description: Task description
            coding_account: Account name for the coding agent
            message_queue: Queue to post events to (for UI integration)
            coding_model: Optional model override
            coding_reasoning: Optional reasoning level
            terminal_cols: Terminal width in columns (calculated from panel width)
            screenshots: Optional list of screenshot file paths for agent reference

        Returns:
            Tuple of (success, final_output_text, server_session_id, work_done)
            where work_done is a dict with files_modified, files_created, commands_run
        """
        # Track file modifications to detect exploration-only vs actual work
        work_done: dict = {
            "files_modified": [],
            "files_created": [],
            "commands_run": [],
            "total_tool_calls": 0,
        }

        # Create server-side session first when not provided
        if server_session_id is None:
            try:
                server_session = self.api_client.create_session(
                    project_path=project_path,
                    name=f"task-{session_id}"
                )
                server_session_id = server_session.id
            except Exception as e:
                message_queue.put(("status", f"❌ Failed to create session: {e}"))
                return False, str(e), None, None

        if server_session_id:
            message_queue.put(("session_id", server_session_id))

        # Use provided terminal_cols or fall back to default
        effective_cols = terminal_cols if terminal_cols else TERMINAL_COLS

        # Start the task via API using the server session ID
        try:
            self.api_client.start_task(
                session_id=server_session_id,
                project_path=project_path,
                task_description=task_description,
                coding_agent=coding_account,
                coding_model=coding_model,
                coding_reasoning=coding_reasoning,
                terminal_rows=TERMINAL_ROWS,
                terminal_cols=effective_cols,
                screenshots=screenshots,
            )
        except Exception as e:
            message_queue.put(("status", f"❌ Failed to start task: {e}"))
            return False, str(e), server_session_id, None

        # Emit message start
        message_queue.put(("ai_switch", "CODING AI"))
        message_queue.put(("message_start", "CODING AI"))

        # Stream output via SSE using server session ID
        stream_client = self._get_stream_client()
        exit_code = 0
        got_complete_event = False

        # Detect if this is a provider that outputs stream-json (needs parsing)
        # Both anthropic (Claude) and qwen use similar JSON formats
        json_parser = None
        try:
            accounts = self.api_client.list_accounts()
            for acc in accounts:
                if acc.name == coding_account and acc.provider in ("anthropic", "qwen"):
                    json_parser = ClaudeStreamJsonParser()
                    break
        except Exception:
            pass  # Fall back to raw output if we can't determine provider

        # For stream-json providers: accumulate parsed text directly (no terminal width constraints)
        # For others: use pyte terminal emulation for proper ANSI/cursor handling
        accumulated_text: list[str] = []
        terminal = None if json_parser else TerminalEmulator(cols=effective_cols, rows=TERMINAL_ROWS)

        # Detect provider for Codex prompt echo filtering
        is_codex = False
        try:
            for acc in accounts:
                if acc.name == coding_account and acc.provider == "openai":
                    is_codex = True
                    break
        except Exception:
            pass

        # Codex prompt echo filtering state
        # Codex echoes stdin after the header. The structure is:
        #   [banner] OpenAI Codex v0.92.0...
        #   --------
        #   [header] workdir, model, etc
        #   --------
        #   [36muser[0m  (or just "user" - ANSI colored)
        #   [prompt text - this is what we want to filter]
        #   [36mmcp startup:[0m no servers
        #   [agent work starts here]
        #
        # We keep everything up to the colored "user" line, filter the prompt,
        # and show everything after "mcp startup:"
        codex_in_prompt_echo = False  # True when we're in the echoed prompt section
        codex_past_prompt_echo = False  # True when we've seen "mcp startup:" and are done
        codex_output_buffer = ""
        codex_ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
        codex_user_line_pattern = re.compile(r'\n--------\nuser\n')
        codex_user_pattern = re.compile(r'\n--------\n(?:\x1b\[[0-9;]*m)*user(?:\x1b\[[0-9;]*m)*\n')
        codex_mcp_pattern = re.compile(
            r'(?:\x1b\[[0-9;]*m)*mcp startup:(?:\x1b\[[0-9;]*m)*[^\n]*\n',
            re.IGNORECASE,
        )

        try:
            for event in stream_client.stream_events(server_session_id, include_terminal=True):
                if event.event_type == "terminal":
                    # Feed terminal data (base64 for live PTY, plain text for logs)
                    raw_data = event.data.get("data", "")
                    if event.data.get("text"):
                        raw_bytes = (raw_data or "").encode("utf-8")
                    else:
                        try:
                            raw_bytes = base64.b64decode(raw_data or "")
                        except Exception:
                            raw_bytes = b""

                    # For anthropic, parse stream-json and render directly (no terminal emulation)
                    if json_parser:
                        text_chunks = json_parser.feed(raw_bytes)
                        if text_chunks:
                            # Accumulate parsed text and render as HTML directly
                            accumulated_text.extend(text_chunks)
                            readable_text = "\n".join(text_chunks) + "\n"
                            # Escape HTML and preserve newlines
                            all_text = "\n".join(accumulated_text)
                            html_output = html.escape(all_text)
                            message_queue.put(("stream", readable_text, html_output))
                        # If no complete lines yet, don't emit anything
                    else:
                        decoded = raw_bytes.decode("utf-8", errors="replace")

                        # Each task phase starts a new Codex process. If we see a fresh
                        # Codex banner while in passthrough mode, restart prompt filtering
                        # so the implementation prompt gets stripped too.
                        if is_codex and codex_past_prompt_echo:
                            banner_probe = codex_ansi_pattern.sub('', decoded).lower()
                            if "openai codex" in banner_probe and "--------" in banner_probe:
                                codex_past_prompt_echo = False
                                codex_in_prompt_echo = False
                                codex_output_buffer = ""

                        # Filter Codex prompt echo
                        # Codex output structure:
                        #   [banner]
                        #   --------
                        #   [header info]
                        #   --------
                        #   user  (or [36muser[0m with ANSI)
                        #   [prompt - FILTER THIS]
                        #   mcp startup: ...
                        #   [agent work - KEEP THIS]
                        if is_codex and not codex_past_prompt_echo:
                            codex_output_buffer += decoded
                            normalized = codex_output_buffer.replace("\r\n", "\n").replace("\r", "\n")

                            # Strip ANSI codes for pattern matching
                            stripped = codex_ansi_pattern.sub('', normalized)

                            # Look for "user" on its own line (after second --------)
                            # This marks the start of the echoed prompt
                            user_line_match = codex_user_line_pattern.search(stripped)

                            # Check if we're entering the prompt echo section
                            if not codex_in_prompt_echo and user_line_match:
                                # Found start of prompt echo - emit content before it
                                # Find the position in the original (non-stripped) string
                                # by finding the pattern with ANSI codes
                                match = codex_user_pattern.search(normalized)
                                if match:
                                    pre_echo = normalized[:match.start()]
                                    if pre_echo.strip():
                                        terminal.feed(pre_echo.encode("utf-8"))
                                        accumulated_text.append(pre_echo)
                                        html_output = terminal.render_html()
                                        message_queue.put(("stream", pre_echo, html_output))
                                    # Now in prompt echo section - update buffer
                                    codex_in_prompt_echo = True
                                    codex_output_buffer = normalized[match.end():]
                                    normalized = codex_output_buffer
                                    stripped = codex_ansi_pattern.sub('', normalized)

                            # Check if we've passed the prompt echo section
                            if codex_in_prompt_echo and "mcp startup:" in stripped.lower():
                                # Found end of prompt echo - extract agent output after marker
                                match = codex_mcp_pattern.search(normalized)
                                if match:
                                    agent_output = normalized[match.end():]
                                else:
                                    # Fallback: find in stripped and estimate position
                                    marker_pos = stripped.lower().find("mcp startup:")
                                    newline_after = stripped.find("\n", marker_pos)
                                    if newline_after != -1:
                                        agent_output = normalized[newline_after + 1:]
                                    else:
                                        agent_output = ""
                                codex_past_prompt_echo = True
                                codex_output_buffer = ""
                                if agent_output.strip():
                                    terminal.feed(agent_output.encode("utf-8"))
                                    accumulated_text.append(agent_output)
                                    html_output = terminal.render_html()
                                    message_queue.put(("stream", agent_output, html_output))
                                continue

                            # If not in prompt echo yet, emit normally but keep buffer small
                            if not codex_in_prompt_echo:
                                if len(codex_output_buffer) > 2000:
                                    # No marker found - emit older content and keep looking
                                    to_emit = codex_output_buffer[:-500]
                                    codex_output_buffer = codex_output_buffer[-500:]
                                    if to_emit.strip():
                                        terminal.feed(to_emit.encode("utf-8"))
                                        accumulated_text.append(to_emit)
                                        html_output = terminal.render_html()
                                        message_queue.put(("stream", to_emit, html_output))
                            continue

                        # Past prompt echo - pass through raw PTY output with terminal emulation
                        terminal.feed(raw_bytes)
                        accumulated_text.append(decoded)
                        html_output = terminal.render_html()
                        message_queue.put(("stream", decoded, html_output))

                elif event.event_type == "complete":
                    exit_code = event.data.get("exit_code", 0)
                    got_complete_event = True
                    # Flush any remaining Codex output buffer
                    if is_codex and codex_output_buffer.strip():
                        if terminal:
                            terminal.feed(codex_output_buffer.encode("utf-8"))
                        accumulated_text.append(codex_output_buffer)
                        codex_output_buffer = ""
                    break

                elif event.event_type == "error":
                    error_msg = event.data.get("error", "Unknown error")
                    message_queue.put(("status", f"❌ Error: {error_msg}"))
                    return False, error_msg, server_session_id, work_done

                elif event.event_type == "event":
                    # Structured events from EventLog
                    event_type = event.data.get("type", "")
                    if event_type == "session_started":
                        pass  # Already handled by task start
                    elif event_type == "terminal_output":
                        # Fallback for parsed terminal output from EventLog
                        # This ensures content is shown even if raw PTY parsing fails
                        data = event.data.get("data", "")
                        if data and data.strip():
                            # For JSON providers, use the already-parsed EventLog text
                            if json_parser:
                                accumulated_text = [data]  # Replace with EventLog content
                                html_output = html.escape(data)
                                message_queue.put(("stream", data, html_output))
                    elif event_type == "tool_call_started":
                        tool = event.data.get("tool", "")
                        work_done["total_tool_calls"] += 1
                        if tool == "bash":
                            cmd = event.data.get("command", "")
                            message_queue.put(("activity", f"● bash: {cmd[:50]}"))
                            # Track significant commands
                            cmd_lower = cmd.lower()
                            keywords = ["pytest", "npm", "make", "cargo", "go ", "yarn", "pnpm", "gradle", "mvn"]
                            if any(kw in cmd_lower for kw in keywords):
                                work_done["commands_run"].append(cmd[:100])
                        elif tool in ("read", "write", "edit"):
                            path = event.data.get("path", "")
                            message_queue.put(("activity", f"● {tool}: {path}"))
                            # Track file modifications
                            if tool == "write" and path and path not in work_done["files_created"]:
                                work_done["files_created"].append(path)
                            elif tool == "edit" and path and path not in work_done["files_modified"]:
                                work_done["files_modified"].append(path)

        except Exception as e:
            message_queue.put(("status", f"❌ Stream error: {e}"))
            return False, str(e), server_session_id, work_done

        # Check if stream ended without completion event - this indicates a bug
        # where the SSE stream dropped unexpectedly (e.g., PTY session marked inactive
        # prematurely). Don't treat this as success or verification will start early.
        if not got_complete_event:
            message_queue.put(("status", "❌ Stream ended unexpectedly without completion"))
            return False, "Stream ended unexpectedly", server_session_id, work_done

        # Get plain text for final output
        # Always prefer accumulated_text (captures full session) over terminal.get_text()
        # (which only returns the visible screen buffer, losing scrollback history)
        if accumulated_text:
            final_output = "\n".join(accumulated_text)
        elif terminal:
            final_output = terminal.get_text()
        else:
            final_output = ""

        # Emit completion
        if exit_code == 0:
            message_queue.put(("message_complete", "CODING AI", final_output))
            # Check if agent only explored without making changes
            made_changes = bool(work_done["files_modified"] or work_done["files_created"])
            if not made_changes:
                message_queue.put(("warning", "Agent completed without modifying any files"))
            return True, final_output, server_session_id, work_done
        else:
            message_queue.put(("status", f"❌ Agent exited with code {exit_code}"))
            message_queue.put(("message_complete", "CODING AI", final_output))
            return False, f"Agent exited with code {exit_code}", server_session_id, work_done

    def get_session(self, session_id: str) -> Session:
        """Get or create a session by ID."""
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(id=session_id)
        return self.sessions[session_id]

    def _worktree_id(self, session_id: str) -> str:
        """Return the session ID used for worktree operations (server-aware)."""
        session = self.get_session(session_id)
        return session.server_session_id or session_id

    def _wait_for_server_session_inactive(
        self,
        server_session_id: str,
        timeout_seconds: float = 3.0,
        poll_interval: float = 0.1,
    ) -> bool:
        """Wait until a server-side session is inactive or gone."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                server_session = self.api_client.get_session(server_session_id)
            except Exception:
                # Missing session is equivalent to inactive for restart purposes
                return True
            if not server_session or not getattr(server_session, "active", False):
                return True
            time.sleep(poll_interval)
        return False

    def _request_server_cancel(self, server_session_id: str, timeout_seconds: float = 3.0) -> bool:
        """Request cancellation of a server-side session and wait for shutdown."""
        try:
            self.api_client.cancel_session(server_session_id)
        except Exception:
            return False
        return self._wait_for_server_session_inactive(server_session_id, timeout_seconds=timeout_seconds)

    def _workspace_label(self, session: Session) -> str:
        """Format the workspace path label shown next to the session log button."""
        workspace_path = ""
        if session.worktree_path:
            workspace_path = str(session.worktree_path)
        elif session.project_path:
            workspace_path = str(session.project_path)
        if workspace_path:
            return f"Workspace: {workspace_path}"
        return "Workspace: Not set"

    def _workspace_html(self, session: Session) -> str:
        """Render workspace label as compact inline HTML."""
        workspace_path = ""
        if session.worktree_path:
            workspace_path = str(session.worktree_path)
        elif session.project_path:
            workspace_path = str(session.project_path)
        tooltip = workspace_path if workspace_path else "Not set"
        return (
            f'<div class="workspace-inline" title="{html.escape(tooltip)}">'
            f"{html.escape(self._workspace_label(session))}"
            "</div>"
        )

    @staticmethod
    def _compute_live_stream_updates(
        live_stream: str,
        live_patch: tuple[str, str] | None,
        session: "Session",
        live_stream_id: str,
        task_ended: bool,
    ) -> tuple[str | None, tuple[str, str] | None, bool]:
        """Decide whether to patch or replace live stream content.

        Returns (display_stream, live_patch_out, has_initial_live_render_flag).
        """
        use_live_patch = live_patch
        has_initial = session.has_initial_live_render

        if not use_live_patch and live_stream and not task_ended:
            has_live_id = f'data-live-id="{live_stream_id}"' in live_stream
            if has_live_id:
                if has_initial:
                    use_live_patch = (live_stream_id, live_stream)
                else:
                    has_initial = True

        if task_ended:
            has_initial = False

        display_stream = None if use_live_patch else live_stream
        return display_stream, use_live_patch, has_initial

    def create_session(self, name: str = "New Session") -> Session:
        """Create a new session with a unique ID."""
        session = Session(name=name)
        self.sessions[session.id] = session
        return session

    SUPPORTED_PROVIDERS = ProviderUIManager.SUPPORTED_PROVIDERS
    OPENAI_REASONING_LEVELS = ProviderUIManager.OPENAI_REASONING_LEVELS

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

    def _get_qwen_remaining_usage(self) -> float:
        return self.provider_ui._get_qwen_remaining_usage()

    def _check_usage_and_switch(self, coding_account: str) -> tuple[str, str | None]:
        """Check if current provider exceeds usage threshold and switch if needed.

        Returns:
            Tuple of (account_to_use, switched_from_account)
            switched_from_account is None if no switch occurred
        """
        try:
            threshold = self.api_client.get_usage_switch_threshold()
            # Ensure threshold is a valid number
            if not isinstance(threshold, (int, float)):
                threshold = 90
        except Exception:
            threshold = 90

        # Threshold of 100 disables usage-based switching
        if threshold >= 100:
            return coding_account, None

        # Check current account usage
        remaining = self.get_remaining_usage(coding_account)
        used_pct = (1.0 - remaining) * 100

        if used_pct < threshold:
            # Usage is below threshold, no switch needed
            return coding_account, None

        # Usage exceeds threshold, try to find a fallback
        try:
            next_account = self.api_client.get_next_fallback_provider(coding_account)
        except Exception:
            return coding_account, None

        if not next_account:
            return coding_account, None

        # Check that fallback has better usage
        next_remaining = self.get_remaining_usage(next_account)
        next_used_pct = (1.0 - next_remaining) * 100

        if next_used_pct >= threshold:
            # Fallback also exceeds threshold, don't switch
            return coding_account, None

        # Switch to fallback
        return next_account, coding_account

    def get_context_remaining(self, account_name: str) -> float:
        """Get remaining context capacity for a provider (0.0-1.0)."""
        return self.provider_ui.get_context_remaining(account_name)

    def _check_context_and_switch(self, coding_account: str) -> tuple[str, str | None]:
        """Check if current provider's context exceeds threshold and switch if needed.

        Returns:
            Tuple of (account_to_use, switched_from_account)
            switched_from_account is None if no switch occurred
        """
        try:
            threshold = self.api_client.get_context_switch_threshold()
        except Exception:
            threshold = 90

        # Threshold of 100 disables context-based switching
        if threshold >= 100:
            return coding_account, None

        # Check current account context usage
        remaining = self.get_context_remaining(coding_account)
        used_pct = (1.0 - remaining) * 100

        if used_pct < threshold:
            # Context usage is below threshold, no switch needed
            return coding_account, None

        # Context exceeds threshold, try to find a fallback
        try:
            next_account = self.api_client.get_next_fallback_provider(coding_account)
        except Exception:
            return coding_account, None

        if not next_account:
            return coding_account, None

        # Fallback provider starts fresh, so it has full context
        # No need to check fallback's context usage
        return next_account, coding_account

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

    def get_provider_choices(self) -> list[str]:
        """Get ordered list of provider type choices for dropdowns."""
        return self.provider_ui.get_provider_choices()

    def _read_project_docs(self, project_path: Path) -> str | None:
        """Read project documentation if present.

        Returns a short reference section pointing to on-disk docs instead of
        inlining their content.
        """
        from chad.util.project_setup import build_doc_reference_text

        return build_doc_reference_text(project_path)

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
        try:
            account = self.api_client.get_account(verification_account)
        except Exception:
            return True, "Verification skipped: account not found"

        verification_provider = account.provider
        stored_model = account.model
        stored_reasoning = account.reasoning
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

        def _run_automated_verification() -> tuple[bool, str | None]:
            try:
                from .verification.tools import verify as run_verify
                if on_activity:
                    on_activity("system", "Running verification (flake8)...")

                verify_result = run_verify(project_root=project_path, lint_only=True)

                # Treat timeout as a pass (coding agent ran their own tests)
                error_msg = verify_result.get("error") or ""
                if "timed out" in error_msg.lower():
                    if on_activity:
                        on_activity("system", "Verification timed out, treating as pass")
                    return True, None

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

            return True, None

        coding_summary = extract_coding_summary(coding_output)
        change_summary = coding_summary.change_summary if coding_summary else None
        trimmed_output = _truncate_verification_output(coding_output)
        exploration_prompt = get_verification_exploration_prompt(trimmed_output, task_description, change_summary)
        conclusion_prompt = get_verification_conclusion_prompt()

        try:
            max_parse_attempts = 2
            last_error = None
            retry_conclusion_prompt = conclusion_prompt

            for attempt in range(max_parse_attempts):
                verifier = create_provider(verification_config)
                if on_activity:
                    verifier.set_activity_callback(on_activity)

                if not verifier.start_session(project_path, None):
                    return True, "Verification skipped: failed to start session"

                try:
                    verifier.send_message(exploration_prompt)
                    _ = verifier.get_response(timeout=timeout)

                    verifier.send_message(retry_conclusion_prompt)
                    response = verifier.get_response(timeout=timeout)

                    if not response:
                        last_error = "No response from verification agent"
                        if on_activity:
                            on_activity(
                                "system",
                                f"Verification response missing (attempt {attempt + 1}/{max_parse_attempts})",
                            )
                        continue

                    try:
                        passed, summary, issues = parse_verification_response(response)

                        if passed:
                            # Skip automated verification for mock provider (testing mode)
                            if verification_provider != "mock" and not check_verification_mentioned(coding_output):
                                verified, feedback = _run_automated_verification()
                                if not verified:
                                    return False, feedback or "Verification failed"
                            return True, summary

                        feedback = summary
                        if issues:
                            feedback += "\n\nIssues:\n" + "\n".join(f"- {issue}" for issue in issues)
                        return False, feedback

                    except VerificationParseError as e:
                        last_error = str(e)
                        if on_activity:
                            on_activity(
                                "system",
                                f"Verification parse failed (attempt {attempt + 1}/{max_parse_attempts}): {last_error}",
                            )
                        if attempt < max_parse_attempts - 1:
                            # Retry with a stronger reminder to use strict JSON in the conclusion phase.
                            retry_conclusion_prompt = (
                                "Your previous response was not valid JSON. "
                                "You MUST respond with ONLY a JSON object like:\n"
                                '```json\n{"passed": true, "summary": "explanation"}\n```\n\n'
                                "Try again.\n\n"
                                f"{conclusion_prompt}"
                            )
                        continue
                finally:
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

        try:
            account = self.api_client.get_account(account_name)
        except Exception:
            return f"❌ Account '{account_name}' not found"

        if account.provider != "openai":
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

    def get_role_config_status(
        self,
        task_state: str | None = None,
        worktree_path: str | None = None,
        project_path: str | None = None,
        verification_account: str | None = None,
        accounts=None,
    ) -> tuple[bool, str]:
        return self.provider_ui.get_role_config_status(
            task_state, worktree_path, project_path=project_path, verification_account=verification_account,
            accounts=accounts,
        )

    def format_role_status(
        self,
        task_state: str | None = None,
        worktree_path: str | None = None,
        switched_from: str | None = None,
        active_account: str | None = None,
        project_path: str | None = None,
        verification_account: str | None = None,
        accounts=None,
    ) -> str:
        return self.provider_ui.format_role_status(
            task_state, worktree_path, switched_from, active_account, project_path, verification_account,
            accounts=accounts,
        )

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

    def cancel_task(self, session_id: str) -> tuple:
        """Cancel the running task for a specific session.

        Returns a tuple of UI component updates matching the cancel_btn.click outputs:
        (chatbot, live_stream, task_status, project_path, task_description,
         start_btn, cancel_btn, followup_row, merge_section_group)
        """
        session = self.get_session(session_id)
        session.cancel_requested = True
        session.active = False  # Mark session as inactive to allow restart
        server_shutdown_confirmed = True
        if session.server_session_id:
            # Cancel the server-side task too; otherwise it keeps running in the background
            # and restart attempts will be blocked by the "already running" guard.
            server_shutdown_confirmed = self._request_server_cancel(
                session.server_session_id,
                timeout_seconds=3.0,
            )
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
        if session.worktree_path and session.project_path and server_shutdown_confirmed:
            try:
                git_mgr = GitWorktreeManager(Path(session.project_path))
                git_mgr.delete_worktree(self._worktree_id(session_id))
            except Exception:
                pass  # Best effort cleanup
            session.worktree_path = None
            session.worktree_base_commit = None

        no_change = gr.update()
        return (
            gr.update(value="🛑 Task cancelled"),  # live_stream
            no_change,  # chatbot - keep existing chat history
            no_change,  # task_status
            no_change,  # project_path - keep so user can restart
            no_change,  # task_description - keep so user can modify and restart
            gr.update(interactive=True),  # start_btn - re-enable to allow new task
            gr.update(interactive=False),  # cancel_btn - disable since nothing to cancel
            gr.update(visible=False),  # followup_row - hide follow-up section
            gr.update(visible=False),  # merge_section_group - hide merge section
        )

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
        accounts = self.api_client.list_accounts()
        account_names = {acc.name for acc in accounts}
        if verification_agent == self.VERIFICATION_NONE:
            return None, coding_model, coding_reasoning
        actual_account = coding_account if verification_agent == self.SAME_AS_CODING else verification_agent
        if not actual_account or actual_account not in account_names:
            return None, coding_model, coding_reasoning

        def normalize(value: str | None, fallback: str) -> str:
            if not value or value == self.SAME_AS_CODING:
                return fallback
            return value

        if verification_agent == self.SAME_AS_CODING:
            resolved_model = normalize(coding_model, "default")
            resolved_reasoning = normalize(coding_reasoning, "default")
            return actual_account, resolved_model, resolved_reasoning

        try:
            acc = self.api_client.get_account(actual_account)
            account_model = acc.model or "default"
            account_reasoning = acc.reasoning or "default"
        except Exception:
            account_model = "default"
            account_reasoning = "default"
        resolved_model = normalize(verification_model, account_model)
        resolved_reasoning = normalize(verification_reasoning, account_reasoning)

        # Persist explicit verification preferences to the verification account only
        try:
            if verification_model and verification_model != self.SAME_AS_CODING:
                self.api_client.set_account_model(actual_account, resolved_model)
                # Also persist to global preferred verification model config
                self.api_client.set_preferred_verification_model(resolved_model)
            if verification_reasoning and verification_reasoning != self.SAME_AS_CODING:
                self.api_client.set_account_reasoning(actual_account, resolved_reasoning)
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
        accounts=None,
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
        if accounts is None:
            accounts = self.api_client.list_accounts()
        accounts_map = {acc.name: acc.provider for acc in accounts}
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
            try:
                acc = self.api_client.get_account(actual_account)
                stored_model = acc.model or "default"
                stored_reasoning = acc.reasoning or "default"
            except Exception:
                stored_model = "default"
                stored_reasoning = "default"
            preferred_model = current_verification_model or stored_model
            model_value = value_or_default(preferred_model, model_choices)

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
        terminal_cols: int | None = None,
        screenshots: list[str] | None = None,
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
        prior_cancel_requested = session.cancel_requested
        session.cancel_requested = False
        session.config = None

        # Check if there's already an active task on the server for this session
        # This prevents accidental double-starts when generators get interrupted
        if session.server_session_id:
            try:
                server_session = self.api_client.get_session(session.server_session_id)
                if server_session and getattr(server_session, "active", False):
                    # User just cancelled and immediately restarted: complete server-side
                    # cancellation first so restart can proceed on the happy path.
                    if prior_cancel_requested:
                        cancelled = self._request_server_cancel(session.server_session_id, timeout_seconds=3.0)
                        if cancelled:
                            server_session = self.api_client.get_session(session.server_session_id)

                    if server_session and getattr(server_session, "active", False):
                        # Task is still running - cancel it first or warn user
                        error_msg = (
                            "⚠️ A task is already running on this session.\n\n"
                            "Please wait for it to complete or cancel it before starting a new task."
                        )
                        yield (
                            gr.update(),  # live_stream
                            [],  # chatbot
                            gr.update(value=error_msg),  # task_status
                            gr.update(),  # project_path
                            gr.update(),  # task_description
                            gr.update(interactive=True),  # start_btn
                            gr.update(interactive=False),  # cancel_btn
                            gr.update(),  # role_status
                            gr.update(),  # session_log_btn
                            gr.update(),  # workspace_display
                            gr.update(),  # followup_input
                            gr.update(),  # followup_row
                            gr.update(),  # send_followup_btn
                            gr.update(),  # merge_section_group
                            gr.update(),  # changes_summary
                            gr.update(),  # merge_target_branch
                            gr.update(),  # diff_full_content
                            "",  # merge_section_header
                            "",  # live_patch_trigger
                            gr.update(),  # exploration_prompt_accordion
                            gr.update(),  # exploration_prompt_content
                            gr.update(),  # implementation_prompt_accordion
                            gr.update(),  # implementation_prompt_content
                            gr.update(),  # verification_prompt_accordion
                            gr.update(),  # verification_prompt_content
                        )
                        return
            except Exception:
                # Session might not exist on server yet, which is fine
                pass

        # Stable live_id for DOM patching across the session
        live_stream_id = f"live-{session.id}"

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
            live_patch: tuple[str, str] | None = None,
            task_state: str | None = None,
            task_ended: bool = False,
            exploration_prompt: str | None = None,
            implementation_prompt: str | None = None,
            verification_prompt: str | None = None,
            verification_account: str | None = None,
        ):
            """Format output tuple for Gradio with current UI state.

            Args:
                live_patch: Optional (live_id, inner_html) tuple for JS patching.
                           When provided, JavaScript will patch the container with
                           data-live-id=live_id using the inner_html content,
                           preserving scroll position and text selection.
                           The live_stream value is ignored when patching is active.
                task_state: Optional task state (running, verifying, completed, failed)
                           for dynamic status display.
                task_ended: When True, task has completed/failed/cancelled and buttons
                           should allow starting a new task (start enabled, cancel disabled).
            """
            display_stream, live_patch, updated_flag = self._compute_live_stream_updates(
                live_stream, live_patch, session, live_stream_id, task_ended
            )
            session.has_initial_live_render = updated_flag
            is_error = "❌" in status
            # Get worktree/project path for status display
            wt_path = str(session.worktree_path) if session.worktree_path else None
            proj_path = session.project_path
            display_role_status = self.format_role_status(
                task_state, wt_path, session.switched_from, session.coding_account, proj_path,
                verification_account=verification_account
            )
            log_btn_update = gr.update(
                label=session.log_path.name if session.log_path else "Session Log",
                value=str(session.log_path) if session.log_path else None,
                visible=session.log_path is not None,
            )
            workspace_update = gr.update(value=self._workspace_html(session))
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
            header_text = "### Changes Ready to Merge" if show_merge else ""
            # Build live patch trigger HTML if patch data provided
            if live_patch:
                live_id, inner_html = live_patch
                # Escape HTML for safe embedding in data attribute
                import html as html_module
                escaped_html = html_module.escape(inner_html)
                patch_html = f'<div data-live-patch="{live_id}" style="display:none">{escaped_html}</div>'
            else:
                patch_html = ""
            # When task ends, enable start button and disable cancel button
            # regardless of the `interactive` flag (which controls input fields)
            start_btn_interactive = True if task_ended else interactive
            cancel_btn_interactive = False if task_ended else not interactive
            # When display_stream is None (patching mode), don't update the live_stream value
            # This allows JS to patch the DOM in-place without Gradio replacing it
            live_stream_update = gr.update() if display_stream is None else gr.update(value=display_stream)
            return (
                live_stream_update,  # live_stream - Updated by JS patching when live_patch is provided
                display_history,  # chatbot
                # Task status - always visible in DOM, CSS :empty hides when blank
                gr.update(value=status if is_error else ""),
                gr.update(value=project_path, interactive=interactive),
                gr.update(value=task_description, interactive=interactive),
                gr.update(interactive=start_btn_interactive),
                gr.update(interactive=cancel_btn_interactive),
                gr.update(value=display_role_status),
                log_btn_update,
                workspace_update,
                gr.update(value=""),  # Clear followup input
                gr.update(visible=show_followup),  # Show/hide followup row
                gr.update(interactive=show_followup),  # Enable/disable send button
                gr.update(visible=show_merge),  # Show/hide merge section group
                gr.update(value=merge_summary),  # Merge changes summary
                branch_update,  # Branch dropdown choices
                gr.update(value=diff_full),  # Full diff content
                header_text,  # merge_section_header - dynamic header
                patch_html,  # live_patch_trigger - JS reads this to patch content
                # Prompt accordions - all three visible, update content when provided
                # Prompts are rendered as markdown directly since they contain markdown content
                gr.update(visible=True) if exploration_prompt else gr.update(),
                gr.update(value=exploration_prompt) if exploration_prompt else gr.update(),
                gr.update(visible=True) if implementation_prompt else gr.update(),
                gr.update(value=implementation_prompt) if implementation_prompt else gr.update(),
                gr.update(visible=True) if verification_prompt else gr.update(),
                gr.update(value=verification_prompt) if verification_prompt else gr.update(),
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

            try:
                account = self.api_client.get_account(coding_agent)
            except Exception:
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
            session.project_path = str(path_obj)
            session.last_live_stream = ""  # Clear for new task

            # Worktree will be created by the API when the task starts
            # For now, set the project path; worktree info will be fetched after task starts

            coding_account = coding_agent
            coding_provider = account.provider

            # Check usage threshold and switch provider if needed
            coding_account, switched_from = self._check_usage_and_switch(coding_account)
            if switched_from:
                # Provider was switched due to usage threshold
                session.switched_from = switched_from
                try:
                    new_account = self.api_client.get_account(coding_account)
                    coding_provider = new_account.provider
                except Exception:
                    # Fallback failed, revert to original
                    coding_account = coding_agent
                    coding_provider = account.provider
                    session.switched_from = None

            self.api_client.set_account_role(coding_account, "CODING")

            selected_model = coding_model or account.model or "default"
            selected_reasoning = coding_reasoning or account.reasoning or "default"
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
                self.api_client.set_account_model(coding_account, selected_model)
            except Exception:
                pass
            try:
                self.api_client.set_account_reasoning(coding_account, selected_reasoning)
            except Exception:
                pass

            coding_config = ModelConfig(
                provider=coding_provider,
                model_name=selected_model,
                account_name=coding_account,
                reasoning_effort=None if selected_reasoning == "default" else selected_reasoning,
            )

            # Create event log for structured logging
            try:
                if not session.event_log:
                    session.event_log = EventLog(session.id)
                session.event_log.log(SessionStartedEvent(
                    task_description=task_description,
                    project_path=str(path_obj),
                    coding_provider=coding_provider,
                    coding_account=coding_account,
                    coding_model=selected_model if selected_model != "default" else None,
                ))
                session.event_log.start_turn()
            except Exception:
                pass  # Event logging is optional

            status_prefix = "**Starting Chad...**\n\n"
            status_prefix += f"• Project: {path_obj}\n"
            status_prefix += f"• CODING: {coding_account} ({coding_provider})\n"
            if selected_model and selected_model != "default":
                status_prefix += f"• Model: {selected_model}\n"
            if selected_reasoning and selected_reasoning != "default":
                status_prefix += f"• Reasoning: {selected_reasoning}\n"
            status_prefix += "• Mode: Direct (coding AI only)\n\n"

            chat_history.append({"role": "user", "content": f"**Task**\n\n{task_description}"})
            session.event_log.log(UserMessageEvent(content=task_description))

            # Build the exploration and implementation prompts for display
            project_docs = self._read_project_docs(path_obj)
            display_exploration_prompt = build_exploration_prompt(
                task_description, project_docs, str(path_obj)
            )
            # Build implementation prompt with placeholder exploration output
            display_implementation_prompt = build_implementation_prompt(
                task_description,
                "{exploration_output}",  # Placeholder until exploration completes
                project_docs,
                str(path_obj),
            )
            session.last_exploration_prompt = display_exploration_prompt
            session.last_implementation_prompt = display_implementation_prompt

            initial_status = f"{status_prefix}⏳ Initializing session..."
            yield make_yield(
                chat_history, initial_status, summary=initial_status, interactive=False,
                task_state="running",
                exploration_prompt=display_exploration_prompt,
                implementation_prompt=display_implementation_prompt,
            )

            # Use the streaming API to run the task
            # This ensures both CLI and Gradio UI use the same PTY-based execution path
            relay_complete = threading.Event()
            task_success = [False]
            completion_reason = [""]
            coding_final_output: list[str] = [""]

            def api_task_loop():
                """Run the task via the API streaming endpoint."""
                try:
                    success, output, server_session_id, work_done = self.run_task_via_api(
                        session_id=session_id,
                        project_path=str(path_obj),
                        task_description=task_description,
                        coding_account=coding_account,
                        message_queue=message_queue,
                        coding_model=selected_model if selected_model != "default" else None,
                        coding_reasoning=selected_reasoning if selected_reasoning != "default" else None,
                        terminal_cols=terminal_cols,
                        screenshots=screenshots,
                    )
                    # Store work_done for later use in revision context
                    session.last_work_done = work_done
                    task_success[0] = success
                    coding_final_output[0] = output
                    if success:
                        completion_reason[0] = "Coding AI completed task"
                    else:
                        completion_reason[0] = output

                    # Fetch worktree info from API after task completes (use server session ID)
                    if server_session_id:
                        session.server_session_id = server_session_id
                        try:
                            wt_status = self.api_client.get_worktree_status(server_session_id)
                            if wt_status and wt_status.exists:
                                session.worktree_path = Path(wt_status.path) if wt_status.path else None
                                session.worktree_branch = wt_status.branch
                                session.worktree_base_commit = wt_status.base_commit
                                session.has_worktree_changes = wt_status.has_changes
                        except Exception:
                            pass  # Worktree info is optional

                except Exception as exc:
                    message_queue.put(("status", f"❌ Error: {str(exc)}"))
                    completion_reason[0] = str(exc)
                finally:
                    session.active = task_success[0]  # Keep active only if task succeeded
                    session.provider = None  # API-based execution doesn't keep a provider
                    relay_complete.set()

            relay_thread = threading.Thread(target=api_task_loop, daemon=True)
            relay_thread.start()

            status_msg = f"{status_prefix}✓ Task started via API\n\n⏳ Processing task..."
            yield make_yield([], status_msg, summary=status_msg, interactive=False, task_state="running")

            current_status = f"{status_prefix}⏳ Coding AI is working..."
            current_ai = "CODING AI"
            current_live_stream = ""
            last_live_stream = session.last_live_stream  # Restore from session for tab switches
            yield make_yield(
                chat_history,
                current_status,
                current_live_stream,
                summary=current_status,
                interactive=False,
                task_state="running",
            )

            import time as time_module

            last_activity = ""
            streaming_buffer = ""
            full_history = []  # Infinite history - list of (ai_name, content, timestamp) tuples
            display_buffer = LiveStreamDisplayBuffer()
            latest_pyte_html = ""  # Track latest pyte-rendered HTML for inline display
            last_yield_time = 0.0
            last_log_update_time = time_module.time()
            log_update_interval = 10.0  # Update session log every 10 seconds
            min_yield_interval = 0.05
            pending_message_idx = None
            render_state = LiveStreamRenderState()
            progress_emitted = False  # Track if we've shown a progress update bubble
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
                        latest_pyte_html = ""
                        render_state.reset()
                        yield make_yield(chat_history, current_status, current_live_stream, task_state="running")
                        last_yield_time = time_module.time()

                    elif msg_type == "message_start":
                        speaker = msg[1]
                        # Track where the final message will be inserted
                        # Don't add a placeholder - use only the dedicated live stream panel
                        pending_message_idx = len(chat_history)
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        latest_pyte_html = ""
                        render_state.reset()
                        current_ai = speaker
                        yield make_yield(chat_history, current_status, current_live_stream, task_state="running")
                        last_yield_time = time_module.time()

                    elif msg_type == "message_complete":
                        speaker, content = msg[1], msg[2]
                        # Insert the final message at the tracked position
                        if pending_message_idx is not None and pending_message_idx <= len(chat_history):
                            chat_history.insert(pending_message_idx, make_chat_message(speaker, content))
                        else:
                            chat_history.append(make_chat_message(speaker, content))
                        pending_message_idx = None
                        streaming_buffer = ""
                        last_activity = ""
                        current_live_stream = ""
                        latest_pyte_html = ""
                        render_state.reset()
                        # Log assistant message completion
                        if session.event_log and content:
                            session.event_log.log(AssistantMessageEvent(
                                blocks=[{"kind": "text", "content": content[:1000]}]
                            ))
                        yield make_yield(chat_history, current_status, current_live_stream, task_state="running")
                        last_yield_time = time_module.time()

                    elif msg_type == "status":
                        current_status = f"{status_prefix}{msg[1]}"
                        streaming_buffer = ""
                        current_live_stream = ""
                        latest_pyte_html = ""
                        render_state.reset()
                        summary_text = current_status
                        yield make_yield(
                            chat_history,
                            current_status,
                            current_live_stream,
                            summary=summary_text,
                            task_state="running",
                        )
                        last_yield_time = time_module.time()

                    elif msg_type == "session_id":
                        session.server_session_id = msg[1]
                        # Fetch worktree info now that task is starting
                        try:
                            wt_status = self.api_client.get_worktree_status(msg[1])
                            if wt_status and wt_status.exists:
                                session.worktree_path = Path(wt_status.path) if wt_status.path else None
                                session.worktree_branch = wt_status.branch
                                session.worktree_base_commit = wt_status.base_commit
                        except Exception:
                            pass
                        yield make_yield(
                            chat_history,
                            current_status,
                            current_live_stream,
                            task_state="running",
                            summary=current_status,
                        )
                        last_yield_time = time_module.time()

                    elif msg_type == "ai_switch":
                        current_ai = msg[1]
                        streaming_buffer = ""
                        full_history.append(_history_entry(current_ai, "Processing request\n"))
                        display_buffer.append("Processing request\n")

                    elif msg_type == "stream":
                        chunk = msg[1]
                        html_chunk = msg[2] if len(msg) > 2 else None
                        # Fetch worktree path on first stream if not yet known
                        if not session.worktree_path and session.server_session_id:
                            try:
                                wt_status = self.api_client.get_worktree_status(session.server_session_id)
                                if wt_status and wt_status.exists:
                                    session.worktree_path = Path(wt_status.path) if wt_status.path else None
                                    session.worktree_branch = wt_status.branch
                                    session.worktree_base_commit = wt_status.base_commit
                            except Exception:
                                pass
                        if html_chunk:
                            latest_pyte_html = html_chunk  # Track for activity/empty handlers
                        if chunk.strip():
                            streaming_buffer += chunk
                            full_history.append(_history_entry(current_ai, chunk))
                            display_buffer.append(chunk)
                            if not html_chunk:
                                current_live_stream = build_live_stream_html(display_buffer.content, current_ai, live_stream_id)
                                if current_live_stream:
                                    last_live_stream = current_live_stream
                                    session.last_live_stream = current_live_stream

                            # Check for progress update in streaming buffer
                            if not progress_emitted:
                                progress = extract_progress_update(streaming_buffer)
                                if progress:
                                    progress_emitted = True
                                    # Insert progress bubble at the tracked position
                                    progress_msg = make_progress_message(progress)
                                    if pending_message_idx is not None:
                                        chat_history.insert(pending_message_idx, progress_msg)
                                        pending_message_idx += 1  # Final message will go after progress
                                        # Reset display buffer so live view starts fresh after progress
                                        display_buffer = LiveStreamDisplayBuffer()
                                        latest_pyte_html = ""
                                        render_state.reset()
                                    else:
                                        chat_history.append(progress_msg)
                                    # Keep showing last live stream content while buffers reset
                                    # Don't yield "" which clears the UI during the gap before new content arrives
                                    yield make_yield(chat_history, current_status, last_live_stream, task_state="running")
                                    last_yield_time = time_module.time()

                        # Track current live stream content for the dedicated panel
                        # Must be outside chunk.strip() check - terminal can have valid HTML
                        # even when raw text is empty (e.g., cursor movements, screen updates)
                        if html_chunk:
                            current_live_stream = build_live_stream_html_from_pyte(html_chunk, current_ai, live_stream_id)
                            if current_live_stream:
                                last_live_stream = current_live_stream
                                session.last_live_stream = current_live_stream

                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            # Only update the dedicated live stream panel during streaming
                            # Don't update chat_history to avoid constant DOM replacement
                            # which breaks scroll position and text selection
                            if current_live_stream:
                                yield make_yield(chat_history, current_status, current_live_stream, task_state="running")
                                last_yield_time = now

                    elif msg_type == "activity":
                        last_activity = msg[1]
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            # Update only the dedicated live stream panel with activity
                            if latest_pyte_html:
                                combined_html = latest_pyte_html + f"\n\n{html.escape(last_activity)}"
                                activity_stream = build_live_stream_html_from_pyte(combined_html, current_ai, live_stream_id)
                            else:
                                display_content = display_buffer.content
                                content = f"{display_content}\n\n{last_activity}" if display_content else last_activity
                                activity_stream = build_live_stream_html(content, current_ai, live_stream_id)
                            if activity_stream:
                                last_live_stream = activity_stream
                                session.last_live_stream = activity_stream
                                yield make_yield(chat_history, current_status, activity_stream, task_state="running")
                                last_yield_time = now

                except queue.Empty:
                    # Yield periodically to keep Gradio generator alive and UI responsive
                    # Without periodic yields, the UI won't update while waiting for SSE events
                    now = time_module.time()
                    if now - last_yield_time >= min_yield_interval:
                        # Yield current state - show waiting message if no content yet
                        display = current_live_stream or last_live_stream
                        if not display:
                            # Show waiting placeholder so user knows task is running
                            display = build_live_stream_html("⏳ Waiting for agent output...", current_ai, live_stream_id)
                        yield make_yield(chat_history, current_status, display, task_state="running")
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
                # Flush any pending stream/activity messages so the live panel keeps latest output
                while True:
                    try:
                        pending_msg = message_queue.get_nowait()
                    except queue.Empty:
                        break
                    pending_type = pending_msg[0]
                    if pending_type == "stream":
                        chunk = pending_msg[1]
                        html_chunk = pending_msg[2] if len(pending_msg) > 2 else None
                        if html_chunk:
                            last_live_stream = build_live_stream_html_from_pyte(html_chunk, current_ai, live_stream_id)
                            session.last_live_stream = last_live_stream
                        elif chunk.strip():
                            display_buffer.append(chunk)
                    elif pending_type == "activity":
                        detail = pending_msg[1]
                        if display_buffer.content:
                            display_buffer.append(f"\n\n{detail}")
                        else:
                            display_buffer.append(detail)
                cancel_live_stream = current_live_stream or last_live_stream
                if not cancel_live_stream:
                    display_content = display_buffer.content
                    if display_content:
                        cancel_live_stream = build_live_stream_html(display_content, current_ai, live_stream_id)
                yield make_yield(
                    chat_history,
                    "🛑 Task cancelled",
                    cancel_live_stream,
                    summary="🛑 Task cancelled",
                    show_followup=True,  # Always show follow-up after task starts
                    task_state="failed",
                    task_ended=True,
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
                            # Log assistant message
                            if session.event_log and content:
                                session.event_log.log(AssistantMessageEvent(
                                    blocks=[{"kind": "text", "content": content[:1000]}]
                                ))
                            # Keep showing last live stream content during transition to verification
                            yield make_yield(chat_history, current_status, last_live_stream, task_state="running")
                    except queue.Empty:
                        break

            relay_thread.join(timeout=1)

            # Track the active configuration only when the session can continue
            session.config = coding_config if session.active else None

            verification_enabled = verification_agent != self.VERIFICATION_NONE
            verification_account_for_run = actual_verification_account if verification_enabled else None
            verification_log: list[dict[str, object]] = []
            verified: bool | None = None  # Track verification result

            sanitized_reason = completion_reason[0] or ""

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
                max_verification_attempts = self.api_client.get_max_verification_attempts()
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

                    # Build verification prompt for display before starting verification
                    coding_summary = extract_coding_summary(last_coding_output)
                    change_summary = coding_summary.change_summary if coding_summary else None
                    trimmed_output = _truncate_verification_output(last_coding_output)
                    display_verification_prompt = get_verification_exploration_prompt(
                        trimmed_output, task_description, change_summary
                    )
                    session.last_verification_prompt = display_verification_prompt

                    # Show verification status with live stream
                    verify_status = (
                        f"{status_prefix}🔍 Running verification "
                        f"(attempt {verification_attempt}/{max_verification_attempts})..."
                    )
                    verify_placeholder = build_live_stream_html(
                        "🔍 Starting verification...", "VERIFICATION AI", live_stream_id
                    )
                    # Reset live render flag so verification gets a full render, not a patch
                    session.has_initial_live_render = False
                    yield make_yield(
                        chat_history, verify_status, verify_placeholder, task_state="verifying",
                        verification_prompt=display_verification_prompt,
                        verification_account=verification_account_for_run,
                    )

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

                    # Poll message queue while verification runs and stream to live view
                    verify_display_buffer = LiveStreamDisplayBuffer()
                    verify_display_buffer.append("🔍 Starting verification...\n")
                    verify_last_yield = 0.0
                    verify_live_stream = ""
                    keepalive_interval = 2.0  # Yield every 2 seconds even without new data
                    while not verification_complete.is_set() and not session.cancel_requested:
                        try:
                            msg = message_queue.get(timeout=0.05)
                            if msg[0] == "stream":
                                chunk = msg[1]
                                html_chunk = msg[2] if len(msg) > 2 else None
                                if chunk.strip():
                                    verify_display_buffer.append(chunk)
                                # Use HTML chunk from API if available, otherwise render from text
                                if html_chunk:
                                    verify_live_stream = build_live_stream_html_from_pyte(
                                        html_chunk, "VERIFICATION AI", live_stream_id
                                    )
                                elif chunk.strip():
                                    verify_live_stream = build_live_stream_html(
                                        verify_display_buffer.content, "VERIFICATION AI", live_stream_id
                                    )
                                if verify_live_stream:
                                    session.last_live_stream = verify_live_stream
                                    now = time_module.time()
                                    if now - verify_last_yield >= min_yield_interval:
                                        yield make_yield(chat_history, verify_status, verify_live_stream, task_state="verifying",
                                                         verification_account=verification_account_for_run)
                                        verify_last_yield = now
                        except queue.Empty:
                            # Yield periodic keepalive updates to show verification is still running
                            now = time_module.time()
                            if now - verify_last_yield >= keepalive_interval:
                                # Build live stream if we have content but haven't shown it yet
                                if not verify_live_stream and verify_display_buffer.content:
                                    verify_live_stream = build_live_stream_html(
                                        verify_display_buffer.content, "VERIFICATION AI", live_stream_id
                                    )
                                if verify_live_stream:
                                    yield make_yield(chat_history, verify_status, verify_live_stream, task_state="verifying",
                                                     verification_account=verification_account_for_run)
                                else:
                                    # Even without content, yield to keep connection alive
                                    yield make_yield(chat_history, verify_status, "", task_state="verifying",
                                                     verification_account=verification_account_for_run)
                                verify_last_yield = now

                    # Final yield to ensure content is shown even if loop exited quickly
                    if verify_live_stream:
                        yield make_yield(chat_history, verify_status, verify_live_stream, task_state="verifying",
                                         verification_account=verification_account_for_run)

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
                        # Log verification attempt error
                        if session.event_log:
                            session.event_log.log(VerificationAttemptEvent(
                                attempt_number=verification_attempt,
                                passed=False,
                                summary="Verification error",
                            ))
                        break
                    elif verified:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                        chat_history.append(
                            {
                                "role": "user",
                                "content": "───────────── ✅ VERIFICATION PASSED ─────────────",
                            }
                        )
                        # Log verification attempt success
                        if session.event_log:
                            session.event_log.log(VerificationAttemptEvent(
                                attempt_number=verification_attempt,
                                passed=True,
                                summary=verification_feedback[:500] if verification_feedback else "",
                            ))
                    else:
                        chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                        # Log verification attempt failure
                        if session.event_log:
                            session.event_log.log(VerificationAttemptEvent(
                                attempt_number=verification_attempt,
                                passed=False,
                                summary=verification_feedback[:500] if verification_feedback else "",
                            ))

                        # If not verified and session is still active with a provider, send feedback to coding agent
                        # Note: API-based execution doesn't support direct provider revision (session.provider is None)
                        can_revise = (
                            session.active
                            and session.provider is not None
                            and hasattr(session.provider, 'is_alive')
                            and session.provider.is_alive()
                            and verification_attempt < max_verification_attempts
                        )
                        if can_revise:
                            revision_content = (
                                "───────────── → REVISION REQUESTED ─────────────\n\n"
                                "*Sending verification feedback to coding agent...*"
                            )
                            chat_history.append({"role": "user", "content": revision_content})
                            # Log revision request
                            if session.event_log:
                                session.event_log.log(UserMessageEvent(content="Revision requested"))
                            revision_status = f"{status_prefix}→ Sending revision request to coding agent..."
                            yield make_yield(chat_history, revision_status, "", task_state="running")

                            # Send feedback to coding agent via session continuation
                            revision_request = (
                                "The verification agent found issues with your work. "
                                "Please address them:\n\n"
                                f"{verification_feedback}\n\n"
                                "Please fix these issues and confirm when done."
                            )

                            # Track where the final revision message will be inserted
                            # Don't add a placeholder - use only the dedicated live stream panel
                            revision_pending_idx = len(chat_history)
                            revision_status_msg = f"{status_prefix}⏳ Coding agent working on revisions..."
                            # Show live stream placeholder during revision setup
                            revision_placeholder = build_live_stream_html("⏳ Preparing revision...", "CODING AI", live_stream_id)
                            # Reset live render flag so revision gets a full render, not a patch
                            session.has_initial_live_render = False
                            yield make_yield(chat_history, revision_status_msg, revision_placeholder, task_state="running")

                            # Run revision in a thread so we can stream output to live view
                            revision_result: list = [None, None]  # [response, error]
                            revision_complete = threading.Event()

                            def run_revision_thread():
                                try:
                                    session.provider.send_message(revision_request)
                                    resp = session.provider.get_response(timeout=DEFAULT_CODING_TIMEOUT)
                                    revision_result[0] = resp
                                except Exception as exc:
                                    revision_result[1] = exc
                                finally:
                                    revision_complete.set()

                            revision_thread = threading.Thread(target=run_revision_thread, daemon=True)
                            revision_thread.start()

                            # Poll message queue while revision runs (live stream updates)
                            rev_display_buffer = LiveStreamDisplayBuffer()
                            rev_last_yield = 0.0
                            rev_live_stream = ""
                            while not revision_complete.is_set() and not session.cancel_requested:
                                try:
                                    msg = message_queue.get(timeout=0.05)
                                    if msg[0] == "stream":
                                        chunk = msg[1]
                                        html_chunk = msg[2] if len(msg) > 2 else None
                                        if chunk.strip():
                                            rev_display_buffer.append(chunk)
                                        # Use HTML chunk from API if available, otherwise render from text
                                        if html_chunk:
                                            rev_live_stream = build_live_stream_html_from_pyte(
                                                html_chunk, "CODING AI", live_stream_id
                                            )
                                        elif chunk.strip():
                                            rev_live_stream = build_live_stream_html(
                                                rev_display_buffer.content, "CODING AI", live_stream_id
                                            )
                                        if rev_live_stream:
                                            session.last_live_stream = rev_live_stream
                                            now = time_module.time()
                                            if now - rev_last_yield >= min_yield_interval:
                                                yield make_yield(chat_history, revision_status_msg, rev_live_stream, task_state="running")
                                                rev_last_yield = now
                                except queue.Empty:
                                    pass

                            # Final yield to ensure content is shown even if loop exited quickly
                            if rev_live_stream:
                                yield make_yield(chat_history, revision_status_msg, rev_live_stream, task_state="running")

                            revision_thread.join(timeout=1.0)
                            revision_response = revision_result[0]
                            revision_error = revision_result[1]

                            if revision_error:
                                chat_history.insert(revision_pending_idx, {
                                    "role": "assistant",
                                    "content": f"**CODING AI**\n\n❌ *Error: {revision_error}*",
                                })
                                session.active = False
                                session.provider = None
                                session.config = None
                                break

                            if revision_response:
                                parsed_revision = parse_codex_output(revision_response)
                                chat_history.insert(revision_pending_idx, make_chat_message("CODING AI", parsed_revision))
                                last_coding_output = parsed_revision
                                # Log revision response
                                if session.event_log:
                                    session.event_log.log(AssistantMessageEvent(
                                        blocks=[{"kind": "text", "content": parsed_revision[:1000]}]
                                    ))
                            else:
                                chat_history[revision_pending_idx] = {
                                    "role": "assistant",
                                    "content": "**CODING AI**\n\n❌ *No response to revision request*",
                                }
                                break

                            reverify_placeholder = build_live_stream_html(
                                "✓ Revision complete, re-verifying...", "VERIFICATION AI", live_stream_id
                            )
                            yield make_yield(
                                chat_history,
                                f"{status_prefix}✓ Revision complete, re-verifying...",
                                reverify_placeholder,
                                task_state="verifying",
                                verification_account=verification_account_for_run,
                            )
                        elif (
                            session.server_session_id
                            and verification_attempt < max_verification_attempts
                            and not session.cancel_requested
                        ):
                            # API-based revision: re-run task via API with revision feedback
                            revision_content = (
                                "───────────── → REVISION REQUESTED ─────────────\n\n"
                                "*Re-running coding agent with verification feedback...*"
                            )
                            chat_history.append({"role": "user", "content": revision_content})
                            if session.event_log:
                                session.event_log.log(UserMessageEvent(content="Revision requested (API)"))
                            revision_status = f"{status_prefix}→ Re-running coding agent with feedback..."
                            yield make_yield(chat_history, revision_status, "", task_state="running")

                            # Build revision task description with context about previous work
                            work_context = ""
                            if session.last_work_done:
                                wd = session.last_work_done
                                work_parts = []
                                if wd.get("files_modified"):
                                    work_parts.append("Files modified:\n" + "\n".join(f"  - {f}" for f in wd["files_modified"]))
                                if wd.get("files_created"):
                                    work_parts.append("Files created:\n" + "\n".join(f"  - {f}" for f in wd["files_created"]))
                                if wd.get("commands_run"):
                                    work_parts.append("Commands run:\n" + "\n".join(f"  - {c}" for c in wd["commands_run"][-5:]))
                                if not work_parts:
                                    work_parts.append("No file modifications were made in the previous attempt.")
                                work_context = "\n\nPrevious attempt work:\n" + "\n".join(work_parts)

                            revision_task = (
                                f"REVISION REQUEST: The previous attempt had verification issues.\n\n"
                                f"Original task: {task_description}\n\n"
                                f"Verification feedback:\n{verification_feedback}"
                                f"{work_context}\n\n"
                                f"Please fix these issues. Make sure to actually modify files, not just analyze them."
                            )

                            # Track where the revision message will be inserted
                            revision_pending_idx = len(chat_history)
                            revision_status_msg = f"{status_prefix}⏳ Coding agent working on revisions..."
                            revision_placeholder = build_live_stream_html("⏳ Preparing revision...", "CODING AI", live_stream_id)
                            # Reset live render flag so revision gets a full render, not a patch
                            session.has_initial_live_render = False
                            yield make_yield(chat_history, revision_status_msg, revision_placeholder, task_state="running")

                            # Run revision via API in a thread
                            revision_result: list = [None, None, None, None]  # [success, output, error, work_done]
                            revision_complete = threading.Event()

                            def run_api_revision_thread():
                                try:
                                    success, output, _, work_done = self.run_task_via_api(
                                        session_id=session.id,
                                        project_path=str(path_obj),
                                        task_description=revision_task,
                                        coding_account=coding_account,
                                        message_queue=message_queue,
                                        coding_model=selected_model if selected_model != "default" else None,
                                        coding_reasoning=selected_reasoning if selected_reasoning != "default" else None,
                                        server_session_id=session.server_session_id,
                                        terminal_cols=terminal_cols,
                                    )
                                    revision_result[0] = success
                                    revision_result[1] = output
                                    revision_result[3] = work_done
                                    # Update session's work_done with revision changes
                                    if work_done:
                                        session.last_work_done = work_done
                                except Exception as exc:
                                    revision_result[2] = exc
                                finally:
                                    revision_complete.set()

                            revision_thread = threading.Thread(target=run_api_revision_thread, daemon=True)
                            revision_thread.start()

                            # Poll message queue while revision runs
                            rev_display_buffer = LiveStreamDisplayBuffer()
                            rev_last_yield = 0.0
                            rev_live_stream = ""
                            while not revision_complete.is_set() and not session.cancel_requested:
                                try:
                                    msg = message_queue.get(timeout=0.05)
                                    if msg[0] == "stream":
                                        chunk = msg[1]
                                        html_chunk = msg[2] if len(msg) > 2 else None
                                        if chunk.strip():
                                            rev_display_buffer.append(chunk)
                                        # Use HTML chunk from API if available, otherwise render from text
                                        if html_chunk:
                                            rev_live_stream = build_live_stream_html_from_pyte(
                                                html_chunk, "CODING AI", live_stream_id
                                            )
                                        elif chunk.strip():
                                            rev_live_stream = build_live_stream_html(
                                                rev_display_buffer.content, "CODING AI", live_stream_id
                                            )
                                        if rev_live_stream:
                                            session.last_live_stream = rev_live_stream
                                            now = time_module.time()
                                            if now - rev_last_yield >= min_yield_interval:
                                                yield make_yield(chat_history, revision_status_msg, rev_live_stream, task_state="running")
                                                rev_last_yield = now
                                except queue.Empty:
                                    pass

                            # Final yield to ensure content is shown even if loop exited quickly
                            if rev_live_stream:
                                yield make_yield(chat_history, revision_status_msg, rev_live_stream, task_state="running")

                            revision_thread.join(timeout=1.0)
                            revision_success = revision_result[0]
                            revision_output = revision_result[1]
                            revision_error = revision_result[2]

                            if revision_error:
                                chat_history.insert(revision_pending_idx, {
                                    "role": "assistant",
                                    "content": f"**CODING AI**\n\n❌ *Revision error: {revision_error}*",
                                })
                                break

                            if revision_success and revision_output:
                                chat_history.insert(revision_pending_idx, make_chat_message("CODING AI", revision_output))
                                last_coding_output = revision_output
                                if session.event_log:
                                    session.event_log.log(AssistantMessageEvent(
                                        blocks=[{"kind": "text", "content": revision_output[:1000]}]
                                    ))
                            else:
                                chat_history.insert(revision_pending_idx, {
                                    "role": "assistant",
                                    "content": "**CODING AI**\n\n❌ *Revision task did not complete successfully*",
                                })
                                break

                            reverify_placeholder = build_live_stream_html(
                                "✓ Revision complete, re-verifying...", "VERIFICATION AI", live_stream_id
                            )
                            yield make_yield(
                                chat_history,
                                f"{status_prefix}✓ Revision complete, re-verifying...",
                                reverify_placeholder,
                                task_state="verifying",
                                verification_account=verification_account_for_run,
                            )
                        else:
                            # Can't continue - max attempts reached or cancelled
                            break

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
                sanitized_reason = completion_reason[0] or ""
                if "End your response with a JSON summary block" in sanitized_reason or '"change_summary": "One sentence describing what was changed"' in sanitized_reason:
                    sanitized_reason = "Task ended before producing any agent output."
                final_status = (
                    f"❌ Task did not complete successfully\n\n*{sanitized_reason}*"
                    if sanitized_reason
                    else "❌ Task did not complete successfully"
                )
                failure_msg = "───────────── ❌ TASK FAILED ─────────────"
                if sanitized_reason:
                    failure_msg += f"\n\n*{sanitized_reason}*"
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

            # Log session end event with tool call count from tracked work
            total_tool_calls = 0
            if session.last_work_done:
                total_tool_calls = session.last_work_done.get("total_tool_calls", 0)
            if session.event_log:
                session.event_log.log(SessionEndedEvent(
                    success=overall_success,
                    reason=sanitized_reason if not overall_success else (completion_reason[0] or session_status),
                    total_tool_calls=total_tool_calls,
                ))
            if session.log_path:
                final_status += f"\n\n*Session log: {session.log_path}*"
            final_summary = f"{status_prefix}{final_status}"

            # Store session state for follow-up messages
            session.chat_history = chat_history
            session.coding_account = coding_account

            # Always show follow-up input after task starts, regardless of outcome
            can_continue = True  # Always allow follow-up messages
            if session.active and overall_success:
                final_status += "\n\n*Session active - you can send follow-up messages*"
            else:
                final_status += "\n\n*You can send follow-up messages*"
            final_summary = f"{status_prefix}{final_status}"

            # Check for worktree changes to show merge section
            # Show merge section whenever there are changes, regardless of task success
            has_changes, merge_summary_text = self.check_worktree_changes(session_id)
            show_merge = has_changes

            # Get available branches and rendered diff for merge target
            branches = []
            diff_html = ""
            if show_merge and session.project_path:
                try:
                    git_mgr = GitWorktreeManager(Path(session.project_path))
                    branches = git_mgr.get_branches()
                    parsed_diff = git_mgr.get_parsed_diff(self._worktree_id(session_id), session.worktree_base_commit)
                    diff_html = self._render_diff_html(parsed_diff)
                except Exception:
                    branches = ["main"]

            final_live_stream = last_live_stream if session.cancel_requested else ""
            final_task_state = "completed" if overall_success else "failed"
            yield make_yield(
                chat_history,
                final_summary,
                final_live_stream,
                summary=final_summary,
                interactive=False,  # Task description locked after work begins
                show_followup=can_continue,
                show_merge=show_merge,
                merge_summary=merge_summary_text if show_merge else "",
                branch_choices=branches if show_merge else None,
                diff_full=diff_html,
                task_state=final_task_state,
                task_ended=True,
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
                show_followup=True,  # Always show follow-up after task starts
                show_merge=False,
                merge_summary="",
                task_state="failed",
                task_ended=True,
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
        screenshots: list[str] | None = None,
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
            screenshots: Optional list of screenshot file paths to include

        Yields:
            Tuples of (chat_history, live_stream, followup_input, followup_row, send_btn, live_patch_trigger,
            merge_section_group, changes_summary, merge_target_branch, diff_content, merge_section_header)
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

        # Stable live_id for DOM patching across the session
        live_stream_id = f"live-{session.id}"

        merge_no_change = (gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

        def make_followup_yield(
            history,
            live_stream: str = "",
            show_followup: bool = True,
            working: bool = False,
            live_patch: tuple[str, str] | None = None,
            merge_updates: tuple = merge_no_change,
        ):
            """Format output for follow-up responses."""
            # Build live patch trigger HTML if patch data provided
            if live_patch:
                live_id, inner_html = live_patch
                import html as html_module
                escaped_html = html_module.escape(inner_html)
                patch_html = f'<div data-live-patch="{live_id}" style="display:none">{escaped_html}</div>'
            else:
                patch_html = ""
            return (
                live_stream,  # live_stream component
                history,  # chatbot
                gr.update(value="" if not working else followup_message),  # Clear input when not working
                gr.update(visible=show_followup),  # Follow-up row visibility
                gr.update(interactive=not working),  # Send button interactivity
                patch_html,  # live_patch_trigger
                *merge_updates,
            )

        if not followup_message or not followup_message.strip():
            yield make_followup_yield(chat_history, "", show_followup=True, merge_updates=merge_no_change)
            return

        # Capture raw message before screenshot/resume-prompt modifications
        raw_followup_message = followup_message

        # Append screenshot paths to message if provided
        if screenshots:
            screenshot_section = "\n\nThe user has attached the following screenshots for reference. " \
                "Use the Read tool to view them:\n"
            for screenshot_path in screenshots:
                screenshot_section += f"- {screenshot_path}\n"
            followup_message = followup_message + screenshot_section

        accounts_list = self.api_client.list_accounts()
        accounts = {acc.name: acc.provider for acc in accounts_list}  # Map name -> provider
        has_account = bool(coding_agent and coding_agent in accounts)

        def normalize_model_value(value: str | None) -> str:
            return value if value else "default"

        def normalize_reasoning_value(value: str | None) -> str:
            return value if value else "default"

        # Get account details for model/reasoning defaults
        coding_acc = None
        if has_account:
            try:
                coding_acc = self.api_client.get_account(coding_agent)
            except Exception:
                pass

        requested_model = normalize_model_value(
            coding_model
            if coding_model is not None
            else (coding_acc.model if coding_acc else "default")
        )
        requested_reasoning = normalize_reasoning_value(
            coding_reasoning
            if coding_reasoning is not None
            else (coding_acc.reasoning if coding_acc else "default")
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
                self.api_client.set_account_model(coding_agent, requested_model)
                self.api_client.set_account_reasoning(coding_agent, requested_reasoning)
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

        # Also need handoff if session is active but provider was released (API-based execution)
        provider_reconnect_needed = has_account and session.active and session.provider is None
        handoff_needed = provider_changed or pref_changed or provider_reconnect_needed

        if handoff_needed:
            # Get new provider type first (needed for formatting handoff context)
            coding_provider_type = accounts[coding_agent]

            # Log handoff checkpoint to event log before stopping old provider
            old_provider_type = ""
            old_model = ""
            if session.event_log and session.provider:
                provider_session_id = None
                if hasattr(session.provider, "get_session_id"):
                    provider_session_id = session.provider.get_session_id()

                log_handoff_checkpoint(
                    session.event_log,
                    session.task_description or "",
                    provider_session_id,
                    target_provider=coding_provider_type,
                )

                # Track old provider info for ProviderSwitchedEvent
                if session.config:
                    old_provider_type = getattr(session.config, "provider", "")
                    old_model = getattr(session.config, "model_name", "") or ""

            # Stop old session if active
            if session.provider:
                try:
                    session.provider.stop_session()
                except Exception:
                    pass
                session.provider = None
                session.active = False

            # Start new provider
            coding_config = ModelConfig(
                provider=coding_provider_type,
                model_name=requested_model,
                account_name=coding_agent,
                reasoning_effort=None if requested_reasoning == "default" else requested_reasoning,
            )

            # Only show handoff message if actually changing provider or preferences
            # Skip message for silent reconnects (when provider was released after API-based execution)
            if provider_changed or pref_changed:
                handoff_detail = f"{coding_agent} ({coding_provider_type}"
                if requested_model and requested_model != "default":
                    handoff_detail += f", {requested_model}"
                if requested_reasoning and requested_reasoning != "default":
                    handoff_detail += f", {requested_reasoning} reasoning"
                handoff_detail += ")"

                handoff_title = "PROVIDER HANDOFF" if provider_changed else "PREFERENCE UPDATE"
                handoff_msg = f"───────────── → {handoff_title} ─────────────\n\n" f"*Switching to {handoff_detail}*"
                chat_history.append({"role": "user", "content": handoff_msg})
            yield make_followup_yield(chat_history, "→ Reconnecting...", working=True, merge_updates=merge_no_change)

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
                yield make_followup_yield(chat_history, "", show_followup=True, merge_updates=merge_no_change)
                return

            # Track the switch for UI indication
            old_account = session.coding_account
            session.switched_from = old_account if old_account else None

            session.provider = new_provider
            session.coding_account = coding_agent
            session.active = True
            session.config = coding_config

            # Log provider switched event
            if session.event_log:
                session.event_log.log(ProviderSwitchedEvent(
                    from_provider=old_provider_type,
                    to_provider=coding_provider_type,
                    from_model=old_model,
                    to_model=requested_model or "",
                    reason="user_requested" if provider_changed else "preference_update",
                ))

            # Build resume prompt from event log if available
            if session.event_log:
                followup_message = build_resume_prompt(
                    session.event_log, followup_message, target_provider=coding_provider_type
                )
            else:
                # Fallback to old method
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
            yield make_followup_yield(chat_history, "", show_followup=False, merge_updates=merge_no_change)
            return

        # Add user's follow-up message to history
        if provider_changed:
            # Extract just the user's actual message after handoff context
            display_msg = followup_message.split("# Follow-up Request")[-1].strip()
            user_content = f"**Follow-up** (via {coding_agent})\n\n{display_msg}"
        else:
            user_content = f"**Follow-up**\n\n{followup_message}"
        chat_history.append({"role": "user", "content": user_content})

        # Log follow-up user message to event log
        if session.event_log:
            session.event_log.start_turn()
            session.event_log.log(UserMessageEvent(content=raw_followup_message))

        # Track where the final message will be inserted
        # Don't add a placeholder - use only the dedicated live stream panel
        pending_idx = len(chat_history)

        yield make_followup_yield(chat_history, "⏳ Processing follow-up...", working=True, merge_updates=merge_no_change)

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

        live_stream = ""
        while not relay_complete.is_set() and not session.cancel_requested:
            try:
                msg = message_queue.get(timeout=0.02)
                msg_type = msg[0]

                if msg_type == "stream":
                    chunk = msg[1]
                    html_chunk = msg[2] if len(msg) > 2 else None
                    if chunk.strip():
                        full_history.append(_history_entry(current_ai, chunk))
                        display_buffer.append(chunk)
                    # Use HTML chunk from API if available, otherwise render from text
                    if html_chunk:
                        live_stream = build_live_stream_html_from_pyte(html_chunk, current_ai, live_stream_id)
                    elif chunk.strip():
                        live_stream = build_live_stream_html(display_buffer.content, current_ai, live_stream_id)
                    if live_stream:
                        session.last_live_stream = live_stream
                        now = time_module.time()
                        if now - last_yield_time >= min_yield_interval:
                            yield make_followup_yield(
                                chat_history,
                                live_stream,
                                working=True,
                                merge_updates=merge_no_change,
                            )
                            last_yield_time = now

                elif msg_type == "activity":
                    now = time_module.time()
                    if now - last_yield_time >= min_yield_interval:
                        display_content = display_buffer.content
                        if display_content:
                            content = display_content + f"\n\n{msg[1]}"
                        else:
                            content = msg[1]
                        # Update only the dedicated live stream panel
                        live_stream = build_live_stream_html(content, current_ai, live_stream_id)
                        yield make_followup_yield(
                            chat_history,
                            live_stream,
                            working=True,
                            merge_updates=merge_no_change,
                        )
                        last_yield_time = now

            except queue.Empty:
                now = time_module.time()
                if now - last_yield_time >= 0.3:
                    display_content = display_buffer.content
                    if display_content:
                        # Update only the dedicated live stream panel
                        live_stream = build_live_stream_html(display_content, current_ai, live_stream_id)
                        yield make_followup_yield(
                            chat_history,
                            live_stream,
                            working=True,
                            merge_updates=merge_no_change,
                        )
                        last_yield_time = now

        relay_thread.join(timeout=1)

        # Insert final response into chat history
        if error_holder[0]:
            # Try auto-switch on quota exhaustion
            switched, new_account = self._try_auto_switch_provider(session, error_holder[0])
            if switched and new_account:
                # Show switch notification in chat
                switch_msg = (
                    f"───────────── → AUTO-SWITCH ─────────────\n\n"
                    f"*Quota/rate limit reached on {session.switched_from}. "
                    f"Switching to {new_account}...*"
                )
                chat_history.append({"role": "user", "content": switch_msg})
                yield make_followup_yield(chat_history, "→ Retrying with fallback provider...", working=True, merge_updates=merge_no_change)

                # Build resume prompt and retry
                new_provider_type = session.config.provider if session.config else "generic"
                if session.event_log:
                    retry_message = build_resume_prompt(
                        session.event_log, followup_message, target_provider=new_provider_type
                    )
                else:
                    retry_message = followup_message

                # Retry with new provider
                retry_response_holder = [None]
                retry_error_holder = [None]
                retry_complete = threading.Event()

                def retry_relay():
                    try:
                        session.provider.send_message(retry_message)
                        response = session.provider.get_response(timeout=DEFAULT_CODING_TIMEOUT)
                        retry_response_holder[0] = response
                    except Exception as e:
                        retry_error_holder[0] = str(e)
                    finally:
                        retry_complete.set()

                retry_thread = threading.Thread(target=retry_relay, daemon=True)
                retry_thread.start()

                # Wait for retry with streaming
                retry_live_stream = ""
                while not retry_complete.is_set() and not session.cancel_requested:
                    try:
                        msg = message_queue.get(timeout=0.1)
                        if msg[0] == "stream":
                            chunk = msg[1]
                            html_chunk = msg[2] if len(msg) > 2 else None
                            if chunk.strip():
                                full_history.append(_history_entry(current_ai, chunk))
                                display_buffer.append(chunk)
                            if html_chunk:
                                retry_live_stream = build_live_stream_html_from_pyte(html_chunk, current_ai, live_stream_id)
                            elif chunk.strip():
                                retry_live_stream = build_live_stream_html(display_buffer.content, current_ai, live_stream_id)
                            if retry_live_stream:
                                session.last_live_stream = retry_live_stream
                                yield make_followup_yield(chat_history, retry_live_stream, working=True, merge_updates=merge_no_change)
                    except queue.Empty:
                        pass

                # Final yield to ensure content is shown
                if retry_live_stream:
                    yield make_followup_yield(chat_history, retry_live_stream, working=True, merge_updates=merge_no_change)

                retry_thread.join(timeout=1)

                if retry_error_holder[0]:
                    # Retry also failed
                    chat_history.insert(pending_idx, {
                        "role": "assistant",
                        "content": f"**CODING AI**\n\n❌ *Error after auto-switch: {retry_error_holder[0]}*",
                    })
                    session.active = False
                    session.provider = None
                    session.config = None
                    self._update_session_log(session, chat_history, full_history)
                    yield make_followup_yield(chat_history, "", show_followup=False, merge_updates=merge_no_change)
                    return

                if retry_response_holder[0]:
                    # Update response holder with retry result
                    response_holder[0] = retry_response_holder[0]
                    error_holder[0] = None
            else:
                # No auto-switch available, show original error
                chat_history.insert(pending_idx, {
                    "role": "assistant",
                    "content": f"**CODING AI**\n\n❌ *Error: {error_holder[0]}*",
                })
                session.active = False
                session.provider = None
                session.config = None
                self._update_session_log(session, chat_history, full_history)
                yield make_followup_yield(chat_history, "", show_followup=False, merge_updates=merge_no_change)
                return

        if not response_holder[0]:
            chat_history.insert(pending_idx, {
                "role": "assistant",
                "content": "**CODING AI**\n\n❌ *No response received*",
            })
            self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)
            yield make_followup_yield(chat_history, "", show_followup=True, merge_updates=merge_no_change)
            return

        parsed = parse_codex_output(response_holder[0])
        chat_history.insert(pending_idx, make_chat_message("CODING AI", parsed))
        last_coding_output = parsed

        # Log assistant response to event log
        if session.event_log and parsed:
            session.event_log.log(AssistantMessageEvent(
                blocks=[{"kind": "text", "content": parsed[:1000]}]
            ))

        # Update stored history
        session.chat_history = chat_history
        self._update_session_log(session, chat_history, full_history, verification_attempts=verification_log)

        yield make_followup_yield(chat_history, "", show_followup=True, working=True, merge_updates=merge_no_change)

        # Run verification on follow-up
        verification_enabled = verification_agent != self.VERIFICATION_NONE
        verification_account_for_run = actual_verification_account if verification_enabled else None

        if verification_account_for_run and verification_account_for_run in accounts:
            # Verification loop (like start_chad_task)
            max_verification_attempts = self.api_client.get_max_verification_attempts()
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

                verify_placeholder = build_live_stream_html(
                    "🔍 Starting verification...", "VERIFICATION AI", live_stream_id
                )
                # Reset live render flag so verification gets a full render, not a patch
                session.has_initial_live_render = False
                yield make_followup_yield(chat_history, verify_placeholder, working=True, merge_updates=merge_no_change)

                def verification_activity(activity_type: str, detail: str):
                    content = detail if activity_type == "stream" else f"[{activity_type}] {detail}\n"
                    message_queue.put(("verify_stream", content))

                # Run verification in worktree so it can see the changes
                verification_path = str(session.worktree_path or session.project_path or Path.cwd())
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

                # Poll message queue while verification runs and stream to live view
                verify_display_buffer = LiveStreamDisplayBuffer()
                verify_display_buffer.append("🔍 Starting verification...\n")
                verify_last_yield = 0.0
                verify_live_stream = ""
                while not verification_complete.is_set() and not session.cancel_requested:
                    try:
                        msg = message_queue.get(timeout=0.05)
                        if msg[0] == "verify_stream":
                            chunk = msg[1]
                            if chunk.strip():
                                verify_display_buffer.append(chunk)
                                verify_live_stream = build_live_stream_html(
                                    verify_display_buffer.content, "VERIFICATION AI", live_stream_id
                                )
                                if verify_live_stream:
                                    session.last_live_stream = verify_live_stream
                                now = time_module.time()
                                if now - verify_last_yield >= min_yield_interval:
                                    yield make_followup_yield(chat_history, verify_live_stream, working=True, merge_updates=merge_no_change)
                                    verify_last_yield = now
                    except queue.Empty:
                        pass

                # Final yield to ensure content is shown even if loop exited quickly
                if verify_live_stream:
                    yield make_followup_yield(chat_history, verify_live_stream, working=True, merge_updates=merge_no_change)

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
                    if session.event_log:
                        session.event_log.log(VerificationAttemptEvent(
                            attempt_number=verification_attempt,
                            passed=False,
                            summary="Verification error",
                        ))
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
                    if session.event_log:
                        session.event_log.log(VerificationAttemptEvent(
                            attempt_number=verification_attempt,
                            passed=True,
                            summary=verification_feedback[:500] if verification_feedback else "",
                        ))
                    self._update_session_log(
                        session, chat_history, full_history, verification_attempts=verification_log
                    )
                else:
                    chat_history.append(make_chat_message("VERIFICATION AI", verification_feedback))
                    if session.event_log:
                        session.event_log.log(VerificationAttemptEvent(
                            attempt_number=verification_attempt,
                            passed=False,
                            summary=verification_feedback[:500] if verification_feedback else "",
                        ))
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
                                "content": "───────────── → REVISION REQUESTED ─────────────",
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
                        # Show live stream placeholder during revision
                        revision_placeholder = build_live_stream_html("→ Revision in progress...", "CODING AI", live_stream_id)
                        # Reset live render flag so revision gets a full render, not a patch
                        session.has_initial_live_render = False
                        yield make_followup_yield(chat_history, revision_placeholder, working=True, merge_updates=merge_no_change)

                        revision_request = (
                            "The verification agent found issues with your work. "
                            "Please address them:\n\n"
                            f"{verification_feedback}\n\n"
                            "Please fix these issues and confirm when done."
                        )
                        if session.event_log:
                            session.event_log.log(UserMessageEvent(content="Revision requested"))
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
                            if session.event_log and parsed_revision:
                                session.event_log.log(AssistantMessageEvent(
                                    blocks=[{"kind": "text", "content": parsed_revision[:1000]}]
                                ))
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

                        reverify_placeholder = build_live_stream_html(
                            "✓ Revision complete, re-verifying...", "VERIFICATION AI", live_stream_id
                        )
                        yield make_followup_yield(
                            chat_history,
                            reverify_placeholder,
                            working=True,
                            merge_updates=merge_no_change,
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

        def build_merge_updates():
            if not session.project_path:
                return merge_no_change

            try:
                has_changes, merge_summary = self.check_worktree_changes(session_id)
                if not has_changes:
                    return (
                        gr.update(visible=False),  # merge_section_group
                        gr.update(value=""),  # changes_summary
                        gr.update(),  # merge_target_branch
                        gr.update(value=""),  # diff_content
                        gr.update(value=""),  # merge_section_header
                    )

                git_mgr = GitWorktreeManager(Path(session.project_path))
                branches = git_mgr.get_branches()
                parsed_diff = git_mgr.get_parsed_diff(self._worktree_id(session_id), session.worktree_base_commit)
                diff_html = self._render_diff_html(parsed_diff)
                header_text = "### Changes Ready to Merge"

                branch_choices = branches or ["main"]
                branch_value = branch_choices[0] if branch_choices else None

                return (
                    gr.update(visible=True),  # merge_section_group
                    gr.update(value=merge_summary),
                    gr.update(choices=branch_choices, value=branch_value),
                    gr.update(value=diff_html),
                    gr.update(value=header_text),
                )
            except Exception:
                return (
                    gr.update(visible=True),
                    gr.update(value=""),
                    gr.update(choices=["main"], value="main"),
                    gr.update(value=""),
                    gr.update(value=""),
                )

        yield make_followup_yield(chat_history, "", show_followup=True, merge_updates=build_merge_updates())

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
        worktree_id = self._worktree_id(session_id)
        has_changes = git_mgr.has_changes(worktree_id)
        session.has_worktree_changes = has_changes

        if has_changes:
            summary = git_mgr.get_diff_summary(worktree_id, session.worktree_base_commit)
            # Only show merge section if there's actually something to display
            if summary:
                return True, summary
        return False, ""

    def attempt_merge(
        self,
        session_id: str,
        commit_message: str = "",
        target_branch: str = "",
    ) -> tuple:
        """Attempt to merge worktree changes to a target branch.

        Returns 14 values for merge_outputs:
        [merge_section_group, changes_summary, conflict_section, conflict_info, conflicts_html,
         task_status, chatbot, start_btn, cancel_btn, live_stream, followup_row, task_description,
         merge_section_header, diff_content]
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.worktree_path or not session.project_path:
            return (
                gr.update(visible=False), no_change, gr.update(visible=False),
                no_change, no_change, gr.update(value="❌ No worktree to merge.", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                "", "",  # merge_section_header, diff_content
            )

        try:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            worktree_id = self._worktree_id(session_id)
            msg = commit_message.strip() if commit_message else None
            branch = target_branch.strip() if target_branch else None
            success, conflicts, error_msg = git_mgr.merge_to_main(worktree_id, msg, branch)

            target_name = branch or git_mgr.get_main_branch()
            if success:
                # Cleanup worktree after successful merge
                git_mgr.cleanup_after_merge(worktree_id)
                session.worktree_path = None
                session.worktree_branch = None
                session.has_worktree_changes = False
                session.worktree_base_commit = None
                session.task_description = ""
                # Preserve chat_history for follow-up conversations
                # Reset merge section but keep chatbot and followup visible
                return (
                    gr.update(visible=False),                    # merge_section_group
                    "",                                          # changes_summary
                    gr.update(visible=False),                    # conflict_section
                    "",                                          # conflict_info
                    "",                                          # conflicts_html
                    gr.update(value=f"✓ Changes merged to {target_name}.", visible=True),
                    no_change,                                   # chatbot - preserve for follow-up
                    gr.update(interactive=False),                # start_btn - never re-enable after task starts
                    gr.update(interactive=False),                # cancel_btn
                    "",                                          # live_stream
                    no_change,                                   # followup_row - preserve for follow-up
                    "",                                          # task_description
                    "",                                          # merge_section_header
                    "",                                          # diff_content
                )
            elif conflicts:
                session.merge_conflicts = conflicts
                conflict_count = sum(len(c.hunks) for c in (conflicts or []))
                file_count = len(conflicts or [])
                conflict_msg = f"**{file_count} file(s)** with **{conflict_count} conflict(s)** need resolution."
                return (
                    gr.update(visible=False),                    # merge_section_group
                    no_change,                                   # changes_summary
                    gr.update(visible=True),                     # conflict_section
                    gr.update(value=conflict_msg),               # conflict_info
                    gr.update(value=self._render_conflicts_html(conflicts or [])),
                    no_change,                                   # task_status
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    "", "",                                      # merge_section_header, diff_content
                )
            else:
                error_detail = error_msg or "Merge failed. Check git status and commit hooks."
                return (
                    gr.update(visible=True),                     # merge_section_group remains visible
                    no_change,                                   # changes_summary unchanged
                    gr.update(visible=False),                    # conflict_section hidden
                    gr.update(value=""),                         # conflict_info cleared
                    gr.update(value=""),                         # conflicts_html cleared
                    gr.update(value=f"❌ {error_detail}", visible=True),
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    no_change, no_change,                        # merge_section_header, diff_content
                )
        except Exception as e:
            return (
                no_change, no_change, no_change, no_change, no_change,
                gr.update(value=f"❌ Merge error: {e}", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change,                            # merge_section_header, diff_content
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
            "pre": "margin: 2px 0; white-space: pre-wrap; word-break: break-all; color: #e5e9f0;",
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

        Returns 14 values for merge_outputs.
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.project_path:
            return (
                no_change, no_change, gr.update(visible=False),
                no_change, no_change, gr.update(value="❌ No project path set.", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change,  # merge_section_header, diff_content
            )

        try:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            git_mgr.resolve_all_conflicts(use_incoming)

            # Complete the merge
            worktree_id = self._worktree_id(session_id)
            if git_mgr.complete_merge():
                git_mgr.cleanup_after_merge(worktree_id)
                session.worktree_path = None
                session.worktree_branch = None
                session.merge_conflicts = None
                session.has_worktree_changes = False
                session.worktree_base_commit = None
                session.task_description = ""
                # Preserve chat_history for follow-up conversations
                # Reset merge section but keep chatbot and followup visible
                return (
                    gr.update(visible=False),                    # merge_section_group
                    "",                                          # changes_summary
                    gr.update(visible=False),                    # conflict_section
                    "",                                          # conflict_info
                    "",                                          # conflicts_html
                    gr.update(value="✓ All conflicts resolved. Merge complete.", visible=True),
                    no_change,                                   # chatbot - preserve for follow-up
                    gr.update(interactive=False),                # start_btn - never re-enable after task starts
                    gr.update(interactive=False),                # cancel_btn
                    "",                                          # live_stream
                    no_change,                                   # followup_row - preserve for follow-up
                    "",                                          # task_description
                    "",                                          # merge_section_header
                    "",                                          # diff_content
                )
            else:
                return (
                    no_change, no_change, no_change, no_change, no_change,
                    gr.update(value="❌ Failed to complete merge. Check git status.", visible=True),
                    no_change, no_change, no_change, no_change, no_change, no_change,
                    no_change, no_change,  # merge_section_header, diff_content
                )
        except Exception as e:
            return (
                no_change, no_change, no_change, no_change, no_change,
                gr.update(value=f"❌ Error resolving conflicts: {e}", visible=True),
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change,  # merge_section_header, diff_content
            )

    def abort_merge_action(self, session_id: str) -> tuple:
        """Abort an in-progress merge, return to merge section.

        Returns 14 values for merge_outputs.
        """
        session = self.get_session(session_id)
        no_change = gr.update()
        if not session.project_path:
            return (
                no_change, no_change, gr.update(visible=False),
                no_change, no_change, no_change,
                no_change, no_change, no_change, no_change, no_change, no_change,
                no_change, no_change,  # merge_section_header, diff_content
            )

        git_mgr = GitWorktreeManager(Path(session.project_path))
        git_mgr.abort_merge()
        session.merge_conflicts = None

        # Check if worktree still has changes - show merge section if so
        has_changes, summary = self.check_worktree_changes(session_id)
        header_text = "### Changes Ready to Merge" if has_changes else ""

        return (
            gr.update(visible=has_changes),              # merge_section_group
            gr.update(value=summary if has_changes else ""),  # changes_summary
            gr.update(visible=False),                    # conflict_section
            no_change,                                   # conflict_info
            no_change,                                   # conflicts_html
            gr.update(value="⚠️ Merge aborted. Changes remain in worktree.", visible=True),
            no_change, no_change, no_change, no_change, no_change, no_change,  # no tab reset on abort
            header_text,                                 # merge_section_header
            no_change,                                   # diff_content
        )

    def discard_worktree_changes(self, session_id: str) -> tuple:
        """Discard worktree and all changes, reset merge UI but keep task description.

        Returns 14 values for merge_outputs. Task description and chat history are
        preserved so user can retry the task or continue the conversation.
        """
        session = self.get_session(session_id)
        if session.project_path:
            git_mgr = GitWorktreeManager(Path(session.project_path))
            worktree_id = self._worktree_id(session_id)
            if git_mgr.worktree_exists(worktree_id):
                git_mgr.reset_worktree(worktree_id, session.worktree_base_commit)
                session.worktree_path = git_mgr._worktree_path(worktree_id)
                session.worktree_branch = git_mgr._branch_name(worktree_id)
            else:
                worktree_path, base_commit = git_mgr.create_worktree(worktree_id)
                session.worktree_path = worktree_path
                session.worktree_branch = git_mgr._branch_name(worktree_id)
                session.worktree_base_commit = base_commit

            session.has_worktree_changes = False
            session.merge_conflicts = None
            # Preserve chat_history for follow-up conversations

        # Reset merge UI but keep task description and chat for follow-up
        return (
            gr.update(visible=False),                    # merge_section_group
            "",                                          # changes_summary
            gr.update(visible=False),                    # conflict_section
            "",                                          # conflict_info
            "",                                          # conflicts_html
            gr.update(value="🗑️ Changes discarded.", visible=True),  # task_status
            gr.update(),                                 # chatbot - preserve for follow-up
            gr.update(interactive=False),                # start_btn - never re-enable after task starts
            gr.update(interactive=False),                # cancel_btn
            "",                                          # live_stream
            gr.update(),                                 # followup_row - preserve for follow-up
            gr.update(value=session.task_description or "", interactive=False),  # task_description - locked after task starts
            "",                                          # merge_section_header
            "",                                          # diff_content
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

    def _try_auto_switch_provider(
        self,
        session: Session,
        error_message: str,
    ) -> tuple[bool, str | None]:
        """Attempt to auto-switch to a fallback provider on quota exhaustion.

        Args:
            session: The current session
            error_message: The error message from the failed provider

        Returns:
            Tuple of (switched: bool, new_account: str | None)
            switched is True if we successfully switched to a new provider
            new_account is the name of the new account, or None if no switch occurred
        """
        if not is_quota_exhaustion_error(error_message):
            return False, None

        current_account = session.coding_account
        if not current_account:
            return False, None

        # Get the next fallback provider
        try:
            next_account = self.api_client.get_next_fallback_provider(current_account)
        except Exception:
            # API error getting fallback order - skip auto-switch
            return False, None

        if not next_account:
            return False, None

        # Get the new account's provider type
        try:
            accounts = {acc.name: acc.provider for acc in self.api_client.list_accounts()}
            if next_account not in accounts:
                return False, None
            next_provider_type = accounts[next_account]
        except Exception:
            return False, None

        # Log handoff checkpoint before stopping old provider
        if session.event_log and session.provider:
            provider_session_id = None
            if hasattr(session.provider, "get_session_id"):
                provider_session_id = session.provider.get_session_id()

            log_handoff_checkpoint(
                session.event_log,
                session.task_description or "",
                provider_session_id,
                target_provider=next_provider_type,
            )

        # Track old provider info
        old_provider_type = ""
        old_model = ""
        if session.config:
            old_provider_type = getattr(session.config, "provider", "")
            old_model = getattr(session.config, "model_name", "") or ""

        # Stop old provider
        if session.provider:
            try:
                session.provider.stop_session()
            except Exception:
                pass
            session.provider = None
            session.active = False

        # Get new account info for model/reasoning
        try:
            next_acc = self.api_client.get_account(next_account)
            next_model = next_acc.model or "default"
            next_reasoning = next_acc.reasoning or "default"
        except Exception:
            next_model = "default"
            next_reasoning = "default"

        # Create new provider
        new_config = ModelConfig(
            provider=next_provider_type,
            model_name=next_model,
            account_name=next_account,
            reasoning_effort=None if next_reasoning == "default" else next_reasoning,
        )

        new_provider = create_provider(new_config)

        # Use worktree path if available
        if session.worktree_path:
            working_dir = str(session.worktree_path)
        else:
            working_dir = session.project_path or "."

        if not new_provider.start_session(working_dir, None):
            return False, None

        # Track the switch
        session.switched_from = current_account
        session.provider = new_provider
        session.coding_account = next_account
        session.active = True
        session.config = new_config

        # Log provider switched event
        reason = get_quota_error_reason(error_message) or "quota_exhaustion"
        if session.event_log:
            session.event_log.log(ProviderSwitchedEvent(
                from_provider=old_provider_type,
                to_provider=next_provider_type,
                from_model=old_model,
                to_model=next_model,
                reason=f"auto_switch_{reason}",
            ))

        return True, next_account

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
        # EventLog handles session logging automatically via events
        pass

    def _create_session_ui(self, session_id: str, is_first: bool = False):
        """Create UI components for a single session within @gr.render.

        Args:
            session_id: The session ID to create UI for
            is_first: Whether this is the first session (adds elem_ids for tests)
        """
        session = self.get_session(session_id)
        default_path = os.environ.get("CHAD_PROJECT_PATH", str(Path.cwd()))

        # Initialize session project_path if not set
        if not session.project_path:
            session.project_path = default_path

        # Use prefetched data during init, fall back to API calls at runtime
        init = getattr(self, "_init_data", None)
        accounts = init["accounts"] if init else self.api_client.list_accounts()
        accounts_map = {acc.name: acc for acc in accounts}
        account_choices = list(accounts_map.keys())

        # Find account with CODING role
        initial_coding = ""
        for acc in accounts:
            if acc.role == "CODING":
                initial_coding = acc.name
                break

        # Auto-select first provider if no coding agent is assigned
        if (not initial_coding or initial_coding not in account_choices) and account_choices:
            initial_coding = account_choices[0]
            # Persist the auto-selection
            try:
                self.api_client.set_account_role(initial_coding, "CODING")
            except Exception:
                pass

        # During init we can derive is_ready from cached accounts
        if init:
            is_ready = bool(initial_coding)
        else:
            is_ready, _ = self.get_role_config_status(project_path=session.project_path)

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
        stored_verification = init["verification_agent"] if init else self.api_client.get_verification_agent()
        if stored_verification == self.VERIFICATION_NONE:
            initial_verification = self.VERIFICATION_NONE
        elif stored_verification in account_choices:
            initial_verification = stored_verification
        else:
            initial_verification = self.SAME_AS_CODING

        # Get initial model/reasoning choices for coding agent
        coding_model_choices = self.get_models_for_account(initial_coding) if initial_coding else ["default"]
        if not coding_model_choices:
            coding_model_choices = ["default"]
        coding_acc = accounts_map.get(initial_coding)
        stored_coding_model = coding_acc.model if coding_acc else "default"
        coding_model_value = (
            stored_coding_model if stored_coding_model in coding_model_choices else coding_model_choices[0]
        )

        coding_provider_type = coding_acc.provider if coding_acc else ""
        coding_reasoning_choices = (
            self.get_reasoning_choices(coding_provider_type, initial_coding) if coding_provider_type else ["default"]
        )
        if not coding_reasoning_choices:
            coding_reasoning_choices = ["default"]
        stored_coding_reasoning = coding_acc.reasoning if coding_acc else "default"
        coding_reasoning_value = (
            stored_coding_reasoning
            if stored_coding_reasoning in coding_reasoning_choices
            else coding_reasoning_choices[0]
        )

        # Load preferred verification model from config
        stored_verification_model = (
            init["preferred_verification_model"] if init else self.api_client.get_preferred_verification_model()
        )

        verif_state = self._build_verification_dropdown_state(
            initial_coding,
            initial_verification,
            coding_model_value,
            coding_reasoning_value,
            current_verification_model=stored_verification_model,
            accounts=accounts,
        )

        with gr.Row(
            elem_id="run-top-row" if is_first else None,
            elem_classes=["run-top-row"],
            equal_height=True,
        ):
            with gr.Column(scale=1):
                with gr.Row(equal_height=True):
                    with gr.Column(scale=3, min_width=260):
                        # Auto-detect initial verification commands for default path (cached)
                        _project_resolved = Path(default_path).expanduser().resolve()
                        if not hasattr(self, "_detected_commands_cache"):
                            self._detected_commands_cache = {}
                        _cache_key = str(_project_resolved)
                        if _cache_key not in self._detected_commands_cache:
                            self._detected_commands_cache[_cache_key] = (
                                detect_verification_commands(_project_resolved),
                                detect_doc_paths(_project_resolved),
                            )
                        initial_detected, initial_docs = self._detected_commands_cache[_cache_key]
                        initial_lint = initial_detected.get("lint_command") or ""
                        initial_test = initial_detected.get("test_command") or ""
                        initial_type = initial_detected.get("project_type", "unknown")
                        initial_instructions = initial_docs.instructions_path or ""
                        initial_architecture = initial_docs.architecture_path or ""

                        project_path = gr.Textbox(
                            label=self._format_project_label(initial_type),
                            placeholder="/path/to/project",
                            value=default_path,
                            scale=3,
                            key=f"project-path-{session_id}",
                            elem_id="project-path-input" if is_first else None,
                            elem_classes=["project-path-input"],
                        )
                        with gr.Row(elem_classes=["project-commands-row"], equal_height=True):
                            with gr.Column(scale=1, elem_classes=["command-column"]):
                                with gr.Row(elem_classes=["command-header", "lint-command-label"], equal_height=True):
                                    gr.Markdown(
                                        "**Lint Command**",
                                        elem_classes=["command-label"],
                                    )
                                    lint_test_btn = gr.Button(
                                        "Test",
                                        variant="secondary",
                                        size="sm",
                                        key=f"lint-test-{session_id}",
                                        elem_classes=["command-test-btn", "lint-test-btn"],
                                    )
                                lint_cmd_input = gr.Textbox(
                                    label="Lint Command",
                                    value=initial_lint,
                                    placeholder=".venv/bin/python -m flake8 .",
                                    key=f"lint-cmd-{session_id}",
                                    show_label=False,
                                    elem_classes=["command-input", "lint-command-input"],
                                )
                                lint_status = gr.Markdown(
                                    "",
                                    key=f"lint-status-{session_id}",
                                    elem_classes=["command-status", "lint-command-status"],
                                )
                            with gr.Column(scale=1, elem_classes=["command-column"]):
                                with gr.Row(elem_classes=["command-header", "test-command-label"], equal_height=True):
                                    gr.Markdown(
                                        "**Test Command**",
                                        elem_classes=["command-label"],
                                    )
                                    test_test_btn = gr.Button(
                                        "Test",
                                        variant="secondary",
                                        size="sm",
                                        key=f"test-test-{session_id}",
                                        elem_classes=["command-test-btn", "test-command-btn"],
                                    )
                                test_cmd_input = gr.Textbox(
                                    label="Test Command",
                                    value=initial_test,
                                    placeholder=".venv/bin/python -m pytest tests/ -v",
                                    key=f"test-cmd-{session_id}",
                                    show_label=False,
                                    elem_classes=["command-input", "test-command-input"],
                                )
                                test_status = gr.Markdown(
                                    "",
                                    key=f"test-status-{session_id}",
                                    elem_classes=["command-status", "test-command-status"],
                                )
                        with gr.Row(elem_classes=["doc-paths-row"], equal_height=True):
                            instructions_input = gr.Textbox(
                                label="Agent Instructions Path",
                                value=initial_instructions,
                                placeholder="AGENTS.md",
                                key=f"instructions-path-{session_id}",
                                elem_classes=["doc-path-input", "instructions-path-input"],
                            )
                            architecture_input = gr.Textbox(
                                label="Architecture Doc Path",
                                value=initial_architecture,
                                placeholder="docs/ARCHITECTURE.md",
                                key=f"architecture-path-{session_id}",
                                elem_classes=["doc-path-input", "architecture-path-input"],
                            )
                    with gr.Column(scale=1, min_width=200, elem_classes=["agent-config"]):
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
                            label="Model",
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
                    with gr.Column(scale=1, min_width=200, elem_classes=["verification-column", "agent-config"]):
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
                            label="Verification Model",
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
        # Action buttons: compact row beneath the agent selector columns, right-aligned
        with gr.Row(variant="compact", equal_height=True):
            # Empty column to push buttons to the right
            with gr.Column(scale=1, min_width=0):
                gr.HTML("")
            cancel_btn = gr.Button(
                "Cancel",
                variant="stop",
                interactive=False,
                key=f"cancel-btn-{session_id}",
                elem_id="cancel-task-btn" if is_first else None,
                elem_classes=["cancel-task-btn"],
                min_width=80,
                scale=0,
            )
            project_save_btn = gr.Button(
                "Save",
                variant="primary",
                size="sm",
                key=f"project-save-{session_id}",
                elem_classes=["project-save-btn"],
                min_width=80,
                scale=0,
            )
            log_path = session.log_path
            session_log_btn = gr.DownloadButton(
                label="Session Log" if not log_path else log_path.name,
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
            workspace_display = gr.HTML(
                self._workspace_html(session),
                key=f"workspace-display-{session_id}",
                elem_id="workspace-display" if is_first else None,
                elem_classes=["workspace-display"],
            )
        # Role status directly below action buttons
        wt_path = str(session.worktree_path) if session.worktree_path else None
        proj_path = session.project_path
        role_status = gr.Markdown(
            self.format_role_status(worktree_path=wt_path, project_path=proj_path, accounts=accounts),
            key=f"role-status-{session_id}",
            elem_id="role-config-status" if is_first else None,
            elem_classes=["role-config-status"],
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
                with gr.Row(elem_classes=["task-input-row"], equal_height=False):
                    task_description = gr.MultimodalTextbox(
                        label="Task Description",
                        placeholder="Describe what you want done... (drag screenshots here)",
                        lines=3,
                        scale=4,
                        file_types=["image"],
                        file_count="multiple",
                        sources=["upload"],
                        key=f"task-desc-{session_id}",
                        elem_classes=["task-desc-input"],
                    )
                    start_btn = gr.Button(
                        "▶ Start Task",
                        variant="primary",
                        interactive=is_ready,
                        key=f"start-btn-{session_id}",
                        elem_id="start-task-btn" if is_first else None,
                        elem_classes=["start-task-btn"],
                        scale=1,
                        min_width=120,
                    )

            # Live stream kept in DOM (visible=True) but hidden via CSS for visual tests
            # Using gr.HTML instead of gr.Markdown for DOM patching support
            # which preserves scroll position and text selection during updates
            live_stream = gr.HTML(
                "",
                visible=True,
                elem_id="live-stream-box" if is_first else None,
                elem_classes=["live-stream-box"],
            )

            chatbot = gr.Chatbot(
                label="Milestones",
                height=400,
                key=f"chatbot-{session_id}",
                elem_id="agent-chatbot" if is_first else None,
                elem_classes=["agent-chatbot"],  # CSS targets this class
                sanitize_html=False,  # Required for inline screenshots - content is internally generated
                type="messages",  # Use OpenAI-style dicts with 'role' and 'content' keys
            )

            # Prompt display accordions - show all three prompts from the start
            # Initial display shows raw templates with placeholders like {task}
            # These get updated with actual values when the task runs
            with gr.Accordion(
                "Exploration Prompt",
                open=False,
                visible=True,
                key=f"exploration-prompt-accordion-{session_id}",
                elem_classes=["prompt-accordion"],
            ) as exploration_prompt_accordion:
                exploration_prompt_display = gr.Markdown(
                    EXPLORATION_PROMPT,
                    key=f"exploration-prompt-display-{session_id}",
                    elem_classes=["prompt-display"],
                )

            with gr.Accordion(
                "Implementation Prompt",
                open=False,
                visible=True,
                key=f"implementation-prompt-accordion-{session_id}",
                elem_classes=["prompt-accordion"],
            ) as implementation_prompt_accordion:
                implementation_prompt_display = gr.Markdown(
                    IMPLEMENTATION_PROMPT,
                    key=f"implementation-prompt-display-{session_id}",
                    elem_classes=["prompt-display"],
                )

            with gr.Accordion(
                "Verification Prompt",
                open=False,
                visible=True,
                key=f"verification-prompt-accordion-{session_id}",
                elem_classes=["prompt-accordion"],
            ) as verification_prompt_accordion:
                verification_prompt_display = gr.Markdown(
                    VERIFICATION_EXPLORATION_PROMPT,
                    key=f"verification-prompt-display-{session_id}",
                    elem_classes=["prompt-display"],
                )

        # Hidden state for dynamic terminal dimensions (calculated from container width)
        # JavaScript updates this when the live-stream-box is resized
        terminal_cols_state = gr.Number(
            value=TERMINAL_COLS,  # Default to constant
            visible=False,
            elem_id="terminal-cols-state" if is_first else None,
            elem_classes=["terminal-cols-state"],
        )

        # Hidden HTML element for JS live content patching
        # When this element's content changes, JS reads the data-live-patch attribute
        # and patches the corresponding container's innerHTML in-place
        live_patch_trigger = gr.HTML(
            "",
            visible=True,  # Keep rendered so MutationObserver can watch for patches
            key=f"live-patch-{session_id}",
            elem_classes=["live-patch-trigger"],
        )
        # Track trigger for cross-tab rehydration
        self._session_live_patches[session_id] = live_patch_trigger
        # Track live stream component for direct tab-switch restoration
        self._session_live_streams[session_id] = live_stream

        with gr.Row(visible=False, key=f"followup-row-{session_id}") as followup_row:
            followup_input = gr.MultimodalTextbox(
                label="Continue conversation...",
                placeholder="Ask for changes or additional work... (drag screenshots here)",
                lines=2,
                scale=5,
                file_types=["image"],
                file_count="multiple",
                sources=["upload"],
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
        # Use visible=True so element is rendered to DOM, hide via CSS initially
        # Gradio 6 doesn't render components with visible=False
        with gr.Column(visible=True, render=True, key=f"merge-section-{session_id}",
                       elem_classes=["merge-section", "merge-section-hidden"]) as merge_section_group:
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

        # Project setup handlers
        def on_project_path_change(path_val):
            """Auto-detect project type and commands when path changes."""
            if not path_val:
                return (
                    gr.update(label=self._format_project_label("enter path")),
                    gr.update(value=""),
                    gr.update(value=""),
                    gr.update(value=""),
                    gr.update(value=""),
                    "",
                    "",
                )
            path_obj = Path(path_val).expanduser().resolve()
            if not path_obj.exists():
                return (
                    gr.update(label=self._format_project_label("not found")),
                    gr.update(value=""),
                    gr.update(value=""),
                    gr.update(value=""),
                    gr.update(value=""),
                    "",
                    "",
                )

            # Try loading existing config first
            config = load_project_config(path_obj)
            if config:
                docs = config.docs or detect_doc_paths(path_obj)
                return (
                    gr.update(label=self._format_project_label(f"{config.project_type} (saved)")),
                    gr.update(value=config.verification.lint_command or ""),
                    gr.update(value=config.verification.test_command or ""),
                    gr.update(value=(docs.instructions_path or "")),
                    gr.update(value=(docs.architecture_path or "")),
                    "",
                    "",
                )

            # Auto-detect
            detected = detect_verification_commands(path_obj)
            detected_docs = detect_doc_paths(path_obj)
            return (
                gr.update(label=self._format_project_label(detected["project_type"])),
                gr.update(value=detected.get("lint_command") or ""),
                gr.update(value=detected.get("test_command") or ""),
                gr.update(value=detected_docs.instructions_path or ""),
                gr.update(value=detected_docs.architecture_path or ""),
                "",
                "",
            )

        project_path.change(
            on_project_path_change,
            inputs=[project_path],
            outputs=[
                project_path,
                lint_cmd_input,
                test_cmd_input,
                instructions_input,
                architecture_input,
                lint_status,
                test_status,
            ],
        )

        def on_lint_test(path_val, lint_cmd):
            if not path_val:
                return "Enter a project path first"
            if not lint_cmd:
                return "Enter a lint command"
            path_obj = Path(path_val).expanduser().resolve()
            success, output = validate_command(lint_cmd, path_obj, timeout=30)
            return "Passed" if success else f"Failed: {output[:100]}"

        lint_test_btn.click(
            on_lint_test,
            inputs=[project_path, lint_cmd_input],
            outputs=[lint_status],
        )

        def on_test_test(path_val, test_cmd):
            if not path_val:
                return "Enter a project path first"
            if not test_cmd:
                return "Enter a test command"
            path_obj = Path(path_val).expanduser().resolve()
            success, output = validate_command(test_cmd, path_obj, timeout=60)
            return "Passed" if success else f"Failed: {output[:100]}"

        test_test_btn.click(
            on_test_test,
            inputs=[project_path, test_cmd_input],
            outputs=[test_status],
        )

        def on_project_save(path_val, lint_cmd, test_cmd, instructions_path_val, architecture_path_val):
            if not path_val:
                return "Enter a project path"
            path_obj = Path(path_val).expanduser().resolve()
            if not path_obj.exists():
                return "Path not found"

            saved = save_project_settings(
                path_obj,
                lint_command=lint_cmd or None,
                test_command=test_cmd or None,
                instructions_path=instructions_path_val or None,
                architecture_path=architecture_path_val or None,
            )
            return f"Project settings saved (type: {saved.project_type})"

        project_save_btn.click(
            on_project_save,
            inputs=[project_path, lint_cmd_input, test_cmd_input, instructions_input, architecture_input],
            outputs=[role_status],
        )

        def start_task_wrapper(
            proj_path,
            task_input,
            coding,
            verification,
            c_model,
            c_reason,
            v_model,
            v_reason,
            term_cols,
        ):
            # Extract text and file paths from MultimodalTextbox
            task_desc = ""
            screenshot_paths = None
            if task_input:
                if isinstance(task_input, dict):
                    task_desc = task_input.get("text", "")
                    files = task_input.get("files", [])
                    if files:
                        screenshot_paths = [f if isinstance(f, str) else f.get("path", "") for f in files]
                else:
                    task_desc = str(task_input)
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
                terminal_cols=int(term_cols) if term_cols else None,
                screenshots=screenshot_paths,
            )

        def cancel_wrapper():
            return self.cancel_task(session_id)

        def followup_wrapper(followup_input, history, coding, verification, c_model, c_reason, v_model, v_reason):
            # Extract text and file paths from MultimodalTextbox
            followup_msg = ""
            screenshot_paths = None
            if followup_input:
                if isinstance(followup_input, dict):
                    followup_msg = followup_input.get("text", "")
                    files = followup_input.get("files", [])
                    if files:
                        screenshot_paths = [f if isinstance(f, str) else f.get("path", "") for f in files]
                else:
                    followup_msg = str(followup_input)
            yield from self.send_followup(
                session_id,
                followup_msg,
                history,
                coding,
                verification,
                c_model,
                c_reason,
                v_model,
                v_reason,
                screenshots=screenshot_paths,
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
                terminal_cols_state,
            ],
            outputs=[
                live_stream,
                chatbot,
                task_status,
                project_path,
                task_description,
                start_btn,
                cancel_btn,
                role_status,
                session_log_btn,
                workspace_display,
                followup_input,
                followup_row,
                send_followup_btn,
                merge_section_group,
                changes_summary,
                merge_target_branch,
                diff_content,
                merge_section_header,
                live_patch_trigger,
                exploration_prompt_accordion,
                exploration_prompt_display,
                implementation_prompt_accordion,
                implementation_prompt_display,
                verification_prompt_accordion,
                verification_prompt_display,
            ],
        )

        cancel_btn.click(
            cancel_wrapper,
            outputs=[
                live_stream,
                chatbot,
                task_status,
                project_path,
                task_description,
                start_btn,
                cancel_btn,
                followup_row,
                merge_section_group,
            ],
        )

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
                live_stream,
                chatbot,
                followup_input,
                followup_row,
                send_followup_btn,
                live_patch_trigger,
                merge_section_group,
                changes_summary,
                merge_target_branch,
                diff_content,
                merge_section_header,
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
        merge_outputs = [
            merge_section_group,    # 0: Hide merge section group
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
            merge_section_header,   # 12: Clear header when hiding
            diff_content,           # 13: Clear diff view inside accordion
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
            wt_path = str(session.worktree_path) if session.worktree_path else None
            proj_path = session.project_path
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
                    gr.update(value=self.format_role_status(worktree_path=wt_path, project_path=proj_path)),
                    gr.update(interactive=False),
                    gr.update(choices=["default"], value="default", interactive=False),
                    gr.update(choices=["default"], value="default", interactive=False),
                    verif_model_update,
                    verif_reasoning_update,
                )

            # Assign the coding role
            try:
                self.api_client.set_account_role(selected_account, "CODING")
            except Exception:
                pass

            # Get updated status
            is_ready, _ = self.get_role_config_status(worktree_path=wt_path, project_path=proj_path)
            status_text = self.format_role_status(worktree_path=wt_path, project_path=proj_path)

            # Get model choices for the selected account
            model_choices = self.get_models_for_account(selected_account)
            if not model_choices:
                model_choices = ["default"]
            try:
                acc = self.api_client.get_account(selected_account)
                stored_model = acc.model or "default"
            except Exception:
                stored_model = "default"
            model_value = stored_model if stored_model in model_choices else model_choices[0]

            # Get reasoning choices
            provider_type = acc.provider if acc else ""
            reasoning_choices = self.get_reasoning_choices(provider_type, selected_account)
            if not reasoning_choices:
                reasoning_choices = ["default"]
            stored_reasoning = acc.reasoning if acc else "default"
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
        """Create the Setup tab UI within @gr.render."""
        init = getattr(self, "_init_data", None)
        account_items = self.provider_ui.get_provider_card_items(
            accounts=init["accounts"] if init else None
        )
        self.provider_card_count = max(12, len(account_items) + 8)

        provider_feedback = gr.Markdown("")
        gr.Markdown("### Setup", elem_classes=["provider-section-title"])

        refresh_btn = gr.Button("→ Refresh", variant="secondary")
        pending_delete_state = gr.State(None)

        provider_cards = []
        with gr.Row(equal_height=True, elem_classes=["provider-cards-row"]):
            for idx in range(self.provider_card_count):
                if idx < len(account_items):
                    account_name, provider_type = account_items[idx]
                    visible = True
                    header_text = self.provider_ui.format_provider_header(account_name, provider_type, idx)
                    usage_text = self.get_provider_usage(account_name)
                    is_mock = provider_type == "mock"
                    mock_usage_value = self.provider_ui.get_mock_remaining_usage(account_name) if is_mock else 0.5
                else:
                    account_name = ""
                    provider_type = ""
                    visible = False
                    header_text = ""
                    usage_text = ""
                    is_mock = False
                    mock_usage_value = 0.5

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
                        usage_box = gr.Markdown(usage_text, elem_classes=["provider-usage"], visible=not is_mock)
                        mock_usage_slider = gr.Slider(
                            minimum=0,
                            maximum=100,
                            value=int(mock_usage_value * 100),
                            step=5,
                            label="Remaining %",
                            visible=is_mock,
                            elem_classes=["mock-usage-slider"],
                        )

                provider_cards.append(
                    {
                        "column": card_column,
                        "group": card_group,
                        "header": card_header,
                        "account_state": account_state,
                        "account_name": account_name,
                        "usage_box": usage_box,
                        "mock_usage_slider": mock_usage_slider,
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
                choices=self.get_provider_choices(),
                label="Provider Type",
                value="anthropic",
            )
            add_btn = gr.Button("Add Provider", variant="primary", interactive=False)

        with gr.Accordion(
            "Config",
            open=False,
            elem_id="config-panel",
            elem_classes=["config-accordion"],
        ):
            config_status = gr.Markdown("", elem_classes=["config-panel__status"])
            # Load settings from prefetched data or API
            try:
                preferences = init["preferences"] if init else self.api_client.get_preferences()
                prefs_dict = {"project_path": preferences.last_project_path} if preferences else {}
                ui_mode = preferences.ui_mode if preferences else "gradio"
            except Exception:
                prefs_dict = {}
                ui_mode = "gradio"

            try:
                cleanup_settings = init["cleanup_settings"] if init else self.api_client.get_cleanup_settings()
                retention_days = cleanup_settings.retention_days
            except Exception:
                retention_days = 7

            accounts = init["accounts"] if init else self.api_client.list_accounts()
            account_choices = [acc.name for acc in accounts]
            coding_assignment = ""
            for acc in accounts:
                if acc.role == "CODING":
                    coding_assignment = acc.name
                    break
            coding_value = coding_assignment if coding_assignment in account_choices else None

            stored_verification = init["verification_agent"] if init else self.api_client.get_verification_agent()
            if stored_verification == self.VERIFICATION_NONE:
                verification_value = self.VERIFICATION_NONE
            elif stored_verification and stored_verification in account_choices:
                verification_value = stored_verification
            else:
                verification_value = self.SAME_AS_CODING
            verification_choices = [
                (self.SAME_AS_CODING, self.SAME_AS_CODING),
                (self.VERIFICATION_NONE_LABEL, self.VERIFICATION_NONE),
                *[(name, name) for name in account_choices],
            ]

            def coding_model_state(selected_agent: str | None) -> tuple[list[str], str, bool]:
                if not selected_agent:
                    return (["default"], "default", False)

                model_choices = self.get_models_for_account(selected_agent) or ["default"]
                try:
                    acc = self.api_client.get_account(selected_agent)
                    stored_model = (acc.model if acc else None) or "default"
                except Exception:
                    stored_model = "default"
                if stored_model not in model_choices:
                    model_choices = [*model_choices, stored_model]
                model_value = stored_model if stored_model else model_choices[0]
                return (model_choices, model_value, True)

            def verification_model_state(selected_agent: str | None) -> tuple[list[str], str, bool, bool]:
                if not selected_agent or selected_agent == self.SAME_AS_CODING or selected_agent == self.VERIFICATION_NONE:
                    return (["default"], "default", False, False)
                model_choices = self.get_models_for_account(selected_agent) or ["default"]
                # Check preferred verification model from config first, then fall back to account model
                preferred_model = self.api_client.get_preferred_verification_model()
                try:
                    acc = self.api_client.get_account(selected_agent)
                    account_model = (acc.model if acc else None) or "default"
                except Exception:
                    account_model = "default"
                stored_model = preferred_model or account_model
                if stored_model not in model_choices:
                    model_choices = [*model_choices, stored_model]
                model_value = stored_model if stored_model else model_choices[0]
                return (model_choices, model_value, True, True)

            coding_model_choices, coding_model_value, coding_model_interactive = coding_model_state(coding_value)
            (
                verification_model_choices,
                verification_model_value,
                verification_model_visible,
                verification_model_interactive,
            ) = verification_model_state(verification_value)

            gr.Markdown(
                "Manage global settings that live in `.chad.conf`. Changes save immediately.",
                elem_classes=["config-panel__intro"],
            )
            with gr.Row():
                coding_pref = gr.Dropdown(
                    label="Preferred Coding Agent",
                    choices=account_choices,
                    value=coding_value,
                    allow_custom_value=False,
                )
                coding_model_pref = gr.Dropdown(
                    label="Preferred Coding Model",
                    choices=coding_model_choices,
                    value=coding_model_value,
                    allow_custom_value=True,
                    interactive=coding_model_interactive,
                )
                verification_pref = gr.Dropdown(
                    label="Preferred Verification Agent",
                    choices=verification_choices,
                    value=verification_value,
                    allow_custom_value=False,
                )
                # Store reference for load-time refresh
                self._config_verification_pref = verification_pref
                self._config_verification_choices_fn = lambda: verification_choices
                verification_model_pref = gr.Dropdown(
                    label="Preferred Verification Model",
                    choices=verification_model_choices,
                    value=verification_model_value,
                    allow_custom_value=True,
                    visible=verification_model_visible,
                    interactive=verification_model_interactive,
                )
            with gr.Row():
                retention_input = gr.Number(
                    label="Retention Days",
                    value=retention_days,
                    minimum=1,
                    precision=0,
                    step=1,
                )
                project_path_pref = gr.Textbox(
                    label="Default Project Path",
                    placeholder="/path/to/project",
                    value=prefs_dict.get("project_path", ""),
                )
                ui_mode_pref = gr.Dropdown(
                    label="UI Mode",
                    choices=["gradio", "cli"],
                    value=ui_mode,
                    allow_custom_value=False,
                    info="Gradio (web) or CLI (terminal) - applies on next launch",
                )

            # Auto-switch settings
            gr.Markdown("### Auto-Switch Settings")
            gr.Markdown(
                "Configure automatic provider switching when quota is exhausted. "
                "Order determines fallback priority."
            )

            # Load current fallback order and threshold
            try:
                fallback_order = init["fallback_order"] if init else self.api_client.get_provider_fallback_order()
                fallback_order_str = ", ".join(fallback_order)
            except Exception:
                fallback_order_str = ""

            usage_threshold = init["usage_threshold"] if init else self.api_client.get_usage_switch_threshold()
            context_threshold = init["context_threshold"] if init else self.api_client.get_context_switch_threshold()

            with gr.Row():
                fallback_order_input = gr.Textbox(
                    label="Provider Fallback Order",
                    placeholder="e.g., work-claude, backup-gpt, gemini-free",
                    value=fallback_order_str,
                    info="Comma-separated account names in priority order (press Enter to save)",
                )
            with gr.Row():
                usage_threshold_input = gr.Slider(
                    label="Usage Switch Threshold (%)",
                    minimum=0,
                    maximum=100,
                    step=5,
                    value=usage_threshold,
                    info="Switch provider when usage exceeds this percentage (100 = disable)",
                )
                context_threshold_input = gr.Slider(
                    label="Context Switch Threshold (%)",
                    minimum=0,
                    maximum=100,
                    step=5,
                    value=context_threshold,
                    info="Switch provider when context exceeds this percentage (100 = disable)",
                )

            # Verification settings
            gr.Markdown("### Verification Settings")
            max_attempts = init["max_verification_attempts"] if init else self.api_client.get_max_verification_attempts()

            with gr.Row():
                max_verification_attempts_input = gr.Number(
                    label="Max Verification Attempts",
                    minimum=1,
                    maximum=20,
                    step=1,
                    value=max_attempts,
                    info="Maximum verification attempts before giving up (1-20)",
                    precision=0,
                )

        provider_outputs = [provider_feedback]
        for card in provider_cards:
            provider_outputs.extend(
                [
                    card["column"],
                    card["group"],
                    card["header"],
                    card["account_state"],
                    card["usage_box"],
                    card["mock_usage_slider"],
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

        def on_retention_change(days):
            try:
                self.api_client.set_cleanup_settings(retention_days=int(days))
                return "✅ Retention days saved"
            except Exception as exc:
                return f"❌ {exc}"

        retention_input.change(on_retention_change, inputs=[retention_input], outputs=[config_status])

        def on_coding_pref_change(account_name):
            if not account_name:
                try:
                    # Find the current coding account and clear its role
                    for acc in self.api_client.list_accounts():
                        if acc.role == "CODING":
                            self.api_client.set_account_role(acc.name, "")
                            break
                    status_msg = "🧹 Cleared preferred coding agent"
                except Exception as exc:
                    status_msg = f"❌ {exc}"
                dropdown_update = gr.update(choices=["default"], value="default", interactive=False)
                return status_msg, dropdown_update
            try:
                self.api_client.set_account_role(account_name, "CODING")
                status_msg = f"✅ Preferred coding agent saved: {account_name}"
            except Exception as exc:
                status_msg = f"❌ {exc}"

            model_choices, model_value, interactive = coding_model_state(account_name)
            dropdown_update = gr.update(choices=model_choices, value=model_value, interactive=interactive)
            return status_msg, dropdown_update

        coding_pref.change(on_coding_pref_change, inputs=[coding_pref], outputs=[config_status, coding_model_pref])

        def on_coding_model_change(model_name, account_name):
            if not account_name:
                return "❌ Select a coding agent before setting a model"
            try:
                self.api_client.set_account_model(account_name, model_name)
                return f"✅ Preferred coding model saved for {account_name}"
            except Exception as exc:
                return f"❌ {exc}"

        coding_model_pref.change(
            on_coding_model_change,
            inputs=[coding_model_pref, coding_pref],
            outputs=[config_status],
        )

        def on_verification_pref_change(account_name):
            if not account_name or account_name == self.SAME_AS_CODING:
                try:
                    self.api_client.set_verification_agent(None)
                    status_msg = "✅ Verification agent set to same as coding"
                except Exception as exc:
                    status_msg = f"❌ {exc}"
            elif account_name == self.VERIFICATION_NONE:
                try:
                    self.api_client.set_verification_agent(self.VERIFICATION_NONE)
                    status_msg = "✅ Verification disabled"
                except Exception as exc:
                    status_msg = f"❌ {exc}"
            else:
                try:
                    self.api_client.set_verification_agent(account_name)
                    status_msg = f"✅ Verification agent saved: {account_name}"
                except Exception as exc:
                    status_msg = f"❌ {exc}"

            model_choices, model_value, visible, interactive = verification_model_state(account_name)
            dropdown_update = gr.update(
                choices=model_choices,
                value=model_value,
                visible=visible,
                interactive=interactive,
            )
            return status_msg, dropdown_update

        verification_pref.change(
            on_verification_pref_change,
            inputs=[verification_pref],
            outputs=[config_status, verification_model_pref],
        )

        def on_verification_model_change(model_name, account_name):
            if not account_name or account_name == self.SAME_AS_CODING or account_name == self.VERIFICATION_NONE:
                return "❌ Select a verification agent before setting a model"
            try:
                self.api_client.set_account_model(account_name, model_name)
                # Also persist to global preferred verification model config
                self.api_client.set_preferred_verification_model(model_name)
                return f"✅ Preferred verification model saved for {account_name}"
            except Exception as exc:
                return f"❌ {exc}"

        verification_model_pref.change(
            on_verification_model_change,
            inputs=[verification_model_pref, verification_pref],
            outputs=[config_status],
        )

        def on_project_path_change(path):
            try:
                self.api_client.set_preferences(last_project_path=path.strip())
                return "✅ Default project path saved"
            except Exception as exc:
                return f"❌ {exc}"

        project_path_pref.input(on_project_path_change, inputs=[project_path_pref], outputs=[config_status])

        def on_ui_mode_change(mode):
            try:
                self.api_client.set_preferences(ui_mode=mode)
                return f"✅ UI mode set to {mode} (applies on next launch)"
            except Exception as exc:
                return f"❌ {exc}"

        ui_mode_pref.change(on_ui_mode_change, inputs=[ui_mode_pref], outputs=[config_status])

        def on_fallback_order_change(order_str):
            try:
                # Parse comma-separated names
                names = [n.strip() for n in order_str.split(",") if n.strip()]
                self.api_client.set_provider_fallback_order(names)
                if names:
                    return f"✅ Fallback order set: {' → '.join(names)}"
                return "✅ Fallback order cleared (auto-switch disabled)"
            except Exception as exc:
                return f"❌ {exc}"

        fallback_order_input.submit(
            on_fallback_order_change,
            inputs=[fallback_order_input],
            outputs=[config_status],
        )

        def on_usage_threshold_change(threshold):
            try:
                self.api_client.set_usage_switch_threshold(int(threshold))
                if threshold >= 100:
                    return "✅ Usage-based switching disabled"
                return f"✅ Will switch providers when usage exceeds {int(threshold)}%"
            except Exception as exc:
                return f"❌ {exc}"

        usage_threshold_input.change(
            on_usage_threshold_change,
            inputs=[usage_threshold_input],
            outputs=[config_status],
        )

        def on_context_threshold_change(threshold):
            try:
                self.api_client.set_context_switch_threshold(int(threshold))
                if threshold >= 100:
                    return "✅ Context-based switching disabled"
                return f"✅ Will switch providers when context exceeds {int(threshold)}%"
            except Exception as exc:
                return f"❌ {exc}"

        context_threshold_input.change(
            on_context_threshold_change,
            inputs=[context_threshold_input],
            outputs=[config_status],
        )

        def on_max_verification_attempts_change(attempts):
            try:
                self.api_client.set_max_verification_attempts(int(attempts))
                return f"✅ Max verification attempts set to {int(attempts)}"
            except Exception as exc:
                return f"❌ {exc}"

        max_verification_attempts_input.change(
            on_max_verification_attempts_change,
            inputs=[max_verification_attempts_input],
            outputs=[config_status],
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

            # Mock usage slider change handler
            def make_mock_usage_handler():
                def handler(value, account_name):
                    if account_name:
                        self.provider_ui.set_mock_remaining_usage(account_name, value / 100.0)
                return handler

            card["mock_usage_slider"].change(
                fn=make_mock_usage_handler(),
                inputs=[card["mock_usage_slider"], card["account_state"]],
            )

    def _prefetch_init_data(self) -> dict:
        """Fetch all data needed for UI construction in one batch.

        This replaces ~49 individual API roundtrips with ~9, cutting
        startup time significantly.
        """
        accounts = self.api_client.list_accounts()
        try:
            verification_agent = self.api_client.get_verification_agent()
        except Exception:
            verification_agent = ""
        try:
            preferred_verification_model = self.api_client.get_preferred_verification_model()
        except Exception:
            preferred_verification_model = ""
        try:
            preferences = self.api_client.get_preferences()
        except Exception:
            preferences = None
        try:
            cleanup_settings = self.api_client.get_cleanup_settings()
        except Exception:
            cleanup_settings = None
        try:
            fallback_order = self.api_client.get_provider_fallback_order()
        except Exception:
            fallback_order = []
        try:
            usage_threshold = self.api_client.get_usage_switch_threshold()
        except Exception:
            usage_threshold = 90
        try:
            context_threshold = self.api_client.get_context_switch_threshold()
        except Exception:
            context_threshold = 80
        try:
            max_verification_attempts = self.api_client.get_max_verification_attempts()
        except Exception:
            max_verification_attempts = 5
        return {
            "accounts": accounts,
            "verification_agent": verification_agent,
            "preferred_verification_model": preferred_verification_model,
            "preferences": preferences,
            "cleanup_settings": cleanup_settings,
            "fallback_order": fallback_order,
            "usage_threshold": usage_threshold,
            "context_threshold": context_threshold,
            "max_verification_attempts": max_verification_attempts,
        }

    def _startup_log(self, msg: str) -> None:
        t0 = getattr(self, "_startup_t0", None)
        if t0 is not None and self.dev_mode:
            print(f"{_startup_elapsed(t0)} {msg}", flush=True)

    def create_interface(self) -> gr.Blocks:
        """Create the Gradio interface."""
        # Create initial session
        initial_session = self.create_session("Task 1")
        initial_session.event_log = EventLog(initial_session.id)

        # Prefetch all data needed during UI construction to avoid
        # redundant API calls (was ~49 HTTP roundtrips, now ~9).
        self._startup_log("Prefetching config...")
        self._init_data = self._prefetch_init_data()
        self._startup_log("Config ready")

        with gr.Blocks(title="Chad", analytics_enabled=False, js="""
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
            const focusLatestTask = () => {
                const root = getRoot();
                const taskTabs = Array.from(root.querySelectorAll('[role=\"tab\"]')).filter((tab) => {
                    const text = (tab.textContent || tab.getAttribute('aria-label') || '').trim();
                    return /^Task\\s+\\d+$/i.test(text);
                });
                if (!taskTabs.length) return;
                const last = taskTabs[taskTabs.length - 1];
                if (last) last.click();
            };
            const clickAddTask = () => {
                const root = getRoot();
                let attempts = 0;
                const tryClick = () => {
                    const btn = root.querySelector('#add-new-task-btn');
                    if (btn) {
                        hideButton(btn);
                        btn.click();
                        setTimeout(focusLatestTask, 140);
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
                return Array.from(root.querySelectorAll('[role="tab"]')).some(
                    (tab) => isPlus(tab) && tab.getAttribute('aria-selected') === 'true'
                );
            };
            const wirePlusButtons = () => {
                const root = getRoot();
                const candidates = [
                    ...root.querySelectorAll('[role="tab"]'),
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
        """) as interface:
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
  const focusLatestTask = () => {
    const root = getRoot();
    const taskTabs = Array.from(root.querySelectorAll('[role=\"tab\"]')).filter((tab) => {
      const text = (tab.textContent || tab.getAttribute('aria-label') || '').trim();
      return /^Task\\s+\\d+$/i.test(text);
    });
    if (!taskTabs.length) return;
    const last = taskTabs[taskTabs.length - 1];
    if (last) last.click();
  };
  const triggerAdd = () => {
    let attempts = 0;
    const tick = () => {
      const root = getRoot();
      const btn = root.querySelector('#add-new-task-btn');
      if (btn) {
        hideButton(btn);
        btn.click();
        setTimeout(focusLatestTask, 140);
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
                  window._liveStreamScrollState = window._liveStreamScrollState || {};
                  window._liveStreamTrackedParents = window._liveStreamTrackedParents || new WeakSet();
                  window._liveStreamTrackedContents = window._liveStreamTrackedContents || new WeakSet();
                  const getParentId = (parent) => {
                    if (parent.id) return parent.id;
                    if (!parent.dataset.scrollTrackId) {
                      parent.dataset.scrollTrackId = 'scroll-' + Math.random().toString(36).substr(2, 9);
                    }
                    return parent.dataset.scrollTrackId;
                  };
                  const getState = (parentId) => {
                    if (!window._liveStreamScrollState[parentId]) {
                      window._liveStreamScrollState[parentId] = {
                        userScrolledUp: false,
                        savedScrollTop: 0,
                        lastScrollHeight: 0,
                        settingScroll: false
                      };
                    }
                    return window._liveStreamScrollState[parentId];
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
                  const initializeLiveDomPatching = () => {
                    const decodeHtml = (html) => {
                      const txt = document.createElement('textarea');
                      txt.innerHTML = html;
                      return txt.value;
                    };

                    const patchLiveContent = (liveId, newHtml) => {
                      const root = getRoot();
                      const wrapper = root.querySelector(`[data-live-id="${liveId}"]`);
                      if (!wrapper) return false;

                      const content = wrapper.querySelector('.live-output-content');
                      if (!content) return false;

                      const parent = wrapper.closest('#live-stream-box, .live-stream-box');
                      const parentId = parent ? getParentId(parent) : null;
                      const state = parentId ? getState(parentId) : null;

                      const scrollTop = content.scrollTop;
                      const scrollHeight = content.scrollHeight;
                      const isAtBottom = scrollTop + content.clientHeight >= scrollHeight - 10;

                      if (state) {
                        state.settingScroll = true;
                        state.savedScrollTop = scrollTop;
                      }

                      const temp = document.createElement('div');
                      temp.innerHTML = newHtml;
                      const newWrapper = temp.querySelector('.live-output-wrapper') || temp;
                      const newContent = newWrapper.querySelector('.live-output-content');

                      if (newContent) {
                        content.innerHTML = newContent.innerHTML;
                      }

                      requestAnimationFrame(() => {
                        requestAnimationFrame(() => {
                          const newScrollHeight = content.scrollHeight;
                          if (state) {
                            state.lastScrollHeight = newScrollHeight;
                          }

                          if (isAtBottom && (!state || !state.userScrolledUp)) {
                            content.scrollTop = newScrollHeight;
                            if (state) {
                              state.savedScrollTop = newScrollHeight;
                              state.userScrolledUp = false;
                            }
                          } else {
                            content.scrollTop = scrollTop;
                          }

                          requestAnimationFrame(() => {
                            if (state) state.settingScroll = false;
                          });
                        });
                      });

                      return true;
                    };

                    const setupPatchTriggerWatcher = () => {
                      const root = getRoot();
                      const triggers = root.querySelectorAll('.live-patch-trigger');

                      triggers.forEach(trigger => {
                        if (trigger._patchObserverSetup) return;
                        trigger._patchObserverSetup = true;

                        const observer = new MutationObserver(() => {
                          const patchEl = trigger.querySelector('[data-live-patch]');
                          if (!patchEl) return;

                          const liveId = patchEl.dataset.livePatch;
                          const escapedHtml = patchEl.textContent || '';
                          if (!liveId || !escapedHtml) return;

                          const html = decodeHtml(escapedHtml);
                          patchLiveContent(liveId, html);
                        });

                        observer.observe(trigger, {
                          childList: true,
                          subtree: true,
                          characterData: true
                        });
                      });
                    };

                    setInterval(setupPatchTriggerWatcher, 500);
                    setTimeout(setupPatchTriggerWatcher, 100);
                  };

                  const initializeLiveStreamScrollTracking = () => {
                    const attachScrollListener = (content, state) => {
                      if (window._liveStreamTrackedContents.has(content)) return;
                      window._liveStreamTrackedContents.add(content);

                      content.addEventListener('scroll', () => {
                        if (state.settingScroll) return;

                        const scrollTop = content.scrollTop;
                        const previousScrollTop = state.savedScrollTop;
                        const isAtBottom = scrollTop + content.clientHeight >= content.scrollHeight - 10;
                        const scrolledDown = scrollTop > previousScrollTop;

                        state.savedScrollTop = scrollTop;

                        if (isAtBottom && scrolledDown) {
                          state.userScrolledUp = false;
                        } else if (!isAtBottom) {
                          state.userScrolledUp = true;
                        }
                      });
                    };

                    const setupParentTracking = (parent) => {
                      if (window._liveStreamTrackedParents.has(parent)) {
                        return;
                      }
                      window._liveStreamTrackedParents.add(parent);

                      const parentId = getParentId(parent);
                      const state = getState(parentId);

                      const initialContent = parent.querySelector('.live-output-content');
                      if (initialContent) {
                        attachScrollListener(initialContent, state);
                        state.lastScrollHeight = initialContent.scrollHeight;
                      }

                      const observer = new MutationObserver(() => {
                        if (state.settingScroll) return;

                        const content = parent.querySelector('.live-output-content');
                        if (!content) return;

                        attachScrollListener(content, state);

                        requestAnimationFrame(() => {
                          requestAnimationFrame(() => {
                            const newScrollHeight = content.scrollHeight;
                            const contentGrew = newScrollHeight > state.lastScrollHeight;

                            // Check if user was at bottom BEFORE content grew (using old height)
                            const wasAtBottomBeforeGrowth = state.savedScrollTop + content.clientHeight >= state.lastScrollHeight - 10;

                            state.lastScrollHeight = newScrollHeight;
                            state.settingScroll = true;

                            if (contentGrew && wasAtBottomBeforeGrowth && !state.userScrolledUp) {
                              // User was at bottom before content grew and hasn't scrolled up - auto-scroll
                              content.scrollTop = newScrollHeight;
                              state.savedScrollTop = newScrollHeight;
                              state.userScrolledUp = false;
                            } else if (state.userScrolledUp) {
                              content.scrollTop = state.savedScrollTop;
                            }

                            requestAnimationFrame(() => {
                              state.settingScroll = false;
                            });
                          });
                        });
                      });

                      observer.observe(parent, {
                        childList: true,
                        subtree: true
                      });
                    };

                    const findAndSetupParents = () => {
                      const root = getRoot();
                      const parents = [
                        root.querySelector('#live-stream-box'),
                        ...root.querySelectorAll('.live-stream-box')
                      ].filter(Boolean);

                      parents.forEach(setupParentTracking);
                    };

                    setInterval(findAndSetupParents, 500);
                    setTimeout(findAndSetupParents, 100);
                  };

                  const initializeTerminalColumnTracking = () => {
                    const CHAR_WIDTH = 8;
                    const PADDING = 24;
                    const SCROLLBAR = 20;
                    const MIN_COLS = 80;
                    const MAX_COLS = 300;

                    const calculateCols = (width) => {
                      const usableWidth = width - PADDING - SCROLLBAR;
                      const cols = Math.floor(usableWidth / CHAR_WIDTH);
                      return Math.min(MAX_COLS, Math.max(MIN_COLS, cols));
                    };

                    const updateTerminalCols = (cols) => {
                      const root = getRoot();
                      const inputs = [
                        root.querySelector('#terminal-cols-state input[type=\"number\"]'),
                        ...root.querySelectorAll('.terminal-cols-state input[type=\"number\"]')
                      ].filter(Boolean);

                      inputs.forEach(input => {
                        if (input && parseInt(input.value) !== cols) {
                          input.value = cols;
                          input.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                      });
                    };

                    const setupResizeObserver = (element) => {
                      if (element._terminalResizeSetup) return;
                      element._terminalResizeSetup = true;

                      const resizeObserver = new ResizeObserver(entries => {
                        for (const entry of entries) {
                          const width = entry.contentRect.width;
                          if (width > 0) {
                            const cols = calculateCols(width);
                            updateTerminalCols(cols);
                          }
                        }
                      });

                      resizeObserver.observe(element);

                      const width = element.getBoundingClientRect().width;
                      if (width > 0) {
                        updateTerminalCols(calculateCols(width));
                      }
                    };

                    const findAndSetupContainers = () => {
                      const root = getRoot();
                      const containers = [
                        root.querySelector('#live-stream-box'),
                        ...root.querySelectorAll('.live-stream-box')
                      ].filter(Boolean);

                      containers.forEach(setupResizeObserver);
                    };

                    setInterval(findAndSetupContainers, 500);
                    setTimeout(findAndSetupContainers, 100);
                  };
                  const ensureDiscardEditable = () => {
                    // Find status elements more broadly
                    const statuses = collectAll('.task-status-header, [id*=\"task-status\"], [class*=\"task-status\"], [class*=\"status\"]');
                    // Also check for any element containing "discarded" text
                    const allElements = collectAll('*');
                    const allWithDiscarded = allElements.filter(el => {
                      const text = (el.textContent || '').toLowerCase();
                      return text.includes('discarded') || text.includes('🗑️');
                    });

                    const hasDiscarded = statuses.some((el) => {
                      const text = (el.textContent || '').toLowerCase().replace(/[^a-z]/g, '');
                      return text.includes('discarded');
                    }) || allWithDiscarded.length > 0;

                    if (!hasDiscarded) return;

                    // Find task description textareas more specifically
                    const textareas = collectAll('textarea');
                    textareas.forEach((ta) => {
                      // Check multiple ways to identify task description textarea
                      const container = ta.closest('.gradio-container, .task-entry-bubble, [class*="textbox"], [class*="textarea"]');
                      const labelEl = container ? container.querySelector('label, span.label') : null;
                      const labelText = labelEl ? (labelEl.textContent || '').toLowerCase() : '';
                      const ariaLabel = (ta.getAttribute('aria-label') || '').toLowerCase();
                      const placeholder = (ta.getAttribute('placeholder') || '').toLowerCase();
                      const key = ta.getAttribute('key') || '';

                      // Match task description by multiple criteria
                      const isTaskDesc = labelText.includes('task description') ||
                                       ariaLabel.includes('task description') ||
                                       placeholder.includes('describe what you want done') ||
                                       key.includes('task-desc');

                      if (!isTaskDesc) return;

                      // Enable the textarea
                      ta.removeAttribute('disabled');
                      ta.removeAttribute('aria-disabled');
                      ta.disabled = false;
                      ta.readOnly = false;

                      // Also enable any parent fieldset
                      const fieldset = ta.closest('fieldset');
                      if (fieldset) {
                        fieldset.removeAttribute('disabled');
                        fieldset.removeAttribute('aria-disabled');
                        fieldset.disabled = false;
                      }

                      // Make sure any Gradio wrapper is also enabled
                      const gradioWrapper = ta.closest('.gr-textbox, .gradio-textbox');
                      if (gradioWrapper) {
                        gradioWrapper.classList.remove('disabled');
                        gradioWrapper.removeAttribute('disabled');
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
                  const syncMergeSectionVisibility = () => {
                    // Find all merge sections and show/hide based on content
                    const mergeSections = collectAll('.merge-section');
                    mergeSections.forEach(section => {
                      // Check if the section has meaningful content (header with text)
                      const headerElem = section.querySelector('[data-testid="markdown"]:first-child');
                      const header = headerElem ? headerElem.querySelector('h3') : null;
                      const hasContent = header && header.textContent.trim().length > 0;

                      // Also check for changes summary text
                      const summaryElems = section.querySelectorAll('[key*="changes-summary"] [data-testid="markdown"]');
                      let hasSummary = false;
                      summaryElems.forEach(elem => {
                        if (elem.textContent.trim().length > 0) {
                          hasSummary = true;
                        }
                      });

                      if (hasContent || hasSummary) {
                        section.classList.remove('merge-section-hidden');
                        section.style.cssText = '';
                        // Show direct children only - skip accordion internals to preserve open/close state
                        section.querySelectorAll(':scope > *').forEach(child => {
                          child.style.display = '';
                          child.style.visibility = '';
                        });
                      } else {
                        section.classList.add('merge-section-hidden');
                      }
                    });
                  };
                  initializeLiveDomPatching();
                  initializeLiveStreamScrollTracking();
                  initializeTerminalColumnTracking();
                  const tickAll = () => {
                    wirePlus();
                    fixAriaLinks();
                    ensureDiscardEditable();
                    syncMergeSectionVisibility();
                  };
                  setInterval(tickAll, 400);
                  setTimeout(tickAll, 80);
                  setInterval(syncMergeSectionVisibility, 200);
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
            MAX_TASKS = 8

            # Pre-create sessions for all potential tabs
            all_sessions = [initial_session]
            for i in range(1, MAX_TASKS):
                s = self.create_session(f"Task {i + 1}")
                s.event_log = EventLog(s.id)
                all_sessions.append(s)

            # Track how many tabs are currently visible (start with 1)
            visible_count = gr.State(1)

            # Tab index 1 = Task 1 (since Setup is index 0)
            with gr.Tabs(selected=1, elem_id="main-tabs") as main_tabs:
                # Setup tab (first, but not default selected)
                self._startup_log("Creating setup tab...")
                with gr.Tab("⚙️ Setup", id=0):
                    self._create_providers_ui()

                # Pre-create ALL task tabs - only first visible initially
                self._startup_log("Creating task tabs...")
                task_tabs = []
                for i in range(MAX_TASKS):
                    tab_id = i + 1  # Setup is 0, tasks start at 1
                    is_visible = (i == 0)  # Only first tab visible initially
                    with gr.Tab(f"Task {i + 1}", id=tab_id, visible=is_visible) as task_tab:
                        self._create_session_ui(all_sessions[i].id, is_first=(i == 0))
                    task_tabs.append(task_tab)
                self._startup_log("Task tabs created")

                # "+" tab to add new tasks - contains a button that triggers task creation
                add_tab_id = MAX_TASKS + 1
                with gr.Tab("➕", id=add_tab_id):
                    add_task_btn = gr.Button(
                        "➕ Add New Task",
                        variant="primary",
                        size="lg",
                        elem_id="add-new-task-btn",
                    )

            self._startup_log("Wiring event handlers...")

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
                """Refresh agent dropdowns and restore live view when switching to a task tab."""
                # Only refresh for task tabs (id 1-MAX_TASKS, not providers tab id=0 or add tab)
                add_tab_id = MAX_TASKS + 1
                n_dropdowns = len(self._session_dropdowns) * 2
                n_patches = len(self._session_live_patches)
                n_streams = len(self._session_live_streams)
                total = n_dropdowns + n_patches + n_streams
                if evt.index == 0 or evt.index == add_tab_id:
                    return [gr.update() for _ in range(total)]
                # Also guard against out-of-bounds indices
                session_index = evt.index - 1
                if session_index < 0 or session_index >= len(all_sessions):
                    return [gr.update() for _ in range(total)]

                # Get current account choices
                accounts = self.api_client.list_accounts()
                account_choices = [acc.name for acc in accounts]
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
                # Push a live patch for the selected tab to rehydrate live view
                selected_session = all_sessions[session_index]
                live_stream_id = f"live-{selected_session.id}"
                patch_html = ""
                if selected_session.last_live_stream:
                    import html as html_module

                    escaped = html_module.escape(selected_session.last_live_stream)
                    patch_html = f'<div data-live-patch="{live_stream_id}" style="display:none">{escaped}</div>'

                for sess in all_sessions:
                    if sess.id == selected_session.id:
                        if patch_html:
                            updates.append(gr.update(value=patch_html))
                        else:
                            updates.append(gr.update())
                    else:
                        updates.append(gr.update())
                # Directly restore live stream content for selected tab to avoid
                # the brief "waiting for agent output" flash before JS patching
                for sess in all_sessions:
                    if sess.id == selected_session.id and selected_session.last_live_stream:
                        updates.append(gr.update(value=selected_session.last_live_stream))
                    else:
                        updates.append(gr.update())
                return updates

            # Collect outputs for the select handler (dropdowns + live patch triggers + live streams)
            all_dropdown_outputs = []
            live_patch_outputs = []
            live_stream_outputs = []
            for sess in all_sessions:
                session_id = sess.id
                dropdowns = self._session_dropdowns.get(session_id)
                if dropdowns:
                    all_dropdown_outputs.append(dropdowns["coding_agent"])
                    all_dropdown_outputs.append(dropdowns["verification_agent"])
                trigger = self._session_live_patches.get(session_id)
                if trigger:
                    live_patch_outputs.append(trigger)
                stream = self._session_live_streams.get(session_id)
                if stream:
                    live_stream_outputs.append(stream)

            if all_dropdown_outputs or live_patch_outputs or live_stream_outputs:
                main_tabs.select(
                    on_tab_select,
                    outputs=all_dropdown_outputs + live_patch_outputs + live_stream_outputs,
                )

            # Chain dropdown refresh to provider delete events
            # This ensures dropdowns update when providers are deleted
            def refresh_dropdowns_after_delete():
                """Refresh all session dropdowns after a provider is deleted."""
                accounts = self.api_client.list_accounts()
                account_choices = [acc.name for acc in accounts]
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

            # Load handler to refresh config panel dropdown values on page load
            def refresh_config_dropdown_on_load():
                """Refresh the config panel verification dropdown from current config."""
                try:
                    accounts = self.api_client.list_accounts()
                    account_choices = [acc.name for acc in accounts]
                    stored_verification = self.api_client.get_verification_agent()

                    if stored_verification == self.VERIFICATION_NONE:
                        verification_value = self.VERIFICATION_NONE
                    elif stored_verification and stored_verification in account_choices:
                        verification_value = stored_verification
                    else:
                        verification_value = self.SAME_AS_CODING

                    verification_choices = [
                        (self.SAME_AS_CODING, self.SAME_AS_CODING),
                        (self.VERIFICATION_NONE_LABEL, self.VERIFICATION_NONE),
                        *[(name, name) for name in account_choices],
                    ]
                    return gr.update(choices=verification_choices, value=verification_value)
                except Exception:
                    return gr.update()

            if hasattr(self, "_config_verification_pref") and self._config_verification_pref:
                interface.load(
                    fn=refresh_config_dropdown_on_load,
                    outputs=[self._config_verification_pref],
                )

            # Init data was only needed during construction
            del self._init_data
            return interface


def _startup_elapsed(t0: float) -> str:
    """Format seconds elapsed since t0."""
    return f"[{time.monotonic() - t0:.1f}s]"


def launch_web_ui(
    api_base_url: str = "http://localhost:8000",
    port: int = 7860,
    dev_mode: bool = False,
) -> tuple[None, int]:
    """Launch the Chad web interface.

    Args:
        api_base_url: Base URL of the Chad API server
        port: Port to run on. Use 0 for ephemeral port.
        dev_mode: If True, enable development features like mock provider

    Returns:
        Tuple of (None, actual_port) where actual_port is the port used
    """
    t0 = time.monotonic()

    # Ensure downstream agents inherit a consistent project root
    try:
        from chad.util.config import ensure_project_root_env

        ensure_project_root_env()
    except Exception:
        # Non-fatal; continue without forcing env
        pass

    # Create API client for communication with server
    from chad.ui.client import APIClient
    api_client = APIClient(api_base_url)

    def _log(msg: str) -> None:
        if dev_mode:
            print(f"{_startup_elapsed(t0)} {msg}", flush=True)

    # Create and launch UI
    _log("Building interface...")
    ui = ChadWebUI(api_client, dev_mode=dev_mode)
    ui._startup_t0 = t0
    app = ui.create_interface()
    _log("Interface built")

    requested_port = port
    port, ephemeral, conflicted = _resolve_port(port)
    screenshot_mode = os.environ.get("CHAD_SCREENSHOT_MODE") == "1"
    open_browser = not screenshot_mode
    if conflicted:
        print(f"Port {requested_port} already in use; launching on ephemeral port {port}")

    _log("Launching Gradio server...")

    # Allow serving session log files from the logs directory
    log_dir = str(EventLog.get_log_dir())

    app.launch(
        server_name="127.0.0.1",
        server_port=port,
        share=False,
        inbrowser=open_browser,
        quiet=False,
        show_api=False,
        enable_monitoring=False,
        allowed_paths=[log_dir],
    )

    return None, port

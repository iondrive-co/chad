"""Server-side event loop for session lifecycle orchestration.

Replaces the 3-phase (exploration → implementation → continuation) model
with a single coding phase + server-side milestone detection and verification.
"""

import queue
import re
import threading
import time
from typing import Any, Callable

from chad.util.event_log import EventLog, MilestoneEvent, ProviderSwitchedEvent, UserMessageEvent
from chad.server.services.pty_stream import get_pty_stream_service
from chad.util.prompts import extract_coding_summary, CodingSummary


class SessionEventLoop:
    """Per-session event loop that orchestrates coding → verification → revision.

    The loop runs in the caller's thread (blocking), while a background tick
    thread handles milestone detection and message processing concurrently.
    """

    def __init__(
        self,
        session_id: str,
        event_log: EventLog,
        task,
        run_phase_fn: Callable,
        emit_fn: Callable,
        worktree_path,
        max_verification_attempts: int = 5,
        is_quota_exhausted_fn: Callable[[str], str | None] | None = None,
        get_session_usage_fn: Callable[[], float | None] | None = None,
        get_weekly_usage_fn: Callable[[], float | None] | None = None,
        get_context_usage_fn: Callable[[], float | None] | None = None,
        action_settings: list[dict] | None = None,
        terminate_pty_fn: Callable[[], None] | None = None,
        get_account_info_fn: Callable[[str], dict | None] | None = None,
        get_session_reset_eta_fn: Callable[[], str | None] | None = None,
        get_weekly_reset_eta_fn: Callable[[], str | None] | None = None,
        notify_slack: bool = False,
    ):
        self.session_id = session_id
        self.event_log = event_log
        self.task = task
        self._notify_slack = notify_slack
        self._run_phase_fn = run_phase_fn
        self._emit_fn = emit_fn
        self.worktree_path = worktree_path
        self._max_verification_attempts = max_verification_attempts

        self._is_quota_exhausted_fn = is_quota_exhausted_fn

        self._state = "idle"
        self._message_queue: queue.Queue = queue.Queue()
        self._output_buffer: list[str] = []
        self._output_lock = threading.Lock()
        self._milestones: list[dict[str, Any]] = []
        self._milestone_seq = 0
        self._milestone_lock = threading.Lock()
        self._running = False
        self._tick_thread: threading.Thread | None = None

        # Milestone detection state
        self._exploration_chunks_processed = 0
        self._exploration_partial_line = ""
        self._seen_exploration_summaries: set[str] = set()
        self._coding_complete_detected = False
        self._coding_summary: CodingSummary | None = None
        self._session_limit_detected = False
        self._session_limit_lock = threading.Lock()
        self._session_limit_summary: str | None = None

        # Usage threshold monitoring state
        self._get_session_usage_fn = get_session_usage_fn
        self._get_weekly_usage_fn = get_weekly_usage_fn
        self._get_context_usage_fn = get_context_usage_fn
        self._usage_check_counter = 0

        # Action settings — each rule tracks its own previous value
        self._action_settings = action_settings or []
        self._prev_pct_per_rule: list[float | None] = [None] * len(self._action_settings)
        self._terminate_pty_fn = terminate_pty_fn
        self._get_account_info_fn = get_account_info_fn
        self._get_session_reset_eta_fn = get_session_reset_eta_fn
        self._get_weekly_reset_eta_fn = get_weekly_reset_eta_fn
        self._pending_action: dict | None = None
        self._pending_action_lock = threading.Lock()

        # Accumulated output from all phases
        self.accumulated_output = ""

    # Map event types to (usage_fn_attr, display_label)
    _EVENT_USAGE_MAP = {
        "session_usage": ("_get_session_usage_fn", "session"),
        "weekly_usage": ("_get_weekly_usage_fn", "weekly"),
        "context_usage": ("_get_context_usage_fn", "context"),
    }

    def feed_output(self, text: str) -> None:
        """Called by _run_phase's PTY callback with parsed text."""
        with self._output_lock:
            self._output_buffer.append(text)

    def enqueue_message(self, content: str, source: str = "ui") -> None:
        """Queue a user message for forwarding to the PTY."""
        self._message_queue.put({"content": content, "source": source})
        if self.event_log:
            self.event_log.log(UserMessageEvent(content=content))

    def get_milestones(self, since_seq: int = 0) -> list[dict[str, Any]]:
        """Return milestones after the given sequence number."""
        with self._milestone_lock:
            return [m for m in self._milestones if m.get("seq", 0) > since_seq]

    def get_latest_milestone_seq(self) -> int:
        """Return the latest milestone sequence number."""
        with self._milestone_lock:
            return self._milestone_seq

    # Display titles for each milestone type - UIs render these directly
    _MILESTONE_TITLES: dict[str, str] = {
        "exploration": "Discovery",
        "coding_complete": "Coding Complete",
        "session_limit_reached": "Session Limit",
        "weekly_limit_reached": "Weekly Limit",
        "usage_threshold": "Usage Warning",
        "verification_started": "Verification",
        "verification_passed": "Verification Passed",
        "verification_failed": "Verification Failed",
        "revision_started": "Re-coding",
    }

    def _emit_milestone(self, milestone_type: str, summary: str, details: dict | None = None) -> None:
        """Emit a milestone event to the EventLog and internal tracking."""
        details = details or {}
        title = self._MILESTONE_TITLES.get(milestone_type, milestone_type)
        event = MilestoneEvent(
            milestone_type=milestone_type,
            title=title,
            summary=summary,
            details=details,
        )
        if self.event_log:
            self.event_log.log(event)

        with self._milestone_lock:
            self._milestone_seq += 1
            self._milestones.append({
                "seq": self._milestone_seq,
                "milestone_type": milestone_type,
                "title": title,
                "summary": summary,
                "details": details,
            })

        self._emit_fn("milestone", milestone_type=milestone_type, title=title, summary=summary, details=details)

        if self._notify_slack:
            from chad.server.services.slack_service import get_slack_service
            get_slack_service().post_milestone_async(
                self.session_id, milestone_type, title, summary,
            )

    def _extract_meaningful_error_summary(self, tail: str) -> str | None:
        """Extract meaningful error summary from terminal output, filtering out JavaScript error objects.

        JavaScript error objects like "[Symbol(gaxios-gaxios-error)]: '6.7.1'" or "[object Object]"
        are not helpful to users. This method prioritizes actual error messages that explain
        the issue in human-readable terms.

        Args:
            tail: Last ~500 chars of terminal output where quota exhaustion was detected.

        Returns:
            Meaningful error message string, or None if no suitable message found.
        """
        import re

        # Patterns that indicate JavaScript error objects (not helpful to users)
        js_error_patterns = [
            r'^\[Symbol\([^)]+\)\]:',  # [Symbol(something)]:
            r'^\[object\s+\w+\]',     # [object Object], [object Error]
            r'^TypeError:.*undefined$',  # TypeErrors with undefined
            r'^ReferenceError:',      # JavaScript reference errors
            r'^SyntaxError:',         # JavaScript syntax errors
        ]

        # Collect all non-JS-error lines and categorize them
        lines = tail.strip().splitlines()
        priority_messages = []  # Lines with quota/limit keywords
        other_candidates = []   # Other meaningful lines

        for line in lines:
            stripped = line.strip()
            if not stripped or len(stripped) <= 10:
                continue

            # Skip JavaScript error objects
            is_js_error = any(re.search(pattern, stripped) for pattern in js_error_patterns)
            if is_js_error:
                continue

            # Categorize by priority keywords (quota/limit terms get higher priority)
            priority_keywords = ['quota', 'limit', 'exceeded', 'exhausted', 'insufficient']
            has_priority_keyword = any(keyword in stripped.lower() for keyword in priority_keywords)

            if has_priority_keyword:
                priority_messages.append(stripped)
            else:
                other_candidates.append(stripped)

        # Return the last (most recent) priority message, or last other candidate
        if priority_messages:
            return priority_messages[-1]
        elif other_candidates:
            return other_candidates[-1]
        else:
            return None

    def _loop(self) -> None:
        """Background tick loop for milestone detection and message processing."""
        while self._running:
            self._process_messages()
            self._analyze_output()
            self._usage_check_counter += 1
            if self._usage_check_counter >= 20:  # 20 * 0.5s = 10 seconds
                self._usage_check_counter = 0
                self._check_usage_thresholds()
            time.sleep(0.5)

    # ---- Exploration marker detection ----
    _EXPLORATION_LINE_RE = re.compile(r"^\s*EXPLORATION_RESULT:\s*(?P<summary>.+?)\s*$")
    _ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    _INVALID_EXPLORATION_PREFIXES = (
        "workdir:",
        "model:",
        "session id:",
        "/bin/bash -lc",
        "exec ",
    )

    def _sanitize_exploration_text(self, text: str) -> str:
        """Strip ANSI/control characters before parsing exploration markers."""
        if not text:
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = self._ANSI_RE.sub("", normalized)
        return self._CONTROL_RE.sub("", normalized)

    def _normalize_exploration_summary(self, summary: str) -> str | None:
        """Normalize and validate an exploration summary line."""
        cleaned = " ".join(summary.split()).strip()
        if len(cleaned) < 8 or len(cleaned) > 400:
            return None
        lower = cleaned.lower()
        if lower.startswith(self._INVALID_EXPLORATION_PREFIXES):
            return None
        return cleaned

    def _scan_exploration_markers(self, new_text: str, finalize: bool = False) -> None:
        """Parse incremental output for EXPLORATION_RESULT markers."""
        text = self._exploration_partial_line + self._sanitize_exploration_text(new_text)
        if not text:
            return

        lines = text.split("\n")
        if text.endswith("\n") or finalize:
            complete_lines = lines
            self._exploration_partial_line = ""
        else:
            complete_lines = lines[:-1]
            self._exploration_partial_line = lines[-1]

        for line in complete_lines:
            match = self._EXPLORATION_LINE_RE.match(line)
            if not match:
                continue
            summary = self._normalize_exploration_summary(match.group("summary"))
            if not summary:
                continue
            summary_key = summary.casefold()
            if summary_key in self._seen_exploration_summaries:
                continue
            self._seen_exploration_summaries.add(summary_key)
            self._emit_milestone("exploration", summary)

    def _analyze_output(self, finalize: bool = False) -> None:
        """Scan output buffer for milestone markers."""
        with self._output_lock:
            if not self._output_buffer:
                return
            joined = "\n".join(self._output_buffer)
            new_chunks = self._output_buffer[self._exploration_chunks_processed:]
            self._exploration_chunks_processed = len(self._output_buffer)

        self._scan_exploration_markers("".join(new_chunks), finalize=finalize)

        # Scan for session/quota limit messages in the tail of output
        # (only check the last ~500 chars to avoid false positives from code edits)
        # Lock prevents duplicate detection from background tick + main thread
        if not self._session_limit_detected:
            with self._session_limit_lock:
                if not self._session_limit_detected:
                    tail = joined[-500:] if len(joined) > 500 else joined
                    if self._is_quota_exhausted_fn:
                        limit_type = self._is_quota_exhausted_fn(tail)
                        if limit_type:
                            self._session_limit_detected = True
                            title = self._MILESTONE_TITLES.get(limit_type, "Limit Reached")
                            summary = f"{title} - quota exhausted"

                            # Extract meaningful error summary, filtering out JavaScript error objects
                            meaningful_summary = self._extract_meaningful_error_summary(tail)
                            if meaningful_summary:
                                summary = meaningful_summary

                            self._session_limit_summary = summary
                            self._emit_milestone(limit_type, summary)

        # Scan for coding completion JSON
        if not self._coding_complete_detected:
            summary = extract_coding_summary(joined)
            if summary:
                self._coding_complete_detected = True
                self._coding_summary = summary
                details = {}
                if summary.files_changed:
                    details["files_changed"] = summary.files_changed
                if summary.completion_status:
                    details["completion_status"] = summary.completion_status
                self._emit_milestone(
                    "coding_complete",
                    summary.change_summary,
                    details,
                )

    def _process_messages(self) -> None:
        """Send queued user messages to the active PTY session."""
        if not self.task or not getattr(self.task, "stream_id", None):
            return

        pty_service = get_pty_stream_service()
        pty_session = pty_service.get_session(self.task.stream_id)
        if not pty_session or not getattr(pty_session, "active", False):
            return

        while not self._message_queue.empty():
            try:
                msg = self._message_queue.get_nowait()
            except queue.Empty:
                break

            content = (msg or {}).get("content", "")
            if not content:
                continue

            data = content if content.endswith("\n") else content + "\n"
            ok = pty_service.send_input(self.task.stream_id, data.encode(), close_stdin=False)
            if not ok:
                self._message_queue.put(msg)
                break

    def _check_usage_thresholds(self) -> None:
        """Check provider usage metrics for threshold crossings based on action_settings."""
        # Cache current values per event type so we only call each usage fn once
        current_cache: dict[str, float | None] = {}

        for idx, setting in enumerate(self._action_settings):
            event_type = setting.get("event")
            threshold = setting.get("threshold", 90)
            action = setting.get("action", "notify")

            mapping = self._EVENT_USAGE_MAP.get(event_type)
            if not mapping:
                continue
            fn_attr, label = mapping

            # Fetch current value (cached per event type)
            if event_type not in current_cache:
                fn = getattr(self, fn_attr, None)
                if fn is None:
                    current_cache[event_type] = None
                else:
                    try:
                        current_cache[event_type] = fn()
                    except Exception:
                        current_cache[event_type] = None
            current = current_cache[event_type]
            if current is None:
                continue

            prev = self._prev_pct_per_rule[idx]
            crossed = prev is not None and prev < threshold and current >= threshold
            self._prev_pct_per_rule[idx] = current

            if not crossed:
                continue

            if action == "notify":
                self._emit_milestone(
                    "usage_threshold",
                    f"{label.title()} usage reached {current:.0f}%",
                    {"metric": label, "percentage": current},
                )
            elif action in ("switch_provider", "await_reset"):
                with self._pending_action_lock:
                    self._pending_action = {**setting, "current_pct": current, "label": label}
                self._emit_milestone(
                    "usage_threshold",
                    f"{label.title()} usage reached {current:.0f}% - {action.replace('_', ' ')}",
                    {"metric": label, "percentage": current, "action": action},
                )
                if self._terminate_pty_fn:
                    self._terminate_pty_fn()

    def run(
        self,
        session,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        screenshots: list[str] | None,
        rows: int,
        cols: int,
        git_mgr,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        override_prompt: str | None = None,
        verification_config: dict | None = None,
    ) -> tuple[int, str]:
        """Run the full task lifecycle. Blocks until complete.

        Returns:
            (final_exit_code, accumulated_output)
        """
        self._running = True
        self._state = "coding"
        self._tick_thread = threading.Thread(target=self._loop, daemon=True)
        self._tick_thread.start()

        try:
            exit_code, output = self._run_coding_phase(
                session=session,
                task_description=task_description,
                coding_account=coding_account,
                coding_provider=coding_provider,
                screenshots=screenshots,
                rows=rows,
                cols=cols,
                git_mgr=git_mgr,
                coding_model=coding_model,
                coding_reasoning=coding_reasoning,
                override_prompt=override_prompt,
            )
            self.accumulated_output = output

            if exit_code < 0:
                return exit_code, self.accumulated_output

            # Run verification if configured
            if verification_config and exit_code == 0:
                self._run_verification_loop(
                    session=session,
                    task_description=task_description,
                    coding_account=coding_account,
                    coding_provider=coding_provider,
                    rows=rows,
                    cols=cols,
                    git_mgr=git_mgr,
                    coding_model=coding_model,
                    coding_reasoning=coding_reasoning,
                    verification_config=verification_config,
                )

            return exit_code, self.accumulated_output

        finally:
            self._running = False
            if self._tick_thread:
                self._tick_thread.join(timeout=2.0)

    def _run_coding_phase(
        self,
        session,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        screenshots: list[str] | None,
        rows: int,
        cols: int,
        git_mgr,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        override_prompt: str | None = None,
    ) -> tuple[int, str]:
        """Run the coding phase with continuation attempts."""
        self._emit_fn("status", status="Coding...")

        exit_code, output = self._run_phase_fn(
            task=self.task,
            session=session,
            worktree_path=self.worktree_path,
            task_description=task_description,
            coding_account=coding_account,
            coding_provider=coding_provider,
            screenshots=screenshots,
            phase="combined",
            exploration_output=None,
            rows=rows,
            cols=cols,
            emit=self._emit_fn,
            git_mgr=git_mgr,
            coding_model=coding_model,
            coding_reasoning=coding_reasoning,
            override_prompt=override_prompt,
        )
        # Final scan to catch output that arrived just before exit
        self._analyze_output(finalize=True)

        if getattr(self.task, "cancel_requested", False):
            return -1, output

        if exit_code < 0:
            return exit_code, output

        # Check for pending action from background threshold check
        with self._pending_action_lock:
            pending = self._pending_action
            self._pending_action = None

        if pending:
            action = pending.get("action")
            if action == "switch_provider":
                action_output = self._handle_switch_provider(
                    pending, session, task_description, output,
                    screenshots, rows, cols, git_mgr,
                    coding_account, coding_provider, coding_model, coding_reasoning,
                )
                return 0, output + "\n" + action_output
            elif action == "await_reset":
                action_output = self._handle_await_reset(
                    pending, session, task_description, output,
                    screenshots, rows, cols, git_mgr,
                    coding_account, coding_provider, coding_model, coding_reasoning,
                )
                return 0, output + "\n" + action_output

        # Check if coding completed
        summary = extract_coding_summary(output)

        # Continuation loop if agent exited without completion
        max_continuation_attempts = 3
        if summary is None and exit_code == 0:
            for attempt in range(max_continuation_attempts):
                self._emit_fn("status", status=f"Agent continuing (attempt {attempt + 1})...")
                cont_exit, cont_output = self._run_phase_fn(
                    task=self.task,
                    session=session,
                    worktree_path=self.worktree_path,
                    task_description=task_description,
                    coding_account=coding_account,
                    coding_provider=coding_provider,
                    screenshots=None,
                    phase="continuation",
                    exploration_output=output,
                    rows=rows,
                    cols=cols,
                    emit=self._emit_fn,
                    git_mgr=git_mgr,
                    coding_model=coding_model,
                    coding_reasoning=coding_reasoning,
                )
                self._analyze_output()
                output += "\n" + cont_output

                if getattr(self.task, "cancel_requested", False):
                    return -1, output

                if cont_exit < 0:
                    return cont_exit, output

                summary = extract_coding_summary(output)
                if summary is not None or cont_exit != 0:
                    break

        return exit_code, output

    def _handle_switch_provider(
        self,
        action: dict,
        session,
        task_description: str,
        previous_output: str,
        screenshots: list[str] | None,
        rows: int,
        cols: int,
        git_mgr,
        old_account: str,
        old_provider: str,
        old_model: str | None,
        old_reasoning: str | None,
    ) -> str:
        """Handle switch_provider action: log checkpoint, switch, resume."""
        from chad.util.handoff import log_handoff_checkpoint, build_resume_prompt

        target_account = action.get("target_account", "")
        label = action.get("label", "usage")

        account_info = self._get_account_info_fn(target_account) if self._get_account_info_fn else None
        if not account_info:
            self._emit_milestone(
                "usage_threshold",
                f"Cannot switch to {target_account}: account not found",
            )
            return ""

        new_provider = account_info["provider"]
        new_model = account_info.get("model")
        new_reasoning = account_info.get("reasoning")

        self._emit_milestone(
            "usage_threshold",
            f"Switching from {old_account} to {target_account} ({label} threshold)",
        )

        if self.event_log:
            log_handoff_checkpoint(
                self.event_log,
                task_description,
                target_provider=new_provider,
            )

        resume_prompt = None
        if self.event_log:
            resume_prompt = build_resume_prompt(self.event_log, target_provider=new_provider)

        if self.event_log:
            self.event_log.log(ProviderSwitchedEvent(
                from_provider=old_provider,
                to_provider=new_provider,
                from_model=old_model or "",
                to_model=new_model or "",
                reason=f"{label} threshold reached",
            ))

        self._emit_fn("status", status=f"Continuing with {target_account}...")
        exit_code, cont_output = self._run_phase_fn(
            task=self.task,
            session=session,
            worktree_path=self.worktree_path,
            task_description=task_description,
            coding_account=target_account,
            coding_provider=new_provider,
            screenshots=None,
            phase="continuation",
            exploration_output=previous_output,
            rows=rows,
            cols=cols,
            emit=self._emit_fn,
            git_mgr=git_mgr,
            coding_model=new_model,
            coding_reasoning=new_reasoning,
            override_prompt=resume_prompt,
        )
        self._analyze_output(finalize=True)
        return cont_output

    def _handle_await_reset(
        self,
        action: dict,
        session,
        task_description: str,
        previous_output: str,
        screenshots: list[str] | None,
        rows: int,
        cols: int,
        git_mgr,
        coding_account: str,
        coding_provider: str,
        coding_model: str | None,
        coding_reasoning: str | None,
    ) -> str:
        """Handle await_reset action: poll until usage drops, then resume."""
        event_type = action.get("event")
        threshold = action.get("threshold", 90)
        label = action.get("label", "usage")

        # Determine which ETA and usage functions to use
        eta_fn = None
        mapping = self._EVENT_USAGE_MAP.get(event_type)
        if not mapping:
            return ""
        fn_attr = mapping[0]
        usage_fn = getattr(self, fn_attr, None)
        if usage_fn is None:
            return ""

        if event_type == "session_usage":
            eta_fn = self._get_session_reset_eta_fn
        elif event_type == "weekly_usage":
            eta_fn = self._get_weekly_reset_eta_fn

        eta_str = ""
        if eta_fn:
            try:
                eta = eta_fn()
                if eta:
                    eta_str = f" (ETA: {eta})"
            except Exception:
                pass

        self._emit_milestone(
            "usage_threshold",
            f"Paused, waiting for {label} reset{eta_str}",
        )

        # Poll until usage drops below threshold
        while self._running and not getattr(self.task, "cancel_requested", False):
            time.sleep(10)
            try:
                current = usage_fn()
            except Exception:
                continue
            if current is not None and current < threshold:
                break

        if not self._running or getattr(self.task, "cancel_requested", False):
            return ""

        self._emit_milestone(
            "usage_threshold",
            f"{label.title()} reset detected, resuming",
        )

        self._emit_fn("status", status="Resuming after reset...")
        exit_code, cont_output = self._run_phase_fn(
            task=self.task,
            session=session,
            worktree_path=self.worktree_path,
            task_description=task_description,
            coding_account=coding_account,
            coding_provider=coding_provider,
            screenshots=None,
            phase="continuation",
            exploration_output=previous_output,
            rows=rows,
            cols=cols,
            emit=self._emit_fn,
            git_mgr=git_mgr,
            coding_model=coding_model,
            coding_reasoning=coding_reasoning,
        )
        self._analyze_output(finalize=True)
        return cont_output

    def _run_verification_loop(
        self,
        session,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        rows: int,
        cols: int,
        git_mgr,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        verification_config: dict | None = None,
    ) -> None:
        """Run the verification → revision cycle."""
        from chad.server.services.verification import run_verification

        config = verification_config or {}
        verification_account = config.get("verification_account", coding_account)
        verification_model = config.get("verification_model")
        verification_reasoning = config.get("verification_reasoning")
        project_path = str(self.worktree_path)

        for attempt in range(self._max_verification_attempts):
            self._emit_milestone("verification_started", f"Attempt {attempt + 1}")

            passed, feedback = run_verification(
                project_path=project_path,
                coding_output=self.accumulated_output,
                task_description=task_description,
                verification_account=verification_account,
                verification_model=verification_model,
                verification_reasoning=verification_reasoning,
                run_phase_fn=self._run_phase_fn,
                task=self.task,
                session=session,
                worktree_path=self.worktree_path,
                rows=rows,
                cols=cols,
                emit=self._emit_fn,
                git_mgr=git_mgr,
                attempt=attempt + 1,
            )

            if passed is True:
                self._emit_milestone("verification_passed", feedback)
                return
            elif passed is None:
                # Verification aborted, don't retry
                self._emit_milestone("verification_failed", feedback or "Verification aborted")
                return

            self._emit_milestone("verification_failed", feedback)

            if attempt < self._max_verification_attempts - 1:
                self._emit_milestone("revision_started", "Sending feedback to coding agent")
                self._run_revision_phase(
                    session=session,
                    task_description=task_description,
                    coding_account=coding_account,
                    coding_provider=coding_provider,
                    feedback=feedback,
                    rows=rows,
                    cols=cols,
                    git_mgr=git_mgr,
                    coding_model=coding_model,
                    coding_reasoning=coding_reasoning,
                )

    def _run_revision_phase(
        self,
        session,
        task_description: str,
        coding_account: str,
        coding_provider: str,
        feedback: str,
        rows: int,
        cols: int,
        git_mgr,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
    ) -> None:
        """Run a revision phase using verification feedback."""
        from chad.util.prompts import get_revision_prompt

        revision_prompt = get_revision_prompt(feedback)

        exit_code, output = self._run_phase_fn(
            task=self.task,
            session=session,
            worktree_path=self.worktree_path,
            task_description=task_description,
            coding_account=coding_account,
            coding_provider=coding_provider,
            screenshots=None,
            phase="revision",
            exploration_output=self.accumulated_output,
            rows=rows,
            cols=cols,
            emit=self._emit_fn,
            git_mgr=git_mgr,
            coding_model=coding_model,
            coding_reasoning=coding_reasoning,
            override_prompt=revision_prompt,
        )
        # Final scan to catch output that arrived just before exit
        self._analyze_output(finalize=True)

        self.accumulated_output += "\n" + output

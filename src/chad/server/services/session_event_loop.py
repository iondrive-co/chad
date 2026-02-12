"""Server-side event loop for session lifecycle orchestration.

Replaces the 3-phase (exploration → implementation → continuation) model
with a single coding phase + server-side milestone detection and verification.
"""

import queue
import re
import threading
import time
from typing import Any, Callable

from chad.util.event_log import EventLog, MilestoneEvent, UserMessageEvent
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
    ):
        self.session_id = session_id
        self.event_log = event_log
        self.task = task
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
        self._output_scan_pos = 0
        self._coding_complete_detected = False
        self._coding_summary: CodingSummary | None = None
        self._session_limit_detected = False
        self._session_limit_summary: str | None = None

        # Usage threshold monitoring state
        self._get_session_usage_fn = get_session_usage_fn
        self._get_weekly_usage_fn = get_weekly_usage_fn
        self._get_context_usage_fn = get_context_usage_fn
        self._prev_session_pct: float | None = None
        self._prev_weekly_pct: float | None = None
        self._prev_context_pct: float | None = None
        self._usage_check_counter = 0

        # Accumulated output from all phases
        self.accumulated_output = ""

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
    _EXPLORATION_RE = re.compile(r"EXPLORATION_RESULT:\s*(.+?)(?:\n\n|\n(?=[A-Z•\-\*#])|$)", re.DOTALL)

    def _analyze_output(self) -> None:
        """Scan output buffer for milestone markers."""
        with self._output_lock:
            if not self._output_buffer:
                return
            joined = "\n".join(self._output_buffer)

        # Scan for EXPLORATION_RESULT: markers
        for match in self._EXPLORATION_RE.finditer(joined, self._output_scan_pos):
            summary_text = match.group(1).strip()
            if summary_text:
                self._emit_milestone("exploration", summary_text)

        self._output_scan_pos = len(joined)

        # Scan for session/quota limit messages in the tail of output
        # (only check the last ~500 chars to avoid false positives from code edits)
        if not self._session_limit_detected:
            tail = joined[-500:] if len(joined) > 500 else joined
            if self._is_quota_exhausted_fn:
                limit_type = self._is_quota_exhausted_fn(tail)
                if limit_type:
                    self._session_limit_detected = True
                    title = self._MILESTONE_TITLES.get(limit_type, "Limit Reached")
                    summary = f"{title} - quota exhausted"
                    # Try to extract a more useful summary from the output
                    for line in tail.strip().splitlines():
                        stripped = line.strip()
                        if stripped and len(stripped) > 10:
                            # Use the last meaningful line as the summary
                            summary = stripped
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

    _USAGE_THRESHOLD = 90.0

    def _check_usage_thresholds(self) -> None:
        """Check provider usage metrics for threshold crossings."""
        checks = [
            ("context", self._get_context_usage_fn, "_prev_context_pct"),
            ("session", self._get_session_usage_fn, "_prev_session_pct"),
            ("weekly", self._get_weekly_usage_fn, "_prev_weekly_pct"),
        ]
        for label, fn, prev_attr in checks:
            if fn is None:
                continue
            try:
                current = fn()
            except Exception:
                continue
            if current is None:
                continue
            prev = getattr(self, prev_attr)
            if prev is not None and prev < self._USAGE_THRESHOLD and current >= self._USAGE_THRESHOLD:
                self._emit_milestone(
                    "usage_threshold",
                    f"{label.title()} usage reached {current:.0f}%",
                    {"metric": label, "percentage": current},
                )
            setattr(self, prev_attr, current)

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
        self._analyze_output()

        if exit_code < 0:
            return exit_code, output

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

                if cont_exit < 0:
                    return cont_exit, output

                summary = extract_coding_summary(output)
                if summary is not None or cont_exit != 0:
                    break

        return exit_code, output

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
        self._analyze_output()

        self.accumulated_output += "\n" + output

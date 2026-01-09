from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _parse_positive_int(value: str | None, default: int | None) -> int | None:
    """Parse a positive integer environment value."""
    if not value:
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except ValueError:
        return default


class SessionLogger:
    """Create and update per-session log files."""

    def __init__(self, base_dir: Path | None = None, max_logs: int | None = None) -> None:
        env_dir = os.environ.get("CHAD_SESSION_LOG_DIR")
        resolved_dir = Path(base_dir) if base_dir else Path(env_dir) if env_dir else (
                Path(tempfile.gettempdir()) / "chad")
        resolved_dir.mkdir(parents=True, exist_ok=True)

        env_max = _parse_positive_int(os.environ.get("CHAD_SESSION_LOG_MAX_FILES"), 1000)
        self.base_dir = resolved_dir
        self.max_logs = max_logs if max_logs is not None else env_max

        self._prune_logs()

    def precreate_log(self) -> Path:
        """Pre-create an empty session log file and return its path.

        The file will be populated later when the task actually starts.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"chad_session_{timestamp}.json"
        filepath = self.base_dir / filename

        session_data = {
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
            "task_description": None,
            "project_path": None,
            "conversation": [],
            "verification_attempts": [],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        self._prune_logs()
        return filepath

    def initialize_log(
        self,
        filepath: Path,
        *,
        task_description: str,
        project_path: str,
        coding_account: str,
        coding_provider: str,
    ) -> None:
        """Initialize a pre-created log file with task details."""
        session_data = {
            "timestamp": datetime.now().isoformat(),
            "task_description": task_description,
            "project_path": project_path,
            "coding": {
                "account": coding_account,
                "provider": coding_provider,
            },
            "status": "running",
            "success": None,
            "completion_reason": None,
            "conversation": [],
            "verification_attempts": [],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

    def create_log(
        self,
        *,
        task_description: str,
        project_path: str,
        coding_account: str,
        coding_provider: str,
    ) -> Path:
        """Create a new session log and return its path."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"chad_session_{timestamp}.json"
        filepath = self.base_dir / filename

        session_data = {
            "timestamp": datetime.now().isoformat(),
            "task_description": task_description,
            "project_path": project_path,
            "coding": {
                "account": coding_account,
                "provider": coding_provider,
            },
            "status": "running",
            "success": None,
            "completion_reason": None,
            "conversation": [],
            "verification_attempts": [],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        self._prune_logs()
        return filepath

    def update_log(
        self,
        filepath: Path,
        chat_history: Iterable,
        *,
        streaming_transcript: str | None = None,
        streaming_history: list[tuple[str, str]] | None = None,
        success: bool | None = None,
        completion_reason: str | None = None,
        status: str = "running",
        verification_attempts: list | None = None,
        final_status: str | None = None,
    ) -> None:
        """Update an existing session log with new data.

        Args:
            filepath: Path to the session log file
            chat_history: Structured chat messages (for backward compatibility)
            streaming_transcript: Full streaming output from the session (flat text)
            streaming_history: Structured streaming output as (ai_name, chunk) tuples
            success: Whether the task succeeded
            completion_reason: Why the task ended
            status: Current status (running, completed, failed)
            final_status: Final task status text to surface failures
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                session_data = json.load(f)

            session_data["conversation"] = list(chat_history)
            session_data["status"] = status
            if verification_attempts is not None:
                session_data["verification_attempts"] = verification_attempts

            # Store structured streaming history with AI names preserved
            if streaming_history is not None:
                session_data["streaming_history"] = [
                    {"agent": agent, "content": content} for agent, content in streaming_history
                ]
                # Also create flat transcript for backward compatibility
                session_data["streaming_transcript"] = "".join(chunk for _, chunk in streaming_history)
            elif streaming_transcript is not None:
                session_data["streaming_transcript"] = streaming_transcript

            if success is not None:
                session_data["success"] = success
            if completion_reason is not None:
                session_data["completion_reason"] = completion_reason
            if final_status is not None:
                session_data["final_status"] = final_status

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)
        except Exception:
            # Logging failures shouldn't break the task flow.
            pass

    def _prune_logs(self) -> None:
        """Trim old session logs to keep the directory small."""
        limit = self.max_logs
        if limit is None or limit <= 0:
            return

        try:
            entries = [
                (entry.stat().st_mtime, entry)
                for entry in self.base_dir.iterdir()
                if entry.is_file()
                and entry.name.startswith("chad_session_")
                and entry.name.endswith(".json")
            ]
        except FileNotFoundError:
            return

        if len(entries) <= limit:
            return

        entries.sort(key=lambda item: item[0], reverse=True)
        for _, path_obj in entries[limit:]:
            try:
                path_obj.unlink(missing_ok=True)
            except Exception:
                continue

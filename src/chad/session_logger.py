from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable


class SessionLogger:
    """Create and update per-session log files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(tempfile.gettempdir()) / "chad"
        self.base_dir.mkdir(exist_ok=True)

    def precreate_log(self) -> Path:
        """Pre-create an empty session log file and return its path.

        The file will be populated later when the task actually starts.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chad_session_{timestamp}.json"
        filepath = self.base_dir / filename

        session_data = {
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
            "task_description": None,
            "project_path": None,
            "conversation": [],
        }

        with open(filepath, "w") as f:
            json.dump(session_data, f, indent=2)

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
        }

        with open(filepath, "w") as f:
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        }

        with open(filepath, "w") as f:
            json.dump(session_data, f, indent=2)

        return filepath

    def update_log(
        self,
        filepath: Path,
        chat_history: Iterable,
        *,
        streaming_transcript: str | None = None,
        success: bool | None = None,
        completion_reason: str | None = None,
        status: str = "running",
    ) -> None:
        """Update an existing session log with new data.

        Args:
            filepath: Path to the session log file
            chat_history: Structured chat messages (for backward compatibility)
            streaming_transcript: Full streaming output from the session
            success: Whether the task succeeded
            completion_reason: Why the task ended
            status: Current status (running, completed, failed)
        """
        try:
            with open(filepath) as f:
                session_data = json.load(f)

            session_data["conversation"] = list(chat_history)
            session_data["status"] = status

            # Store the full streaming transcript if provided
            if streaming_transcript is not None:
                session_data["streaming_transcript"] = streaming_transcript

            if success is not None:
                session_data["success"] = success
            if completion_reason is not None:
                session_data["completion_reason"] = completion_reason

            with open(filepath, "w") as f:
                json.dump(session_data, f, indent=2)
        except Exception:
            # Logging failures shouldn't break the task flow.
            pass

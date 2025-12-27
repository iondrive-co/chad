"""Investigation report management for structured debugging workflows.

This module provides tools for agents to track their investigation process
when debugging issues. Reports are stored as JSON files in /tmp/chad/investigations/
and are automatically kept valid through schema enforcement.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Type aliases
FindingSource = Literal["web_search", "unit_test", "tool_use", "code_review", "screenshot_analysis"]
FindingVerdict = Literal["supports", "rejects", "inconclusive"]
HypothesisStatus = Literal["active", "confirmed", "rejected"]
FindingStatus = Literal["open", "resolved", "rejected_approach"]


class InvestigationReport:
    """Manages a structured investigation report with auto-persistence."""

    BASE_DIR = Path(tempfile.gettempdir()) / "chad" / "investigations"

    def __init__(self, investigation_id: str | None = None) -> None:
        """Create a new report or load an existing one.

        Args:
            investigation_id: If provided, loads existing report. Otherwise creates new.
        """
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

        if investigation_id:
            self._id = investigation_id
            self._file_path = self.BASE_DIR / f"{investigation_id}.json"
            self._load()
        else:
            # Include microseconds to avoid collisions in rapid succession
            self._id = f"inv_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
            self._file_path = self.BASE_DIR / f"{self._id}.json"
            self._data = self._empty_report()
            self._save()

    def _empty_report(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": self._id,
            "file_path": str(self._file_path),
            "created_at": now,
            "updated_at": now,
            "request": {"description": "", "issue_id": ""},
            "screenshots": {"before": None, "after": None},
            "hypotheses": [],
            "findings": [],
            "rejected_approaches": [],
            "tests_designed": [],
            "test_framework_gaps": [],
            "fix": None,
            "post_incident_analysis": None,
        }

    def _load(self) -> None:
        if not self._file_path.exists():
            raise FileNotFoundError(f"Investigation {self._id} not found at {self._file_path}")
        with open(self._file_path) as f:
            self._data = json.load(f)

    def _save(self) -> None:
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self._file_path, "w") as f:
            json.dump(self._data, f, indent=2)

    @property
    def id(self) -> str:
        return self._id

    @property
    def file_path(self) -> Path:
        return self._file_path

    def set_request(self, description: str, issue_id: str = "") -> None:
        """Set the original request/task description."""
        self._data["request"] = {"description": description, "issue_id": issue_id}
        self._save()

    def add_hypothesis(self, description: str) -> int:
        """Add a theory about the cause. Returns the hypothesis ID."""
        hypothesis_id = len(self._data["hypotheses"]) + 1
        self._data["hypotheses"].append({
            "id": hypothesis_id,
            "description": description,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save()
        return hypothesis_id

    def update_hypothesis_status(self, hypothesis_id: int, status: HypothesisStatus) -> bool:
        """Update a hypothesis status. Returns True if found."""
        for h in self._data["hypotheses"]:
            if h["id"] == hypothesis_id:
                h["status"] = status
                self._save()
                return True
        return False

    def add_finding(
        self,
        source: FindingSource,
        content: str,
        hypothesis_id: int | None = None,
        verdict: FindingVerdict = "inconclusive",
        notes: str = "",
    ) -> int:
        """Add a finding from investigation. Returns the finding ID."""
        finding_id = len(self._data["findings"]) + 1
        self._data["findings"].append({
            "id": finding_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "content": content,
            "hypothesis_id": hypothesis_id,
            "verdict": verdict,
            "status": "open",
            "notes": notes,
        })
        self._save()
        return finding_id

    def update_finding_status(self, finding_id: int, status: FindingStatus) -> bool:
        """Update a finding's status. Returns True if found."""
        for f in self._data["findings"]:
            if f["id"] == finding_id:
                f["status"] = status
                self._save()
                return True
        return False

    def mark_approach_rejected(
        self, description: str, why_rejected: str, finding_ids: list[int]
    ) -> None:
        """Move a failed approach to rejected_approaches to reduce context pollution."""
        self._data["rejected_approaches"].append({
            "description": description,
            "why_rejected": why_rejected,
            "finding_ids": finding_ids,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        })
        # Mark associated findings as rejected_approach
        for f in self._data["findings"]:
            if f["id"] in finding_ids:
                f["status"] = "rejected_approach"
        self._save()

    def set_screenshots(self, before: str | None = None, after: str | None = None) -> None:
        """Set before/after screenshot paths."""
        if before is not None:
            self._data["screenshots"]["before"] = before
        if after is not None:
            self._data["screenshots"]["after"] = after
        self._save()

    def add_test_design(
        self, name: str, file_path: str, purpose: str, framework_gap: str | None = None
    ) -> None:
        """Record a test that was designed during investigation."""
        self._data["tests_designed"].append({
            "name": name,
            "file_path": file_path,
            "purpose": purpose,
        })
        if framework_gap and framework_gap not in self._data["test_framework_gaps"]:
            self._data["test_framework_gaps"].append(framework_gap)
        self._save()

    def record_fix(
        self, description: str, files_modified: list[str], test_changes: list[str]
    ) -> None:
        """Record the fix that was implemented."""
        self._data["fix"] = {
            "description": description,
            "files_modified": files_modified,
            "test_changes": test_changes,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def add_post_incident_analysis(self, analysis: str) -> None:
        """Add the hypothetical failure analysis."""
        self._data["post_incident_analysis"] = analysis
        self._save()

    def get_summary(self) -> dict[str, Any]:
        """Get a compact summary for context refresh."""
        active_hypotheses = [h for h in self._data["hypotheses"] if h["status"] == "active"]
        open_findings = [f for f in self._data["findings"] if f["status"] == "open"]
        confirmed_hypotheses = [h for h in self._data["hypotheses"] if h["status"] == "confirmed"]

        return {
            "investigation_id": self._id,
            "file_path": str(self._file_path),
            "request": self._data["request"]["description"],
            "issue_id": self._data["request"]["issue_id"],
            "screenshots": self._data["screenshots"],
            "active_hypotheses": len(active_hypotheses),
            "active_hypothesis_descriptions": [h["description"] for h in active_hypotheses],
            "confirmed_hypotheses": [h["description"] for h in confirmed_hypotheses],
            "open_findings": len(open_findings),
            "total_findings": len(self._data["findings"]),
            "rejected_approaches": len(self._data["rejected_approaches"]),
            "tests_designed": len(self._data["tests_designed"]),
            "has_fix": self._data["fix"] is not None,
            "has_post_incident_analysis": self._data["post_incident_analysis"] is not None,
            "is_complete": (
                self._data["fix"] is not None
                and self._data["post_incident_analysis"] is not None
            ),
        }

    def get_full_report(self) -> dict[str, Any]:
        """Get the complete report data."""
        return self._data.copy()

    @classmethod
    def list_investigations(cls) -> list[dict[str, str]]:
        """List all investigation reports."""
        cls.BASE_DIR.mkdir(parents=True, exist_ok=True)
        investigations = []
        for f in sorted(cls.BASE_DIR.glob("inv_*.json"), reverse=True):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                investigations.append({
                    "id": data["id"],
                    "file_path": str(f),
                    "created_at": data["created_at"],
                    "request": data["request"]["description"][:100],
                    "is_complete": data["fix"] is not None and data["post_incident_analysis"] is not None,
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return investigations

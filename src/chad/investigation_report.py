"""Hypothesis tracking for debugging workflows.

Agents record hypotheses with binary checks that would reject them.
Checks must be completed and results filed. A report is generated at completion.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HypothesisTracker:
    """Tracks hypotheses with binary rejection checks."""

    BASE_DIR = Path(tempfile.gettempdir()) / "chad" / "hypotheses"

    def __init__(self, tracker_id: str | None = None) -> None:
        """Create a new tracker or load an existing one."""
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

        if tracker_id:
            self._id = tracker_id
            self._file_path = self.BASE_DIR / f"{tracker_id}.json"
            self._load()
        else:
            self._id = f"hyp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
            self._file_path = self.BASE_DIR / f"{self._id}.json"
            self._data = self._empty_tracker()
            self._save()

    def _empty_tracker(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": self._id,
            "file_path": str(self._file_path),
            "created_at": now,
            "updated_at": now,
            "hypotheses": [],
            "screenshots": {"before": None, "after": None},
        }

    def _load(self) -> None:
        if not self._file_path.exists():
            raise FileNotFoundError(f"Tracker {self._id} not found")
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

    def add_hypothesis(self, description: str, checks: list[str]) -> int:
        """Add a hypothesis with binary rejection checks.

        Args:
            description: What you think is causing the issue
            checks: List of binary checks that would REJECT this hypothesis if they fail

        Returns:
            hypothesis_id (1-indexed)
        """
        hypothesis_id = len(self._data["hypotheses"]) + 1
        self._data["hypotheses"].append({
            "id": hypothesis_id,
            "description": description,
            "checks": [
                {"description": check, "passed": None, "notes": None}
                for check in checks
            ],
            "status": "pending",  # pending, confirmed, rejected
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save()
        return hypothesis_id

    def update_hypothesis(self, hypothesis_id: int, description: str | None = None,
                          add_checks: list[str] | None = None) -> bool:
        """Update an existing hypothesis description or add more checks."""
        for h in self._data["hypotheses"]:
            if h["id"] == hypothesis_id:
                if description:
                    h["description"] = description
                if add_checks:
                    for check in add_checks:
                        h["checks"].append({"description": check, "passed": None, "notes": None})
                self._save()
                return True
        return False

    def file_check_result(self, hypothesis_id: int, check_index: int,
                          passed: bool, notes: str = "") -> dict[str, Any]:
        """File the result of a binary check.

        Args:
            hypothesis_id: Which hypothesis (1-indexed)
            check_index: Which check (0-indexed)
            passed: True if the check passed (hypothesis survives), False if rejected
            notes: Optional notes about the result

        Returns:
            Status of the hypothesis after this check
        """
        for h in self._data["hypotheses"]:
            if h["id"] == hypothesis_id:
                if 0 <= check_index < len(h["checks"]):
                    h["checks"][check_index]["passed"] = passed
                    h["checks"][check_index]["notes"] = notes

                    # Update hypothesis status based on all checks
                    all_complete = all(c["passed"] is not None for c in h["checks"])
                    any_failed = any(c["passed"] is False for c in h["checks"])

                    if any_failed:
                        h["status"] = "rejected"
                    elif all_complete:
                        h["status"] = "confirmed"

                    self._save()
                    return {
                        "hypothesis_id": hypothesis_id,
                        "check_index": check_index,
                        "hypothesis_status": h["status"],
                        "checks_complete": sum(1 for c in h["checks"] if c["passed"] is not None),
                        "checks_total": len(h["checks"]),
                    }
        return {"error": f"Hypothesis {hypothesis_id} or check {check_index} not found"}

    def set_screenshot(self, label: str, path: str) -> None:
        """Set a screenshot path (before or after)."""
        if label in ("before", "after"):
            self._data["screenshots"][label] = path
            self._save()

    def get_report(self) -> dict[str, Any]:
        """Get the final report for the user."""
        hypotheses_summary = []
        for h in self._data["hypotheses"]:
            checks_summary = []
            for i, c in enumerate(h["checks"]):
                status = "✓" if c["passed"] else ("✗" if c["passed"] is False else "?")
                checks_summary.append(f"  [{status}] {c['description']}")
                if c["notes"]:
                    checks_summary.append(f"      → {c['notes']}")

            status_icon = {"pending": "⏳", "confirmed": "✓", "rejected": "✗"}.get(h["status"], "?")
            hypotheses_summary.append({
                "id": h["id"],
                "status": h["status"],
                "status_icon": status_icon,
                "description": h["description"],
                "checks": checks_summary,
            })

        pending = [h for h in self._data["hypotheses"] if h["status"] == "pending"]
        confirmed = [h for h in self._data["hypotheses"] if h["status"] == "confirmed"]
        rejected = [h for h in self._data["hypotheses"] if h["status"] == "rejected"]

        incomplete_checks = []
        for h in self._data["hypotheses"]:
            for i, c in enumerate(h["checks"]):
                if c["passed"] is None:
                    incomplete_checks.append(f"H{h['id']}.{i}: {c['description']}")

        return {
            "tracker_id": self._id,
            "file_path": str(self._file_path),
            "screenshots": self._data["screenshots"],
            "summary": {
                "total_hypotheses": len(self._data["hypotheses"]),
                "pending": len(pending),
                "confirmed": len(confirmed),
                "rejected": len(rejected),
            },
            "confirmed_hypotheses": [h["description"] for h in confirmed],
            "rejected_hypotheses": [h["description"] for h in rejected],
            "incomplete_checks": incomplete_checks,
            "all_checks_complete": len(incomplete_checks) == 0,
            "hypotheses": hypotheses_summary,
        }

    def get_pending_checks(self) -> list[dict[str, Any]]:
        """Get list of checks that still need results."""
        pending = []
        for h in self._data["hypotheses"]:
            for i, c in enumerate(h["checks"]):
                if c["passed"] is None:
                    pending.append({
                        "hypothesis_id": h["id"],
                        "hypothesis": h["description"],
                        "check_index": i,
                        "check": c["description"],
                    })
        return pending

    @classmethod
    def list_trackers(cls) -> list[dict[str, str]]:
        """List all hypothesis trackers."""
        cls.BASE_DIR.mkdir(parents=True, exist_ok=True)
        trackers = []
        for f in sorted(cls.BASE_DIR.glob("hyp_*.json"), reverse=True):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                confirmed = len([h for h in data["hypotheses"] if h["status"] == "confirmed"])
                trackers.append({
                    "id": data["id"],
                    "file_path": str(f),
                    "created_at": data["created_at"],
                    "hypotheses": len(data["hypotheses"]),
                    "confirmed": confirmed,
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return trackers[:10]  # Last 10


# Keep old class for backward compatibility during transition
InvestigationReport = HypothesisTracker

"""REST API client for Chad server."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


@dataclass
class Session:
    """Session data from API."""

    id: str
    name: str
    project_path: str | None
    active: bool
    has_worktree: bool
    has_changes: bool
    created_at: datetime
    last_activity: datetime


@dataclass
class Account:
    """Account data from API."""

    name: str
    provider: str
    model: str | None
    reasoning: str | None
    role: str | None
    ready: bool


@dataclass
class TaskStatus:
    """Task status from API."""

    task_id: str
    session_id: str
    status: str  # pending, running, completed, failed, cancelled
    progress: str | None
    result: str | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass
class WorktreeStatus:
    """Worktree status from API."""

    exists: bool
    path: str | None
    branch: str | None
    base_commit: str | None
    has_changes: bool


@dataclass
class DiffSummary:
    """Diff summary from API."""

    summary: str
    files_changed: int
    insertions: int
    deletions: int


@dataclass
class MergeResult:
    """Merge operation result."""

    success: bool
    message: str
    conflicts: list[dict[str, Any]] | None


class APIClient:
    """Client for Chad server REST API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize the API client.

        Args:
            base_url: Base URL of the Chad server
        """
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _url(self, path: str) -> str:
        """Build full URL for API path."""
        return f"{self.base_url}/api/v1{path}"

    def _parse_datetime(self, value: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    # Health check
    def health_check(self) -> dict[str, Any]:
        """Check server health."""
        resp = self._client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    # Sessions
    def create_session(
        self,
        project_path: str | None = None,
        name: str | None = None,
    ) -> Session:
        """Create a new session."""
        data = {}
        if project_path:
            data["project_path"] = project_path
        if name:
            data["name"] = name

        resp = self._client.post(self._url("/sessions"), json=data)
        resp.raise_for_status()
        return self._parse_session(resp.json())

    def list_sessions(self) -> list[Session]:
        """List all sessions."""
        resp = self._client.get(self._url("/sessions"))
        resp.raise_for_status()
        data = resp.json()
        return [self._parse_session(s) for s in data.get("sessions", [])]

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID."""
        resp = self._client.get(self._url(f"/sessions/{session_id}"))
        resp.raise_for_status()
        return self._parse_session(resp.json())

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        resp = self._client.delete(self._url(f"/sessions/{session_id}"))
        resp.raise_for_status()

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        """Request cancellation of the current task in a session."""
        resp = self._client.post(self._url(f"/sessions/{session_id}/cancel"))
        resp.raise_for_status()
        return resp.json()

    def _parse_session(self, data: dict) -> Session:
        """Parse session response data."""
        return Session(
            id=data["id"],
            name=data["name"],
            project_path=data.get("project_path"),
            active=data.get("active", False),
            has_worktree=data.get("has_worktree", False),
            has_changes=data.get("has_changes", False),
            created_at=self._parse_datetime(data["created_at"]),
            last_activity=self._parse_datetime(data["last_activity"]),
        )

    # Accounts
    def list_accounts(self) -> list[Account]:
        """List all configured accounts."""
        resp = self._client.get(self._url("/accounts"))
        resp.raise_for_status()
        data = resp.json()
        return [self._parse_account(a) for a in data.get("accounts", [])]

    def get_account(self, name: str) -> Account:
        """Get an account by name."""
        resp = self._client.get(self._url(f"/accounts/{name}"))
        resp.raise_for_status()
        return self._parse_account(resp.json())

    def delete_account(self, name: str) -> None:
        """Delete an account."""
        resp = self._client.delete(self._url(f"/accounts/{name}"))
        resp.raise_for_status()

    def set_account_model(self, name: str, model: str) -> Account:
        """Set the model for an account."""
        resp = self._client.put(
            self._url(f"/accounts/{name}/model"),
            json={"model": model},
        )
        resp.raise_for_status()
        return self._parse_account(resp.json())

    def set_account_reasoning(self, name: str, reasoning: str) -> Account:
        """Set the reasoning level for an account."""
        resp = self._client.put(
            self._url(f"/accounts/{name}/reasoning"),
            json={"reasoning": reasoning},
        )
        resp.raise_for_status()
        return self._parse_account(resp.json())

    def set_account_role(self, name: str, role: str) -> Account:
        """Assign a role to an account."""
        resp = self._client.put(
            self._url(f"/accounts/{name}/role"),
            json={"role": role},
        )
        resp.raise_for_status()
        return self._parse_account(resp.json())

    def get_account_models(self, name: str) -> list[str]:
        """Get available models for an account."""
        resp = self._client.get(self._url(f"/accounts/{name}/models"))
        resp.raise_for_status()
        return resp.json().get("models", [])

    def _parse_account(self, data: dict) -> Account:
        """Parse account response data."""
        return Account(
            name=data["name"],
            provider=data["provider"],
            model=data.get("model"),
            reasoning=data.get("reasoning"),
            role=data.get("role"),
            ready=data.get("ready", False),
        )

    # Tasks
    def start_task(
        self,
        session_id: str,
        project_path: str,
        task_description: str,
        coding_agent: str,
        coding_model: str | None = None,
        coding_reasoning: str | None = None,
        verification_agent: str | None = None,
        verification_model: str | None = None,
        verification_reasoning: str | None = None,
        target_branch: str | None = None,
    ) -> TaskStatus:
        """Start a new coding task."""
        data = {
            "project_path": project_path,
            "task_description": task_description,
            "coding_agent": coding_agent,
        }
        if coding_model:
            data["coding_model"] = coding_model
        if coding_reasoning:
            data["coding_reasoning"] = coding_reasoning
        if verification_agent:
            data["verification_agent"] = verification_agent
        if verification_model:
            data["verification_model"] = verification_model
        if verification_reasoning:
            data["verification_reasoning"] = verification_reasoning
        if target_branch:
            data["target_branch"] = target_branch

        resp = self._client.post(
            self._url(f"/sessions/{session_id}/tasks"),
            json=data,
        )
        resp.raise_for_status()
        return self._parse_task_status(resp.json())

    def get_task_status(self, session_id: str, task_id: str) -> TaskStatus:
        """Get the status of a task."""
        resp = self._client.get(self._url(f"/sessions/{session_id}/tasks/{task_id}"))
        resp.raise_for_status()
        return self._parse_task_status(resp.json())

    def _parse_task_status(self, data: dict) -> TaskStatus:
        """Parse task status response data."""
        return TaskStatus(
            task_id=data["task_id"],
            session_id=data["session_id"],
            status=data["status"],
            progress=data.get("progress"),
            result=data.get("result"),
            started_at=self._parse_datetime(data.get("started_at")),
            completed_at=self._parse_datetime(data.get("completed_at")),
        )

    # Worktree
    def create_worktree(self, session_id: str) -> WorktreeStatus:
        """Create a worktree for a session."""
        resp = self._client.post(self._url(f"/sessions/{session_id}/worktree"))
        resp.raise_for_status()
        return self._parse_worktree_status(resp.json())

    def get_worktree_status(self, session_id: str) -> WorktreeStatus:
        """Get worktree status for a session."""
        resp = self._client.get(self._url(f"/sessions/{session_id}/worktree"))
        resp.raise_for_status()
        return self._parse_worktree_status(resp.json())

    def get_diff_summary(self, session_id: str) -> DiffSummary:
        """Get diff summary for a session's worktree."""
        resp = self._client.get(self._url(f"/sessions/{session_id}/worktree/diff"))
        resp.raise_for_status()
        data = resp.json()
        return DiffSummary(
            summary=data["summary"],
            files_changed=data["files_changed"],
            insertions=data["insertions"],
            deletions=data["deletions"],
        )

    def get_full_diff(self, session_id: str) -> dict[str, Any]:
        """Get full diff with file details for a session's worktree."""
        resp = self._client.get(self._url(f"/sessions/{session_id}/worktree/diff/full"))
        resp.raise_for_status()
        return resp.json()

    def merge_worktree(
        self,
        session_id: str,
        target_branch: str | None = None,
    ) -> MergeResult:
        """Merge worktree changes to target branch."""
        data = {}
        if target_branch:
            data["target_branch"] = target_branch

        resp = self._client.post(
            self._url(f"/sessions/{session_id}/worktree/merge"),
            json=data,
        )
        resp.raise_for_status()
        result = resp.json()
        return MergeResult(
            success=result["success"],
            message=result["message"],
            conflicts=result.get("conflicts"),
        )

    def reset_worktree(self, session_id: str) -> dict[str, Any]:
        """Reset worktree to original state."""
        resp = self._client.post(self._url(f"/sessions/{session_id}/worktree/reset"))
        resp.raise_for_status()
        return resp.json()

    def delete_worktree(self, session_id: str) -> None:
        """Delete a session's worktree."""
        resp = self._client.delete(self._url(f"/sessions/{session_id}/worktree"))
        resp.raise_for_status()

    def _parse_worktree_status(self, data: dict) -> WorktreeStatus:
        """Parse worktree status response data."""
        return WorktreeStatus(
            exists=data["exists"],
            path=data.get("path"),
            branch=data.get("branch"),
            base_commit=data.get("base_commit"),
            has_changes=data.get("has_changes", False),
        )

    # Config
    def get_verification_settings(self) -> dict[str, Any]:
        """Get verification settings."""
        resp = self._client.get(self._url("/config/verification"))
        resp.raise_for_status()
        return resp.json()

    def update_verification_settings(
        self,
        enabled: bool | None = None,
        auto_run: bool | None = None,
    ) -> dict[str, Any]:
        """Update verification settings."""
        data = {}
        if enabled is not None:
            data["enabled"] = enabled
        if auto_run is not None:
            data["auto_run"] = auto_run

        resp = self._client.put(self._url("/config/verification"), json=data)
        resp.raise_for_status()
        return resp.json()

    def get_cleanup_settings(self) -> dict[str, Any]:
        """Get cleanup settings."""
        resp = self._client.get(self._url("/config/cleanup"))
        resp.raise_for_status()
        return resp.json()

    def update_cleanup_settings(
        self,
        cleanup_days: int | None = None,
        auto_cleanup: bool | None = None,
    ) -> dict[str, Any]:
        """Update cleanup settings."""
        data = {}
        if cleanup_days is not None:
            data["cleanup_days"] = cleanup_days
        if auto_cleanup is not None:
            data["auto_cleanup"] = auto_cleanup

        resp = self._client.put(self._url("/config/cleanup"), json=data)
        resp.raise_for_status()
        return resp.json()

    def get_preferences(self) -> dict[str, Any]:
        """Get user preferences."""
        resp = self._client.get(self._url("/config/preferences"))
        resp.raise_for_status()
        return resp.json()

    def update_preferences(
        self,
        last_project_path: str | None = None,
        dark_mode: bool | None = None,
    ) -> dict[str, Any]:
        """Update user preferences."""
        data = {}
        if last_project_path is not None:
            data["last_project_path"] = last_project_path
        if dark_mode is not None:
            data["dark_mode"] = dark_mode

        resp = self._client.put(self._url("/config/preferences"), json=data)
        resp.raise_for_status()
        return resp.json()

    # Providers
    def list_providers(self) -> list[dict[str, Any]]:
        """List all supported provider types."""
        resp = self._client.get(self._url("/providers"))
        resp.raise_for_status()
        return resp.json().get("providers", [])

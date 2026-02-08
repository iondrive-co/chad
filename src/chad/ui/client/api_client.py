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


@dataclass
class Preferences:
    """User preferences from API."""

    last_project_path: str | None
    dark_mode: bool
    ui_mode: str


@dataclass
class CleanupSettings:
    """Cleanup settings from API."""

    retention_days: int
    auto_cleanup: bool


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

    # Status
    def get_status(self) -> dict[str, Any]:
        """Get server status including health, version, and uptime."""
        resp = self._client.get(f"{self.base_url}/status")
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

    def create_account(self, name: str, provider: str) -> Account:
        """Register an account after OAuth authentication.

        Args:
            name: Account name
            provider: Provider type (anthropic, openai, etc.)

        Returns:
            The created Account
        """
        resp = self._client.post(
            self._url("/accounts"),
            json={"name": name, "provider": provider},
        )
        resp.raise_for_status()
        return self._parse_account(resp.json())

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
        terminal_rows: int | None = None,
        terminal_cols: int | None = None,
        screenshots: list[str] | None = None,
        override_exploration_prompt: str | None = None,
        override_implementation_prompt: str | None = None,
    ) -> TaskStatus:
        """Start a new coding task.

        Args:
            terminal_rows: Terminal height in rows (for PTY sizing)
            terminal_cols: Terminal width in columns (for PTY sizing)
            screenshots: Optional list of screenshot file paths for agent reference
            override_exploration_prompt: User-edited exploration prompt override
            override_implementation_prompt: User-edited implementation prompt override
        """
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
        if terminal_rows:
            data["terminal_rows"] = terminal_rows
        if terminal_cols:
            data["terminal_cols"] = terminal_cols
        if screenshots:
            data["screenshots"] = screenshots
        if override_exploration_prompt:
            data["override_exploration_prompt"] = override_exploration_prompt
        if override_implementation_prompt:
            data["override_implementation_prompt"] = override_implementation_prompt

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

    def get_cleanup_settings(self) -> CleanupSettings:
        """Get cleanup settings."""
        resp = self._client.get(self._url("/config/cleanup"))
        resp.raise_for_status()
        data = resp.json()
        return CleanupSettings(
            retention_days=data.get("cleanup_days", 7),
            auto_cleanup=data.get("auto_cleanup", True),
        )

    def set_cleanup_settings(
        self,
        retention_days: int | None = None,
        auto_cleanup: bool | None = None,
    ) -> CleanupSettings:
        """Update cleanup settings."""
        data = {}
        if retention_days is not None:
            data["cleanup_days"] = retention_days
        if auto_cleanup is not None:
            data["auto_cleanup"] = auto_cleanup

        resp = self._client.put(self._url("/config/cleanup"), json=data)
        resp.raise_for_status()
        result = resp.json()
        return CleanupSettings(
            retention_days=result.get("cleanup_days", 7),
            auto_cleanup=result.get("auto_cleanup", True),
        )

    def get_preferences(self) -> "Preferences":
        """Get user preferences."""
        resp = self._client.get(self._url("/config/preferences"))
        resp.raise_for_status()
        data = resp.json()
        return Preferences(
            last_project_path=data.get("last_project_path"),
            dark_mode=data.get("dark_mode", True),
            ui_mode=data.get("ui_mode", "gradio"),
        )

    def set_preferences(
        self,
        last_project_path: str | None = None,
        dark_mode: bool | None = None,
        ui_mode: str | None = None,
    ) -> "Preferences":
        """Update user preferences."""
        data = {}
        if last_project_path is not None:
            data["last_project_path"] = last_project_path
        if dark_mode is not None:
            data["dark_mode"] = dark_mode
        if ui_mode is not None:
            data["ui_mode"] = ui_mode

        resp = self._client.put(self._url("/config/preferences"), json=data)
        resp.raise_for_status()
        result = resp.json()
        return Preferences(
            last_project_path=result.get("last_project_path"),
            dark_mode=result.get("dark_mode", True),
            ui_mode=result.get("ui_mode", "gradio"),
        )

    # Providers
    def list_providers(self) -> list[dict[str, Any]]:
        """List all supported provider types."""
        resp = self._client.get(self._url("/providers"))
        resp.raise_for_status()
        return resp.json().get("providers", [])

    # Verification Agent
    def get_verification_agent(self) -> str | None:
        """Get the account configured as verification agent."""
        resp = self._client.get(self._url("/config/verification-agent"))
        resp.raise_for_status()
        return resp.json().get("account_name")

    def set_verification_agent(self, account_name: str | None) -> str | None:
        """Set or clear the verification agent account.

        Args:
            account_name: Account name to set, or None to clear

        Returns:
            The account name that was set (or None if cleared)
        """
        resp = self._client.put(
            self._url("/config/verification-agent"),
            json={"account_name": account_name},
        )
        resp.raise_for_status()
        return resp.json().get("account_name")

    def get_preferred_verification_model(self) -> str | None:
        """Get the preferred model for verification."""
        resp = self._client.get(self._url("/config/preferred-verification-model"))
        resp.raise_for_status()
        return resp.json().get("model")

    def set_preferred_verification_model(self, model: str | None) -> str | None:
        """Set or clear the preferred verification model.

        Args:
            model: Model name to set, or None to clear

        Returns:
            The model that was set (or None if cleared)
        """
        resp = self._client.put(
            self._url("/config/preferred-verification-model"),
            json={"model": model},
        )
        resp.raise_for_status()
        return resp.json().get("model")

    def get_provider_fallback_order(self) -> list[str]:
        """Get the ordered list of account names for auto-switching on quota exhaustion.

        Returns:
            List of account names in fallback priority order
        """
        resp = self._client.get(self._url("/config/provider-fallback-order"))
        resp.raise_for_status()
        return resp.json().get("order", [])

    def set_provider_fallback_order(self, account_names: list[str]) -> list[str]:
        """Set the ordered list of account names for auto-switching.

        Args:
            account_names: List of account names in fallback priority order

        Returns:
            The order that was set
        """
        resp = self._client.put(
            self._url("/config/provider-fallback-order"),
            json={"order": account_names},
        )
        resp.raise_for_status()
        return resp.json().get("order", [])

    def get_next_fallback_provider(self, current_account: str) -> str | None:
        """Get the next provider in the fallback order after the current one.

        Args:
            current_account: The currently active account name

        Returns:
            Next account name in fallback order, or None if no more fallbacks
        """
        order = self.get_provider_fallback_order()
        if not order:
            return None

        try:
            current_idx = order.index(current_account)
            if current_idx + 1 < len(order):
                return order[current_idx + 1]
        except ValueError:
            # Current account not in fallback order, return first in order
            if order:
                return order[0]

        return None

    def get_usage_switch_threshold(self) -> int:
        """Get the usage percentage threshold for auto-switching providers.

        Returns:
            Percentage threshold (0-100), defaults to 90
        """
        resp = self._client.get(self._url("/config/usage-switch-threshold"))
        resp.raise_for_status()
        return resp.json().get("threshold", 90)

    def set_usage_switch_threshold(self, threshold: int) -> int:
        """Set the usage percentage threshold for auto-switching providers.

        Args:
            threshold: Percentage threshold (0-100). Use 100 to disable
                      usage-based switching.

        Returns:
            The threshold that was set
        """
        resp = self._client.put(
            self._url("/config/usage-switch-threshold"),
            json={"threshold": threshold},
        )
        resp.raise_for_status()
        return resp.json().get("threshold", threshold)

    def get_mock_remaining_usage(self, account_name: str) -> float:
        """Get mock remaining usage for a mock provider account.

        Used for testing usage-based provider switching.

        Args:
            account_name: The mock account name

        Returns:
            Remaining usage as 0.0-1.0 (1.0 = full capacity remaining)
        """
        resp = self._client.get(self._url(f"/config/mock-remaining-usage/{account_name}"))
        resp.raise_for_status()
        return resp.json().get("remaining", 0.5)

    def set_mock_remaining_usage(self, account_name: str, remaining: float) -> float:
        """Set mock remaining usage for a mock provider account.

        Used for testing usage-based provider switching.

        Args:
            account_name: The mock account name
            remaining: Remaining usage as 0.0-1.0 (1.0 = full capacity remaining)

        Returns:
            The remaining usage that was set
        """
        resp = self._client.put(
            self._url("/config/mock-remaining-usage"),
            json={"account_name": account_name, "remaining": remaining},
        )
        resp.raise_for_status()
        return resp.json().get("remaining", remaining)

    def get_context_switch_threshold(self) -> int:
        """Get the context usage percentage threshold for auto-switching providers.

        Returns:
            Percentage threshold (0-100), defaults to 90
        """
        resp = self._client.get(self._url("/config/context-switch-threshold"))
        resp.raise_for_status()
        return resp.json().get("threshold", 90)

    def set_context_switch_threshold(self, threshold: int) -> int:
        """Set the context usage percentage threshold for auto-switching providers.

        Args:
            threshold: Percentage threshold (0-100). Use 100 to disable
                      context-based switching.

        Returns:
            The threshold that was set
        """
        resp = self._client.put(
            self._url("/config/context-switch-threshold"),
            json={"threshold": threshold},
        )
        resp.raise_for_status()
        return resp.json().get("threshold", threshold)

    def get_mock_context_remaining(self, account_name: str) -> float:
        """Get mock context remaining for a mock provider account.

        Used for testing context-based provider switching.

        Args:
            account_name: The mock account name

        Returns:
            Remaining context as 0.0-1.0 (1.0 = full context available)
        """
        resp = self._client.get(self._url(f"/config/mock-context-remaining/{account_name}"))
        resp.raise_for_status()
        return resp.json().get("remaining", 1.0)

    def set_mock_context_remaining(self, account_name: str, remaining: float) -> float:
        """Set mock context remaining for a mock provider account.

        Used for testing context-based provider switching.

        Args:
            account_name: The mock account name
            remaining: Remaining context as 0.0-1.0 (1.0 = full context available)

        Returns:
            The remaining context that was set
        """
        resp = self._client.put(
            self._url("/config/mock-context-remaining"),
            json={"account_name": account_name, "remaining": remaining},
        )
        resp.raise_for_status()
        return resp.json().get("remaining", remaining)

    def get_mock_run_duration_seconds(self, account_name: str) -> int:
        """Get mock run duration for a mock provider account.

        Used for testing handover timing.

        Args:
            account_name: The mock account name

        Returns:
            Run duration in seconds (0-3600)
        """
        resp = self._client.get(self._url(f"/config/mock-run-duration/{account_name}"))
        resp.raise_for_status()
        return resp.json().get("seconds", 0)

    def set_mock_run_duration_seconds(self, account_name: str, seconds: int) -> int:
        """Set mock run duration for a mock provider account.

        Used for testing handover timing.

        Args:
            account_name: The mock account name
            seconds: Run duration in seconds (0-3600)

        Returns:
            The run duration that was set
        """
        resp = self._client.put(
            self._url("/config/mock-run-duration"),
            json={"account_name": account_name, "seconds": seconds},
        )
        resp.raise_for_status()
        return resp.json().get("seconds", seconds)

    def get_max_verification_attempts(self) -> int:
        """Get the maximum number of verification attempts.

        Returns:
            Maximum attempts (default 5)
        """
        resp = self._client.get(self._url("/config/max-verification-attempts"))
        resp.raise_for_status()
        return resp.json().get("attempts", 5)

    def set_max_verification_attempts(self, attempts: int) -> int:
        """Set the maximum number of verification attempts.

        Args:
            attempts: Maximum attempts (1-20)

        Returns:
            The attempts that was set
        """
        resp = self._client.put(
            self._url("/config/max-verification-attempts"),
            json={"attempts": attempts},
        )
        resp.raise_for_status()
        return resp.json().get("attempts", attempts)

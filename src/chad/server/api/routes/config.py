"""Configuration management endpoints."""

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from chad.server.api.schemas import (
    VerificationSettings,
    CleanupSettings,
    UserPreferences,
    SlackSettingsResponse,
    SlackSettingsUpdate,
)
from chad.server.state import get_config_manager

router = APIRouter()


class VerificationSettingsUpdate(BaseModel):
    """Partial update model for verification settings."""

    enabled: bool | None = Field(default=None, description="Whether verification is enabled")


class VerificationAgentResponse(BaseModel):
    """Response for verification agent endpoint."""

    account_name: str | None = Field(description="Account assigned as verification agent")


class VerificationAgentUpdate(BaseModel):
    """Request to set verification agent."""

    account_name: str | None = Field(description="Account name to set as verification agent, or null to clear")


class PreferredVerificationModelResponse(BaseModel):
    """Response for preferred verification model endpoint."""

    model: str | None = Field(description="Preferred model for verification")


class PreferredVerificationModelUpdate(BaseModel):
    """Request to set preferred verification model."""

    model: str | None = Field(description="Model name to set, or null to clear")


class ActionSettingItem(BaseModel):
    """A single action setting entry."""

    event: str = Field(description="Event type: session_usage, weekly_usage, or context_usage")
    threshold: int = Field(ge=0, le=100, description="Percentage threshold (0-100)")
    action: str = Field(description="Action: notify, switch_provider, or await_reset")
    target_account: str | None = Field(default=None, description="Target account for switch_provider")


class ActionSettingsResponse(BaseModel):
    """Response for action settings endpoint."""

    settings: list[ActionSettingItem]


class ActionSettingsUpdate(BaseModel):
    """Request to update action settings."""

    settings: list[ActionSettingItem]


class MockRemainingUsageResponse(BaseModel):
    """Response for mock remaining usage endpoint."""

    account_name: str = Field(description="The mock account name")
    remaining: float = Field(description="Remaining usage as 0.0-1.0 (1.0 = full capacity)")


class MockRemainingUsageUpdate(BaseModel):
    """Request to set mock remaining usage."""

    account_name: str = Field(description="The mock account name")
    remaining: float = Field(
        ge=0.0,
        le=1.0,
        description="Remaining usage as 0.0-1.0 (1.0 = full capacity remaining)",
    )


class MockRunDurationResponse(BaseModel):
    """Response for mock run duration endpoint."""

    account_name: str = Field(description="The mock account name")
    seconds: int = Field(description="Mock run duration in seconds (0-3600)")


class MockRunDurationUpdate(BaseModel):
    """Request to set mock run duration."""

    account_name: str = Field(description="The mock account name")
    seconds: int = Field(
        ge=0,
        le=3600,
        description="Mock run duration in seconds (0-3600)",
    )


@router.get("/verification", response_model=VerificationSettings)
async def get_verification_settings() -> VerificationSettings:
    """Get verification agent settings.

    Default is enabled=True.
    """
    config_mgr = get_config_manager()
    enabled = config_mgr.get_runtime_verification_settings()
    return VerificationSettings(enabled=enabled)


@router.put("/verification", response_model=VerificationSettings)
async def update_verification_settings(request: VerificationSettingsUpdate) -> VerificationSettings:
    """Update verification flags (persisted across restarts).

    Supports partial updates; unspecified fields keep their previous values.
    The verification agent account is configured separately via /verification-agent.
    """
    config_mgr = get_config_manager()
    enabled = config_mgr.set_runtime_verification_settings(
        enabled=request.enabled,
    )
    return VerificationSettings(enabled=enabled)


@router.get("/cleanup", response_model=CleanupSettings)
async def get_cleanup_settings() -> CleanupSettings:
    """Get cleanup settings."""
    config_mgr = get_config_manager()
    cleanup_days = config_mgr.get_cleanup_days()

    return CleanupSettings(
        cleanup_days=cleanup_days,
        auto_cleanup=True,  # Always enabled on startup
    )


@router.put("/cleanup", response_model=CleanupSettings)
async def update_cleanup_settings(request: CleanupSettings) -> CleanupSettings:
    """Update cleanup settings."""
    config_mgr = get_config_manager()

    if request.cleanup_days >= 1:
        config_mgr.set_cleanup_days(request.cleanup_days)

    return CleanupSettings(
        cleanup_days=config_mgr.get_cleanup_days(),
        auto_cleanup=request.auto_cleanup,
    )


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences() -> UserPreferences:
    """Get user preferences."""
    config_mgr = get_config_manager()
    prefs = config_mgr.load_preferences()
    project_path = prefs.get("project_path") if prefs else None

    # If the saved project path doesn't exist on this machine (e.g. imported
    # config from another host), fall back to the server's launch directory.
    if project_path and not Path(project_path).is_dir():
        project_path = None
    if not project_path:
        project_path = os.getcwd()

    return UserPreferences(
        last_project_path=project_path,
        ui_mode=config_mgr.get_ui_mode(),
    )


@router.put("/preferences", response_model=UserPreferences)
async def update_preferences(request: UserPreferences) -> UserPreferences:
    """Update user preferences."""
    config_mgr = get_config_manager()

    if request.last_project_path is not None:
        config_mgr.save_preferences(request.last_project_path)

    if request.ui_mode:
        config_mgr.set_ui_mode(request.ui_mode)

    # Return the updated preferences
    prefs = config_mgr.load_preferences()

    return UserPreferences(
        last_project_path=prefs.get("project_path") if prefs else None,
        ui_mode=config_mgr.get_ui_mode(),
    )


@router.get("/verification-agent", response_model=VerificationAgentResponse)
async def get_verification_agent() -> VerificationAgentResponse:
    """Get the account configured as verification agent."""
    config_mgr = get_config_manager()
    account_name = config_mgr.get_verification_agent()

    return VerificationAgentResponse(account_name=account_name)


@router.put("/verification-agent", response_model=VerificationAgentResponse)
async def set_verification_agent(request: VerificationAgentUpdate) -> VerificationAgentResponse:
    """Set or clear the verification agent account."""
    config_mgr = get_config_manager()

    if request.account_name:
        config_mgr.set_verification_agent(request.account_name)
    else:
        config_mgr.set_verification_agent(None)

    return VerificationAgentResponse(account_name=request.account_name)


@router.get("/preferred-verification-model", response_model=PreferredVerificationModelResponse)
async def get_preferred_verification_model() -> PreferredVerificationModelResponse:
    """Get the preferred model for verification."""
    config_mgr = get_config_manager()
    model = config_mgr.get_preferred_verification_model()

    return PreferredVerificationModelResponse(model=model)


@router.put("/preferred-verification-model", response_model=PreferredVerificationModelResponse)
async def set_preferred_verification_model(
    request: PreferredVerificationModelUpdate,
) -> PreferredVerificationModelResponse:
    """Set or clear the preferred verification model."""
    config_mgr = get_config_manager()
    config_mgr.set_preferred_verification_model(request.model)

    return PreferredVerificationModelResponse(model=request.model)


@router.get("/action-settings", response_model=ActionSettingsResponse)
async def get_action_settings() -> ActionSettingsResponse:
    """Get usage action settings for all event types."""
    config_mgr = get_config_manager()
    settings = config_mgr.get_action_settings()
    return ActionSettingsResponse(settings=[ActionSettingItem(**s) for s in settings])


@router.put("/action-settings", response_model=ActionSettingsResponse)
async def set_action_settings(request: ActionSettingsUpdate) -> ActionSettingsResponse:
    """Set usage action settings."""
    config_mgr = get_config_manager()
    raw = [s.model_dump(exclude_none=True) for s in request.settings]
    try:
        config_mgr.set_action_settings(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ActionSettingsResponse(settings=request.settings)


@router.get("/mock-remaining-usage/{account_name}", response_model=MockRemainingUsageResponse)
async def get_mock_remaining_usage(account_name: str) -> MockRemainingUsageResponse:
    """Get mock remaining usage for a mock provider account.

    Used for testing usage-based provider switching without real providers.
    """
    config_mgr = get_config_manager()
    remaining = config_mgr.get_mock_remaining_usage(account_name)

    return MockRemainingUsageResponse(account_name=account_name, remaining=remaining)


@router.put("/mock-remaining-usage", response_model=MockRemainingUsageResponse)
async def set_mock_remaining_usage(
    request: MockRemainingUsageUpdate,
) -> MockRemainingUsageResponse:
    """Set mock remaining usage for a mock provider account.

    Used for testing usage-based provider switching without real providers.
    """
    config_mgr = get_config_manager()
    config_mgr.set_mock_remaining_usage(request.account_name, request.remaining)

    return MockRemainingUsageResponse(
        account_name=request.account_name,
        remaining=request.remaining,
    )


@router.get("/mock-run-duration/{account_name}", response_model=MockRunDurationResponse)
async def get_mock_run_duration(account_name: str) -> MockRunDurationResponse:
    """Get mock run duration for a mock provider account.

    Used for testing handover timing by extending mock task runtime.
    """
    config_mgr = get_config_manager()
    seconds = config_mgr.get_mock_run_duration_seconds(account_name)

    return MockRunDurationResponse(account_name=account_name, seconds=seconds)


@router.put("/mock-run-duration", response_model=MockRunDurationResponse)
async def set_mock_run_duration(
    request: MockRunDurationUpdate,
) -> MockRunDurationResponse:
    """Set mock run duration for a mock provider account.

    Used for testing handover timing by extending mock task runtime.
    """
    config_mgr = get_config_manager()
    config_mgr.set_mock_run_duration_seconds(request.account_name, request.seconds)

    return MockRunDurationResponse(
        account_name=request.account_name,
        seconds=request.seconds,
    )


class MaxVerificationAttemptsResponse(BaseModel):
    """Response for max verification attempts endpoint."""

    attempts: int = Field(description="Maximum number of verification attempts")


class MaxVerificationAttemptsUpdate(BaseModel):
    """Request to set max verification attempts."""

    attempts: int = Field(
        ge=1,
        le=20,
        description="Maximum number of verification attempts (1-20)",
    )


@router.get("/max-verification-attempts", response_model=MaxVerificationAttemptsResponse)
async def get_max_verification_attempts() -> MaxVerificationAttemptsResponse:
    """Get the maximum number of verification attempts.

    When verification fails, the system will retry up to this many times
    before giving up.
    """
    config_mgr = get_config_manager()
    attempts = config_mgr.get_max_verification_attempts()

    return MaxVerificationAttemptsResponse(attempts=attempts)


@router.put("/max-verification-attempts", response_model=MaxVerificationAttemptsResponse)
async def set_max_verification_attempts(
    request: MaxVerificationAttemptsUpdate,
) -> MaxVerificationAttemptsResponse:
    """Set the maximum number of verification attempts.

    Set lower values for faster failure, higher values for more retries.
    """
    config_mgr = get_config_manager()
    config_mgr.set_max_verification_attempts(request.attempts)

    return MaxVerificationAttemptsResponse(attempts=request.attempts)


@router.get("/slack", response_model=SlackSettingsResponse)
async def get_slack_settings() -> SlackSettingsResponse:
    """Get Slack integration settings."""
    config_mgr = get_config_manager()
    return SlackSettingsResponse(
        enabled=config_mgr.get_slack_enabled(),
        channel=config_mgr.get_slack_channel(),
        has_token=bool(config_mgr.get_slack_bot_token()),
    )


@router.put("/slack", response_model=SlackSettingsResponse)
async def set_slack_settings(request: SlackSettingsUpdate) -> SlackSettingsResponse:
    """Update Slack integration settings."""
    config_mgr = get_config_manager()
    if request.enabled is not None:
        config_mgr.set_slack_enabled(request.enabled)
    if request.channel is not None:
        config_mgr.set_slack_channel(request.channel)
    if request.bot_token is not None:
        config_mgr.set_slack_bot_token(request.bot_token)
    return SlackSettingsResponse(
        enabled=config_mgr.get_slack_enabled(),
        channel=config_mgr.get_slack_channel(),
        has_token=bool(config_mgr.get_slack_bot_token()),
    )


class ProjectSettingsResponse(BaseModel):
    """Response for project settings endpoint."""

    project_path: str = Field(description="Path to the project")
    project_type: str | None = Field(description="Detected project type")
    lint_command: str | None = Field(description="Lint command for the project")
    test_command: str | None = Field(description="Test command for the project")
    instructions_paths: list[str] = Field(default_factory=list, description="Paths to agent instruction/doc files")
    preview_port_mode: str = Field(default="disabled", description="Preview mode: disabled, auto, manual")
    preview_port: int | None = Field(default=None, description="Local port for preview (manual mode)")
    preview_command: str | None = Field(default=None, description="Command to start the app for preview")
    preferred_coding_agent: str | None = Field(default=None, description="Default coding agent for this project")
    autoconfigure_agent: str | None = Field(default=None, description="Agent used for autoconfigure")


class ProjectSettingsUpdate(BaseModel):
    """Request to update project settings."""

    project_path: str = Field(description="Path to the project")
    lint_command: str | None = Field(default=None, description="Lint command")
    test_command: str | None = Field(default=None, description="Test command")
    instructions_paths: list[str] | None = Field(default=None, description="Paths to agent instruction/doc files")
    preview_port_mode: str | None = Field(default=None, description="Preview mode: disabled, auto, manual")
    preview_port: int | None = Field(default=None, description="Local port for preview (manual mode)")
    preview_command: str | None = Field(default=None, description="Command to start the app for preview")
    preferred_coding_agent: str | None = Field(default=None, description="Default coding agent for this project")
    autoconfigure_agent: str | None = Field(default=None, description="Agent used for autoconfigure")


@router.get("/projects")
async def list_projects() -> list[ProjectSettingsResponse]:
    """List all configured projects."""
    from chad.util.project_setup import ProjectConfig

    config_mgr = get_config_manager()
    project_configs = config_mgr.list_project_configs()
    results = []
    for path_str, data in project_configs.items():
        try:
            config = ProjectConfig.from_dict(data)
        except (KeyError, TypeError):
            config = None

        if config:
            docs = config.docs
            results.append(ProjectSettingsResponse(
                project_path=path_str,
                project_type=config.project_type,
                lint_command=config.verification.lint_command,
                test_command=config.verification.test_command,
                instructions_paths=docs.instructions_paths if docs else [],
                preview_port_mode=config.preview_port_mode,
                preview_port=config.preview_port,
                preview_command=config.preview_command,
                preferred_coding_agent=config.preferred_coding_agent,
                autoconfigure_agent=config.autoconfigure_agent,
            ))
        else:
            results.append(ProjectSettingsResponse(
                project_path=path_str,
                project_type=data.get("project_type"),
                lint_command=None,
                test_command=None,
                instructions_paths=[],
                preview_port=None,
                preview_command=None,
                preferred_coding_agent=data.get("preferred_coding_agent"),
                autoconfigure_agent=data.get("autoconfigure_agent"),
            ))
    return results


@router.delete("/project")
async def delete_project(project_path: str) -> dict:
    """Delete a project configuration."""
    config_mgr = get_config_manager()
    path = Path(project_path).expanduser().resolve()
    config_mgr.delete_project_config(str(path))
    return {"deleted": True, "project_path": str(path)}


@router.get("/project", response_model=ProjectSettingsResponse)
async def get_project_settings(
    project_path: str | None = None,
) -> ProjectSettingsResponse:
    """Get project settings for a specific project path.

    Project settings include lint/test commands and documentation paths.
    If no settings exist, returns defaults (possibly with auto-detected values).
    """
    if not project_path:
        raise HTTPException(status_code=400, detail="project_path parameter required")

    from chad.util.project_setup import load_project_config, detect_project_type

    path = Path(project_path).expanduser().resolve()
    config = load_project_config(path)

    if config:
        return ProjectSettingsResponse(
            project_path=str(path),
            project_type=config.project_type,
            lint_command=config.verification.lint_command,
            test_command=config.verification.test_command,
            instructions_paths=config.docs.instructions_paths if config.docs else [],
            preview_port_mode=config.preview_port_mode,
            preview_port=config.preview_port,
            preview_command=config.preview_command,
            preferred_coding_agent=config.preferred_coding_agent,
            autoconfigure_agent=config.autoconfigure_agent,
        )

    # Return defaults for new project
    project_type = detect_project_type(path) if path.exists() else None
    return ProjectSettingsResponse(
        project_path=str(path),
        project_type=project_type,
        lint_command=None,
        test_command=None,
        instructions_paths=[],
        preview_port=None,
        preview_command=None,
        preferred_coding_agent=None,
    )


@router.put("/project", response_model=ProjectSettingsResponse)
async def set_project_settings(request: ProjectSettingsUpdate) -> ProjectSettingsResponse:
    """Update project settings.

    Saves lint/test commands and documentation paths for a project.
    Fields not included in the request body are left unchanged (using ellipsis sentinel).
    """
    from chad.util.project_setup import save_project_settings

    path = Path(request.project_path).expanduser().resolve()

    # Use model_fields_set to determine which fields were actually sent in the request.
    # Fields not sent should use ellipsis (...) to signal "leave unchanged".
    fields_set = request.model_fields_set

    config = save_project_settings(
        path,
        lint_command=request.lint_command if "lint_command" in fields_set else ...,
        test_command=request.test_command if "test_command" in fields_set else ...,
        instructions_paths=request.instructions_paths if "instructions_paths" in fields_set else ...,
        preview_port_mode=request.preview_port_mode if "preview_port_mode" in fields_set else ...,
        preview_port=request.preview_port if "preview_port" in fields_set else ...,
        preview_command=request.preview_command if "preview_command" in fields_set else ...,
        preferred_coding_agent=request.preferred_coding_agent if "preferred_coding_agent" in fields_set else ...,
        autoconfigure_agent=request.autoconfigure_agent if "autoconfigure_agent" in fields_set else ...,
    )

    return ProjectSettingsResponse(
        project_path=str(path),
        project_type=config.project_type,
        lint_command=config.verification.lint_command,
        test_command=config.verification.test_command,
        instructions_paths=config.docs.instructions_paths if config.docs else [],
        preview_port_mode=config.preview_port_mode,
        preview_port=config.preview_port,
        preview_command=config.preview_command,
        preferred_coding_agent=config.preferred_coding_agent,
        autoconfigure_agent=config.autoconfigure_agent,
    )


class PromptPreviewsResponse(BaseModel):
    """Response for prompt previews endpoint."""

    coding: str = Field(description="Coding prompt template with {task} placeholder")
    verification: str = Field(description="Verification prompt template")


@router.get("/prompt-previews", response_model=PromptPreviewsResponse)
async def get_prompt_previews(
    project_path: str | None = None,
) -> PromptPreviewsResponse:
    """Get prompt previews with project docs filled in but {task} as placeholder."""
    from chad.util.prompts import build_prompt_previews

    previews = build_prompt_previews(project_path)
    return PromptPreviewsResponse(
        coding=previews.coding,
        verification=previews.verification,
    )


# ── Config Export / Import ──


@router.get("/export")
async def export_config() -> JSONResponse:
    """Export the full config for transfer to another machine.

    The exported data contains encrypted API keys (not plaintext),
    so it requires the same master password on the destination.
    """
    config_mgr = get_config_manager()
    data = config_mgr.export_config()
    return JSONResponse(content=data)


class ConfigImportRequest(BaseModel):
    """Request body for config import."""

    config: dict[str, Any] = Field(description="Full config dictionary from export")


PROVIDER_TO_TOOL_KEY: dict[str, str] = {
    "anthropic": "claude",
    "openai": "codex",
    "gemini": "gemini",
    "qwen": "qwen",
    "opencode": "opencode",
    "kimi": "kimi",
    "mistral": "vibe",
}


def _install_provider_tools(config_data: dict[str, Any]) -> dict[str, str]:
    """Install CLI binaries for every provider found in a config.

    Returns a dict mapping tool_key to error message for any that failed.
    """
    import logging

    from chad.util.installer import AIToolInstaller

    log = logging.getLogger("chad.config.import")
    accounts = config_data.get("accounts", {})
    seen_tools: set[str] = set()
    errors: dict[str, str] = {}

    for account_name, account in accounts.items():
        provider = account.get("provider", "") if isinstance(account, dict) else account
        tool_key = PROVIDER_TO_TOOL_KEY.get(provider)
        if not tool_key or tool_key in seen_tools:
            continue
        seen_tools.add(tool_key)

        log.info("Installing %s CLI for provider %s (account %s)...", tool_key, provider, account_name)
        installer = AIToolInstaller()
        ok, detail = installer.ensure_tool(tool_key)
        if ok:
            log.info("Installed %s: %s", tool_key, detail)
        else:
            log.warning("Failed to install %s: %s", tool_key, detail)
            errors[tool_key] = detail

    return errors


@router.post("/import")
async def import_config(request: ConfigImportRequest) -> JSONResponse:
    """Import a config exported from another machine.

    Replaces the current config entirely. Requires the same master
    password that was used on the source machine.  After importing,
    installs the CLI binaries for every provider in the config.
    """
    import asyncio

    config_mgr = get_config_manager()
    try:
        config_mgr.import_config(request.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    loop = asyncio.get_running_loop()
    install_errors = await loop.run_in_executor(
        None, _install_provider_tools, request.config
    )

    if install_errors:
        details = "; ".join(f"{k}: {v}" for k, v in install_errors.items())
        return JSONResponse(content={
            "ok": True,
            "message": f"Config imported. Some tools failed to install: {details}",
            "install_errors": install_errors,
        })

    return JSONResponse(content={"ok": True, "message": "Config imported successfully"})


# ── Project Autoconfigure ──


class AutoconfigureRequest(BaseModel):
    """Request to autoconfigure project settings."""

    project_path: str = Field(description="Path to the project")
    coding_agent: str = Field(description="Account name of the coding agent to use")


class AutoconfigureStartResponse(BaseModel):
    """Response when autoconfigure discovery starts."""

    job_id: str = Field(description="Job ID for polling")


class AutoconfigureResultResponse(BaseModel):
    """Response when autoconfigure discovery completes."""

    status: str = Field(description="Job status: running, completed, failed")
    settings: ProjectSettingsResponse | None = Field(
        default=None, description="Discovered settings (only when status=completed)"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    output: list[str] = Field(default_factory=list, description="Agent output lines so far")


@router.post("/project/autoconfigure", response_model=AutoconfigureStartResponse)
async def start_autoconfigure(request: AutoconfigureRequest) -> AutoconfigureStartResponse:
    """Start project autoconfiguration using a coding agent.

    Runs a lightweight one-shot agent query (no session, no worktree,
    no continuation) to discover lint/test commands, dev server port,
    and documentation files.
    """
    from chad.server.services.autoconfigure_service import start_autoconfigure as _start

    path = Path(request.project_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Invalid project path: {request.project_path}")

    config_mgr = get_config_manager()
    accounts = config_mgr.list_accounts()
    if request.coding_agent not in accounts:
        raise HTTPException(status_code=400, detail=f"Account '{request.coding_agent}' not found")

    provider = accounts[request.coding_agent]

    job_id = _start(
        provider=provider,
        account_name=request.coding_agent,
        project_path=path,
    )

    return AutoconfigureStartResponse(job_id=job_id)


@router.get("/project/autoconfigure/{job_id}", response_model=AutoconfigureResultResponse)
async def get_autoconfigure_result(job_id: str) -> AutoconfigureResultResponse:
    """Poll for autoconfigure result."""
    from chad.server.services.autoconfigure_service import get_job, cleanup_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Autoconfigure job not found")

    if job.status == "running":
        return AutoconfigureResultResponse(status="running", output=job.output_lines)

    if job.status == "failed":
        error = job.error
        output = list(job.output_lines)
        cleanup_job(job_id)
        return AutoconfigureResultResponse(status="failed", error=error, output=output)

    # Completed — return discovered settings for the frontend to save.
    discovered = job.result or {}
    output = list(job.output_lines)
    cleanup_job(job_id)

    return AutoconfigureResultResponse(
        status="completed",
        output=output,
        settings=ProjectSettingsResponse(
            project_path="",  # filled by frontend
            project_type=None,
            lint_command=discovered.get("lint_command"),
            test_command=discovered.get("test_command"),
            instructions_paths=discovered.get("instructions_paths", []),
            preview_port_mode="manual" if discovered.get("preview_port") else "disabled",
            preview_port=discovered.get("preview_port"),
            preview_command=discovered.get("preview_command"),
        ),
    )


@router.post("/project/autoconfigure/{job_id}/cancel")
async def cancel_autoconfigure(job_id: str) -> AutoconfigureResultResponse:
    """Cancel a running autoconfigure job."""
    from chad.server.services.autoconfigure_service import get_job, cleanup_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Autoconfigure job not found")

    job.cancel()
    cleanup_job(job_id)
    return AutoconfigureResultResponse(status="failed", error="Cancelled")

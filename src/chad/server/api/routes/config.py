"""Configuration management endpoints."""

from fastapi import APIRouter, HTTPException
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
    auto_run: bool | None = Field(default=None, description="Whether to auto-run verification after coding")


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

    Defaults are enabled=True, auto_run=True.
    """
    config_mgr = get_config_manager()
    enabled, auto_run = config_mgr.get_runtime_verification_settings()
    return VerificationSettings(enabled=enabled, auto_run=auto_run)


@router.put("/verification", response_model=VerificationSettings)
async def update_verification_settings(request: VerificationSettingsUpdate) -> VerificationSettings:
    """Update verification flags (persisted across restarts).

    Supports partial updates; unspecified fields keep their previous values.
    The verification agent account is configured separately via /verification-agent.
    """
    config_mgr = get_config_manager()
    enabled, auto_run = config_mgr.set_runtime_verification_settings(
        enabled=request.enabled,
        auto_run=request.auto_run,
    )
    return VerificationSettings(enabled=enabled, auto_run=auto_run)


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

    return UserPreferences(
        last_project_path=prefs.get("project_path") if prefs else None,
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
        has_signing_secret=bool(config_mgr.get_slack_signing_secret()),
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
    if request.signing_secret is not None:
        config_mgr.set_slack_signing_secret(request.signing_secret)
    return SlackSettingsResponse(
        enabled=config_mgr.get_slack_enabled(),
        channel=config_mgr.get_slack_channel(),
        has_token=bool(config_mgr.get_slack_bot_token()),
        has_signing_secret=bool(config_mgr.get_slack_signing_secret()),
    )


class ProjectSettingsResponse(BaseModel):
    """Response for project settings endpoint."""

    project_path: str = Field(description="Path to the project")
    project_type: str | None = Field(description="Detected project type")
    lint_command: str | None = Field(description="Lint command for the project")
    test_command: str | None = Field(description="Test command for the project")
    instructions_path: str | None = Field(description="Path to agent instructions file")
    architecture_path: str | None = Field(description="Path to architecture docs")


class ProjectSettingsUpdate(BaseModel):
    """Request to update project settings."""

    project_path: str = Field(description="Path to the project")
    lint_command: str | None = Field(default=None, description="Lint command")
    test_command: str | None = Field(default=None, description="Test command")
    instructions_path: str | None = Field(default=None, description="Agent instructions path")
    architecture_path: str | None = Field(default=None, description="Architecture docs path")


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

    from pathlib import Path
    from chad.util.project_setup import load_project_config, detect_project_type

    path = Path(project_path).expanduser().resolve()
    config = load_project_config(path)

    if config:
        return ProjectSettingsResponse(
            project_path=str(path),
            project_type=config.project_type,
            lint_command=config.verification.lint_command,
            test_command=config.verification.test_command,
            instructions_path=config.docs.instructions_path if config.docs else None,
            architecture_path=config.docs.architecture_path if config.docs else None,
        )

    # Return defaults for new project
    project_type = detect_project_type(path) if path.exists() else None
    return ProjectSettingsResponse(
        project_path=str(path),
        project_type=project_type,
        lint_command=None,
        test_command=None,
        instructions_path=None,
        architecture_path=None,
    )


@router.put("/project", response_model=ProjectSettingsResponse)
async def set_project_settings(request: ProjectSettingsUpdate) -> ProjectSettingsResponse:
    """Update project settings.

    Saves lint/test commands and documentation paths for a project.
    """
    from pathlib import Path
    from chad.util.project_setup import save_project_settings

    path = Path(request.project_path).expanduser().resolve()
    config = save_project_settings(
        path,
        lint_command=request.lint_command,
        test_command=request.test_command,
        instructions_path=request.instructions_path,
        architecture_path=request.architecture_path,
    )

    return ProjectSettingsResponse(
        project_path=str(path),
        project_type=config.project_type,
        lint_command=config.verification.lint_command,
        test_command=config.verification.test_command,
        instructions_path=config.docs.instructions_path if config.docs else None,
        architecture_path=config.docs.architecture_path if config.docs else None,
    )

"""Configuration management endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chad.server.api.schemas import (
    VerificationSettings,
    CleanupSettings,
    UserPreferences,
)
from chad.server.state import get_config_manager

router = APIRouter()


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


class ProviderFallbackOrderResponse(BaseModel):
    """Response for provider fallback order endpoint."""

    order: list[str] = Field(
        default_factory=list,
        description="Ordered list of account names for auto-switching on quota exhaustion",
    )


class ProviderFallbackOrderUpdate(BaseModel):
    """Request to set provider fallback order."""

    order: list[str] = Field(
        description="Ordered list of account names for auto-switching",
    )


class UsageSwitchThresholdResponse(BaseModel):
    """Response for usage switch threshold endpoint."""

    threshold: int = Field(
        description="Percentage threshold (0-100) for triggering provider switch based on usage",
    )


class UsageSwitchThresholdUpdate(BaseModel):
    """Request to set usage switch threshold."""

    threshold: int = Field(
        ge=0,
        le=100,
        description="Percentage threshold (0-100). Use 100 to disable usage-based switching.",
    )


@router.get("/verification", response_model=VerificationSettings)
async def get_verification_settings() -> VerificationSettings:
    """Get verification agent settings."""
    # Verification is always enabled; the actual account is set via role assignment
    # auto_run defaults to True (the UI controls this behavior)
    return VerificationSettings(
        enabled=True,
        auto_run=True,
    )


@router.put("/verification", response_model=VerificationSettings)
async def update_verification_settings(request: VerificationSettings) -> VerificationSettings:
    """Update verification agent settings.

    Note: The account used for verification is set via the account role
    assignment, not through this endpoint.
    """
    # VerificationSettings contains enabled/auto_run which are runtime flags
    # The actual verification agent account is set via role assignment
    # For now, just return the request as acknowledgement
    return request


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
        dark_mode=True,  # Default, not persisted in ConfigManager
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
        dark_mode=request.dark_mode,
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


@router.get("/provider-fallback-order", response_model=ProviderFallbackOrderResponse)
async def get_provider_fallback_order() -> ProviderFallbackOrderResponse:
    """Get the ordered list of provider accounts for auto-switching on quota exhaustion.

    When a provider runs out of credits/quota, the system will automatically
    switch to the next provider in this list.
    """
    config_mgr = get_config_manager()
    order = config_mgr.get_provider_fallback_order()

    return ProviderFallbackOrderResponse(order=order)


@router.put("/provider-fallback-order", response_model=ProviderFallbackOrderResponse)
async def set_provider_fallback_order(
    request: ProviderFallbackOrderUpdate,
) -> ProviderFallbackOrderResponse:
    """Set the ordered list of provider accounts for auto-switching.

    Accounts not in this list will not be used for automatic switching.
    """
    config_mgr = get_config_manager()
    config_mgr.set_provider_fallback_order(request.order)

    return ProviderFallbackOrderResponse(order=request.order)


@router.get("/usage-switch-threshold", response_model=UsageSwitchThresholdResponse)
async def get_usage_switch_threshold() -> UsageSwitchThresholdResponse:
    """Get the usage percentage threshold for auto-switching providers.

    When a provider reports usage above this percentage, the system will
    automatically switch to the next fallback provider.
    """
    config_mgr = get_config_manager()
    threshold = config_mgr.get_usage_switch_threshold()

    return UsageSwitchThresholdResponse(threshold=threshold)


@router.put("/usage-switch-threshold", response_model=UsageSwitchThresholdResponse)
async def set_usage_switch_threshold(
    request: UsageSwitchThresholdUpdate,
) -> UsageSwitchThresholdResponse:
    """Set the usage percentage threshold for auto-switching providers.

    Set to 100 to disable usage-based switching (only error-based switching).
    """
    config_mgr = get_config_manager()
    config_mgr.set_usage_switch_threshold(request.threshold)

    return UsageSwitchThresholdResponse(threshold=request.threshold)

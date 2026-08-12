"""Provider and account Pydantic schemas."""

from typing import Literal
from pydantic import BaseModel, Field


ProviderType = Literal["anthropic", "openai", "gemini", "qwen", "local", "mistral", "kimi", "mock"]
# Only CODING exists as an assignable role — the verification agent is
# configured via /config/verification-agent, not a role assignment.
RoleType = Literal["CODING"]


class ProviderInfo(BaseModel):
    """Information about a supported provider type."""

    type: ProviderType = Field(description="Provider type identifier")
    name: str = Field(description="Human-readable provider name")
    description: str = Field(description="Provider description")
    supports_reasoning: bool = Field(default=False, description="Whether provider supports reasoning levels")
    reasoning_levels: list[str] = Field(
        default_factory=list,
        description="Reasoning effort levels this provider supports (empty if none)",
    )


class ProviderListResponse(BaseModel):
    """Response model for listing supported providers."""

    providers: list[ProviderInfo] = Field(default_factory=list)


class AccountCreate(BaseModel):
    """Request model for adding a new account.

    Note: Actual credentials are handled via OAuth flow, not directly through API.
    """

    name: str = Field(
        description="Account name/identifier",
        min_length=1,
        max_length=64,
        # Account names become directory names (CLAUDE_CONFIG_DIR, codex/kimi
        # homes) — restrict to a safe charset so '../'-style names can't
        # escape ~/.chad/.
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    provider: ProviderType = Field(description="Provider type")


class AccountResponse(BaseModel):
    """Response model for account details."""

    name: str = Field(description="Account name/identifier")
    provider: ProviderType = Field(description="Provider type")
    model: str | None = Field(default=None, description="Currently selected model")
    reasoning: str | None = Field(default=None, description="Currently selected reasoning level")
    role: RoleType | None = Field(default=None, description="Assigned role if any")
    ready: bool = Field(default=False, description="Whether account is ready to use")


class AccountListResponse(BaseModel):
    """Response model for listing accounts."""

    accounts: list[AccountResponse] = Field(default_factory=list)
    total: int = Field(description="Total number of accounts")


class AccountUsage(BaseModel):
    """Response model for account usage statistics."""

    account_name: str
    provider: ProviderType
    session_usage_pct: float | None = Field(
        default=None, description="Session usage percentage (0-100), None if unavailable"
    )
    weekly_usage_pct: float | None = Field(
        default=None, description="Weekly usage percentage (0-100), None if unavailable"
    )
    session_reset_eta: str | None = Field(
        default=None, description="Human-readable time until session reset"
    )
    weekly_reset_eta: str | None = Field(
        default=None, description="Human-readable time until weekly reset"
    )
    usage_as_of: str | None = Field(
        default=None,
        description="ISO-8601 time the usage reading was sampled, for staleness display",
    )
    logged_out: bool = Field(
        default=False,
        description="Account has no usable credentials — usage is unknown until re-login",
    )


class AccountModelUpdate(BaseModel):
    """Request model for updating account model."""

    model: str = Field(description="Model name to set")


class AccountReasoningUpdate(BaseModel):
    """Request model for updating account reasoning level."""

    reasoning: str = Field(description="Reasoning level to set")


class AccountRoleUpdate(BaseModel):
    """Request model for updating account role."""

    role: RoleType | None = Field(default=None, description="Role to assign, or null to clear")


class AccountModelsResponse(BaseModel):
    """Response model for listing available models for an account."""

    account_name: str
    provider: ProviderType
    models: list[str] = Field(default_factory=list, description="Available model names")


class AccountDeleteResponse(BaseModel):
    """Response model for account deletion."""

    account_name: str
    deleted: bool = True
    message: str = "Account deleted successfully"


class AccountLoginRequest(BaseModel):
    """Request model for logging in / authorizing an account.

    For OAuth providers the api_key is ignored (a browser flow is launched).
    For API-key providers (mistral) the key is required.
    """

    api_key: str = Field(default="", description="API key for providers that require one")


class AccountLoginResponse(BaseModel):
    """Response model for an account login attempt."""

    account_name: str
    success: bool = Field(description="Whether the login was started/completed successfully")
    ready: bool = Field(description="Whether the account is now authenticated")
    message: str = Field(description="Human-readable status message")

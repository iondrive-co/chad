"""Provider and account Pydantic schemas."""

from typing import Literal
from pydantic import BaseModel, Field


ProviderType = Literal["anthropic", "openai", "gemini", "qwen", "mistral", "opencode", "kimi", "mock"]
RoleType = Literal["CODING", "VERIFICATION"]


class ProviderInfo(BaseModel):
    """Information about a supported provider type."""

    type: ProviderType = Field(description="Provider type identifier")
    name: str = Field(description="Human-readable provider name")
    description: str = Field(description="Provider description")
    supports_reasoning: bool = Field(default=False, description="Whether provider supports reasoning levels")


class ProviderListResponse(BaseModel):
    """Response model for listing supported providers."""

    providers: list[ProviderInfo] = Field(default_factory=list)


class AccountCreate(BaseModel):
    """Request model for adding a new account.

    Note: Actual credentials are handled via OAuth flow, not directly through API.
    """

    name: str = Field(description="Account name/identifier")
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


class AccountModelUpdate(BaseModel):
    """Request model for updating account model."""

    model: str = Field(description="Model name to set")


class AccountReasoningUpdate(BaseModel):
    """Request model for updating account reasoning level."""

    reasoning: str = Field(description="Reasoning level to set")


class AccountRoleUpdate(BaseModel):
    """Request model for updating account role."""

    role: RoleType = Field(description="Role to assign")


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

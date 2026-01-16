"""Provider and account Pydantic schemas."""

from typing import Literal
from pydantic import BaseModel, Field


ProviderType = Literal["anthropic", "openai", "gemini", "qwen", "mistral", "mock"]
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
    usage_text: str = Field(description="Formatted usage text for display")
    remaining_capacity: float = Field(ge=0.0, le=1.0, description="Remaining capacity as fraction (0.0-1.0)")


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

"""Provider and account management endpoints."""

from fastapi import APIRouter, HTTPException

from chad.server.api.schemas import (
    ProviderListResponse,
    ProviderInfo,
    AccountCreate,
    AccountResponse,
    AccountListResponse,
    AccountUsage,
    AccountModelUpdate,
    AccountReasoningUpdate,
    AccountRoleUpdate,
    AccountModelsResponse,
    AccountDeleteResponse,
    RoleType,
)
from chad.server.state import get_config_manager, get_model_catalog

router = APIRouter()


def _get_account_role(config_mgr, account_name: str) -> RoleType | None:
    """Get the role assigned to an account, if any."""
    role_assignments = config_mgr.list_role_assignments()
    for role, assigned_account in role_assignments.items():
        if assigned_account == account_name:
            return role
    return None


def _account_to_response(
    name: str,
    provider: str,
    config_mgr,
) -> AccountResponse:
    """Convert account info to AccountResponse."""
    model = config_mgr.get_account_model(name)
    reasoning = config_mgr.get_account_reasoning(name)
    role = _get_account_role(config_mgr, name)

    return AccountResponse(
        name=name,
        provider=provider,
        model=model if model != "default" else None,
        reasoning=reasoning if reasoning != "default" else None,
        role=role,
        ready=True,  # Accounts in config are ready
    )


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers() -> ProviderListResponse:
    """List all supported provider types."""
    providers = [
        ProviderInfo(
            type="anthropic",
            name="Anthropic (Claude Code)",
            description="Claude AI models via Claude Code CLI",
            supports_reasoning=False,
        ),
        ProviderInfo(
            type="openai",
            name="OpenAI (Codex)",
            description="OpenAI models via Codex CLI",
            supports_reasoning=True,
        ),
        ProviderInfo(
            type="gemini",
            name="Google (Gemini)",
            description="Google Gemini models",
            supports_reasoning=False,
        ),
        ProviderInfo(
            type="qwen",
            name="Alibaba (Qwen)",
            description="Qwen models via Qwen Code CLI",
            supports_reasoning=False,
        ),
        ProviderInfo(
            type="mistral",
            name="Mistral (Vibe)",
            description="Mistral models via Vibe CLI",
            supports_reasoning=False,
        ),
        ProviderInfo(
            type="opencode",
            name="OpenCode",
            description="OpenCode models via OpenCode CLI",
            supports_reasoning=False,
        ),
        ProviderInfo(
            type="kimi",
            name="Moonshot (Kimi Code)",
            description="Kimi models via Kimi Code CLI",
            supports_reasoning=False,
        ),
    ]
    return ProviderListResponse(providers=providers)


@router.get("/accounts", response_model=AccountListResponse)
async def list_accounts() -> AccountListResponse:
    """List all configured accounts."""
    config_mgr = get_config_manager()
    accounts_dict = config_mgr.list_accounts()

    accounts = [
        _account_to_response(name, provider, config_mgr)
        for name, provider in accounts_dict.items()
    ]

    return AccountListResponse(
        accounts=accounts,
        total=len(accounts),
    )


@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(request: AccountCreate) -> AccountResponse:
    """Register a new account after OAuth authentication.

    The UI handles the OAuth flow; this endpoint stores the account
    configuration after authentication succeeds.
    """
    config_mgr = get_config_manager()

    if config_mgr.has_account(request.name):
        raise HTTPException(
            status_code=409,
            detail=f"Account '{request.name}' already exists"
        )

    # Store account with empty API key (OAuth handles auth)
    config_mgr.store_account(
        account_name=request.name,
        provider=request.provider,
        api_key="",
        password="",  # Not used for OAuth accounts
    )

    return _account_to_response(request.name, request.provider, config_mgr)


@router.get("/accounts/{name}", response_model=AccountResponse)
async def get_account(name: str) -> AccountResponse:
    """Get details of a specific account."""
    config_mgr = get_config_manager()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    accounts_dict = config_mgr.list_accounts()
    provider = accounts_dict.get(name)

    return _account_to_response(name, provider, config_mgr)


@router.delete("/accounts/{name}", response_model=AccountDeleteResponse)
async def delete_account(name: str) -> AccountDeleteResponse:
    """Delete an account."""
    config_mgr = get_config_manager()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    config_mgr.delete_account(name)

    return AccountDeleteResponse(
        account_name=name,
        deleted=True,
        message=f"Account '{name}' deleted successfully",
    )


@router.put("/accounts/{name}/model", response_model=AccountResponse)
async def set_account_model(name: str, request: AccountModelUpdate) -> AccountResponse:
    """Set the model for an account."""
    config_mgr = get_config_manager()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    config_mgr.set_account_model(name, request.model)

    accounts_dict = config_mgr.list_accounts()
    provider = accounts_dict.get(name)

    return _account_to_response(name, provider, config_mgr)


@router.put("/accounts/{name}/reasoning", response_model=AccountResponse)
async def set_account_reasoning(name: str, request: AccountReasoningUpdate) -> AccountResponse:
    """Set the reasoning level for an account."""
    config_mgr = get_config_manager()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    config_mgr.set_account_reasoning(name, request.reasoning)

    accounts_dict = config_mgr.list_accounts()
    provider = accounts_dict.get(name)

    return _account_to_response(name, provider, config_mgr)


@router.put("/accounts/{name}/role", response_model=AccountResponse)
async def set_account_role(name: str, request: AccountRoleUpdate) -> AccountResponse:
    """Assign a role to an account."""
    config_mgr = get_config_manager()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    config_mgr.assign_role(name, request.role)

    accounts_dict = config_mgr.list_accounts()
    provider = accounts_dict.get(name)

    return _account_to_response(name, provider, config_mgr)


@router.get("/accounts/{name}/usage", response_model=AccountUsage)
async def get_account_usage(name: str) -> AccountUsage:
    """Get usage statistics for an account."""
    # Usage stats require provider-specific integrations
    raise HTTPException(
        status_code=501,
        detail="Usage stats not implemented in API - use the UI"
    )


@router.get("/accounts/{name}/models", response_model=AccountModelsResponse)
async def get_account_models(name: str) -> AccountModelsResponse:
    """Get available models for an account."""
    config_mgr = get_config_manager()
    model_catalog = get_model_catalog()

    if not config_mgr.has_account(name):
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")

    accounts_dict = config_mgr.list_accounts()
    provider = accounts_dict.get(name)

    models = model_catalog.get_models(provider, account_name=name)

    return AccountModelsResponse(
        account_name=name,
        provider=provider,
        models=models,
    )

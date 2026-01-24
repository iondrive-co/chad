"""Configuration Pydantic schemas."""

from pydantic import BaseModel, Field


class VerificationSettings(BaseModel):
    """Settings for verification agent."""

    enabled: bool = Field(default=True, description="Whether verification is enabled")
    auto_run: bool = Field(default=True, description="Whether to auto-run verification after coding")


class CleanupSettings(BaseModel):
    """Settings for automatic cleanup."""

    cleanup_days: int = Field(default=7, ge=1, description="Days to keep old sessions/logs")
    auto_cleanup: bool = Field(default=True, description="Whether to auto-cleanup on startup")


class UserPreferences(BaseModel):
    """User preferences."""

    last_project_path: str | None = Field(default=None, description="Last used project path")
    dark_mode: bool = Field(default=True, description="Whether dark mode is enabled")
    ui_mode: str = Field(default="gradio", description="UI mode: gradio or cli")

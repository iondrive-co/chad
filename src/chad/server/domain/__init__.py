"""Domain layer - re-exports core business logic from chad package."""

# Re-export from chad.util.providers
from chad.util.providers import (
    AIProvider,
    ClaudeCodeProvider,
    OpenAICodexProvider,
    GeminiCodeAssistProvider,
    QwenCodeProvider,
    MistralVibeProvider,
    MockProvider,
    ModelConfig,
    create_provider,
    parse_codex_output,
)

# Re-export from chad.util.git_worktree
from chad.util.git_worktree import (
    GitWorktreeManager,
    MergeConflict,
    ConflictHunk,
    FileDiff,
    DiffHunk,
    DiffLine,
)

# Re-export from chad.util.config_manager
from chad.util.config_manager import ConfigManager

# Re-export from chad.util.model_catalog
from chad.util.model_catalog import ModelCatalog

# Re-export from chad.util.prompts
from chad.util.prompts import (
    EXPLORATION_PROMPT,
    IMPLEMENTATION_PROMPT,
    CODING_AGENT_PROMPT,  # Legacy - use EXPLORATION_PROMPT + IMPLEMENTATION_PROMPT
    VERIFICATION_AGENT_PROMPT,
    SUMMARY_COMPLETION_PROMPT,
    build_exploration_prompt,
    build_implementation_prompt,
    build_coding_prompt,  # Legacy - use build_exploration_prompt + build_implementation_prompt
    get_verification_prompt,
    parse_verification_response,
    extract_coding_summary,
    extract_progress_update,
    check_verification_mentioned,
    get_summary_completion_prompt,
    CodingSummary,
    ProgressUpdate,
    VerificationParseError,
)

# Re-export from chad.util.event_log
from chad.util.event_log import (
    EventLog,
    SessionStartedEvent,
    SessionEndedEvent,
    UserMessageEvent,
    AssistantMessageEvent,
    ToolCallStartedEvent,
    ToolCallFinishedEvent,
    VerificationAttemptEvent,
)

# Re-export from chad.util.installer
from chad.util.installer import AIToolInstaller, DEFAULT_TOOLS_DIR

# Re-export from chad.util.cleanup
from chad.util.cleanup import (
    cleanup_old_worktrees,
    cleanup_old_logs,
    cleanup_old_screenshots,
    cleanup_temp_files,
    cleanup_on_startup,
    cleanup_on_shutdown,
)

# Re-export from chad.util.utils
from chad.util.utils import platform_path, safe_home

# Re-export from chad.util.process_registry
from chad.util.process_registry import ProcessRegistry

__all__ = [
    # Providers
    "AIProvider",
    "ClaudeCodeProvider",
    "OpenAICodexProvider",
    "GeminiCodeAssistProvider",
    "QwenCodeProvider",
    "MistralVibeProvider",
    "MockProvider",
    "ModelConfig",
    "create_provider",
    "parse_codex_output",
    # Git worktree
    "GitWorktreeManager",
    "MergeConflict",
    "ConflictHunk",
    "FileDiff",
    "DiffHunk",
    "DiffLine",
    # Config
    "ConfigManager",
    # Model catalog
    "ModelCatalog",
    # Prompts
    "EXPLORATION_PROMPT",
    "IMPLEMENTATION_PROMPT",
    "CODING_AGENT_PROMPT",
    "VERIFICATION_AGENT_PROMPT",
    "SUMMARY_COMPLETION_PROMPT",
    "build_exploration_prompt",
    "build_implementation_prompt",
    "build_coding_prompt",
    "get_verification_prompt",
    "parse_verification_response",
    "extract_coding_summary",
    "extract_progress_update",
    "check_verification_mentioned",
    "get_summary_completion_prompt",
    "CodingSummary",
    "ProgressUpdate",
    "VerificationParseError",
    # Event log
    "EventLog",
    "SessionStartedEvent",
    "SessionEndedEvent",
    "UserMessageEvent",
    "AssistantMessageEvent",
    "ToolCallStartedEvent",
    "ToolCallFinishedEvent",
    "VerificationAttemptEvent",
    # Installer
    "AIToolInstaller",
    "DEFAULT_TOOLS_DIR",
    # Cleanup
    "cleanup_old_worktrees",
    "cleanup_old_logs",
    "cleanup_old_screenshots",
    "cleanup_temp_files",
    "cleanup_on_startup",
    "cleanup_on_shutdown",
    # Utils
    "platform_path",
    "safe_home",
    # Process registry
    "ProcessRegistry",
]

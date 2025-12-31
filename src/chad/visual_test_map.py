"""Static mapping of UI components and source files to visual tests.

This module helps agents quickly determine:
1. Which screenshot component captures a UI element
2. Which tests cover that element
3. Which source files define it

AGENT USAGE
-----------
When modifying UI elements, search this file for keywords from the task:

    Example task: "Move the reasoning effort dropdown..."
    Search for: "reasoning" → finds REASONING_EFFORT entry
    Result: screenshot(tab="run", component="project-path"), tests=["TestCodingAgentLayout"]

UI_COMPONENT_MAP: Maps UI element names to screenshot params and test coverage.
VISUAL_TEST_MAP: Maps source files to test classes (for targeted test runs).
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class UIComponent:
    """Metadata for a UI component's visual test coverage."""
    tab: str                    # MCP screenshot tab parameter
    component: str | None       # MCP screenshot component parameter (None = full tab)
    tests: list[str]            # Test classes that verify this component
    source_file: str            # Primary source file defining this component
    keywords: list[str]         # Search terms agents might use to find this


# =============================================================================
# UI COMPONENT MAP - Agent-friendly lookup by UI element name
# =============================================================================
# Search this map using keywords from your task to find:
# - Which screenshot(tab, component) to use for before/after captures
# - Which test classes verify the component
#
# Example: Task mentions "preferred model" → look up PREFERRED_MODEL entry

UI_COMPONENT_MAP: dict[str, UIComponent] = {
    # --- Run Tab: Agent Selection Row ---
    "CODING_AGENT_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["coding agent", "agent dropdown", "agent selection"],
    ),
    "VERIFICATION_AGENT_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["verification agent", "verifier", "verification dropdown"],
    ),
    "PREFERRED_MODEL_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["preferred model", "model dropdown", "model selection", "coding model"],
    ),
    "VERIFICATION_MODEL_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=[
            "verification model",
            "verification preferred model",
            "verifier model",
            "verification model dropdown",
        ],
    ),
    "REASONING_EFFORT_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["reasoning effort", "reasoning dropdown", "effort level"],
    ),
    "VERIFICATION_REASONING_DROPDOWN": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=[
            "verification reasoning",
            "verification reasoning effort",
            "verifier reasoning",
            "verification reasoning dropdown",
        ],
    ),
    "PROJECT_PATH_INPUT": UIComponent(
        tab="run",
        component="project-path",
        tests=["TestCodingAgentLayout", "TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["project path", "directory", "working directory"],
    ),

    # --- Run Tab: Chat Interface ---
    "CHAT_INTERFACE": UIComponent(
        tab="run",
        component="agent-communication",
        tests=["TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["chat", "chatbot", "messages", "conversation", "agent communication"],
    ),
    "TASK_DESCRIPTION": UIComponent(
        tab="run",
        component="agent-communication",
        tests=["TestUIElements"],
        source_file="chad/web_ui.py",
        keywords=["task", "task description", "input", "prompt"],
    ),

    # --- Run Tab: Live View ---
    "LIVE_VIEW": UIComponent(
        tab="run",
        component="live-view",
        tests=["TestLiveViewFormat", "TestRealisticLiveContent", "TestLiveActivityFormat"],
        source_file="chad/web_ui.py",
        keywords=["live view", "activity", "stream", "output", "live activity"],
    ),

    # --- Providers Tab ---
    "PROVIDER_CARD": UIComponent(
        tab="providers",
        component="provider-card",
        tests=["TestProvidersTab", "TestProviderTwoColumnLayout"],
        source_file="chad/provider_ui.py",
        keywords=["provider card", "account card", "provider settings"],
    ),
    "PROVIDER_MODEL_DROPDOWN": UIComponent(
        tab="providers",
        component="provider-card",
        tests=["TestProvidersTab"],
        source_file="chad/provider_ui.py",
        keywords=["provider model", "default model", "account model"],
    ),
    "PROVIDER_REASONING_DROPDOWN": UIComponent(
        tab="providers",
        component="provider-card",
        tests=["TestProvidersTab"],
        source_file="chad/provider_ui.py",
        keywords=["provider reasoning", "account reasoning effort"],
    ),
    "PROVIDER_SUMMARY": UIComponent(
        tab="providers",
        component="provider-summary",
        tests=["TestProvidersTab", "TestProviderTwoColumnLayout"],
        source_file="chad/provider_ui.py",
        keywords=["provider summary", "all providers", "provider list"],
    ),
    "ADD_PROVIDER": UIComponent(
        tab="providers",
        component="add-provider",
        tests=["TestProvidersTab"],
        source_file="chad/provider_ui.py",
        keywords=["add provider", "new provider", "add account"],
    ),
}


def find_component(search_term: str) -> UIComponent | None:
    """Find a UI component by searching keywords.

    Args:
        search_term: Term to search for (e.g., "reasoning effort", "verification")

    Returns:
        UIComponent if found, None otherwise

    Example:
        >>> comp = find_component("reasoning effort")
        >>> print(f"screenshot(tab='{comp.tab}', component='{comp.component}')")
        screenshot(tab='run', component='project-path')
    """
    search_lower = search_term.lower()
    for name, component in UI_COMPONENT_MAP.items():
        if search_lower in name.lower():
            return component
        for keyword in component.keywords:
            if search_lower in keyword or keyword in search_lower:
                return component
    return None


def get_screenshot_params(search_term: str) -> tuple[str, str | None] | None:
    """Get MCP screenshot parameters for a UI element.

    Args:
        search_term: UI element to find (e.g., "preferred model")

    Returns:
        Tuple of (tab, component) for screenshot() call, or None if not found

    Example:
        >>> tab, comp = get_screenshot_params("verification agent")
        >>> # Use: mcp__chad-ui-playwright__screenshot(tab=tab, component=comp)
    """
    component = find_component(search_term)
    return (component.tab, component.component) if component else None


# =============================================================================
# FILE-TO-TEST MAP - For targeted test runs when modifying source files
# =============================================================================

VISUAL_TEST_MAP: dict[str, list[str]] = {
    # Provider UI - card rendering, deletion, model selection, role assignment
    "chad/provider_ui.py": [
        "TestProvidersTab",
        "TestDeleteProvider",
    ],
    # Main web UI - tabs, elements, live stream, task execution, two-column provider layout
    "chad/web_ui.py": [
        "TestUIElements",
        "TestReadyStatus",
        "TestCodingAgentLayout",
        "TestProvidersTab",
        "TestLiveActivityFormat",
        "TestTaskStatusHeader",
        "TestSubtaskTabs",
        "TestLiveViewFormat",
        "TestRealisticLiveContent",
        "TestNoStatusBox",
        "TestScreenshots",
        "TestProviderTwoColumnLayout",
    ],
    # Security manager - affects provider authentication display
    "chad/security.py": [
        "TestProvidersTab",
    ],
    # MCP playwright server - screenshot tools and UI automation
    "chad/mcp_playwright.py": [
        "TestScreenshots",
        "TestProvidersTab",
        "TestDeleteProvider",
    ],
    # Playwright test utilities - affects all visual test measurements
    "chad/ui_playwright_runner.py": [
        "TestDeleteProvider",
        "TestProvidersTab",
        "TestLiveViewFormat",
        "TestRealisticLiveContent",
        "TestScreenshots",
    ],
    # Providers - affects provider card display and model choices
    "chad/providers.py": [
        "TestProvidersTab",
        "TestReadyStatus",
    ],
    # Model catalog - affects model dropdown choices
    "chad/model_catalog.py": [
        "TestProvidersTab",
    ],
}


def get_tests_for_file(file_path: str) -> list[str]:
    """Get visual test class names that cover a source file.

    Args:
        file_path: Path to source file (absolute or relative, e.g. 'src/chad/provider_ui.py')

    Returns:
        List of test class names from test_ui_integration.py
    """
    # Normalize path to chad/filename.py format
    if "/src/chad/" in file_path:
        rel = "chad/" + file_path.split("/src/chad/")[-1]
    elif file_path.startswith("src/chad/"):
        rel = "chad/" + file_path[9:]
    elif file_path.startswith("chad/"):
        rel = file_path
    else:
        rel = file_path

    return list(VISUAL_TEST_MAP.get(rel, []))


def get_tests_for_files(file_paths: list[str]) -> list[str]:
    """Get visual test class names for multiple files (deduplicated).

    Args:
        file_paths: List of source file paths

    Returns:
        Deduplicated list of test class names
    """
    tests = set()
    for path in file_paths:
        tests.update(get_tests_for_file(path))
    return list(tests)

"""Static mapping of source files to visual tests.

This module provides a registry mapping source files to the visual test
classes that cover them. Agents use this to run only relevant visual tests
when modifying specific files.

To add coverage for a new file, add an entry to VISUAL_TEST_MAP.
"""

from __future__ import annotations

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

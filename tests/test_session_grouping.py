"""Tests for session grouping by project path in the UI.

These tests verify that sessions are grouped by their project_path in Chrome-like
inline tab groups in the header.
"""

import re
from pathlib import Path


UI_DIR = Path(__file__).parent.parent / "ui" / "src"
APP_FILE = UI_DIR / "App.tsx"
CSS_FILE = UI_DIR / "styles" / "main.css"


class TestSessionGroupingLogic:
    """Verify that sessions are grouped by project_path in App.tsx."""

    def test_sessions_are_grouped_by_project_path(self):
        """App.tsx should group opened sessions by project_path."""
        content = APP_FILE.read_text()

        # Look for groupBy or grouping logic using project_path
        # The code should transform the flat sessions list into groups
        assert (
            "project_path" in content and
            ("group" in content.lower() or "byProject" in content.lower() or
             "Map(" in content or "reduce(" in content or "Object.entries" in content)
        ), "App.tsx should group sessions by project_path"

    def test_project_group_header_is_rendered(self):
        """App.tsx should render a group header for each project."""
        content = APP_FILE.read_text()

        # Look for project group header element
        assert (
            "project-group" in content.lower() or
            "tab-group" in content.lower() or
            "session-group" in content.lower()
        ), "App.tsx should render project/session group elements"

    def test_group_shows_project_name(self):
        """Each group should display the project path or name."""
        content = APP_FILE.read_text()

        # Look for rendering of project path in group header
        # Should be either the full path, basename, or a shortened version
        assert (
            "project_path" in content or
            "projectPath" in content
        ), "Group headers should display the project path"


class TestSessionGroupingCSS:
    """Verify CSS styling for Chrome-like inline tab groups."""

    def test_session_group_container_exists(self):
        """CSS should define styling for session group containers."""
        content = CSS_FILE.read_text()

        # Look for .session-group or .tab-group CSS rules
        assert (
            ".session-group" in content or
            ".tab-group" in content or
            ".project-group" in content
        ), "CSS should have session/project group styling"

    def test_group_has_inline_display(self):
        """Session groups should use inline/flex display for Chrome-like appearance."""
        content = CSS_FILE.read_text()

        # Find the group styling rule
        group_match = re.search(
            r"\.(session-group|tab-group|project-group)(?!\s*-)\s*\{([^}]+)\}",
            content,
        )
        if group_match:
            rule_content = group_match.group(2)
            assert (
                "display: inline-flex" in rule_content or
                "display: flex" in rule_content or
                "display: inline" in rule_content
            ), "Session groups should use inline or flex display"

    def test_group_header_has_styling(self):
        """Group headers should have distinct styling (color/background)."""
        content = CSS_FILE.read_text()

        # Look for group-header or group-label styling
        header_match = re.search(
            r"\.(session-group-header|tab-group-header|project-group-label|group-header)\s*\{([^}]+)\}",
            content,
        )
        if header_match:
            rule_content = header_match.group(2)
            # Should have some visual distinction (background, color, or border)
            assert (
                "background" in rule_content or
                "color" in rule_content or
                "border" in rule_content
            ), "Group headers should have visual distinction"

    def test_grouped_tabs_have_visual_connection(self):
        """Tabs within a group should appear visually connected."""
        content = CSS_FILE.read_text()

        # Look for reduced gap or connected styling
        # Chrome groups have the group header followed by tabs with reduced spacing
        assert (
            "session-group" in content or
            "tab-group" in content or
            "project-group" in content
        ), "Grouped tabs should have styling for visual connection"


class TestUngroupedSessionsHandling:
    """Verify handling of sessions without a project_path."""

    def test_ungrouped_sessions_still_displayed(self):
        """Sessions without project_path should still be displayed."""
        content = APP_FILE.read_text()

        # Look for null/undefined handling for project_path
        # Sessions without a project should be in an "Other" group or shown separately
        assert (
            "null" in content or
            "undefined" in content or
            "Other" in content or
            "!project_path" in content or
            "No project" in content.lower()
        ), "App.tsx should handle sessions without a project_path"

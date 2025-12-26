"""Unit tests for MCP tools - runs in CI without browser.

These tests verify the MCP tool infrastructure works correctly
without requiring Playwright or a browser.
"""

from unittest.mock import MagicMock, patch


class TestVisualTestMap:
    """Test the visual test mapping system."""

    def test_get_tests_for_provider_ui(self):
        from chad.visual_test_map import get_tests_for_file

        tests = get_tests_for_file("src/chad/provider_ui.py")
        assert "TestProvidersTab" in tests
        assert "TestDeleteProvider" in tests

    def test_get_tests_for_web_ui(self):
        from chad.visual_test_map import get_tests_for_file

        tests = get_tests_for_file("src/chad/web_ui.py")
        assert len(tests) > 0
        assert "TestUIElements" in tests

    def test_get_tests_for_unknown_file(self):
        from chad.visual_test_map import get_tests_for_file

        tests = get_tests_for_file("src/chad/unknown_file.py")
        assert tests == []

    def test_get_tests_for_files_multiple(self):
        from chad.visual_test_map import get_tests_for_files

        tests = get_tests_for_files([
            "src/chad/provider_ui.py",
            "src/chad/web_ui.py",
        ])
        # Should combine tests from both files (deduplicated)
        assert "TestProvidersTab" in tests
        assert "TestUIElements" in tests

    def test_get_tests_for_files_empty_list(self):
        from chad.visual_test_map import get_tests_for_files

        tests = get_tests_for_files([])
        assert tests == []

    def test_path_normalization_absolute(self):
        from chad.visual_test_map import get_tests_for_file

        tests = get_tests_for_file("/home/user/chad/src/chad/provider_ui.py")
        assert "TestProvidersTab" in tests

    def test_path_normalization_chad_prefix(self):
        from chad.visual_test_map import get_tests_for_file

        tests = get_tests_for_file("chad/provider_ui.py")
        assert "TestProvidersTab" in tests


class TestMCPHelpers:
    """Test MCP tool helper functions."""

    def test_viewport_helper(self):
        from chad.mcp_playwright import _viewport

        vp = _viewport(1920, 1080)
        assert vp == {"width": 1920, "height": 1080}

    def test_failure_helper(self):
        from chad.mcp_playwright import _failure

        result = _failure("Test error message")
        assert result["success"] is False
        assert result["error"] == "Test error message"

    def test_project_root(self):
        from chad.mcp_playwright import _project_root

        root = _project_root()
        assert root.exists()
        assert (root / "src" / "chad").exists()


class TestMCPToolsWithMocks:
    """Test MCP tools with mocked subprocess/playwright."""

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_ci_tests_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All tests passed",
            stderr="",
        )

        from chad.mcp_playwright import run_ci_tests

        result = run_ci_tests(include_visual=False)

        assert result["success"] is True
        assert result["include_visual"] is False
        # Verify -m "not visual" was passed
        call_args = mock_run.call_args[0][0]
        assert "-m" in call_args
        assert "not visual" in call_args

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_ci_tests_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="FAILED test_something",
            stderr="",
        )

        from chad.mcp_playwright import run_ci_tests

        result = run_ci_tests(include_visual=False)

        assert result["success"] is False
        assert result["return_code"] == 1

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_ci_tests_with_visual(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="passed",
            stderr="",
        )

        from chad.mcp_playwright import run_ci_tests

        result = run_ci_tests(include_visual=True)

        assert result["success"] is True
        assert result["include_visual"] is True
        # Verify -m "not visual" was NOT passed
        call_args = mock_run.call_args[0][0]
        assert "not visual" not in " ".join(call_args)

    def test_run_tests_for_file_no_mappings(self):
        from chad.mcp_playwright import run_tests_for_file

        result = run_tests_for_file("src/chad/nonexistent.py")

        assert result["success"] is True
        assert "No visual tests mapped" in result["message"]
        assert result["tests_run"] == []

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_tests_for_file_with_mappings(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="tests passed",
            stderr="",
        )

        from chad.mcp_playwright import run_tests_for_file

        result = run_tests_for_file("src/chad/provider_ui.py")

        assert result["success"] is True
        assert "TestProvidersTab" in result["tests_run"]
        assert mock_run.called

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_tests_for_modified_files_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",  # No modified files
            stderr="",
        )

        from chad.mcp_playwright import run_tests_for_modified_files

        result = run_tests_for_modified_files()

        assert result["success"] is True
        assert result["modified_files"] == []

    @patch("chad.mcp_playwright.subprocess.run")
    def test_run_tests_for_modified_files_with_changes(self, mock_run):
        # First call is git status, second is pytest
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=" M src/chad/provider_ui.py\n", stderr=""),
            MagicMock(returncode=0, stdout="tests passed", stderr=""),
        ]

        from chad.mcp_playwright import run_tests_for_modified_files

        result = run_tests_for_modified_files()

        assert result["success"] is True
        assert "src/chad/provider_ui.py" in result["modified_files"]
        assert "TestProvidersTab" in result["tests_run"]


class TestListMappings:
    """Test the list_visual_test_mappings tool."""

    def test_list_mappings_returns_dict(self):
        from chad.mcp_playwright import list_visual_test_mappings

        result = list_visual_test_mappings()

        assert result["success"] is True
        assert "mappings" in result
        assert isinstance(result["mappings"], dict)
        assert result["total_mappings"] > 0

    def test_list_mappings_contains_expected_files(self):
        from chad.mcp_playwright import list_visual_test_mappings

        result = list_visual_test_mappings()

        mappings = result["mappings"]
        assert "chad/provider_ui.py" in mappings
        assert "chad/web_ui.py" in mappings


class TestVerifyAllTestsPass:
    """Test the verify_all_tests_pass tool."""

    @patch("chad.mcp_playwright.subprocess.run")
    @patch("chad.mcp_playwright.run_tests_for_modified_files")
    def test_verify_all_pass(self, mock_visual, mock_run):
        # Lint and unit tests pass
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # lint
            MagicMock(returncode=0, stdout="tests passed", stderr=""),  # unit tests
        ]
        # Visual tests pass
        mock_visual.return_value = {"success": True, "tests_run": []}

        from chad.mcp_playwright import verify_all_tests_pass

        result = verify_all_tests_pass()

        assert result["success"] is True
        assert result["message"] == "All verification phases passed"

    @patch("chad.mcp_playwright.subprocess.run")
    def test_verify_lint_fails(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="E501 line too long",
            stderr="",
        )

        from chad.mcp_playwright import verify_all_tests_pass

        result = verify_all_tests_pass()

        assert result["success"] is False
        assert result["failed_phase"] == "lint"

    @patch("chad.mcp_playwright.subprocess.run")
    def test_verify_unit_tests_fail(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # lint passes
            MagicMock(returncode=1, stdout="FAILED", stderr=""),  # unit tests fail
        ]

        from chad.mcp_playwright import verify_all_tests_pass

        result = verify_all_tests_pass()

        assert result["success"] is False
        assert result["failed_phase"] == "unit_tests"

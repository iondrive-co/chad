"""Unit tests for simplified MCP tools.

Tests verify the 3 core MCP tools work correctly:
1. verify() - lint + all tests
2. screenshot() - capture UI
3. hypothesis/check_result/report - hypothesis tracking
"""

from unittest.mock import MagicMock, patch

from chad.investigation_report import HypothesisTracker


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

        tests = get_tests_for_file("chad/src/chad/provider_ui.py")
        assert "TestProvidersTab" in tests


class TestMCPHelpers:
    """Test MCP helper functions."""

    def test_failure_helper(self):
        from chad.mcp_playwright import _failure
        result = _failure("Test error")
        assert result["success"] is False
        assert result["error"] == "Test error"

    def test_project_root(self):
        from chad.mcp_playwright import _project_root
        root = _project_root()
        assert root.exists()
        assert (root / "src" / "chad").exists()


class TestListTools:
    """Test the list_tools discovery function."""

    def test_list_tools_returns_all_tools(self):
        from chad.mcp_playwright import list_tools
        result = list_tools()
        assert result["success"] is True
        assert "verify" in result["tools"]
        assert "screenshot" in result["tools"]
        assert "hypothesis" in result["tools"]
        assert "check_result" in result["tools"]
        assert "report" in result["tools"]

    def test_list_tools_includes_workflow(self):
        from chad.mcp_playwright import list_tools
        result = list_tools()
        assert "workflow" in result
        assert len(result["workflow"]) > 0


class TestVerify:
    """Test the unified verify() tool."""

    def test_verify_lint_success(self):
        from chad.mcp_playwright import verify

        with patch("subprocess.run") as mock_run:
            # Mock lint success, test success
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # lint
                MagicMock(returncode=0, stdout="10 passed", stderr=""),  # tests
            ]
            result = verify()
            assert result["success"] is True
            assert "phases" in result

    def test_verify_lint_failure(self):
        from chad.mcp_playwright import verify

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="src/file.py:1:1: E501 line too long",
                stderr=""
            )
            result = verify()
            assert result["success"] is False
            assert result["failed_phase"] == "lint"

    def test_verify_test_failure(self):
        from chad.mcp_playwright import verify

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # lint passes
                MagicMock(returncode=1, stdout="5 passed, 2 failed", stderr=""),  # tests fail
            ]
            result = verify()
            assert result["success"] is False
            assert result["failed_phase"] == "tests"


class TestScreenshot:
    """Test the screenshot() tool."""

    def test_screenshot_run_tab(self):
        from chad.mcp_playwright import screenshot

        with patch("chad.mcp_playwright.run_screenshot_subprocess") as mock:
            mock.return_value = {
                "success": True,
                "screenshot": "/tmp/run.png",
            }
            result = screenshot(tab="run", label="test")
            assert result["success"] is True
            assert result["tab"] == "run"
            mock.assert_called_once()

    def test_screenshot_providers_tab(self):
        from chad.mcp_playwright import screenshot

        with patch("chad.mcp_playwright.run_screenshot_subprocess") as mock:
            mock.return_value = {
                "success": True,
                "screenshot": "/tmp/providers.png",
            }
            result = screenshot(tab="providers", label="before")
            assert result["success"] is True
            assert result["tab"] == "providers"

    def test_screenshot_failure(self):
        from chad.mcp_playwright import screenshot

        with patch("chad.mcp_playwright.run_screenshot_subprocess") as mock:
            mock.return_value = {
                "success": False,
                "stderr": "Browser failed to start",
            }
            result = screenshot()
            assert result["success"] is False
            assert "Browser failed" in result["error"]


class TestHypothesisMCPTools:
    """Test the hypothesis tracking MCP tools."""

    def test_hypothesis_creates_tracker(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None  # Reset global

        result = hypothesis(
            description="CSS is not being applied",
            checks="Element has class,Stylesheet loaded"
        )

        assert result["success"] is True
        assert "tracker_id" in result
        assert result["hypothesis_id"] == 1
        assert len(result["checks_to_verify"]) == 2

    def test_hypothesis_with_existing_tracker(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        # Create first hypothesis
        result1 = hypothesis(description="First theory", checks="Check A")
        tracker_id = result1["tracker_id"]

        # Add second hypothesis to same tracker
        result2 = hypothesis(
            description="Second theory",
            checks="Check B",
            tracker_id=tracker_id
        )

        assert result2["success"] is True
        assert result2["hypothesis_id"] == 2

    def test_hypothesis_requires_checks(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        result = hypothesis(description="No checks", checks="")
        assert result["success"] is False
        assert "At least one check" in result["error"]


class TestCheckResult:
    """Test the check_result() tool."""

    def test_check_result_pass(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, check_result
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        hyp_result = hypothesis(description="Theory", checks="Check A,Check B")
        tracker_id = hyp_result["tracker_id"]

        result = check_result(
            tracker_id=tracker_id,
            hypothesis_id=1,
            check_index=0,
            passed=True,
            notes="Verified OK"
        )

        assert result["success"] is True
        assert result["passed"] is True
        assert result["hypothesis_status"] == "pending"  # Still pending, one check left

    def test_check_result_fail_rejects(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, check_result
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        hyp_result = hypothesis(description="Bad theory", checks="Will fail")
        tracker_id = hyp_result["tracker_id"]

        result = check_result(
            tracker_id=tracker_id,
            hypothesis_id=1,
            check_index=0,
            passed=False,
            notes="Check failed"
        )

        assert result["success"] is True
        assert result["hypothesis_status"] == "rejected"

    def test_check_result_all_pass_confirms(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, check_result
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        hyp_result = hypothesis(description="Good theory", checks="Check A,Check B")
        tracker_id = hyp_result["tracker_id"]

        check_result(tracker_id, 1, 0, passed=True)
        result = check_result(tracker_id, 1, 1, passed=True)

        assert result["hypothesis_status"] == "confirmed"

    def test_check_result_invalid_tracker(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import check_result

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)

        result = check_result(
            tracker_id="nonexistent",
            hypothesis_id=1,
            check_index=0,
            passed=True
        )

        assert result["success"] is False
        assert "not found" in result["error"]


class TestReport:
    """Test the report() tool."""

    def test_report_empty_tracker(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, report
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        # Create tracker with no hypotheses
        hyp_result = hypothesis(description="Theory", checks="Check")
        tracker_id = hyp_result["tracker_id"]

        result = report(tracker_id)

        assert result["success"] is True
        assert result["summary"]["total_hypotheses"] == 1

    def test_report_with_screenshots(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, check_result, report
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        hyp_result = hypothesis(description="UI bug", checks="Visual check")
        tracker_id = hyp_result["tracker_id"]
        check_result(tracker_id, 1, 0, passed=True)

        result = report(
            tracker_id,
            screenshot_before="/tmp/before.png",
            screenshot_after="/tmp/after.png"
        )

        assert result["success"] is True
        assert result["screenshots"]["before"] == "/tmp/before.png"
        assert result["screenshots"]["after"] == "/tmp/after.png"

    def test_report_shows_incomplete_checks(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import hypothesis, report
        import chad.mcp_playwright as mcp_module

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        mcp_module._current_tracker = None

        hyp_result = hypothesis(description="Theory", checks="Unfiled check")
        tracker_id = hyp_result["tracker_id"]

        result = report(tracker_id)

        assert result["all_checks_complete"] is False
        assert len(result["incomplete_checks"]) == 1

    def test_report_invalid_tracker(self, tmp_path, monkeypatch):
        from chad.mcp_playwright import report

        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)

        result = report("nonexistent")

        assert result["success"] is False
        assert "not found" in result["error"]

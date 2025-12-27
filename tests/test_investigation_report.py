"""Tests for the investigation report module."""

import json

import pytest

from chad.investigation_report import InvestigationReport


@pytest.fixture
def temp_investigations_dir(tmp_path):
    """Create a temporary investigations directory."""
    investigations_dir = tmp_path / "investigations"
    investigations_dir.mkdir()
    return investigations_dir


@pytest.fixture
def report(temp_investigations_dir, monkeypatch):
    """Create an investigation report in a temp directory."""
    monkeypatch.setattr(InvestigationReport, "BASE_DIR", temp_investigations_dir)
    return InvestigationReport()


class TestInvestigationReportCreation:
    """Tests for report creation and loading."""

    def test_creates_report_file(self, report, temp_investigations_dir):
        assert report.file_path.exists()
        assert report.file_path.parent == temp_investigations_dir

    def test_generates_unique_id(self, report):
        assert report.id.startswith("inv_")
        assert len(report.id) > 10

    def test_initial_report_structure(self, report):
        data = report.get_full_report()
        assert data["id"] == report.id
        assert data["request"] == {"description": "", "issue_id": ""}
        assert data["screenshots"] == {"before": None, "after": None}
        assert data["hypotheses"] == []
        assert data["findings"] == []
        assert data["rejected_approaches"] == []
        assert data["tests_designed"] == []
        assert data["test_framework_gaps"] == []
        assert data["fix"] is None
        assert data["post_incident_analysis"] is None

    def test_loads_existing_report(self, report, temp_investigations_dir, monkeypatch):
        monkeypatch.setattr(InvestigationReport, "BASE_DIR", temp_investigations_dir)
        report.set_request("Test request", "ISSUE-123")

        loaded = InvestigationReport(report.id)

        assert loaded.id == report.id
        assert loaded.get_full_report()["request"]["description"] == "Test request"

    def test_load_nonexistent_raises(self, temp_investigations_dir, monkeypatch):
        monkeypatch.setattr(InvestigationReport, "BASE_DIR", temp_investigations_dir)
        with pytest.raises(FileNotFoundError):
            InvestigationReport("inv_nonexistent")


class TestRequestHandling:
    """Tests for setting the request/task description."""

    def test_set_request(self, report):
        report.set_request("Fix the gap issue", "GAP-42")

        data = report.get_full_report()
        assert data["request"]["description"] == "Fix the gap issue"
        assert data["request"]["issue_id"] == "GAP-42"

    def test_set_request_without_issue_id(self, report):
        report.set_request("General fix")

        data = report.get_full_report()
        assert data["request"]["description"] == "General fix"
        assert data["request"]["issue_id"] == ""


class TestHypotheses:
    """Tests for hypothesis tracking."""

    def test_add_hypothesis(self, report):
        h_id = report.add_hypothesis("CSS gap property not applied")

        assert h_id == 1
        data = report.get_full_report()
        assert len(data["hypotheses"]) == 1
        assert data["hypotheses"][0]["description"] == "CSS gap property not applied"
        assert data["hypotheses"][0]["status"] == "active"

    def test_add_multiple_hypotheses(self, report):
        h1 = report.add_hypothesis("First theory")
        h2 = report.add_hypothesis("Second theory")

        assert h1 == 1
        assert h2 == 2
        data = report.get_full_report()
        assert len(data["hypotheses"]) == 2

    def test_update_hypothesis_status(self, report):
        h_id = report.add_hypothesis("Test theory")

        result = report.update_hypothesis_status(h_id, "confirmed")

        assert result is True
        data = report.get_full_report()
        assert data["hypotheses"][0]["status"] == "confirmed"

    def test_update_nonexistent_hypothesis(self, report):
        result = report.update_hypothesis_status(999, "confirmed")
        assert result is False


class TestFindings:
    """Tests for finding tracking."""

    def test_add_finding(self, report):
        f_id = report.add_finding(
            source="unit_test",
            content="Test passes but gap is 48px",
            hypothesis_id=None,
            verdict="inconclusive",
        )

        assert f_id == 1
        data = report.get_full_report()
        assert len(data["findings"]) == 1
        assert data["findings"][0]["source"] == "unit_test"
        assert data["findings"][0]["status"] == "open"

    def test_add_finding_with_hypothesis(self, report):
        h_id = report.add_hypothesis("CSS issue")
        report.add_finding(
            source="code_review",
            content="Found missing flex-shrink",
            hypothesis_id=h_id,
            verdict="supports",
            notes="Need to verify in browser",
        )

        data = report.get_full_report()
        assert data["findings"][0]["hypothesis_id"] == h_id
        assert data["findings"][0]["verdict"] == "supports"
        assert data["findings"][0]["notes"] == "Need to verify in browser"

    def test_update_finding_status(self, report):
        f_id = report.add_finding(source="tool_use", content="Test content")

        result = report.update_finding_status(f_id, "resolved")

        assert result is True
        data = report.get_full_report()
        assert data["findings"][0]["status"] == "resolved"

    def test_update_nonexistent_finding(self, report):
        result = report.update_finding_status(999, "resolved")
        assert result is False


class TestRejectedApproaches:
    """Tests for marking approaches as rejected."""

    def test_mark_approach_rejected(self, report):
        f1 = report.add_finding(source="tool_use", content="Tried margin: 0")
        f2 = report.add_finding(source="tool_use", content="Still has gap")

        report.mark_approach_rejected(
            description="Adding margin: 0 to accordion",
            why_rejected="Gap remained unchanged",
            finding_ids=[f1, f2],
        )

        data = report.get_full_report()
        assert len(data["rejected_approaches"]) == 1
        assert data["rejected_approaches"][0]["description"] == "Adding margin: 0 to accordion"
        assert data["rejected_approaches"][0]["finding_ids"] == [f1, f2]
        # Associated findings should be marked as rejected_approach
        assert data["findings"][0]["status"] == "rejected_approach"
        assert data["findings"][1]["status"] == "rejected_approach"


class TestScreenshots:
    """Tests for screenshot path tracking."""

    def test_set_before_screenshot(self, report):
        report.set_screenshots(before="/tmp/before.png")

        data = report.get_full_report()
        assert data["screenshots"]["before"] == "/tmp/before.png"
        assert data["screenshots"]["after"] is None

    def test_set_after_screenshot(self, report):
        report.set_screenshots(after="/tmp/after.png")

        data = report.get_full_report()
        assert data["screenshots"]["before"] is None
        assert data["screenshots"]["after"] == "/tmp/after.png"

    def test_set_both_screenshots(self, report):
        report.set_screenshots(before="/tmp/before.png", after="/tmp/after.png")

        data = report.get_full_report()
        assert data["screenshots"]["before"] == "/tmp/before.png"
        assert data["screenshots"]["after"] == "/tmp/after.png"


class TestTestDesign:
    """Tests for test design tracking."""

    def test_add_test_design(self, report):
        report.add_test_design(
            name="test_provider_gap",
            file_path="tests/test_ui_integration.py",
            purpose="Verify gap is <= 16px",
        )

        data = report.get_full_report()
        assert len(data["tests_designed"]) == 1
        assert data["tests_designed"][0]["name"] == "test_provider_gap"

    def test_add_test_design_with_framework_gap(self, report):
        report.add_test_design(
            name="test_accordion_spacing",
            file_path="tests/test_ui_integration.py",
            purpose="Test accordion layout",
            framework_gap="No existing test for accordion measurements",
        )

        data = report.get_full_report()
        assert "No existing test for accordion measurements" in data["test_framework_gaps"]

    def test_framework_gaps_are_deduplicated(self, report):
        gap = "Missing measurement utility"
        report.add_test_design(name="test1", file_path="test.py", purpose="p1", framework_gap=gap)
        report.add_test_design(name="test2", file_path="test.py", purpose="p2", framework_gap=gap)

        data = report.get_full_report()
        assert data["test_framework_gaps"].count(gap) == 1


class TestFix:
    """Tests for fix recording."""

    def test_record_fix(self, report):
        report.record_fix(
            description="Added flex-shrink: 0 to container",
            files_modified=["src/chad/provider_ui.py"],
            test_changes=["Added test_accordion_gap"],
        )

        data = report.get_full_report()
        assert data["fix"]["description"] == "Added flex-shrink: 0 to container"
        assert "src/chad/provider_ui.py" in data["fix"]["files_modified"]
        assert "Added test_accordion_gap" in data["fix"]["test_changes"]


class TestPostIncidentAnalysis:
    """Tests for post-incident analysis."""

    def test_add_post_incident_analysis(self, report):
        report.add_post_incident_analysis("If this fails, check Gradio version")

        data = report.get_full_report()
        assert data["post_incident_analysis"] == "If this fails, check Gradio version"


class TestSummary:
    """Tests for the summary functionality."""

    def test_empty_summary(self, report):
        summary = report.get_summary()

        assert summary["investigation_id"] == report.id
        assert summary["active_hypotheses"] == 0
        assert summary["open_findings"] == 0
        assert summary["is_complete"] is False

    def test_summary_with_content(self, report):
        report.set_request("Fix gap", "GAP-1")
        report.add_hypothesis("CSS issue")
        report.add_finding(source="unit_test", content="Found issue")
        report.set_screenshots(before="/tmp/before.png")

        summary = report.get_summary()

        assert summary["request"] == "Fix gap"
        assert summary["issue_id"] == "GAP-1"
        assert summary["active_hypotheses"] == 1
        assert summary["open_findings"] == 1
        assert summary["screenshots"]["before"] == "/tmp/before.png"

    def test_summary_is_complete(self, report):
        report.record_fix("Fixed it", ["file.py"], [])
        report.add_post_incident_analysis("Analysis here")

        summary = report.get_summary()

        assert summary["is_complete"] is True
        assert summary["has_fix"] is True
        assert summary["has_post_incident_analysis"] is True


class TestListInvestigations:
    """Tests for listing investigations."""

    def test_list_empty(self, temp_investigations_dir, monkeypatch):
        monkeypatch.setattr(InvestigationReport, "BASE_DIR", temp_investigations_dir)
        investigations = InvestigationReport.list_investigations()
        assert investigations == []

    def test_list_investigations(self, temp_investigations_dir, monkeypatch):
        monkeypatch.setattr(InvestigationReport, "BASE_DIR", temp_investigations_dir)

        r1 = InvestigationReport()
        r1.set_request("First investigation")
        r2 = InvestigationReport()
        r2.set_request("Second investigation")

        investigations = InvestigationReport.list_investigations()

        assert len(investigations) == 2
        requests = [inv["request"] for inv in investigations]
        assert "First investigation" in requests
        assert "Second investigation" in requests


class TestPersistence:
    """Tests for file persistence."""

    def test_changes_are_persisted(self, report):
        report.set_request("Persistent test")

        # Read file directly
        with open(report.file_path) as f:
            data = json.load(f)

        assert data["request"]["description"] == "Persistent test"

    def test_updated_at_changes(self, report):
        import time

        initial_updated = report.get_full_report()["updated_at"]
        time.sleep(0.01)
        report.add_hypothesis("New hypothesis")
        new_updated = report.get_full_report()["updated_at"]

        assert new_updated > initial_updated

    def test_json_is_always_valid(self, report):
        # Perform various operations
        report.set_request("Test")
        report.add_hypothesis("H1")
        report.add_finding(source="unit_test", content="F1")
        report.mark_approach_rejected("A1", "Why", [1])

        # File should be valid JSON at each point
        with open(report.file_path) as f:
            data = json.load(f)

        assert data["request"]["description"] == "Test"
        assert len(data["hypotheses"]) == 1
        assert len(data["rejected_approaches"]) == 1

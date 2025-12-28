"""Tests for the HypothesisTracker class."""

import json
import pytest

from chad.investigation_report import HypothesisTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """Create a tracker with a temporary directory."""
    monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
    return HypothesisTracker()


class TestTrackerCreation:
    """Tests for tracker creation and loading."""

    def test_creates_tracker_file(self, tracker):
        assert tracker.file_path.exists()

    def test_generates_unique_id(self, tracker):
        assert tracker.id.startswith("hyp_")

    def test_initial_tracker_structure(self, tracker):
        data = json.loads(tracker.file_path.read_text())
        assert "hypotheses" in data
        assert "screenshots" in data
        assert data["hypotheses"] == []

    def test_loads_existing_tracker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        t1 = HypothesisTracker()
        t1.add_hypothesis("Test hypothesis", ["Check 1"])
        t2 = HypothesisTracker(t1.id)
        assert len(t2._data["hypotheses"]) == 1

    def test_load_nonexistent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            HypothesisTracker("nonexistent_id")


class TestHypotheses:
    """Tests for hypothesis management."""

    def test_add_hypothesis(self, tracker):
        hypothesis_id = tracker.add_hypothesis(
            "The CSS is not being applied",
            ["Element has correct class", "Stylesheet is loaded"]
        )
        assert hypothesis_id == 1
        assert len(tracker._data["hypotheses"]) == 1
        assert tracker._data["hypotheses"][0]["description"] == "The CSS is not being applied"
        assert len(tracker._data["hypotheses"][0]["checks"]) == 2

    def test_add_multiple_hypotheses(self, tracker):
        id1 = tracker.add_hypothesis("First theory", ["Check A"])
        id2 = tracker.add_hypothesis("Second theory", ["Check B"])
        assert id1 == 1
        assert id2 == 2
        assert len(tracker._data["hypotheses"]) == 2

    def test_update_hypothesis(self, tracker):
        tracker.add_hypothesis("Original", ["Check 1"])
        result = tracker.update_hypothesis(1, description="Updated")
        assert result is True
        assert tracker._data["hypotheses"][0]["description"] == "Updated"

    def test_update_hypothesis_add_checks(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1"])
        tracker.update_hypothesis(1, add_checks=["Check 2", "Check 3"])
        assert len(tracker._data["hypotheses"][0]["checks"]) == 3

    def test_update_nonexistent_hypothesis(self, tracker):
        result = tracker.update_hypothesis(999, description="New")
        assert result is False


class TestCheckResults:
    """Tests for filing check results."""

    def test_file_check_result_pass(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1", "Check 2"])
        result = tracker.file_check_result(1, 0, passed=True, notes="Verified OK")
        assert result["hypothesis_status"] == "pending"
        assert result["checks_complete"] == 1
        assert result["checks_total"] == 2
        assert tracker._data["hypotheses"][0]["checks"][0]["passed"] is True

    def test_file_check_result_fail_rejects_hypothesis(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1"])
        result = tracker.file_check_result(1, 0, passed=False, notes="Failed")
        assert result["hypothesis_status"] == "rejected"
        assert tracker._data["hypotheses"][0]["status"] == "rejected"

    def test_all_checks_pass_confirms_hypothesis(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1", "Check 2"])
        tracker.file_check_result(1, 0, passed=True)
        result = tracker.file_check_result(1, 1, passed=True)
        assert result["hypothesis_status"] == "confirmed"

    def test_file_check_invalid_hypothesis(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1"])
        result = tracker.file_check_result(999, 0, passed=True)
        assert "error" in result

    def test_file_check_invalid_index(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1"])
        result = tracker.file_check_result(1, 999, passed=True)
        assert "error" in result


class TestScreenshots:
    """Tests for screenshot management."""

    def test_set_before_screenshot(self, tracker):
        tracker.set_screenshot("before", "/path/to/before.png")
        assert tracker._data["screenshots"]["before"] == "/path/to/before.png"

    def test_set_after_screenshot(self, tracker):
        tracker.set_screenshot("after", "/path/to/after.png")
        assert tracker._data["screenshots"]["after"] == "/path/to/after.png"

    def test_set_both_screenshots(self, tracker):
        tracker.set_screenshot("before", "/path/before.png")
        tracker.set_screenshot("after", "/path/after.png")
        assert tracker._data["screenshots"]["before"] == "/path/before.png"
        assert tracker._data["screenshots"]["after"] == "/path/after.png"

    def test_invalid_label_ignored(self, tracker):
        tracker.set_screenshot("invalid", "/path/to/file.png")
        assert tracker._data["screenshots"]["before"] is None
        assert tracker._data["screenshots"]["after"] is None


class TestReport:
    """Tests for report generation."""

    def test_empty_report(self, tracker):
        report = tracker.get_report()
        assert report["summary"]["total_hypotheses"] == 0
        assert report["all_checks_complete"] is True

    def test_report_with_content(self, tracker):
        tracker.add_hypothesis("Theory 1", ["Check A", "Check B"])
        tracker.file_check_result(1, 0, passed=True, notes="OK")
        report = tracker.get_report()
        assert report["summary"]["total_hypotheses"] == 1
        assert report["summary"]["pending"] == 1
        assert len(report["incomplete_checks"]) == 1

    def test_report_with_confirmed_hypothesis(self, tracker):
        tracker.add_hypothesis("Theory 1", ["Check A"])
        tracker.file_check_result(1, 0, passed=True)
        report = tracker.get_report()
        assert report["summary"]["confirmed"] == 1
        assert "Theory 1" in report["confirmed_hypotheses"]

    def test_report_with_rejected_hypothesis(self, tracker):
        tracker.add_hypothesis("Bad theory", ["Check fails"])
        tracker.file_check_result(1, 0, passed=False)
        report = tracker.get_report()
        assert report["summary"]["rejected"] == 1
        assert "Bad theory" in report["rejected_hypotheses"]


class TestPendingChecks:
    """Tests for getting pending checks."""

    def test_get_pending_checks_empty(self, tracker):
        pending = tracker.get_pending_checks()
        assert pending == []

    def test_get_pending_checks_with_hypotheses(self, tracker):
        tracker.add_hypothesis("Theory 1", ["Check A", "Check B"])
        pending = tracker.get_pending_checks()
        assert len(pending) == 2
        assert pending[0]["check"] == "Check A"

    def test_pending_checks_updates_after_filing(self, tracker):
        tracker.add_hypothesis("Theory 1", ["Check A", "Check B"])
        tracker.file_check_result(1, 0, passed=True)
        pending = tracker.get_pending_checks()
        assert len(pending) == 1
        assert pending[0]["check"] == "Check B"


class TestListTrackers:
    """Tests for listing trackers."""

    def test_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        trackers = HypothesisTracker.list_trackers()
        assert trackers == []

    def test_list_trackers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        t1 = HypothesisTracker()
        t1.add_hypothesis("Theory", ["Check"])
        t1.file_check_result(1, 0, passed=True)
        t2 = HypothesisTracker()
        trackers = HypothesisTracker.list_trackers()
        assert {t["id"] for t in trackers} == {t1.id, t2.id}


class TestPersistence:
    """Tests for data persistence."""

    def test_changes_are_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HypothesisTracker, "BASE_DIR", tmp_path)
        t1 = HypothesisTracker()
        t1.add_hypothesis("Persisted", ["Check"])
        t2 = HypothesisTracker(t1.id)
        assert t2._data["hypotheses"][0]["description"] == "Persisted"

    def test_updated_at_changes(self, tracker):
        import time
        original = tracker._data["updated_at"]
        time.sleep(0.01)
        tracker.add_hypothesis("New", ["Check"])
        assert tracker._data["updated_at"] != original

    def test_json_is_always_valid(self, tracker):
        tracker.add_hypothesis("Theory", ["Check 1", "Check 2"])
        tracker.file_check_result(1, 0, passed=True)
        tracker.set_screenshot("before", "/path/file.png")
        # Should not raise
        data = json.loads(tracker.file_path.read_text())
        assert data is not None

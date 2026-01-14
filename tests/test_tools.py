"""Tests for chad.tools module."""

import os
from unittest.mock import patch

import pytest


class TestVerify:
    """Test the verify function."""

    def test_verify_lint_only_success(self):
        """verify(lint_only=True) should run only flake8."""
        from chad.verification.tools import verify

        with patch("chad.verification.tools.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""

            result = verify(lint_only=True)

            assert result["success"] is True
            assert "lint" in result["phases"]
            assert "tests" not in result["phases"]

    def test_verify_lint_failure(self):
        """verify should report lint failures."""
        from chad.verification.tools import verify

        with patch("chad.verification.tools.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = "file.py:1:1: E001 error"

            result = verify(lint_only=True)

            assert result["success"] is False
            assert result["failed_phase"] == "lint"
            assert result["phases"]["lint"]["issue_count"] == 1

    def test_verify_prefers_project_venv(self, tmp_path, monkeypatch):
        """verify should use project venv python when available."""
        from chad.verification.tools import verify

        venv_dir = tmp_path / "venv" / ("Scripts" if os.name == "nt" else "bin")
        venv_dir.mkdir(parents=True, exist_ok=True)
        venv_python = venv_dir / ("python.exe" if os.name == "nt" else "python")
        venv_python.write_text("")  # presence is enough

        monkeypatch.setattr("chad.verification.tools.resolve_project_root", lambda: (tmp_path, "test"))

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd[0])
            return type("Proc", (), {"returncode": 0, "stdout": ""})

        monkeypatch.setattr("chad.verification.tools.subprocess.run", fake_run)

        result = verify(lint_only=True)

        assert result["success"] is True
        assert commands[0] == str(venv_python)

    def test_verify_reports_pytest_stderr_on_error(self, monkeypatch, tmp_path):
        """verify should surface stderr when pytest fails before running tests."""
        from chad.verification.tools import verify

        monkeypatch.setattr("chad.verification.tools.resolve_project_root", lambda: (tmp_path, "test"))

        class Proc:
            def __init__(self, returncode, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, **kwargs):
            if cmd[2] == "flake8":
                return Proc(0, "")
            if cmd[2] == "pip":
                return Proc(0, "")
            return Proc(2, "", "ERROR: unrecognized arguments: -n")

        monkeypatch.setattr("chad.verification.tools.subprocess.run", fake_run)

        result = verify()

        assert result["success"] is False
        assert result["failed_phase"] == "tests"
        assert "Tests failed to run" in result["message"]
        assert "unrecognized arguments: -n" in result["message"]


class TestScreenshot:
    """Test the screenshot function."""

    def test_screenshot_unknown_component(self):
        """screenshot with unknown component should fail."""
        from chad.verification.tools import screenshot

        result = screenshot(tab="run", component="nonexistent")

        assert result["success"] is False
        assert "Unknown component" in result["error"]

    def test_screenshot_component_selector_mapping(self):
        """Verify component selectors are correctly mapped."""
        from chad.verification.tools import COMPONENT_SELECTORS

        assert "project-path" in COMPONENT_SELECTORS
        assert "live-view" in COMPONENT_SELECTORS
        assert "provider-summary" in COMPONENT_SELECTORS


class TestParseVerificationResponse:
    """Test parse_verification_response with various inputs."""

    def test_parse_with_thinking_prefix(self):
        """Should handle responses with *Thinking:* prefix before JSON."""
        from chad.prompts import parse_verification_response

        response = ('*Thinking: **Ensuring valid JSON output***\n\n'
                    '{"passed":false,"summary":"Found issues","issues":["Error 1"]}')

        passed, summary, issues = parse_verification_response(response)

        assert passed is False
        assert summary == "Found issues"
        assert issues == ["Error 1"]

    def test_parse_nested_json(self):
        """Should handle JSON with nested objects."""
        from chad.prompts import parse_verification_response

        response = ('{"passed":true,"summary":"All good","issues":[],'
                    '"details":{"lint":{"status":"ok"}}}')

        passed, summary, issues = parse_verification_response(response)

        assert passed is True
        assert summary == "All good"
        assert issues == []

    def test_parse_markdown_code_block(self):
        """Should extract JSON from markdown code blocks."""
        from chad.prompts import parse_verification_response

        response = '```json\n{"passed":true,"summary":"OK"}\n```'

        passed, summary, issues = parse_verification_response(response)

        assert passed is True
        assert summary == "OK"

    def test_parse_plain_json(self):
        """Should handle plain JSON without any wrapper."""
        from chad.prompts import parse_verification_response

        response = '{"passed":false,"summary":"Test failure"}'

        passed, summary, issues = parse_verification_response(response)

        assert passed is False
        assert summary == "Test failure"

    def test_parse_invalid_json_raises(self):
        """Should raise VerificationParseError for invalid JSON."""
        from chad.prompts import parse_verification_response, VerificationParseError

        with pytest.raises(VerificationParseError):
            parse_verification_response("not json at all")

    def test_parse_missing_passed_field_raises(self):
        """Should raise VerificationParseError if 'passed' field is missing."""
        from chad.prompts import parse_verification_response, VerificationParseError

        with pytest.raises(VerificationParseError):
            parse_verification_response('{"summary":"No passed field"}')

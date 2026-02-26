"""Tests for chad.tools module."""

import os
from unittest.mock import patch

import pytest


class TestVerify:
    """Test the verify function."""

    def test_verify_lint_only_success(self):
        """verify(lint_only=True) should run only flake8."""
        from chad.util.verification.tools import verify

        with patch("chad.util.verification.tools.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""

            result = verify(lint_only=True)

            assert result["success"] is True
            assert result["lint"]["passed"] is True
            assert result["test"] is None

    def test_verify_lint_failure(self):
        """verify should report lint failures."""
        from chad.util.verification.tools import verify

        with patch("chad.util.verification.tools.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = "file.py:1:1: E001 error"
            mock_run.return_value.stderr = ""

            result = verify(lint_only=True)

            assert result["success"] is False
            assert result["lint"]["passed"] is False
            assert "E001" in result["lint"]["output"]

    def test_verify_prefers_project_venv(self, tmp_path):
        """verify should use project venv python when available."""
        from chad.util.verification.tools import find_python_executable

        venv_dir = tmp_path / "venv" / ("Scripts" if os.name == "nt" else "bin")
        venv_dir.mkdir(parents=True, exist_ok=True)
        venv_python = venv_dir / ("python.exe" if os.name == "nt" else "python")
        venv_python.write_text("")  # presence is enough

        result = find_python_executable(tmp_path)
        assert result == str(venv_python)

    def test_verify_reports_test_failure(self):
        """verify should report test failures."""
        from chad.util.verification.tools import verify

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "flake8" in cmd:
                return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            # pytest failure
            return type("Proc", (), {"returncode": 1, "stdout": "FAILED", "stderr": "ERROR"})()

        with patch("chad.util.verification.tools.subprocess.run", fake_run):
            result = verify()

        assert result["success"] is False
        assert result["test"]["passed"] is False

    def test_verify_runs_both_lint_and_tests(self):
        """verify() should run both flake8 and pytest."""
        from chad.util.verification.tools import verify

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        with patch("chad.util.verification.tools.subprocess.run", fake_run):
            result = verify()

        assert result["success"] is True
        assert len(commands) == 2
        assert any("flake8" in cmd for cmd in commands)
        assert any("pytest" in cmd for cmd in commands)

    def test_verify_visual_only_returns_error(self):
        """verify(visual_only=True) should return error since visual tests are not supported."""
        from chad.util.verification.tools import verify

        result = verify(visual_only=True)

        assert result["success"] is False
        assert "not supported" in result["error"]


class TestParseVerificationResponse:
    """Test parse_verification_response with various inputs."""

    def test_parse_with_thinking_prefix(self):
        """Should handle responses with *Thinking:* prefix before JSON."""
        from chad.util.prompts import parse_verification_response

        response = ('*Thinking: **Ensuring valid JSON output***\n\n'
                    '{"passed":false,"summary":"Found issues","issues":["Error 1"]}')

        passed, summary, issues = parse_verification_response(response)

        assert passed is False
        assert summary == "Found issues"
        assert issues == ["Error 1"]

    def test_parse_nested_json(self):
        """Should handle JSON with nested objects."""
        from chad.util.prompts import parse_verification_response

        response = ('{"passed":true,"summary":"All good","issues":[],'
                    '"details":{"lint":{"status":"ok"}}}')

        passed, summary, issues = parse_verification_response(response)

        assert passed is True
        assert summary == "All good"
        assert issues == []

    def test_parse_markdown_code_block(self):
        """Should extract JSON from markdown code blocks."""
        from chad.util.prompts import parse_verification_response

        response = '```json\n{"passed":true,"summary":"OK"}\n```'

        passed, summary, issues = parse_verification_response(response)

        assert passed is True
        assert summary == "OK"

    def test_parse_plain_json(self):
        """Should handle plain JSON without any wrapper."""
        from chad.util.prompts import parse_verification_response

        response = '{"passed":false,"summary":"Test failure"}'

        passed, summary, issues = parse_verification_response(response)

        assert passed is False
        assert summary == "Test failure"

    def test_parse_invalid_json_raises(self):
        """Should raise VerificationParseError for invalid JSON."""
        from chad.util.prompts import parse_verification_response, VerificationParseError

        with pytest.raises(VerificationParseError):
            parse_verification_response("not json at all")

    def test_parse_missing_passed_field_raises(self):
        """Should raise VerificationParseError if 'passed' field is missing."""
        from chad.util.prompts import parse_verification_response, VerificationParseError

        with pytest.raises(VerificationParseError):
            parse_verification_response('{"summary":"No passed field"}')

    def test_parse_does_not_misclassify_embedded_timeout_text_when_json_exists(self):
        """Embedded timeout strings in analysis text should not override valid verdict JSON."""
        from chad.util.prompts import parse_verification_response

        response = (
            "I inspected this line from a test fixture:\n"
            'response = "Error: Qwen execution timed out (30 minutes)"\n\n'
            "Final verification verdict:\n"
            '```json\n{"passed": false, "summary": "Needs fixes", "issues": ["A"]}\n```'
        )

        passed, summary, issues = parse_verification_response(response)

        assert passed is False
        assert summary == "Needs fixes"
        assert issues == ["A"]

    def test_parse_embedded_timeout_text_without_json_raises(self):
        """Timeout-like text inside other content should not be treated as provider execution error."""
        from chad.util.prompts import parse_verification_response, VerificationParseError

        response = (
            'This code snippet is only an example: "Error: Gemini execution timed out (30 minutes)".\n'
            "No verification JSON was produced."
        )

        with pytest.raises(VerificationParseError):
            parse_verification_response(response)

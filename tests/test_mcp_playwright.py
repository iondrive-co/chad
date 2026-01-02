from __future__ import annotations

from types import SimpleNamespace


def test_verify_lint_only(monkeypatch):
    """verify(lint_only=True) should run only flake8 and return immediately."""
    from chad import mcp_playwright

    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(mcp_playwright.subprocess, "run", fake_run)

    result = mcp_playwright.verify(lint_only=True)

    assert result["success"] is True
    assert result["message"] == "Lint-only run completed"
    assert len(calls) == 1
    assert "flake8" in calls[0][2]


def test_verify_lint_only_failure(monkeypatch):
    """Lint-only mode should surface failures and skip tests."""
    from chad import mcp_playwright

    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="E999 bad")

    monkeypatch.setattr(mcp_playwright.subprocess, "run", fake_run)

    result = mcp_playwright.verify(lint_only=True)

    assert result["success"] is False
    assert result["failed_phase"] == "lint"
    assert len(calls) == 1

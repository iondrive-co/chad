"""Tests for configuration utilities."""

from pathlib import Path

from chad.util.config import resolve_project_root


def test_resolve_project_root_defaults_to_repo_root(monkeypatch):
    """resolve_project_root should locate the repository root via pyproject/git."""
    monkeypatch.delenv("CHAD_PROJECT_ROOT", raising=False)

    here = Path(__file__).resolve()
    expected = None
    for candidate in [here] + list(here.parents):
        if (candidate / "pyproject.toml").exists():
            expected = candidate
            break

    assert expected is not None, "pyproject.toml not found in ancestor chain"

    root, reason = resolve_project_root()
    assert root == expected
    assert (root / "pyproject.toml").exists()
    assert reason.startswith(("auto:", "env:", "argument"))


def test_resolve_project_root_honors_env(monkeypatch, tmp_path):
    """CHAD_PROJECT_ROOT must override auto-detection when it exists."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n")

    monkeypatch.setenv("CHAD_PROJECT_ROOT", str(project_root))

    root, reason = resolve_project_root()
    assert root == project_root.resolve()
    assert reason.startswith("env:")

    # Cleanup env for other tests
    monkeypatch.delenv("CHAD_PROJECT_ROOT", raising=False)

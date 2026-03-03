"""Dependency declarations expected for runtime features."""

from pathlib import Path


def test_python_multipart_dependency_present():
    """Uploads require python-multipart for FastAPI's UploadFile parsing."""
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")

    assert "python-multipart" in pyproject, "python-multipart must be declared in dependencies"

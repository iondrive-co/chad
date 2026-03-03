from pathlib import Path


def test_readme_uses_absolute_image_links():
    """Ensure README images render on PyPI by avoiding relative doc paths."""
    text = Path("README.md").read_text(encoding="utf-8")
    assert "src=\"docs/" not in text
    assert "](docs/" not in text

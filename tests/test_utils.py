"""Tests for utility functions."""

from chad.utils import ensure_directory


class TestUtils:
    """Test cases for utility functions."""

    def test_ensure_directory_creates_dir(self, tmp_path):
        """Test that ensure_directory creates a directory."""
        test_dir = tmp_path / "test" / "nested" / "dir"
        ensure_directory(test_dir)
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_directory_existing_dir(self, tmp_path):
        """Test that ensure_directory works with existing directory."""
        test_dir = tmp_path / "existing"
        test_dir.mkdir()
        ensure_directory(test_dir)  # Should not raise
        assert test_dir.exists()

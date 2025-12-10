"""Tests for utility functions."""

import pytest
from pathlib import Path
from chad.utils import ensure_directory, get_platform, is_tool_installed


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

    def test_get_platform(self):
        """Test platform detection."""
        platform = get_platform()
        assert platform in ['linux', 'darwin', 'win32']

    def test_is_tool_installed_python(self):
        """Test that Python is detected as installed."""
        assert is_tool_installed('python3') is True

    def test_is_tool_installed_nonexistent(self):
        """Test that nonexistent tool returns False."""
        assert is_tool_installed('definitely-not-a-real-command-12345') is False

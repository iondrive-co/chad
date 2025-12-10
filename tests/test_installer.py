"""Tests for the installer module."""

import pytest
from pathlib import Path
from chad.installer import AIToolInstaller


class TestAIToolInstaller:
    """Test cases for AIToolInstaller class."""

    def test_init_default_paths(self):
        """Test installer initialization with default paths."""
        installer = AIToolInstaller()
        assert installer.install_dir == Path.home() / ".local" / "bin"
        assert installer.config_dir == Path.home() / ".config" / "chad"

    def test_init_custom_path(self):
        """Test installer initialization with custom install directory."""
        custom_dir = Path("/tmp/test_install")
        installer = AIToolInstaller(install_dir=custom_dir)
        assert installer.install_dir == custom_dir

    def test_authenticate_tool_claude(self):
        """Test authentication prompt for Claude."""
        installer = AIToolInstaller()
        # Just test that it doesn't crash - actual authentication requires user interaction
        result = installer.authenticate_tool('claude')
        assert result is True

    def test_authenticate_tool_openai(self):
        """Test authentication prompt for OpenAI."""
        installer = AIToolInstaller()
        # Just test that it doesn't crash - actual authentication requires user interaction
        result = installer.authenticate_tool('openai')
        assert result is True

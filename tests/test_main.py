"""Tests for main module."""

from unittest.mock import Mock, patch, MagicMock
import pytest
from chad.__main__ import main


class TestMain:
    """Test cases for main function."""

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('getpass.getpass', return_value='test-password')
    def test_main_existing_user(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with existing user."""
        # Mock security manager
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        # Mock launch_web_ui to not actually launch
        mock_launch.return_value = None

        result = main()

        assert result == 0
        mock_getpass.assert_called_once()
        mock_launch.assert_called_once_with('test-password')

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('getpass.getpass', side_effect=['test-password', 'test-password'])
    def test_main_first_run(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with first run."""
        # Mock security manager
        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security_class.return_value = mock_security

        # Mock launch_web_ui to not actually launch
        mock_launch.return_value = None

        result = main()

        assert result == 0
        assert mock_getpass.call_count == 2  # Password + confirmation
        mock_launch.assert_called_once_with('test-password')

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('getpass.getpass', side_effect=['password1', 'password2', 'test-password', 'test-password'])
    def test_main_first_run_password_mismatch(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with password mismatch on first run."""
        # Mock security manager
        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security_class.return_value = mock_security

        # Mock launch_web_ui to not actually launch
        mock_launch.return_value = None

        result = main()

        assert result == 0
        assert mock_getpass.call_count == 4  # Two failed attempts + one successful
        mock_launch.assert_called_once_with('test-password')

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('getpass.getpass', return_value='test-password')
    def test_main_launch_error(self, mock_getpass, mock_security_class, mock_launch):
        """Test main when web UI launch fails."""
        # Mock security manager
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        # Mock launch_web_ui to raise error
        mock_launch.side_effect = ValueError("Invalid password")

        result = main()

        assert result == 1

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('getpass.getpass', return_value='test-password')
    def test_main_keyboard_interrupt(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with keyboard interrupt."""
        # Mock security manager
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        # Mock launch_web_ui to raise KeyboardInterrupt
        mock_launch.side_effect = KeyboardInterrupt()

        result = main()

        assert result == 0

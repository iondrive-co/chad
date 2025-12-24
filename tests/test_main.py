"""Tests for main module."""

import sys
from unittest.mock import Mock, patch
from chad.__main__ import main


class TestMain:
    """Test cases for main function."""

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    def test_main_existing_user(self, mock_security_class, mock_launch):
        """Test main with existing user - password handled by launch_web_ui."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.return_value = (None, 7860)

        with patch.object(sys, 'argv', ['chad']):
            result = main()

        assert result == 0
        mock_launch.assert_called_once_with(None, port=7860)

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    @patch('chad.__main__.getpass.getpass', return_value='test-password')
    def test_main_first_run(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with first run - prompts for password."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security_class.return_value = mock_security

        mock_launch.return_value = (None, 7860)

        with patch.object(sys, 'argv', ['chad']):
            result = main()

        assert result == 0
        mock_getpass.assert_called_once()
        mock_launch.assert_called_once_with('test-password', port=7860)

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    def test_main_launch_error(self, mock_security_class, mock_launch):
        """Test main when web UI launch fails."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.side_effect = ValueError("Invalid password")

        with patch.object(sys, 'argv', ['chad']):
            result = main()

        assert result == 1

    @patch('chad.__main__.launch_web_ui')
    @patch('chad.__main__.SecurityManager')
    def test_main_keyboard_interrupt(self, mock_security_class, mock_launch):
        """Test main with keyboard interrupt."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.side_effect = KeyboardInterrupt()

        with patch.object(sys, 'argv', ['chad']):
            result = main()

        assert result == 0

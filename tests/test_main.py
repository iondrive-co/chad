"""Tests for main module."""

import os
import sys
import threading
import time
from unittest.mock import Mock, patch
from chad.__main__ import main, _start_parent_watchdog


class TestMain:
    """Test cases for main function."""

    @patch("chad.__main__.launch_web_ui")
    @patch("chad.__main__.SecurityManager")
    def test_main_existing_user(self, mock_security_class, mock_launch):
        """Test main with existing user - password handled by launch_web_ui."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.return_value = (None, 7860)

        with patch.object(sys, "argv", ["chad"]):
            result = main()

        assert result == 0
        mock_launch.assert_called_once_with(None, port=7860)

    @patch("chad.__main__.launch_web_ui")
    @patch("chad.__main__.SecurityManager")
    @patch("chad.__main__.getpass.getpass", return_value="test-password")
    def test_main_first_run(self, mock_getpass, mock_security_class, mock_launch):
        """Test main with first run - prompts for password."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security_class.return_value = mock_security

        mock_launch.return_value = (None, 7860)

        with patch.object(sys, "argv", ["chad"]):
            result = main()

        assert result == 0
        mock_getpass.assert_called_once()
        mock_launch.assert_called_once_with("test-password", port=7860)

    @patch("chad.__main__.launch_web_ui")
    @patch("chad.__main__.SecurityManager")
    def test_main_launch_error(self, mock_security_class, mock_launch):
        """Test main when web UI launch fails."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.side_effect = ValueError("Invalid password")

        with patch.object(sys, "argv", ["chad"]):
            result = main()

        assert result == 1

    @patch("chad.__main__.launch_web_ui")
    @patch("chad.__main__.SecurityManager")
    def test_main_keyboard_interrupt(self, mock_security_class, mock_launch):
        """Test main with keyboard interrupt."""
        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_launch.side_effect = KeyboardInterrupt()

        with patch.object(sys, "argv", ["chad"]):
            result = main()

        assert result == 0


class TestParentWatchdog:
    """Test cases for parent process watchdog."""

    def test_watchdog_not_started_without_env_var(self, monkeypatch):
        """Watchdog thread should not start without CHAD_PARENT_PID."""
        monkeypatch.delenv("CHAD_PARENT_PID", raising=False)

        thread_count_before = threading.active_count()
        _start_parent_watchdog()
        thread_count_after = threading.active_count()

        # Should not have started a new thread
        assert thread_count_after == thread_count_before

    def test_watchdog_not_started_with_invalid_pid(self, monkeypatch):
        """Watchdog thread should not start with invalid PID."""
        monkeypatch.setenv("CHAD_PARENT_PID", "not_a_number")

        thread_count_before = threading.active_count()
        _start_parent_watchdog()
        thread_count_after = threading.active_count()

        # Should not have started a new thread
        assert thread_count_after == thread_count_before

    def test_watchdog_starts_with_valid_pid(self, monkeypatch):
        """Watchdog thread should start with valid parent PID."""
        # Use current process as parent (it exists, so watchdog won't kill us)
        monkeypatch.setenv("CHAD_PARENT_PID", str(os.getpid()))

        thread_count_before = threading.active_count()
        _start_parent_watchdog()
        # Give thread time to start
        time.sleep(0.1)
        thread_count_after = threading.active_count()

        # Should have started a new thread
        assert thread_count_after == thread_count_before + 1

"""Tests for main module."""

import os
import sys
import threading
import time
from unittest.mock import Mock, patch
from chad.__main__ import main, _start_parent_watchdog


class TestMain:
    """Test cases for main function."""

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_existing_user(self, mock_config_class, mock_run_unified):
        """Test main with existing user - password verified via ConfigManager."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "verified-password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad"]):
            with patch.dict(os.environ, {}, clear=True):
                # Remove CHAD_PASSWORD to force interactive mode
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 0
        mock_config.verify_main_password.assert_called_once()
        mock_run_unified.assert_called_once()
        call_args = mock_run_unified.call_args
        assert call_args.args[0] == "verified-password"  # main_password

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_first_run(self, mock_config_class, mock_run_unified):
        """Test main with first run - prompts for password setup."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = True
        mock_config.setup_main_password.return_value = "new-password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad"]):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 0
        mock_config.setup_main_password.assert_called_once()
        mock_run_unified.assert_called_once()
        call_args = mock_run_unified.call_args
        assert call_args.args[0] == "new-password"  # main_password

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_with_env_password(self, mock_config_class, mock_run_unified):
        """Test main uses CHAD_PASSWORD env var when set."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad"]):
            with patch.dict(os.environ, {"CHAD_PASSWORD": "env-password"}):
                result = main()

        assert result == 0
        # Should NOT call verify_main_password when env var is set
        mock_config.verify_main_password.assert_not_called()
        mock_config.setup_main_password.assert_not_called()
        mock_run_unified.assert_called_once()
        call_args = mock_run_unified.call_args
        assert call_args.args[0] == "env-password"

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_launch_error(self, mock_config_class, mock_run_unified):
        """Test main when UI launch fails."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        mock_run_unified.side_effect = ValueError("Invalid password")

        with patch.object(sys, "argv", ["chad"]):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 1

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_keyboard_interrupt(self, mock_config_class, mock_run_unified):
        """Test main with keyboard interrupt."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        mock_run_unified.side_effect = KeyboardInterrupt()

        with patch.object(sys, "argv", ["chad"]):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 0

    @patch("chad.__main__.run_server")
    @patch("chad.__main__.ConfigManager")
    def test_main_server_only_mode(self, mock_config_class, mock_run_server):
        """Test main in server-only mode (no password needed)."""
        mock_config = Mock()
        mock_config.get_cleanup_days.return_value = 3
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad", "--mode", "server"]):
            result = main()

        assert result == 0
        mock_run_server.assert_called_once()
        # Should NOT prompt for password in server mode
        mock_config.verify_main_password.assert_not_called()
        mock_config.setup_main_password.assert_not_called()

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_cli_mode(self, mock_config_class, mock_run_unified):
        """Test main with CLI ui mode."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "cli"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad", "--ui", "cli"]):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 0
        mock_run_unified.assert_called_once()
        call_kwargs = mock_run_unified.call_args.kwargs
        assert call_kwargs.get("ui_mode") == "cli"


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

"""Tests for main module."""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch
from chad.__main__ import (
    main,
    _start_parent_watchdog,
    write_server_port,
    read_server_port,
    get_chad_dir,
)


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

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_server_url_auto_discovers_port(self, mock_config_class, mock_run_unified, tmp_path):
        """Test that --server-url auto autodiscovers port from file."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        # Write a port file for autodiscovery
        port_file = tmp_path / "server.port"
        port_file.write_text("9876\n")

        with patch.object(sys, "argv", ["chad", "--server-url", "auto"]):
            with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path), "CHAD_PASSWORD": "test"}):
                result = main()

        assert result == 0
        mock_run_unified.assert_called_once()
        call_kwargs = mock_run_unified.call_args.kwargs
        assert call_kwargs.get("server_url") == "http://127.0.0.1:9876"

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_server_url_skips_password_prompt(self, mock_config_class, mock_run_unified):
        """Connecting to existing server should not ask for main password."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad", "--server-url", "http://127.0.0.1:9999"]):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("CHAD_PASSWORD", None)
                result = main()

        assert result == 0
        mock_config.verify_main_password.assert_not_called()
        mock_config.setup_main_password.assert_not_called()
        mock_run_unified.assert_called_once()
        call_args = mock_run_unified.call_args
        assert call_args.args[0] is None  # main_password
        assert call_args.kwargs.get("server_url") == "http://127.0.0.1:9999"

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_server_url_auto_skips_password_prompt(self, mock_config_class, mock_run_unified, tmp_path):
        """Autodiscovery should also skip password prompts."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        port_file = tmp_path / "server.port"
        port_file.write_text("5555\n")

        with patch.object(sys, "argv", ["chad", "--server-url", "auto"]):
            with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}, clear=True):
                result = main()

        assert result == 0
        mock_config.verify_main_password.assert_not_called()
        mock_config.setup_main_password.assert_not_called()
        mock_run_unified.assert_called_once()
        call_args = mock_run_unified.call_args
        assert call_args.args[0] is None
        assert call_args.kwargs.get("server_url") == "http://127.0.0.1:5555"

    @patch("chad.__main__.run_unified")
    @patch("chad.__main__.ConfigManager")
    def test_main_server_url_auto_fails_when_no_port_file(self, mock_config_class, mock_run_unified, tmp_path):
        """Test that --server-url auto fails gracefully when port file missing."""
        mock_config = Mock()
        mock_config.is_first_run.return_value = False
        mock_config.verify_main_password.return_value = "password"
        mock_config.get_cleanup_days.return_value = 3
        mock_config.get_ui_mode.return_value = "gradio"
        mock_config_class.return_value = mock_config

        with patch.object(sys, "argv", ["chad", "--server-url", "auto"]):
            with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path), "CHAD_PASSWORD": "test"}):
                result = main()

        assert result == 1
        mock_run_unified.assert_not_called()


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


class TestServerPortAutodiscovery:
    """Test cases for server port autodiscovery."""

    def test_write_server_port(self, tmp_path):
        """Test writing server port to file."""
        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}):
            write_server_port(8765)

            port_file = tmp_path / "server.port"
            assert port_file.exists()
            assert port_file.read_text().strip() == "8765"

    def test_read_server_port(self, tmp_path):
        """Test reading server port from file."""
        port_file = tmp_path / "server.port"
        port_file.write_text("9999\n")

        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}):
            port = read_server_port()

        assert port == 9999

    def test_read_server_port_returns_none_when_file_missing(self, tmp_path):
        """Test reading port returns None when file doesn't exist."""
        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}):
            port = read_server_port()

        assert port is None

    def test_read_server_port_returns_none_when_invalid(self, tmp_path):
        """Test reading port returns None when file has invalid content."""
        port_file = tmp_path / "server.port"
        port_file.write_text("not_a_number\n")

        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}):
            port = read_server_port()

        assert port is None

    def test_get_chad_dir_uses_env(self, tmp_path):
        """Test get_chad_dir uses CHAD_DIR env var when set."""
        with patch.dict(os.environ, {"CHAD_DIR": str(tmp_path)}):
            result = get_chad_dir()

        assert result == tmp_path

    def test_get_chad_dir_defaults_to_home(self):
        """Test get_chad_dir defaults to ~/.chad when CHAD_DIR not set."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove CHAD_DIR if present
            os.environ.pop("CHAD_DIR", None)
            result = get_chad_dir()

        assert result == Path.home() / ".chad"

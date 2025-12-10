"""Tests for Chad orchestrator."""

from unittest.mock import Mock, patch
import pytest
from pathlib import Path
from chad.orchestrator import Chad
from chad.providers import ModelConfig


class TestChad:
    """Test cases for Chad orchestrator."""

    def test_init(self, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        assert chad.project_path == tmp_path
        assert chad.task_description == "Test task"
        assert chad.session_manager is not None

    def test_completion_signal_detection(self, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        chad = Chad(coding_config, management_config, tmp_path, "Test")

        assert chad._is_task_complete_signal("TASK COMPLETE")
        assert chad._is_task_complete_signal("task_complete")
        assert chad._is_task_complete_signal("[COMPLETE]")
        assert chad._is_task_complete_signal("Implementation complete and verified")
        assert not chad._is_task_complete_signal("Working on task")
        assert not chad._is_task_complete_signal("Nearly done")

    def test_completion_signal_case_insensitive(self, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        chad = Chad(coding_config, management_config, tmp_path, "Test")

        assert chad._is_task_complete_signal("TaSk CoMpLeTe")
        assert chad._is_task_complete_signal("IMPLEMENTATION COMPLETE AND VERIFIED")

    @patch('chad.orchestrator.SessionManager')
    def test_run_start_sessions_fails(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = False
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        mock_manager.start_sessions.assert_called_once_with(str(tmp_path), "Test task")

    @patch('chad.orchestrator.SessionManager')
    def test_run_sessions_not_alive(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = False
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_no_coding_response(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.return_value = ""
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_task_complete_signal(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.return_value = "TASK COMPLETE"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_management_no_further_action(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.return_value = "Summary text"
        mock_manager.get_management_response.return_value = "No further action needed—awaiting next task."
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_relay_loop(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True

        responses = ["Working on it", "Still working", "TASK COMPLETE"]
        mock_manager.get_coding_response.side_effect = responses
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_management_response.return_value = "Keep going"
        mock_manager.stop_all = Mock()

        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        assert mock_manager.send_to_coding.call_count == 3
        assert mock_manager.send_to_management.call_count == 2

    @patch('chad.orchestrator.SessionManager')
    def test_run_no_management_response(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.return_value = "Working"
        mock_manager.get_management_response.return_value = ""
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        mock_manager.stop_all.assert_called_once()

    def test_looks_like_no_more_action(self, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        chad = Chad(coding_config, management_config, tmp_path, "Test")

        assert chad._looks_like_no_more_action("No further action needed—awaiting next task.")
        assert chad._looks_like_no_more_action("no FURTHER action needed") is True
        assert chad._looks_like_no_more_action("Continue") is False

    @patch('chad.orchestrator.SessionManager')
    def test_run_keyboard_interrupt(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = KeyboardInterrupt()
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        mock_manager.stop_all.assert_called_once()

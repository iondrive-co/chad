"""Tests for Chad orchestrator."""

from unittest.mock import Mock, patch
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

    @patch('chad.orchestrator.SessionManager')
    def test_run_uses_long_timeouts(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = ["Working", "TASK COMPLETE"]
        mock_manager.get_management_response.return_value = "Keep going"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.get_coding_response.assert_any_call(timeout=1800.0)
        mock_manager.get_management_response.assert_any_call(timeout=120.0)
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_uses_extended_gemini_timeout(self, mock_session_manager_class, tmp_path):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="gemini", model_name="default")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = ["Working", "TASK COMPLETE"]
        mock_manager.get_management_response.return_value = "Keep going"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.get_management_response.assert_any_call(timeout=600.0)
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_timeout_values_for_openai_provider(self, mock_session_manager_class, tmp_path):
        """OpenAI uses default timeouts for coding/management."""
        coding_config = ModelConfig(provider="openai", model_name="gpt-4")
        management_config = ModelConfig(provider="openai", model_name="gpt-4")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.return_value = "TASK COMPLETE"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.get_coding_response.assert_called_with(timeout=1800.0)
        mock_manager.get_management_response.assert_not_called()
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_timeout_values_for_mistral_provider(self, mock_session_manager_class, tmp_path):
        """Mistral falls back to default timeout helpers."""
        coding_config = ModelConfig(provider="mistral", model_name="default")
        management_config = ModelConfig(provider="mistral", model_name="default")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = ["Working", "TASK COMPLETE"]
        mock_manager.get_management_response.return_value = "Continue"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is True
        mock_manager.get_coding_response.assert_any_call(timeout=1800.0)
        mock_manager.get_management_response.assert_any_call(timeout=120.0)
        mock_manager.stop_all.assert_called_once()


class TestTaskPhaseStateMachine:
    """Additional Chad orchestration flow tests."""

    @patch('chad.orchestrator.SessionManager')
    def test_run_retry_logic_on_empty_coding_response(self, mock_session_manager_class, tmp_path):
        """Test retry counter logic when coding AI returns empty responses."""
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        # First three attempts return empty string, then success
        mock_manager.get_coding_response.side_effect = ["", "", "", "TASK COMPLETE"]
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        # Should fail after 3 attempts (max_retries = 3)
        assert result is False
        assert mock_manager.get_coding_response.call_count == 3
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_retry_logic_reset_on_success(self, mock_session_manager_class, tmp_path):
        """Retry counter should reset after a non-empty response."""
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = [
            "", "", "Working on it", "", "", ""
        ]
        mock_manager.get_management_response.return_value = "Continue"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        # Three retries after the reset plus the initial attempts
        assert mock_manager.get_coding_response.call_count == 6
        mock_manager.stop_all.assert_called_once()

    @patch('chad.orchestrator.SessionManager')
    def test_run_with_long_idle_then_response(self, mock_session_manager_class, tmp_path):
        """Ensure long idle periods respect the max retry limit."""
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.return_value = True
        mock_manager.get_coding_response.side_effect = ["", "", "Finally working now", "", "", ""]
        mock_manager.get_management_response.return_value = "Continue"
        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        chad = Chad(coding_config, management_config, tmp_path, "Test task")
        result = chad.run()

        assert result is False
        assert mock_manager.get_coding_response.call_count == 6
        mock_manager.stop_all.assert_called_once()

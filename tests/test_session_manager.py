"""Tests for session manager."""

from unittest.mock import Mock, patch
from chad.session_manager import SessionManager, get_coding_timeout, get_management_timeout
from chad.providers import ModelConfig


class TestSessionManager:
    """Test cases for SessionManager."""

    def test_init(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config)
        assert manager.coding_provider is None
        assert manager.management_provider is None
        assert manager.coding_config == coding_config
        assert manager.management_config == management_config
        assert manager.task_description is None

    def test_timeout_helpers(self):
        assert get_coding_timeout("anthropic") == 1800.0
        assert get_management_timeout("anthropic") == 120.0
        assert get_management_timeout("gemini") == 600.0

    def test_insane_mode_excludes_safety_constraints(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config, insane_mode=True)
        assert manager.insane_mode is True

    def test_safe_mode_includes_safety_constraints(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config, insane_mode=False)
        assert manager.insane_mode is False

        # Default should be safe mode
        manager2 = SessionManager(coding_config, management_config)
        assert manager2.insane_mode is False

    @patch('chad.session_manager.create_provider')
    def test_start_sessions_includes_safety_in_safe_mode(self, mock_create_provider):
        from chad.session_manager import SAFETY_CONSTRAINTS

        coding_provider = Mock()
        coding_provider.start_session.return_value = True

        management_provider = Mock()
        management_provider.start_session.return_value = True

        mock_create_provider.side_effect = [coding_provider, management_provider]

        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config, insane_mode=False)
        result = manager.start_sessions("/tmp/project", "Build a feature")

        assert result is True
        # Verify safety constraints were included in the prompt
        management_call_args = management_provider.start_session.call_args
        prompt_used = management_call_args[0][1]  # Second arg is the system prompt
        assert "SAFETY_CONSTRAINTS" in prompt_used
        assert "filesystem" in prompt_used
        assert "NEVER ask for deletion" in prompt_used

    @patch('chad.session_manager.create_provider')
    def test_start_sessions_excludes_safety_in_insane_mode(self, mock_create_provider):
        coding_provider = Mock()
        coding_provider.start_session.return_value = True

        management_provider = Mock()
        management_provider.start_session.return_value = True

        mock_create_provider.side_effect = [coding_provider, management_provider]

        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config, insane_mode=True)
        result = manager.start_sessions("/tmp/project", "Build a feature")

        assert result is True
        # Verify safety constraints were NOT included in the prompt
        management_call_args = management_provider.start_session.call_args
        prompt_used = management_call_args[0][1]  # Second arg is the system prompt
        # The key check is that SAFETY_CONSTRAINTS section is not in the prompt
        assert "SAFETY_CONSTRAINTS:" not in prompt_used

    @patch('chad.session_manager.create_provider')
    def test_start_sessions_success(self, mock_create_provider):
        coding_provider = Mock()
        coding_provider.start_session.return_value = True

        management_provider = Mock()
        management_provider.start_session.return_value = True

        mock_create_provider.side_effect = [coding_provider, management_provider]

        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config)
        result = manager.start_sessions("/tmp/project", "Build a feature")

        assert result is True
        assert manager.coding_provider == coding_provider
        assert manager.management_provider == management_provider
        assert manager.task_description == "Build a feature"

    @patch('chad.session_manager.create_provider')
    def test_start_sessions_coding_fails(self, mock_create_provider):
        coding_provider = Mock()
        coding_provider.start_session.return_value = False

        mock_create_provider.return_value = coding_provider

        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config)
        result = manager.start_sessions("/tmp/project", "Build a feature")

        assert result is False

    @patch('chad.session_manager.create_provider')
    def test_start_sessions_management_fails(self, mock_create_provider):
        coding_provider = Mock()
        coding_provider.start_session.return_value = True
        coding_provider.stop_session = Mock()

        management_provider = Mock()
        management_provider.start_session.return_value = False

        mock_create_provider.side_effect = [coding_provider, management_provider]

        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")

        manager = SessionManager(coding_config, management_config)
        result = manager.start_sessions("/tmp/project", "Build a feature")

        assert result is False
        coding_provider.stop_session.assert_called_once()

    def test_send_to_coding(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_provider = Mock()
        manager.coding_provider = mock_provider

        manager.send_to_coding("Test message")
        mock_provider.send_message.assert_called_once_with("Test message")

    def test_send_to_coding_no_provider(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        # Should not raise when provider is None
        manager.send_to_coding("Test message")

    def test_get_coding_response(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_provider = Mock()
        mock_provider.get_response.return_value = "Response text"
        manager.coding_provider = mock_provider

        response = manager.get_coding_response(timeout=10.0)
        assert response == "Response text"
        mock_provider.get_response.assert_called_once_with(10.0)

    def test_get_coding_response_no_provider(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        response = manager.get_coding_response()
        assert response == ""

    def test_send_to_management(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_provider = Mock()
        manager.management_provider = mock_provider

        manager.send_to_management("Test message")
        mock_provider.send_message.assert_called_once_with("Test message")

    def test_send_to_management_no_provider(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        # Should not raise when provider is None
        manager.send_to_management("Test message")

    def test_get_management_response(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_provider = Mock()
        mock_provider.get_response.return_value = "Management response"
        manager.management_provider = mock_provider

        response = manager.get_management_response(timeout=15.0)
        assert response == "Management response"
        mock_provider.get_response.assert_called_once_with(15.0)

    def test_get_management_response_no_provider(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        response = manager.get_management_response()
        assert response == ""

    def test_stop_all(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_coding = Mock()
        mock_management = Mock()
        manager.coding_provider = mock_coding
        manager.management_provider = mock_management

        manager.stop_all()
        mock_coding.stop_session.assert_called_once()
        mock_management.stop_session.assert_called_once()

    def test_stop_all_no_providers(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        manager.stop_all()

    def test_are_sessions_alive_both_alive(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_coding = Mock()
        mock_coding.is_alive.return_value = True
        mock_management = Mock()
        mock_management.is_alive.return_value = True

        manager.coding_provider = mock_coding
        manager.management_provider = mock_management

        assert manager.are_sessions_alive() is True

    def test_are_sessions_alive_coding_dead(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        mock_coding = Mock()
        mock_coding.is_alive.return_value = False
        mock_management = Mock()
        mock_management.is_alive.return_value = True

        manager.coding_provider = mock_coding
        manager.management_provider = mock_management

        assert manager.are_sessions_alive() is False

    def test_are_sessions_alive_no_providers(self):
        coding_config = ModelConfig(provider="anthropic", model_name="claude")
        management_config = ModelConfig(provider="anthropic", model_name="claude")
        manager = SessionManager(coding_config, management_config)

        assert manager.are_sessions_alive() is False

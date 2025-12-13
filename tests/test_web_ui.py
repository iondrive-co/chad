"""Tests for web UI module."""

from unittest.mock import Mock, patch, MagicMock
import pytest


class TestChadWebUI:
    """Test cases for ChadWebUI class."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic', 'gpt': 'openai'}
        mgr.list_role_assignments.return_value = {'CODING': 'claude', 'MANAGEMENT': 'gpt'}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    def test_init(self, web_ui, mock_security_mgr):
        """Test ChadWebUI initialization."""
        assert web_ui.security_mgr == mock_security_mgr
        assert web_ui.main_password == 'test-password'

    def test_list_providers_with_accounts(self, web_ui):
        """Test listing providers when accounts exist."""
        result = web_ui.list_providers()

        assert 'claude' in result
        assert 'anthropic' in result
        assert 'gpt' in result
        assert 'openai' in result
        assert 'CODING' in result
        assert 'MANAGEMENT' in result

    def test_list_providers_empty(self, mock_security_mgr):
        """Test listing providers when no accounts exist."""
        from chad.web_ui import ChadWebUI
        mock_security_mgr.list_accounts.return_value = {}
        mock_security_mgr.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        result = web_ui.list_providers()

        assert 'No providers configured yet' in result

    def test_add_provider_success(self, web_ui, mock_security_mgr):
        """Test adding a new provider successfully."""
        mock_security_mgr.list_accounts.return_value = {}

        result = web_ui.add_provider('my-claude', 'anthropic')[0]

        assert '✓' in result
        assert 'my-claude' in result
        # Either shows authenticate instructions or confirms logged in
        assert 'authenticate' in result.lower() or 'logged in' in result.lower()
        mock_security_mgr.store_account.assert_called_once_with(
            'my-claude', 'anthropic', '', 'test-password'
        )

    @patch('subprocess.run')
    def test_add_provider_auto_name(self, mock_run, web_ui, mock_security_mgr, tmp_path):
        """Test adding provider with auto-generated name."""
        mock_security_mgr.list_accounts.return_value = {}
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with patch.object(web_ui, '_setup_codex_account', return_value=str(tmp_path)):
            result = web_ui.add_provider('', 'openai')[0]

        assert '✓' in result or 'Provider' in result
        assert 'openai' in result
        mock_security_mgr.store_account.assert_called_once_with(
            'openai', 'openai', '', 'test-password'
        )

    def test_add_provider_duplicate_name(self, web_ui, mock_security_mgr):
        """Test adding provider when name already exists."""
        mock_security_mgr.list_accounts.return_value = {'anthropic': 'anthropic'}

        result = web_ui.add_provider('', 'anthropic')[0]

        # Should create anthropic-1
        assert '✓' in result
        mock_security_mgr.store_account.assert_called_once_with(
            'anthropic-1', 'anthropic', '', 'test-password'
        )

    def test_add_provider_error(self, web_ui, mock_security_mgr):
        """Test adding provider when error occurs."""
        mock_security_mgr.list_accounts.return_value = {}
        mock_security_mgr.store_account.side_effect = Exception("Storage error")

        result = web_ui.add_provider('test', 'anthropic')[0]

        assert '❌' in result
        assert 'Error' in result

    def test_assign_role_success(self, web_ui, mock_security_mgr):
        """Test assigning a role successfully."""
        result = web_ui.assign_role('claude', 'CODING')[0]

        assert '✓' in result
        assert 'CODING' in result
        mock_security_mgr.assign_role.assert_called_once_with('claude', 'CODING')

    def test_assign_role_not_found(self, web_ui, mock_security_mgr):
        """Test assigning role to non-existent provider."""
        result = web_ui.assign_role('nonexistent', 'CODING')[0]

        assert '❌' in result
        assert 'not found' in result

    def test_assign_role_lowercase_converted(self, web_ui, mock_security_mgr):
        """Test that lowercase role names are converted to uppercase."""
        result = web_ui.assign_role('claude', 'coding')[0]

        mock_security_mgr.assign_role.assert_called_once_with('claude', 'CODING')

    def test_assign_role_missing_account(self, web_ui, mock_security_mgr):
        """Test assigning role without selecting account."""
        result = web_ui.assign_role('', 'CODING')[0]

        assert '❌' in result
        assert 'select an account' in result

    def test_assign_role_missing_role(self, web_ui, mock_security_mgr):
        """Test assigning role without selecting role."""
        result = web_ui.assign_role('claude', '')[0]

        assert '❌' in result
        assert 'select a role' in result

    def test_delete_provider_success(self, web_ui, mock_security_mgr):
        """Test deleting a provider successfully."""
        result = web_ui.delete_provider('claude', True)[0]

        assert '✓' in result
        assert 'deleted' in result
        mock_security_mgr.delete_account.assert_called_once_with('claude')

    def test_delete_provider_requires_confirmation(self, web_ui, mock_security_mgr):
        """Test that deletion requires confirmation."""
        result = web_ui.delete_provider('claude', False)[0]

        # When not confirmed, deletion is cancelled
        assert 'cancelled' in result.lower()
        mock_security_mgr.delete_account.assert_not_called()

    def test_delete_provider_error(self, web_ui, mock_security_mgr):
        """Test deleting provider when error occurs."""
        mock_security_mgr.delete_account.side_effect = Exception("Delete error")

        result = web_ui.delete_provider('claude', True)[0]

        assert '❌' in result
        assert 'Error' in result

    def test_delete_provider_missing_account(self, web_ui, mock_security_mgr):
        """Test deleting provider without selecting account."""
        result = web_ui.delete_provider('', False)[0]

        assert '❌' in result
        assert 'no provider selected' in result.lower()

    def test_get_account_choices(self, web_ui, mock_security_mgr):
        """Test getting account choices for dropdowns."""
        choices = web_ui.get_account_choices()

        assert 'claude' in choices
        assert 'gpt' in choices

    def test_cancel_task(self, web_ui, mock_security_mgr):
        """Test cancelling a running task."""
        mock_session_mgr = Mock()
        web_ui.session_manager = mock_session_mgr

        result = web_ui.cancel_task()

        assert '🛑' in result
        assert 'cancelled' in result.lower()
        assert web_ui.cancel_requested is True
        mock_session_mgr.stop_all.assert_called_once()

    def test_cancel_task_no_session(self, web_ui, mock_security_mgr):
        """Test cancelling when no session is running."""
        web_ui.session_manager = None

        result = web_ui.cancel_task()

        assert '🛑' in result
        assert web_ui.cancel_requested is True


class TestChadWebUITaskExecution:
    """Test cases for task execution in ChadWebUI."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic'}
        mgr.list_role_assignments.return_value = {'CODING': 'claude', 'MANAGEMENT': 'claude'}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    def test_start_task_missing_project(self, web_ui):
        """Test starting task without project path."""
        results = list(web_ui.start_chad_task('', 'do something', False))

        assert len(results) > 0
        last_result = results[-1]
        assert '❌' in last_result[1]
        assert 'project path' in last_result[1].lower() or 'task description' in last_result[1].lower()

    def test_start_task_missing_description(self, web_ui):
        """Test starting task without task description."""
        results = list(web_ui.start_chad_task('/tmp', '', False))

        assert len(results) > 0
        last_result = results[-1]
        assert '❌' in last_result[1]

    def test_start_task_invalid_path(self, web_ui):
        """Test starting task with invalid project path."""
        results = list(web_ui.start_chad_task('/nonexistent/path/xyz', 'do something', False))

        assert len(results) > 0
        last_result = results[-1]
        assert '❌' in last_result[1]
        assert 'Invalid project path' in last_result[1]

    def test_start_task_missing_roles(self, mock_security_mgr):
        """Test starting task when roles are not assigned."""
        from chad.web_ui import ChadWebUI
        mock_security_mgr.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        results = list(web_ui.start_chad_task('/tmp', 'do something', False))

        assert len(results) > 0
        last_result = results[-1]
        assert '❌' in last_result[1]
        assert 'CODING' in last_result[1] or 'MANAGEMENT' in last_result[1]


class TestChadWebUIInterface:
    """Test cases for Gradio interface creation."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {}
        mgr.list_role_assignments.return_value = {}
        return mgr

    @patch('chad.web_ui.gr')
    def test_create_interface(self, mock_gr, mock_security_mgr):
        """Test that create_interface creates a Gradio Blocks interface."""
        from chad.web_ui import ChadWebUI

        # Mock the Gradio components
        mock_blocks = MagicMock()
        mock_gr.Blocks.return_value.__enter__ = Mock(return_value=mock_blocks)
        mock_gr.Blocks.return_value.__exit__ = Mock(return_value=None)

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        web_ui.create_interface()

        # Verify Blocks was called
        mock_gr.Blocks.assert_called_once()


class TestLaunchWebUI:
    """Test cases for launch_web_ui function."""

    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_with_existing_password(self, mock_security_class, mock_webui_class):
        """Test launching with existing user and password."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security.load_config.return_value = {'password_hash': 'hash'}
        mock_security.verify_main_password.return_value = 'test-password'
        mock_security_class.return_value = mock_security

        mock_app = Mock()
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        launch_web_ui('test-password')

        mock_security.verify_main_password.assert_called_once()
        mock_webui_class.assert_called_once_with(mock_security, 'test-password')
        mock_app.launch.assert_called_once()

    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_with_wrong_password(self, mock_security_class, mock_webui_class):
        """Test launching with wrong password raises error."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security.load_config.return_value = {'password_hash': 'hash'}
        mock_security.verify_main_password.side_effect = ValueError("Incorrect password")
        mock_security_class.return_value = mock_security

        with pytest.raises(ValueError, match="Incorrect password"):
            launch_web_ui('wrong-password')

        mock_security.verify_main_password.assert_called_once()

    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_first_run_with_password(self, mock_security_class, mock_webui_class):
        """Test launching on first run with password provided."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security.hash_password.return_value = 'hashed'
        mock_security_class.return_value = mock_security

        mock_app = Mock()
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        launch_web_ui('new-password')

        mock_security.hash_password.assert_called_once_with('new-password')
        mock_security.save_config.assert_called_once()
        mock_app.launch.assert_called_once()


class TestGeminiUsage:
    """Test cases for Gemini usage stats parsing."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'gemini': 'gemini'}
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    @patch('pathlib.Path.home')
    def test_gemini_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage when not logged in."""
        mock_home.return_value = tmp_path
        (tmp_path / ".gemini").mkdir()
        # No oauth_creds.json file

        result = web_ui._get_gemini_usage()

        assert '❌' in result
        assert 'Not logged in' in result

    @patch('pathlib.Path.home')
    def test_gemini_logged_in_no_sessions(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage when logged in but no session data."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')
        # No tmp directory

        result = web_ui._get_gemini_usage()

        assert '✅' in result
        assert 'Logged in' in result
        assert 'No session data' in result

    @patch('pathlib.Path.home')
    def test_gemini_usage_aggregates_models(self, mock_home, web_ui, tmp_path):
        """Test Gemini usage aggregates token counts by model."""
        import json

        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        # Create session file with model usage data
        session_dir = gemini_dir / "tmp" / "project123" / "chats"
        session_dir.mkdir(parents=True)

        session_data = {
            "sessionId": "test-session",
            "messages": [
                {
                    "type": "gemini",
                    "model": "gemini-2.5-pro",
                    "tokens": {"input": 1000, "output": 100, "cached": 500}
                },
                {
                    "type": "gemini",
                    "model": "gemini-2.5-pro",
                    "tokens": {"input": 2000, "output": 200, "cached": 1000}
                },
                {
                    "type": "gemini",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 500, "output": 50, "cached": 200}
                },
                {"type": "user", "content": "test"},  # Should be ignored
            ]
        }
        (session_dir / "session-test.json").write_text(json.dumps(session_data))

        result = web_ui._get_gemini_usage()

        assert '✅' in result
        assert 'Model Usage' in result
        assert 'gemini-2.5-pro' in result
        assert 'gemini-2.5-flash' in result
        assert '3,000' in result  # 1000 + 2000 input for pro
        assert '300' in result    # 100 + 200 output for pro
        assert 'Cache savings' in result


class TestModelSelection:
    """Test cases for model selection functionality."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic', 'gpt': 'openai'}
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    def test_set_model_success(self, web_ui, mock_security_mgr):
        """Test setting model successfully."""
        result = web_ui.set_model('claude', 'claude-opus-4-20250514')[0]

        assert '✓' in result
        assert 'claude-opus-4-20250514' in result
        mock_security_mgr.set_account_model.assert_called_once_with('claude', 'claude-opus-4-20250514')

    def test_set_model_missing_account(self, web_ui, mock_security_mgr):
        """Test setting model without selecting account."""
        result = web_ui.set_model('', 'some-model')[0]

        assert '❌' in result
        assert 'select an account' in result

    def test_set_model_missing_model(self, web_ui, mock_security_mgr):
        """Test setting model without selecting model."""
        result = web_ui.set_model('claude', '')[0]

        assert '❌' in result
        assert 'select a model' in result

    def test_set_model_account_not_found(self, web_ui, mock_security_mgr):
        """Test setting model for non-existent account."""
        result = web_ui.set_model('nonexistent', 'some-model')[0]

        assert '❌' in result
        assert 'not found' in result

    def test_get_models_for_anthropic(self, web_ui):
        """Test getting models for anthropic provider."""
        models = web_ui.get_models_for_account('claude')

        assert 'claude-sonnet-4-20250514' in models
        assert 'claude-opus-4-20250514' in models
        assert 'default' in models

    def test_get_models_for_openai(self, web_ui):
        """Test getting models for openai provider."""
        models = web_ui.get_models_for_account('gpt')

        assert 'o3' in models
        assert 'o4-mini' in models
        assert 'default' in models

    def test_get_models_for_unknown_account(self, web_ui):
        """Test getting models for unknown account returns default."""
        models = web_ui.get_models_for_account('unknown')

        assert models == ['default']

    def test_get_models_for_empty_account(self, web_ui):
        """Test getting models with empty account name."""
        models = web_ui.get_models_for_account('')

        assert models == ['default']

    def test_provider_models_constant(self, web_ui):
        """Test that PROVIDER_MODELS includes expected providers."""
        from chad.web_ui import ChadWebUI

        assert 'anthropic' in ChadWebUI.PROVIDER_MODELS
        assert 'openai' in ChadWebUI.PROVIDER_MODELS
        assert 'gemini' in ChadWebUI.PROVIDER_MODELS


class TestUILayout:
    """Test cases for UI layout and CSS."""


class TestStateMachineIntegration:
    """Integration tests for the state machine relay loop."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager with roles assigned."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'coding-ai': 'anthropic', 'mgmt-ai': 'openai'}
        mgr.list_role_assignments.return_value = {'CODING': 'coding-ai', 'MANAGEMENT': 'mgmt-ai'}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    @patch('chad.web_ui.SessionManager')
    def test_immediate_plan_accepted(self, mock_session_manager_class, web_ui, tmp_path):
        """Test that management can create a plan immediately without investigation."""
        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.side_effect = [True, True, True, False]

        # Management immediately outputs a PLAN without investigating - should be accepted
        mock_manager.get_management_response.return_value = "PLAN:\n1. Do something\n2. Do another thing"

        # Coding AI response for implementation
        mock_manager.get_coding_response.return_value = "Done. Completed both steps."

        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        # Create a test directory
        test_dir = tmp_path / "test_project"
        test_dir.mkdir()

        # Run the task
        results = []
        for i, result in enumerate(web_ui.start_chad_task(str(test_dir), 'test task', False)):
            results.append(result)
            if i > 5:
                web_ui.cancel_requested = True
                break

        # Check that plan was sent to coding AI (implementation started)
        coding_calls = mock_manager.send_to_coding.call_args_list
        assert len(coding_calls) >= 1
        first_coding_call = str(coding_calls[0])
        assert 'plan' in first_coding_call.lower() or 'execute' in first_coding_call.lower()

    @patch('chad.web_ui.SessionManager')
    def test_plan_accepted_after_investigation(self, mock_session_manager_class, web_ui, tmp_path):
        """Test that plan is accepted after proper investigation."""
        mock_manager = Mock()
        mock_manager.start_sessions.return_value = True
        mock_manager.are_sessions_alive.side_effect = [True, True, True, True, False]  # Stop after a few iterations

        # Management asks investigation question first, then creates plan
        mgmt_responses = [
            "Please search for files related to the header component",  # Investigation question
            "PLAN:\n1. Modify header.css\n2. Update colors",            # Plan after receiving findings
        ]
        mock_manager.get_management_response.side_effect = mgmt_responses

        # Coding AI provides investigation findings
        mock_manager.get_coding_response.return_value = "Found: src/header.css with current styles"

        mock_manager.stop_all = Mock()
        mock_session_manager_class.return_value = mock_manager

        test_dir = tmp_path / "test_project"
        test_dir.mkdir()

        results = list(web_ui.start_chad_task(str(test_dir), 'change header colors', False))

        # Verify that coding AI was called for investigation
        coding_calls = mock_manager.send_to_coding.call_args_list
        assert len(coding_calls) >= 1
        # First coding call should be the investigation question
        first_coding_call = str(coding_calls[0])
        assert 'header' in first_coding_call.lower() or 'search' in first_coding_call.lower()

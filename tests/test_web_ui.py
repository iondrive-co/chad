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
        mgr.get_account_reasoning.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.web_ui import ChadWebUI
        ui = ChadWebUI(mock_security_mgr, 'test-password')
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "/tmp/codex"))
        return ui

    def test_init(self, web_ui, mock_security_mgr):
        """Test ChadWebUI initialization."""
        assert web_ui.security_mgr == mock_security_mgr
        assert web_ui.main_password == 'test-password'

    def test_progress_bar_helper(self, web_ui):
        """Progress bar helper should clamp values and preserve width."""
        half_bar = web_ui._progress_bar(50)
        assert len(half_bar) == 20
        assert half_bar.startswith('█████')
        assert half_bar.endswith('░░░░░')
        full_bar = web_ui._progress_bar(150)
        assert full_bar == '█' * 20

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

        with patch.object(web_ui.provider_ui, '_setup_codex_account', return_value=str(tmp_path)):
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
        web_ui.assign_role('claude', 'coding')

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

    def test_set_reasoning_success(self, web_ui, mock_security_mgr):
        """Test setting reasoning level for an account."""
        result = web_ui.set_reasoning('claude', 'high')[0]

        assert '✓' in result
        assert 'high' in result
        mock_security_mgr.set_account_reasoning.assert_called_once_with('claude', 'high')

    def test_add_provider_install_failure(self, web_ui, mock_security_mgr):
        """Installer failures should surface to the user."""
        web_ui.provider_ui.installer.ensure_tool = Mock(return_value=(False, "Node missing"))
        mock_security_mgr.list_accounts.return_value = {}

        result = web_ui.add_provider('', 'openai')[0]

        assert '❌' in result
        assert 'Node missing' in result
        mock_security_mgr.store_account.assert_not_called()

    def test_get_models_includes_stored_model(self, web_ui, mock_security_mgr, tmp_path):
        """Stored models should always be present in dropdown choices."""
        mock_security_mgr.list_accounts.return_value = {'gpt': 'openai'}
        mock_security_mgr.get_account_model.return_value = 'gpt-5.1-codex-max'
        from chad.model_catalog import ModelCatalog

        web_ui.model_catalog = ModelCatalog(security_mgr=mock_security_mgr, home_dir=tmp_path, cache_ttl=0)
        models = web_ui.get_models_for_account('gpt')

        assert 'gpt-5.1-codex-max' in models
        assert 'default' in models

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
        mgr.get_account_reasoning.return_value = 'default'
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
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'project path' in status_value.lower() or 'task description' in status_value.lower()

    def test_start_task_missing_description(self, web_ui):
        """Test starting task without task description."""
        results = list(web_ui.start_chad_task('/tmp', '', False))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value

    def test_start_task_invalid_path(self, web_ui):
        """Test starting task with invalid project path."""
        results = list(web_ui.start_chad_task('/nonexistent/path/xyz', 'do something', False))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'Invalid project path' in status_value

    def test_start_task_missing_roles(self, mock_security_mgr):
        """Test starting task when roles are not assigned."""
        from chad.web_ui import ChadWebUI
        mock_security_mgr.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        results = list(web_ui.start_chad_task('/tmp', 'do something', False))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'CODING' in status_value or 'MANAGEMENT' in status_value


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
        """Test launching with existing user and provided password (trusted)."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security.load_config.return_value = {'password_hash': 'hash'}
        mock_security_class.return_value = mock_security

        mock_server = Mock()
        mock_server.server_port = 7860
        mock_app = Mock()
        mock_app.launch.return_value = (mock_server, 'http://127.0.0.1:7860', None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui('test-password')

        # When password is provided, verify_main_password should NOT be called
        mock_security.verify_main_password.assert_not_called()
        mock_webui_class.assert_called_once_with(mock_security, 'test-password')
        mock_app.launch.assert_called_once()
        assert result == (None, 7860)

    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_without_password_verifies(self, mock_security_class, mock_webui_class):
        """Test launching without password triggers verification."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security.load_config.return_value = {'password_hash': 'hash'}
        mock_security.verify_main_password.return_value = 'verified-password'
        mock_security_class.return_value = mock_security

        mock_server = Mock()
        mock_server.server_port = 7860
        mock_app = Mock()
        mock_app.launch.return_value = (mock_server, 'http://127.0.0.1:7860', None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui(None)

        mock_security.verify_main_password.assert_called_once()
        mock_webui_class.assert_called_once_with(mock_security, 'verified-password')
        assert result == (None, 7860)

    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_first_run_with_password(self, mock_security_class, mock_webui_class):
        """Test launching on first run with password provided."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = True
        mock_security.hash_password.return_value = 'hashed'
        mock_security_class.return_value = mock_security

        mock_server = Mock()
        mock_server.server_port = 7860
        mock_app = Mock()
        mock_app.launch.return_value = (mock_server, 'http://127.0.0.1:7860', None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui('new-password')

        mock_security.hash_password.assert_called_once_with('new-password')
        mock_security.save_config.assert_called_once()
        mock_app.launch.assert_called_once()
        assert result == (None, 7860)


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
        assert 'Usage data unavailable' in result

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

        assert 'anthropic' in ChadWebUI.SUPPORTED_PROVIDERS
        assert 'openai' in ChadWebUI.SUPPORTED_PROVIDERS
        assert 'gemini' in ChadWebUI.SUPPORTED_PROVIDERS


class TestUILayout:
    """Test cases for UI layout and CSS."""


class TestRemainingUsage:
    """Test cases for remaining_usage calculation and sorting."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic', 'codex': 'openai', 'gemini': 'gemini'}
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    def test_remaining_usage_unknown_account(self, web_ui):
        """Unknown account returns 0.0."""
        result = web_ui.get_remaining_usage('nonexistent')
        assert result == 0.0

    @patch('pathlib.Path.home')
    def test_gemini_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Gemini not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".gemini").mkdir()

        result = web_ui._get_gemini_remaining_usage()
        assert result == 0.0

    @patch('pathlib.Path.home')
    def test_gemini_remaining_usage_logged_in(self, mock_home, web_ui, tmp_path):
        """Gemini logged in returns low estimate (0.3)."""
        mock_home.return_value = tmp_path
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{"access_token": "test"}')

        result = web_ui._get_gemini_remaining_usage()
        assert result == 0.3

    @patch('pathlib.Path.home')
    def test_mistral_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".vibe").mkdir()

        result = web_ui._get_mistral_remaining_usage()
        assert result == 0.0

    @patch('pathlib.Path.home')
    def test_mistral_remaining_usage_logged_in(self, mock_home, web_ui, tmp_path):
        """Mistral logged in returns low estimate (0.3)."""
        mock_home.return_value = tmp_path
        vibe_dir = tmp_path / ".vibe"
        vibe_dir.mkdir()
        (vibe_dir / "config.toml").write_text('[general]\napi_key = "test"')

        result = web_ui._get_mistral_remaining_usage()
        assert result == 0.3

    @patch('pathlib.Path.home')
    def test_claude_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Claude not logged in returns 0.0."""
        mock_home.return_value = tmp_path
        (tmp_path / ".claude").mkdir()

        result = web_ui._get_claude_remaining_usage()
        assert result == 0.0

    @patch('pathlib.Path.home')
    @patch('requests.get')
    def test_claude_remaining_usage_from_api(self, mock_get, mock_home, web_ui, tmp_path):
        """Claude calculates remaining from API utilization."""
        import json
        mock_home.return_value = tmp_path
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = {'claudeAiOauth': {'accessToken': 'test-token', 'subscriptionType': 'PRO'}}
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'five_hour': {'utilization': 25}}
        mock_get.return_value = mock_response

        result = web_ui._get_claude_remaining_usage()
        assert result == 0.75  # 1.0 - 0.25

    @patch('pathlib.Path.home')
    def test_codex_remaining_usage_not_logged_in(self, mock_home, web_ui, tmp_path):
        """Codex not logged in returns 0.0."""
        mock_home.return_value = tmp_path

        result = web_ui._get_codex_remaining_usage('codex')
        assert result == 0.0


class TestSessionLogging:
    """Test cases for session log saving."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic'}
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    def test_session_log_lifecycle(self, web_ui):
        """Session log should be created, updated, and finalized correctly."""
        import json
        import tempfile
        from pathlib import Path

        # Create initial session log
        log_path = web_ui.session_logger.create_log(
            task_description="Test task",
            project_path="/tmp/test-project",
            coding_account="claude",
            coding_provider="anthropic",
            management_account="gpt",
            management_provider="openai",
            managed_mode=False
        )

        assert log_path is not None
        assert log_path.exists()
        assert str(log_path).startswith(tempfile.gettempdir())
        assert "chad" in str(log_path)  # In /tmp/chad/ directory
        assert "chad_session_" in str(log_path)
        assert str(log_path).endswith(".json")

        # Verify initial content
        with open(log_path) as f:
            data = json.load(f)

        assert data["task_description"] == "Test task"
        assert data["project_path"] == "/tmp/test-project"
        assert data["status"] == "running"
        assert data["success"] is None
        assert data["managed_mode"] is False
        assert len(data["conversation"]) == 0

        # Update with conversation
        chat_history = [
            {"role": "user", "content": "**MANAGEMENT:** Plan the task"},
            {"role": "assistant", "content": "**CODING:** Done!"}
        ]
        web_ui.session_logger.update_log(log_path, chat_history)

        with open(log_path) as f:
            data = json.load(f)
        assert len(data["conversation"]) == 2
        assert data["status"] == "running"

        # Final update with completion
        web_ui.session_logger.update_log(
            log_path, chat_history,
            success=True,
            completion_reason="Task completed successfully",
            status="completed"
        )

        with open(log_path) as f:
            data = json.load(f)
        assert data["success"] is True
        assert data["completion_reason"] == "Task completed successfully"
        assert data["status"] == "completed"
        assert data["coding"]["account"] == "claude"
        assert data["coding"]["provider"] == "anthropic"
        assert data["management"]["account"] == "gpt"
        assert data["management"]["provider"] == "openai"

        # Cleanup
        log_path.unlink()
        # Also clean up the chad directory if empty
        chad_dir = Path(tempfile.gettempdir()) / "chad"
        if chad_dir.exists() and not any(chad_dir.iterdir()):
            chad_dir.rmdir()


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

        # Run the task in managed mode (third param True)
        results = []
        for i, result in enumerate(web_ui.start_chad_task(str(test_dir), 'test task', True)):
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

        # Run in managed mode (third param True)
        list(web_ui.start_chad_task(str(test_dir), 'change header colors', True))

        # Verify that coding AI was called for investigation
        coding_calls = mock_manager.send_to_coding.call_args_list
        assert len(coding_calls) >= 1
        # First coding call should be the investigation question
        first_coding_call = str(coding_calls[0])
        assert 'header' in first_coding_call.lower() or 'search' in first_coding_call.lower()


class TestDeleteConfirmationIcon:
    """Test that delete confirmation button shows tick icon."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic', 'gpt': 'openai'}
        mgr.list_role_assignments.return_value = {'CODING': 'claude'}
        mgr.get_account_model.return_value = 'default'
        mgr.get_account_reasoning.return_value = 'default'
        return mgr

    def test_delete_button_shows_tick_for_pending(self, mock_security_mgr):
        """Delete button should show tick symbol when pending confirmation."""
        from chad.provider_ui import ProviderUIManager

        provider_ui = ProviderUIManager(mock_security_mgr, 'test-password')
        state = provider_ui.provider_state(5, pending_delete='claude')

        # Check that one of the delete button updates contains tick symbol
        found_tick = False
        for item in state:
            if isinstance(item, dict) and 'value' in item:
                if item.get('value') == '✓':
                    found_tick = True
                    break
        assert found_tick, "Delete confirmation button should show ✓ symbol"

    def test_delete_button_shows_trash_normally(self, mock_security_mgr):
        """Delete button should show trash icon when not pending."""
        from chad.provider_ui import ProviderUIManager

        provider_ui = ProviderUIManager(mock_security_mgr, 'test-password')
        state = provider_ui.provider_state(5, pending_delete=None)

        # Check that delete buttons show trash icon
        found_trash = False
        for item in state:
            if isinstance(item, dict) and 'value' in item:
                if '🗑' in str(item.get('value', '')):
                    found_trash = True
                    break
        assert found_trash, "Delete button should show trash icon when not pending"


class TestSessionLogIncludesTask:
    """Test that session log conversation includes the task description."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager with roles assigned."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'coding-ai': 'mock', 'mgmt-ai': 'mock'}
        mgr.list_role_assignments.return_value = {'CODING': 'coding-ai', 'MANAGEMENT': 'mgmt-ai'}
        mgr.get_account_model.return_value = 'default'
        mgr.get_account_reasoning.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance."""
        from chad.web_ui import ChadWebUI
        return ChadWebUI(mock_security_mgr, 'test-password')

    @patch('chad.providers.create_provider')
    def test_session_log_starts_with_task(self, mock_create_provider, web_ui, tmp_path):
        """Session log should include task description as first message."""
        import json

        # Setup mock provider
        mock_provider = Mock()
        mock_provider.start_session.return_value = True
        mock_provider.get_response.return_value = "Task completed successfully"
        mock_provider.stop_session.return_value = None
        mock_provider.is_alive.return_value = False  # Task completes immediately
        mock_create_provider.return_value = mock_provider

        test_dir = tmp_path / "test_project"
        test_dir.mkdir()

        task_description = "Fix the login bug"

        # Run task in direct mode (third param False or omitted)
        list(web_ui.start_chad_task(str(test_dir), task_description, False))

        # Get the session log path
        session_log_path = web_ui.current_session_log_path
        assert session_log_path is not None
        assert session_log_path.exists()

        # Read and verify the log contains the task description in conversation
        with open(session_log_path) as f:
            data = json.load(f)

        # Conversation should include the task description
        assert len(data["conversation"]) >= 1
        first_message = data["conversation"][0]
        assert "Task" in first_message.get("content", "")
        assert task_description in first_message.get("content", "")

        # Cleanup
        session_log_path.unlink(missing_ok=True)


class TestAnsiToHtml:
    """Test that ANSI escape codes are properly converted to HTML spans."""

    def test_converts_basic_color_codes_to_html(self):
        """Basic SGR color codes should be converted to HTML spans."""
        from chad.web_ui import ansi_to_html
        # Purple/magenta color code
        text = "\x1b[35mPurple text\x1b[0m"
        result = ansi_to_html(text)
        assert '<span style="color:rgb(' in result
        assert "Purple text" in result
        assert "</span>" in result
        assert "\x1b" not in result

    def test_converts_256_color_codes(self):
        """256-color codes should be converted to HTML spans."""
        from chad.web_ui import ansi_to_html
        # 256-color purple
        text = "\x1b[38;5;141mColored\x1b[0m"
        result = ansi_to_html(text)
        assert "Colored" in result
        assert '<span style="color:rgb(' in result

    def test_converts_rgb_color_codes(self):
        """RGB true-color codes should be converted to HTML spans."""
        from chad.web_ui import ansi_to_html
        # RGB purple
        text = "\x1b[38;2;198;120;221mRGB color\x1b[0m"
        result = ansi_to_html(text)
        assert "RGB color" in result
        assert '<span style="color:rgb(' in result

    def test_strips_cursor_codes(self):
        """Cursor control sequences with ? should be stripped."""
        from chad.web_ui import ansi_to_html
        # Show/hide cursor - these use different ending chars, should be skipped
        text = "\x1b[?25hVisible\x1b[?25l"
        result = ansi_to_html(text)
        assert "Visible" in result

    def test_strips_osc_sequences(self):
        """OSC sequences (like terminal title) should be stripped."""
        from chad.web_ui import ansi_to_html
        # Set terminal title - uses different format, should be skipped
        text = "\x1b]0;My Title\x07Content here"
        result = ansi_to_html(text)
        assert "Content here" in result

    def test_preserves_newlines(self):
        """Newlines should be preserved."""
        from chad.web_ui import ansi_to_html
        text = "Line 1\n\nLine 3"
        result = ansi_to_html(text)
        assert result == "Line 1\n\nLine 3"

    def test_escapes_html_entities(self):
        """HTML entities should be escaped."""
        from chad.web_ui import ansi_to_html
        text = "<script>alert('xss')</script>"
        result = ansi_to_html(text)
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_converts_unclosed_color_codes(self):
        """Unclosed color codes should generate HTML span that closes at end."""
        from chad.web_ui import ansi_to_html
        # Color without reset
        text = "\x1b[35mPurple start\n\nText after blank line"
        result = ansi_to_html(text)
        assert '<span style="color:rgb(' in result
        assert "Purple start" in result
        assert "Text after blank line" in result
        # Span should be auto-closed at end
        assert result.endswith("</span>")
        assert "\x1b" not in result

    def test_handles_stray_escape_characters(self):
        """Stray escape characters in non-m sequences should be handled."""
        from chad.web_ui import ansi_to_html
        # Stray escape that doesn't match known patterns - skipped
        text = "Before\x1b[999zAfter"
        result = ansi_to_html(text)
        # The content before and after should be present
        assert "Before" in result
        assert "After" in result

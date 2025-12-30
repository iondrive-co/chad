"""Tests for web UI module."""

import re
import socket
from unittest.mock import Mock, patch, MagicMock
import pytest


class TestChadWebUI:
    """Test cases for ChadWebUI class."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic', 'gpt': 'openai'}
        mgr.list_role_assignments.return_value = {'CODING': 'claude'}
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

    def test_list_providers_empty(self, mock_security_mgr):
        """Test listing providers when no accounts exist."""
        from chad.web_ui import ChadWebUI
        mock_security_mgr.list_accounts.return_value = {}
        mock_security_mgr.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        result = web_ui.list_providers()

        assert 'No providers configured yet' in result

    @patch('subprocess.run')
    def test_add_provider_success(self, mock_run, web_ui, mock_security_mgr, tmp_path):
        """Test adding a new provider successfully (OpenAI/Codex)."""
        mock_security_mgr.list_accounts.return_value = {}
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with patch.object(web_ui.provider_ui, '_setup_codex_account', return_value=str(tmp_path)):
            result = web_ui.add_provider('my-codex', 'openai')[0]

        assert '✅' in result or '✓' in result
        assert 'my-codex' in result
        mock_security_mgr.store_account.assert_called_once_with(
            'my-codex', 'openai', '', 'test-password'
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

    @patch('subprocess.run')
    def test_add_provider_duplicate_name(self, mock_run, web_ui, mock_security_mgr, tmp_path):
        """Test adding provider when name already exists (OpenAI/Codex)."""
        mock_security_mgr.list_accounts.return_value = {'openai': 'openai'}
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        with patch.object(web_ui.provider_ui, '_setup_codex_account', return_value=str(tmp_path)):
            result = web_ui.add_provider('', 'openai')[0]

        # Should create openai-1
        assert '✅' in result or '✓' in result
        mock_security_mgr.store_account.assert_called_once_with(
            'openai-1', 'openai', '', 'test-password'
        )

    @patch('subprocess.run')
    def test_add_provider_error(self, mock_run, web_ui, mock_security_mgr, tmp_path):
        """Test adding provider when login fails (OpenAI/Codex)."""
        mock_security_mgr.list_accounts.return_value = {}
        # Mock Codex login to fail
        mock_run.return_value = Mock(returncode=1, stderr="Login cancelled", stdout="")

        with patch.object(web_ui.provider_ui, '_setup_codex_account', return_value=str(tmp_path)):
            result = web_ui.add_provider('test', 'openai')[0]

        assert '❌' in result
        assert 'Login failed' in result or 'cancelled' in result.lower()

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
        mock_provider = Mock()
        web_ui._active_coding_provider = mock_provider

        result = web_ui.cancel_task()

        assert '🛑' in result
        assert 'cancelled' in result.lower()
        assert web_ui.cancel_requested is True
        mock_provider.stop_session.assert_called_once()

    def test_cancel_task_no_session(self, web_ui, mock_security_mgr):
        """Test cancelling when no session is running."""
        result = web_ui.cancel_task()

        assert '🛑' in result
        assert web_ui.cancel_requested is True


class TestLiveStreamPresentation:
    """Formatting and styling tests for the live activity stream."""

    def test_live_stream_spacing_removes_all_blank_lines(self):
        """Live stream should remove all blank lines for compact display."""
        from chad import web_ui

        raw = "first line\n\n\nsecond block\n\n\n\nthird"
        normalized = web_ui.normalize_live_stream_spacing(raw)

        assert normalized == "first line\nsecond block\nthird"
        rendered = web_ui.build_live_stream_html(raw, "AI")

        assert "first line\nsecond block\nthird" in rendered
        assert "\n\n" not in rendered


class TestPortResolution:
    """Ensure the UI chooses a safe port when launching."""

    @pytest.fixture(autouse=True)
    def skip_when_sockets_blocked(self):
        """Skip port resolution tests when sockets are not permitted."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
                pass
        except PermissionError:
            pytest.skip("Socket operations not permitted in this environment")

    def test_resolve_port_keeps_requested_when_free(self):
        """Requested port should be used when it is available."""
        from chad.web_ui import _resolve_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        port, ephemeral, conflicted = _resolve_port(free_port)

        assert port == free_port
        assert ephemeral is False
        assert conflicted is False

    def test_resolve_port_returns_ephemeral_when_in_use(self):
        """If the requested port is busy, fall back to an ephemeral choice."""
        from chad.web_ui import _resolve_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            busy_port = s.getsockname()[1]
            s.listen(1)

            port, ephemeral, conflicted = _resolve_port(busy_port)

        assert port != busy_port
        assert ephemeral is True
        assert conflicted is True

    def test_resolve_port_supports_explicit_ephemeral(self):
        """Port zero should always yield an ephemeral assignment."""
        from chad.web_ui import _resolve_port

        port, ephemeral, conflicted = _resolve_port(0)

        assert port > 0
        assert ephemeral is True
        assert conflicted is False

    def test_live_stream_inline_code_is_color_only(self):
        """Inline code should be colored text without a background box."""
        from chad import web_ui

        code_css_match = re.search(
            r"#live-stream-box\s+code[^}]*\{([^}]*)\}",
            web_ui.PROVIDER_PANEL_CSS,
        )
        assert code_css_match, "Expected live stream code style block"

        code_block = code_css_match.group(1)
        assert "background: none" in code_block or "background: transparent" in code_block
        assert "padding: 0" in code_block


class TestChadWebUITaskExecution:
    """Test cases for task execution in ChadWebUI."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude': 'anthropic'}
        mgr.list_role_assignments.return_value = {'CODING': 'claude'}
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
        results = list(web_ui.start_chad_task('', 'do something', 'test-coding'))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'project path' in status_value.lower() or 'task description' in status_value.lower()

    def test_start_task_missing_description(self, web_ui):
        """Test starting task without task description."""
        results = list(web_ui.start_chad_task('/tmp', '', 'test-coding'))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value

    def test_start_task_invalid_path(self, web_ui):
        """Test starting task with invalid project path."""
        results = list(web_ui.start_chad_task('/nonexistent/path/xyz', 'do something', 'test-coding'))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'Invalid project path' in status_value

    def test_start_task_missing_agents(self, mock_security_mgr):
        """Test starting task when agents are not selected."""
        from chad.web_ui import ChadWebUI
        mock_security_mgr.list_role_assignments.return_value = {}

        web_ui = ChadWebUI(mock_security_mgr, 'test-password')
        results = list(web_ui.start_chad_task('/tmp', 'do something', ''))

        assert len(results) > 0
        last_result = results[-1]
        # Error is in status header (position 2), not live stream box
        status_header = last_result[2]
        status_value = status_header.get('value', '') if isinstance(status_header, dict) else str(status_header)
        assert '❌' in status_value
        assert 'Coding Agent' in status_value

    def test_followup_restarts_with_updated_preferences(self, tmp_path, monkeypatch):
        """Follow-up should honor updated model/reasoning after task completion."""
        from chad import web_ui

        security_mgr = Mock()
        security_mgr.list_accounts.return_value = {'claude': 'anthropic'}
        security_mgr.list_role_assignments.return_value = {'CODING': 'claude'}
        security_mgr.get_account_model.return_value = 'claude-3'
        security_mgr.get_account_reasoning.return_value = 'medium'
        security_mgr.assign_role = Mock()
        security_mgr.set_account_model = Mock()
        security_mgr.set_account_reasoning = Mock()
        security_mgr.set_verification_agent = Mock()
        security_mgr.clear_role = Mock()

        created_configs = []

        class StubProvider:
            def __init__(self, config):
                self.config = config
                self.stopped = False

            def set_activity_callback(self, cb):
                self.cb = cb

            def start_session(self, project_path, context):
                return True

            def send_message(self, message):
                return None

            def get_response(self, timeout=None):
                return "codex\nok"

            def stop_session(self):
                self.stopped = True

            def supports_multi_turn(self):
                return True

            def is_alive(self):
                return not self.stopped

        def fake_create_provider(config):
            created_configs.append(config)
            return StubProvider(config)

        monkeypatch.setattr(web_ui, "create_provider", fake_create_provider)
        monkeypatch.setattr(web_ui.ChadWebUI, "_run_verification", lambda *args, **kwargs: (True, "ok"))

        ui = web_ui.ChadWebUI(security_mgr, 'test-password')
        ui.session_logger.base_dir = tmp_path

        list(ui.start_chad_task(str(tmp_path), "do something", "claude", ""))

        security_mgr.get_account_model.return_value = "claude-latest"
        security_mgr.get_account_reasoning.return_value = "high"

        list(
            ui.send_followup(
                "continue",
                ui._current_chat_history,
                coding_agent="claude",
                verification_agent="",
                coding_model="claude-latest",
                coding_reasoning="high"
            )
        )

        assert len(created_configs) == 2
        assert created_configs[-1].model_name == "claude-latest"
        assert created_configs[-1].reasoning_effort == "high"


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

    @patch('chad.web_ui._resolve_port', return_value=(7860, False, False))
    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_with_existing_password(self, mock_security_class, mock_webui_class, mock_resolve_port):
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
        mock_resolve_port.assert_called_once_with(7860)
        assert result == (None, 7860)

    @patch('chad.web_ui._resolve_port', return_value=(7860, False, False))
    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_without_password_verifies(self, mock_security_class, mock_webui_class, mock_resolve_port):
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
        mock_resolve_port.assert_called_once_with(7860)
        assert result == (None, 7860)

    @patch('chad.web_ui._resolve_port', return_value=(7860, False, False))
    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_first_run_with_password(self, mock_security_class, mock_webui_class, mock_resolve_port):
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
        mock_resolve_port.assert_called_once_with(7860)
        assert result == (None, 7860)

    @patch('chad.web_ui._resolve_port', return_value=(43210, True, True))
    @patch('chad.web_ui.ChadWebUI')
    @patch('chad.web_ui.SecurityManager')
    def test_launch_falls_back_when_port_busy(self, mock_security_class, mock_webui_class, mock_resolve_port):
        """New launches should fall back to an ephemeral port if the default is in use."""
        from chad.web_ui import launch_web_ui

        mock_security = Mock()
        mock_security.is_first_run.return_value = False
        mock_security_class.return_value = mock_security

        mock_app = Mock()
        mock_app.launch.return_value = (Mock(server_port=43210), 'http://127.0.0.1:43210', None)
        mock_webui = Mock()
        mock_webui.create_interface.return_value = mock_app
        mock_webui_class.return_value = mock_webui

        result = launch_web_ui('test-password')

        mock_resolve_port.assert_called_once_with(7860)
        mock_app.launch.assert_called_once_with(
            server_name="127.0.0.1",
            server_port=43210,
            share=False,
            inbrowser=True,
            quiet=False,
        )
        assert result == (None, 43210)


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


class TestClaudeMultiAccount:
    """Test cases for Claude multi-account support."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'claude-1': 'anthropic', 'claude-2': 'anthropic'}
        mgr.list_role_assignments.return_value = {}
        mgr.get_account_model.return_value = 'default'
        mgr.get_account_reasoning.return_value = 'default'
        return mgr

    @pytest.fixture
    def web_ui(self, mock_security_mgr):
        """Create a ChadWebUI instance with mocked dependencies."""
        from chad.web_ui import ChadWebUI
        ui = ChadWebUI(mock_security_mgr, 'test-password')
        ui.provider_ui.installer.ensure_tool = Mock(return_value=(True, "claude"))
        return ui

    @patch('pathlib.Path.home')
    def test_get_claude_config_dir_returns_isolated_path(self, mock_home, web_ui, tmp_path):
        """Each Claude account gets its own config directory."""
        mock_home.return_value = tmp_path

        config_dir_1 = web_ui.provider_ui._get_claude_config_dir('claude-1')
        config_dir_2 = web_ui.provider_ui._get_claude_config_dir('claude-2')

        assert str(config_dir_1) == str(tmp_path / ".chad" / "claude-configs" / "claude-1")
        assert str(config_dir_2) == str(tmp_path / ".chad" / "claude-configs" / "claude-2")
        assert config_dir_1 != config_dir_2

    @patch('pathlib.Path.home')
    def test_setup_claude_account_creates_directory(self, mock_home, web_ui, tmp_path):
        """Setup creates the isolated config directory."""
        mock_home.return_value = tmp_path

        result = web_ui.provider_ui._setup_claude_account('test-account')

        assert result == str(tmp_path / ".chad" / "claude-configs" / "test-account")
        assert (tmp_path / ".chad" / "claude-configs" / "test-account").exists()

    @patch('pathlib.Path.home')
    def test_claude_usage_reads_from_isolated_config(self, mock_home, web_ui, tmp_path):
        """Claude usage reads credentials from account-specific config dir."""
        import json
        mock_home.return_value = tmp_path

        # Setup isolated config directory for claude-1
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        creds = {'claudeAiOauth': {'accessToken': '', 'subscriptionType': 'PRO'}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        result = web_ui.provider_ui._get_claude_usage('claude-1')

        # Should report not logged in due to empty access token
        assert "Not logged in" in result

    @patch('pathlib.Path.home')
    @patch('requests.get')
    def test_claude_usage_with_valid_credentials(self, mock_get, mock_home, web_ui, tmp_path):
        """Claude usage fetches data when credentials are valid."""
        import json
        mock_home.return_value = tmp_path

        # Setup isolated config directory with valid credentials
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        creds = {'claudeAiOauth': {'accessToken': 'test-token', 'subscriptionType': 'PRO'}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'five_hour': {'utilization': 25}}
        mock_get.return_value = mock_response

        result = web_ui.provider_ui._get_claude_usage('claude-1')

        assert "Logged in" in result
        assert "PRO" in result

    @patch('pathlib.Path.home')
    def test_check_provider_login_uses_isolated_config(self, mock_home, web_ui, tmp_path):
        """Provider login check uses account-specific config directory."""
        import json
        mock_home.return_value = tmp_path

        # claude-1 has credentials, claude-2 does not
        config_dir_1 = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir_1.mkdir(parents=True)
        creds = {'claudeAiOauth': {'accessToken': 'test-token'}}
        (config_dir_1 / ".credentials.json").write_text(json.dumps(creds))

        # claude-2 has no credentials
        config_dir_2 = tmp_path / ".chad" / "claude-configs" / "claude-2"
        config_dir_2.mkdir(parents=True)

        logged_in_1, _ = web_ui.provider_ui._check_provider_login('anthropic', 'claude-1')
        logged_in_2, _ = web_ui.provider_ui._check_provider_login('anthropic', 'claude-2')

        assert logged_in_1 is True
        assert logged_in_2 is False

    @patch('pathlib.Path.home')
    def test_delete_provider_cleans_up_claude_config(self, mock_home, web_ui, mock_security_mgr, tmp_path):
        """Deleting Claude provider removes its config directory."""
        mock_home.return_value = tmp_path

        # Setup config directory for claude-1
        config_dir = tmp_path / ".chad" / "claude-configs" / "claude-1"
        config_dir.mkdir(parents=True)
        (config_dir / ".credentials.json").write_text('{}')

        # Delete the provider
        web_ui.provider_ui.delete_provider('claude-1', confirmed=True, card_slots=4)

        # Config directory should be removed
        assert not config_dir.exists()

    @patch('pathlib.Path.home')
    def test_add_provider_claude_login_timeout(
        self, mock_home, web_ui, mock_security_mgr, tmp_path
    ):
        """Adding Claude provider times out if OAuth not completed."""
        mock_home.return_value = tmp_path
        mock_security_mgr.list_accounts.return_value = {}

        # Mock pexpect to simulate timeout (no credentials created)
        mock_child = Mock()
        mock_child.expect = Mock(return_value=0)
        mock_child.send = Mock()
        mock_child.close = Mock()

        with patch('pexpect.spawn', return_value=mock_child):
            # Patch time to simulate timeout quickly
            with patch('time.time', side_effect=[0, 0, 200]):  # Instant timeout
                with patch('time.sleep'):
                    result = web_ui.add_provider('my-claude', 'anthropic')[0]

        # Provider should NOT be stored (login timed out)
        mock_security_mgr.store_account.assert_not_called()

        # Should show timeout error
        assert '❌' in result
        assert 'timed out' in result.lower()

        # Config directory should be cleaned up
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        assert not config_dir.exists()

    @patch('pathlib.Path.home')
    def test_add_provider_claude_login_success(
        self, mock_home, web_ui, mock_security_mgr, tmp_path
    ):
        """Adding Claude provider succeeds when OAuth completes."""
        import json
        mock_home.return_value = tmp_path
        mock_security_mgr.list_accounts.return_value = {}

        # Create the config directory and credentials file to simulate successful OAuth
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        config_dir.mkdir(parents=True)
        creds = {'claudeAiOauth': {'accessToken': 'test-token', 'subscriptionType': 'pro'}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock pexpect - credentials already exist so pexpect won't be called
        # (the login check passes before pexpect is used)
        result = web_ui.add_provider('my-claude', 'anthropic')[0]

        # Provider should be stored
        mock_security_mgr.store_account.assert_called_once_with(
            'my-claude', 'anthropic', '', 'test-password'
        )

        # Should show success
        assert '✅' in result
        assert 'logged in' in result.lower()

    @patch('pathlib.Path.home')
    @patch('requests.get')
    def test_add_provider_claude_already_logged_in(self, mock_get, mock_home, web_ui, mock_security_mgr, tmp_path):
        """Adding Claude provider when already logged in shows success."""
        import json
        mock_home.return_value = tmp_path
        mock_security_mgr.list_accounts.return_value = {}

        # Pre-create credentials file (user already logged in)
        config_dir = tmp_path / ".chad" / "claude-configs" / "my-claude"
        config_dir.mkdir(parents=True)
        creds = {'claudeAiOauth': {'accessToken': 'test-token', 'subscriptionType': 'pro'}}
        (config_dir / ".credentials.json").write_text(json.dumps(creds))

        # Mock successful API call
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'five_hour': {'utilization': 25}}
        mock_get.return_value = mock_response

        result = web_ui.add_provider('my-claude', 'anthropic')[0]

        # Should show logged in status
        assert '✅' in result
        assert 'logged in' in result.lower()
        mock_security_mgr.store_account.assert_called_once()


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
        assert "managed_mode" not in data
        assert len(data["conversation"]) == 0

        # Update with conversation
        chat_history = [
            {"role": "user", "content": "**Task:** Plan the work"},
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

        # Cleanup
        log_path.unlink()
        # Also clean up the chad directory if empty
        chad_dir = Path(tempfile.gettempdir()) / "chad"
        if chad_dir.exists() and not any(chad_dir.iterdir()):
            chad_dir.rmdir()


class TestSessionLogIncludesTask:
    """Test that session log conversation includes the task description."""

    @pytest.fixture
    def mock_security_mgr(self):
        """Create a mock security manager with roles assigned."""
        mgr = Mock()
        mgr.list_accounts.return_value = {'coding-ai': 'mock'}
        mgr.list_role_assignments.return_value = {'CODING': 'coding-ai'}
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

        # Run task with agent selections
        list(web_ui.start_chad_task(str(test_dir), task_description, 'coding-ai'))

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

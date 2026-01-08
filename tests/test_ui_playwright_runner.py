import os
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch


def test_run_screenshot_subprocess_builds_output(monkeypatch, tmp_path):
    from chad.verification import ui_playwright_runner as upr

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = "ok"
            self.stderr = ""

    captured_cmd = []
    captured_env = {}

    def fake_run(cmd, capture_output, text, cwd, env, **kwargs):
        captured_cmd[:] = cmd
        captured_env.update(env)
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).parent.mkdir(parents=True, exist_ok=True)
        base_output = Path(cmd[out_idx])
        base_output.write_text("png")
        light_path = base_output.with_name(f"{base_output.stem}_light{base_output.suffix}")
        light_path.write_text("png")
        return DummyResult()

    monkeypatch.setattr(upr.subprocess, "run", fake_run)
    res = upr.run_screenshot_subprocess(
        tab="run",
        headless=True,
        viewport={"width": 100, "height": 100},
        label="before",
        issue_id="abc",
    )

    assert res["success"] is True
    assert Path(res["screenshot"]).exists()
    assert len(res["screenshots"]) == 2
    expected_python = upr.PROJECT_ROOT / "venv" / "bin" / "python"
    if not expected_python.exists():
        expected_python = Path(sys.executable)
    assert captured_cmd[0] == os.fspath(expected_python)
    assert captured_env["PLAYWRIGHT_BROWSERS_PATH"].endswith("ms-playwright")


class TestProcessCleanup:
    """Test cases for ProcessRegistry-based cleanup mechanism."""

    def test_registry_created_on_first_call(self, monkeypatch, tmp_path):
        """_get_test_registry should create a registry on first call."""
        from chad.verification import ui_playwright_runner as upr

        # Reset the module-level registry
        upr._test_server_registry = None

        # Set up a temp pidfile location
        monkeypatch.setattr(
            "tempfile.gettempdir",
            lambda: str(tmp_path),
        )

        registry = upr._get_test_registry()

        assert registry is not None
        assert upr._test_server_registry is registry

    def test_start_chad_registers_process(self, monkeypatch, tmp_path):
        """start_chad should register the spawned process in the registry."""
        from chad.verification import ui_playwright_runner as upr
        from chad.process_registry import ProcessRegistry

        # Create a test registry with temp pidfile
        test_pidfile = tmp_path / "test_servers.pids"
        test_registry = ProcessRegistry(pidfile=test_pidfile)
        monkeypatch.setattr(upr, "_test_server_registry", test_registry)

        # Mock Popen to return a fake process
        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.poll.return_value = None
        mock_process.stdout.readline.return_value = "CHAD_PORT=9999\n"

        with patch.object(upr.subprocess, "Popen", return_value=mock_process):
            with patch.object(upr, "_wait_for_ready"):
                env = Mock()
                env.config_path = Path("/tmp/config.json")
                env.project_dir = Path("/tmp/project")
                env.password = ""
                env.env_vars = {}

                upr.start_chad(env)

        # Process should be registered
        assert 54321 in test_registry._processes

    def test_stop_chad_terminates_via_registry(self, monkeypatch, tmp_path):
        """stop_chad should use registry.terminate to clean up."""
        from chad.verification import ui_playwright_runner as upr
        from chad.process_registry import ProcessRegistry

        # Create a test registry
        test_pidfile = tmp_path / "test_servers.pids"
        test_registry = ProcessRegistry(pidfile=test_pidfile)
        monkeypatch.setattr(upr, "_test_server_registry", test_registry)

        # Track terminate calls
        terminated_pids = []

        def track_terminate(pid, timeout=2.0):
            terminated_pids.append(pid)
            # Don't actually try to terminate (no real process)
            test_registry.unregister(pid)
            return True

        monkeypatch.setattr(test_registry, "terminate", track_terminate)

        # Create a mock instance
        mock_process = Mock()
        mock_process.pid = 99999
        instance = Mock()
        instance.process = mock_process

        upr.stop_chad(instance)

        assert 99999 in terminated_pids

    def test_start_chad_passes_parent_pid(self, monkeypatch, tmp_path):
        """start_chad should set CHAD_PARENT_PID in environment."""
        from chad.verification import ui_playwright_runner as upr
        from chad.process_registry import ProcessRegistry

        # Create a test registry
        test_pidfile = tmp_path / "test_servers.pids"
        test_registry = ProcessRegistry(pidfile=test_pidfile)
        monkeypatch.setattr(upr, "_test_server_registry", test_registry)

        captured_env = {}

        def fake_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            mock_process = Mock()
            mock_process.pid = 11111
            mock_process.poll.return_value = None
            mock_process.stdout.readline.return_value = "CHAD_PORT=9999\n"
            return mock_process

        with patch.object(upr.subprocess, "Popen", side_effect=fake_popen):
            with patch.object(upr, "_wait_for_ready"):
                env = Mock()
                env.config_path = Path("/tmp/config.json")
                env.project_dir = Path("/tmp/project")
                env.password = ""
                env.env_vars = {}

                upr.start_chad(env)

        assert "CHAD_PARENT_PID" in captured_env
        assert captured_env["CHAD_PARENT_PID"] == str(os.getpid())

    def test_cleanup_all_test_servers_calls_registry(self, monkeypatch, tmp_path):
        """cleanup_all_test_servers should use registry methods."""
        from chad.verification import ui_playwright_runner as upr
        from chad.process_registry import ProcessRegistry

        # Create a test registry
        test_pidfile = tmp_path / "test_servers.pids"
        test_registry = ProcessRegistry(pidfile=test_pidfile)
        monkeypatch.setattr(upr, "_test_server_registry", test_registry)

        # Track method calls
        cleanup_stale_called = False
        terminate_all_called = False

        def track_cleanup_stale():
            nonlocal cleanup_stale_called
            cleanup_stale_called = True
            return []

        def track_terminate_all():
            nonlocal terminate_all_called
            terminate_all_called = True
            return []

        monkeypatch.setattr(test_registry, "cleanup_stale", track_cleanup_stale)
        monkeypatch.setattr(test_registry, "terminate_all", track_terminate_all)

        upr.cleanup_all_test_servers()

        assert cleanup_stale_called
        assert terminate_all_called


class TestProcessRegistry:
    """Test cases for the ProcessRegistry class itself."""

    def test_register_adds_to_memory_and_pidfile(self, tmp_path):
        """register should track process in both memory and pidfile."""
        from chad.process_registry import ProcessRegistry

        pidfile = tmp_path / "test.pids"
        registry = ProcessRegistry(pidfile=pidfile)

        # Create a mock process
        mock_process = Mock()
        mock_process.pid = 12345

        registry.register(mock_process, "test process")

        # Check in-memory tracking
        assert 12345 in registry._processes

        # Check pidfile tracking
        assert pidfile.exists()
        content = pidfile.read_text()
        assert "12345" in content

    def test_unregister_removes_from_both(self, tmp_path):
        """unregister should remove from both memory and pidfile."""
        from chad.process_registry import ProcessRegistry

        pidfile = tmp_path / "test.pids"
        registry = ProcessRegistry(pidfile=pidfile)

        # Create and register a mock process
        mock_process = Mock()
        mock_process.pid = 67890
        registry.register(mock_process, "test process")

        # Verify registered
        assert 67890 in registry._processes

        # Unregister
        registry.unregister(67890)

        # Check removed from both
        assert 67890 not in registry._processes
        content = pidfile.read_text()
        assert "67890" not in content

    def test_cleanup_stale_kills_old_processes(self, monkeypatch, tmp_path):
        """cleanup_stale should kill processes older than max_age."""
        from chad.process_registry import ProcessRegistry

        pidfile = tmp_path / "test.pids"
        registry = ProcessRegistry(pidfile=pidfile, max_age_seconds=1.0)

        # Write an old entry directly to pidfile
        old_time = time.time() - 2.0
        pidfile.write_text(f"11111:{old_time}")

        # Track terminate calls
        terminated_pids = []

        def fake_terminate(pid, timeout=2.0):
            terminated_pids.append(pid)
            registry.unregister(pid)
            return True

        monkeypatch.setattr(registry, "terminate", fake_terminate)

        # Also mock _is_running to return True
        monkeypatch.setattr(registry, "_is_running", lambda pid: True)

        registry.cleanup_stale()

        assert 11111 in terminated_pids

    def test_verify_cleanup_returns_running_processes(self, monkeypatch, tmp_path):
        """verify_cleanup should return PIDs that are still running."""
        from chad.process_registry import ProcessRegistry

        pidfile = tmp_path / "test.pids"
        registry = ProcessRegistry(pidfile=pidfile)

        # Add some PIDs to tracking
        mock_process = Mock()
        mock_process.pid = 22222
        registry.register(mock_process, "test")

        # Mock _is_running to return True
        monkeypatch.setattr(registry, "_is_running", lambda pid: True)

        still_running = registry.verify_cleanup()

        assert 22222 in still_running

    def test_file_locking_prevents_corruption(self, tmp_path):
        """Multiple registry instances should use file locking."""
        from chad.process_registry import ProcessRegistry

        pidfile = tmp_path / "shared.pids"

        # Create two registries with same pidfile
        registry1 = ProcessRegistry(pidfile=pidfile)
        registry2 = ProcessRegistry(pidfile=pidfile)

        # Both should be able to write without corruption
        mock_process1 = Mock()
        mock_process1.pid = 1111
        mock_process2 = Mock()
        mock_process2.pid = 2222

        registry1.register(mock_process1, "p1")
        registry2.register(mock_process2, "p2")

        # Both PIDs should be in the file
        content = pidfile.read_text()
        assert "1111" in content
        assert "2222" in content

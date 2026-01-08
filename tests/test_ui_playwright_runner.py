import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch


def test_run_screenshot_subprocess_builds_output(monkeypatch, tmp_path):
    from chad import ui_playwright_runner as upr

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
    """Test cases for orphan process cleanup mechanism."""

    def test_atexit_handler_registered_on_start(self, monkeypatch):
        """start_chad should register the atexit cleanup handler."""
        from chad import ui_playwright_runner as upr

        # Reset registration state
        upr._atexit_registered = False
        upr._spawned_chad_pids.clear()

        # Track atexit.register calls
        registered_funcs = []
        monkeypatch.setattr("atexit.register", lambda f: registered_funcs.append(f))

        # Mock Popen to return a fake process
        mock_process = Mock()
        mock_process.pid = 12345
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

        assert len(registered_funcs) == 1
        assert registered_funcs[0] == upr._cleanup_orphaned_processes

    def test_spawned_pid_tracked(self, monkeypatch):
        """start_chad should track spawned process PIDs."""
        from chad import ui_playwright_runner as upr

        # Reset state
        upr._spawned_chad_pids.clear()

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

        assert 54321 in upr._spawned_chad_pids

    def test_stop_chad_removes_pid_from_registry(self, monkeypatch):
        """stop_chad should remove PID from registry."""
        from chad import ui_playwright_runner as upr

        # Add a PID to the registry
        upr._spawned_chad_pids.add(99999)

        # Mock process
        mock_process = Mock()
        mock_process.pid = 99999
        mock_process.wait.return_value = 0

        instance = Mock()
        instance.process = mock_process

        # Mock os.killpg to avoid errors on non-existent process
        if os.name != "nt":
            monkeypatch.setattr("os.killpg", lambda pid, sig: None)

        upr.stop_chad(instance)

        assert 99999 not in upr._spawned_chad_pids

    def test_start_chad_passes_parent_pid(self, monkeypatch):
        """start_chad should set CHAD_PARENT_PID in environment."""
        from chad import ui_playwright_runner as upr

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

    def test_cleanup_handler_kills_tracked_processes(self, monkeypatch):
        """_cleanup_orphaned_processes should kill all tracked PIDs."""
        from chad import ui_playwright_runner as upr

        # Add some fake PIDs
        upr._spawned_chad_pids.clear()
        upr._spawned_chad_pids.add(11111)
        upr._spawned_chad_pids.add(22222)

        killed_pids = []

        def fake_killpg(pid, sig):
            killed_pids.append(pid)

        if os.name != "nt":
            monkeypatch.setattr("os.killpg", fake_killpg)
        else:
            monkeypatch.setattr("os.kill", lambda pid, sig: killed_pids.append(pid))

        upr._cleanup_orphaned_processes()

        assert 11111 in killed_pids
        assert 22222 in killed_pids
        assert len(upr._spawned_chad_pids) == 0

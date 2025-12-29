import os
import sys
from pathlib import Path


def test_run_screenshot_subprocess_builds_output(monkeypatch, tmp_path):
    from chad import ui_playwright_runner as upr

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = "ok"
            self.stderr = ""

    captured_cmd = []
    captured_env = {}

    def fake_run(cmd, capture_output, text, cwd, env):
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
